#!/usr/bin/env python3
"""Phase 0 pilot harness: same OpenAI Codex CLI agent, same normal tools,
with or without OXIDE wired in as a real MCP tool the agent chooses whether
to call.

Both conditions run with `--ignore-user-config` -- verified empirically that
without it, Codex loads this machine's personal `~/.codex/config.toml`
(other MCP servers, skills, plugins), which would confound "normal tools"
between machines/runs. `--ignore-user-config` still uses `$CODEX_HOME` for
auth and keeps Codex's own bundled skills/tools (the same for every Codex
user) -- input tokens for a trivial prompt dropped from ~113k to ~25k with it
on, confirming it strips exactly the personal-machine bloat and nothing else.

Condition "baseline": no `-c mcp_servers.oxide...` flags at all -- Codex has
no oxide MCP server to see.
Condition "oxide": repo copy WITH the pre-built `.oxide` index (root meta
patched to the copy's own path) and `-c mcp_servers.oxide.command=...`/
`-c mcp_servers.oxide.args=["mcp"]` wiring `oxide mcp` in for this run only.
No config file is written to the repo copy at all -- Codex's MCP wiring is
pure command-line, unlike opencode's project-local opencode.json.

`--approve-for-me` is required for MCP tool calls to succeed non-interactively
(verified: without it, every oxide MCP call fails with "requires approval, but
approval policy is never"); it auto-reviews requests under a workspace-write
sandbox rather than fully disabling sandboxing.

Usage (needs the local embedder running for indexing -- see
scripts/embedder.sh -- and `index_repo.py` to have been run at least once):
    OXIDE_EMBED_URL=http://127.0.0.1:8088 \
        eval-agent/.venv/bin/python eval-agent/agentbench/run_pilot.py \
        --model gpt-5.6-luna --reps 3
"""
import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from index_repo import ensure_indexed_repo  # noqa: E402

OXIDE_BIN = ROOT / "target" / "release" / "oxide"
HERE = Path(__file__).parent
TASKS_PATH = HERE / "tasks.json"
OUT_DIR = HERE / "results"

# Heuristic file-path extraction from raw shell command strings -- Codex has
# no structured "read file" tool the way opencode/Claude Code do; every read
# goes through `command_execution` (cat/sed/rg/head/...). This is
# approximate by construction: a real methodology limitation, not a bug --
# see RESULTS.md's "methodology problems" section.
FILE_TOKEN_RE = re.compile(r"[\w./-]*/[\w./-]+\.\w+|(?<![\w.])[\w-]+\.(?:py|ts|tsx|js|jsx|rs|go|java)\b")


def sh(cmd, cwd=None, env=None, timeout=120):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, **(env or {})})


def oxide_git_sha() -> str:
    return sh(["git", "rev-parse", "HEAD"], cwd=ROOT).stdout.strip()


def codex_version() -> str:
    return sh(["codex", "--version"]).stdout.strip()


def patch_index_root(repo_copy: Path) -> None:
    """`.oxide/index.db` records its repo root as an absolute path
    (src/index.rs's `set_meta_all(..., ("root", root_str), ...)`). Copying
    `.oxide` to a new path without fixing this makes OXIDE read source from
    the OLD location. This is a plain key/value UPDATE on the `meta` table
    (schema: `meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)`) -- it doesn't
    touch index_generation/index_id/embedding identity, so it's safe."""
    db = repo_copy / ".oxide" / "index.db"
    conn = sqlite3.connect(db)
    conn.execute("UPDATE meta SET value = ? WHERE key = 'root'", (str(repo_copy.resolve()),))
    conn.commit()
    conn.close()


def stage_repo_copy(task: dict, condition: str, rep: int, workdir: Path, embedder_url: str) -> Path:
    fixture_repo = ensure_indexed_repo(task, embedder_url)
    repo_copy = workdir / f'{task["id"]}-{condition}-{rep}'
    if repo_copy.exists():
        shutil.rmtree(repo_copy)
    if condition == "oxide":
        shutil.copytree(fixture_repo, repo_copy)
        patch_index_root(repo_copy)
    else:
        shutil.copytree(fixture_repo, repo_copy, ignore=shutil.ignore_patterns(".oxide"))
    return repo_copy


def parse_stream_events(path: Path) -> list[dict]:
    events = []
    if not path.exists():
        return events
    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def classify_tool_calls(events: list[dict]) -> tuple[int, int, set[str]]:
    """Returns (fs_search_calls, oxide_calls, unique_files_inspected)."""
    fs_search, oxide_calls, files = 0, 0, set()
    for e in events:
        if e.get("type") != "item.completed":
            continue
        item = e.get("item", {})
        kind = item.get("type")
        if kind == "mcp_tool_call":
            if item.get("server") == "oxide":
                oxide_calls += 1
            else:
                fs_search += 1  # some other MCP server slipped through
        elif kind == "command_execution":
            fs_search += 1
            files.update(FILE_TOKEN_RE.findall(item.get("command", "")))
    return fs_search, oxide_calls, files


def usage_from_events(events: list[dict]) -> dict:
    for e in events:
        if e.get("type") == "turn.completed":
            return e.get("usage", {})
    return {}


def run_condition(task: dict, condition: str, rep: int, workdir: Path, embedder_url: str,
                   embedder_model: str, model: str) -> dict:
    repo_copy = stage_repo_copy(task, condition, rep, workdir, embedder_url)
    prompt = (
        task["prompt"]
        + "\n\nUse any available repository tools you find useful. Base your answer on "
        "the checked-out source rather than prior knowledge."
        "\n\nAnswer the question directly and completely in your final message; "
        "you do not need to edit any files."
    )
    events_path = OUT_DIR / "logs" / f'{task["id"]}-{condition}-{rep}.jsonl'
    answer_path = OUT_DIR / "logs" / f'{task["id"]}-{condition}-{rep}.answer.txt'
    events_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "codex", "exec", "-m", model,
        "--ignore-user-config", "--skip-git-repo-check", "--approve-for-me",
        "-C", str(repo_copy), "--json", "-o", str(answer_path),
    ]
    if condition == "oxide":
        # Codex does NOT inherit this process's env into an MCP server's
        # subprocess by default -- verified empirically: `oxide mcp` spawned
        # with an empty env falls back to OXIDE's default native embedder
        # and returns `provider_mismatch` against a Jina-built index, the
        # exact error real runs hit once tool use actually started
        # happening (see run_pilot_study2.py). The embedder identity must
        # match how the index was actually built, so it's passed explicitly
        # per-server rather than relying on inheritance.
        cmd += ["-c", f'mcp_servers.oxide.command="{OXIDE_BIN}"',
                "-c", 'mcp_servers.oxide.args=["mcp"]',
                "-c", f'mcp_servers.oxide.env.OXIDE_EMBED_URL="{embedder_url}"',
                "-c", f'mcp_servers.oxide.env.OXIDE_EMBED_MODEL="{embedder_model}"']
    cmd.append(prompt)

    start = time.time()
    with events_path.open("w") as ef:
        r = subprocess.run(cmd, cwd=repo_copy, stdout=ef, stderr=subprocess.PIPE,
                            text=True, timeout=900)
    wall = time.time() - start

    events = parse_stream_events(events_path)
    fs_search_calls, oxide_calls, files = classify_tool_calls(events)
    usage = usage_from_events(events)
    final_answer = answer_path.read_text() if answer_path.exists() else ""

    result = {
        "task": task["id"],
        "repo": task["repo"],
        "shape": task["shape"],
        "condition": condition,
        "rep": rep,
        "model_requested": model,
        "codex_version": codex_version(),
        "oxide_git_sha": oxide_git_sha(),
        "wall_s": round(wall, 1),
        "input_tokens": usage.get("input_tokens"),
        "cached_input_tokens": usage.get("cached_input_tokens"),
        "cache_write_input_tokens": usage.get("cache_write_input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "reasoning_output_tokens": usage.get("reasoning_output_tokens"),
        "tool_calls_total": fs_search_calls + oxide_calls,
        "fs_search_calls": fs_search_calls,
        "oxide_calls": oxide_calls,
        "unique_files_inspected": len(files),
        "files_inspected": sorted(files),
        "final_answer": final_answer,
        "events_path": str(events_path.relative_to(ROOT)),
        "error": None if r.returncode == 0 else (r.stderr or "")[-500:],
    }
    # repo_copy can be a several-hundred-MB OXIDE index copy; delete it right
    # after this run instead of leaving every run's copy on disk until the
    # whole grid finishes -- a fixed tmpfs /tmp (8GB on this machine) fills
    # up fast otherwise (see main()'s tmp dir, moved off /tmp for the same
    # reason).
    shutil.rmtree(repo_copy, ignore_errors=True)
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gpt-5.6-luna")
    ap.add_argument("--conditions", default="baseline,oxide")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--tasks", default="")  # comma-separated task ids, empty = all
    args = ap.parse_args()

    embedder_url = os.environ.get("OXIDE_EMBED_URL")
    assert embedder_url, "set OXIDE_EMBED_URL (see scripts/embedder.sh start)"
    embedder_model = os.environ.get("OXIDE_EMBED_MODEL")
    assert embedder_model, "set OXIDE_EMBED_MODEL (must match how the index was built)"

    tasks = json.loads(TASKS_PATH.read_text())
    if args.tasks:
        wanted = set(args.tasks.split(","))
        tasks = [t for t in tasks if t["id"] in wanted]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results_path = OUT_DIR / "results.jsonl"
    done = set()
    if results_path.exists():
        for line in results_path.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                done.add((rec["task"], rec["condition"], rec["rep"]))

    conditions = args.conditions.split(",")
    # Not the default /tmp: it's an 8GB tmpfs on this machine and a handful
    # of large repo copies (e.g. Deno's .oxide is 250MB+) exhausts it fast,
    # especially since each run now also cleans up its own copy immediately
    # (see run_condition) rather than leaving everything until this whole
    # grid finishes.
    tmp_base = HERE / ".run_tmp"
    tmp_base.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="oxide-agentbench-", dir=str(tmp_base)))
    total = len(tasks) * len(conditions) * args.reps
    print(f"{total} runs planned ({len(tasks)} tasks x {conditions} x {args.reps} reps), "
          f"model={args.model}")

    with results_path.open("a") as sink:
        for task in tasks:
            for condition in conditions:
                for rep in range(args.reps):
                    if (task["id"], condition, rep) in done:
                        continue
                    try:
                        rec = run_condition(task, condition, rep, tmp, embedder_url, embedder_model, args.model)
                    except subprocess.TimeoutExpired:
                        rec = {"task": task["id"], "condition": condition, "rep": rep,
                               "error": "timeout", "wall_s": 900}
                    sink.write(json.dumps(rec) + "\n")
                    sink.flush()
                    print(f'{task["id"]:<40} {condition:<9} rep{rep} '
                          f'tools={rec.get("tool_calls_total")} '
                          f'oxide={rec.get("oxide_calls")} '
                          f'wall={rec.get("wall_s")}s '
                          f'in_tok={rec.get("input_tokens")} '
                          f'err={rec.get("error") is not None}')
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
