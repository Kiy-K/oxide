#!/usr/bin/env python3
"""Study 2: does a tiny realistic routing policy change OXIDE adoption, and
if OXIDE is used, does it actually reduce repository wandering without
costing answer quality?

Three conditions, otherwise identical to run_pilot.py's harness (same model,
same --ignore-user-config/--approve-for-me Codex invocation, same repo
copy/index staging reused from run_pilot.py/index_repo.py):

  native       -- no OXIDE MCP server at all (same as Study 1's "baseline").
  oxide_guided -- OXIDE available + one extra paragraph in the prompt naming
                  a realistic routing policy ("use OXIDE query before broad
                  grep/read for unfamiliar discovery; use normal tools for
                  exact literals/known files/verification"). This is the
                  "realistic product integration" condition.
  oxide_first  -- OXIDE available + a hard instruction to call oxide query
                  exactly once before anything else, then unrestricted.
                  DIAGNOSTIC ONLY: this is prompt-level compliance, not a
                  code-enforced constraint (Codex's CLI has no hook to force
                  a specific first tool call) -- `first_call_is_oxide` in
                  each result records whether the model actually complied,
                  so non-compliance is itself visible, not hidden.

This is a separate, held-out task set (tasks_study2.json) from Study 1's
frozen 12 tasks -- Study 1's results/tasks are never touched by this file.

Usage:
    eval-agent/.venv/bin/python eval-agent/agentbench/run_pilot_study2.py \
        --model gpt-5.6-luna --reps 1
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from run_pilot import (  # noqa: E402
    OXIDE_BIN, ROOT, classify_tool_calls, codex_version, oxide_git_sha,
    parse_stream_events, patch_index_root, usage_from_events,
)
from index_repo import ensure_indexed_repo  # noqa: E402

TASKS_PATH = HERE / "tasks_study2.json"
OUT_DIR = HERE / "results_study2"

CONDITIONS = ["native", "oxide_guided", "oxide_first"]

NEUTRAL_LINE = (
    "Use any available repository tools you find useful. Base your answer on "
    "the checked-out source rather than prior knowledge."
)
ROUTING_POLICY = (
    "For unfamiliar multi-file repository discovery, use OXIDE query before broad "
    "grep/read exploration. Use OXIDE search for focused symbol/concept lookup. "
    "Use normal grep/read for exact literals, known files, and verification."
)
FORCE_FIRST_QUERY = (
    "Before doing anything else, call the OXIDE query MCP tool exactly once with "
    "this task as your query. After that one call, you may explore freely with any "
    "tool you find useful."
)
FINAL_ANSWER_LINE = (
    "Answer the question directly and completely in your final message; "
    "you do not need to edit any files."
)


def build_prompt(task: dict, condition: str) -> str:
    parts = [task["prompt"], NEUTRAL_LINE]
    if condition == "oxide_guided":
        parts.append(ROUTING_POLICY)
    elif condition == "oxide_first":
        parts.append(FORCE_FIRST_QUERY)
    parts.append(FINAL_ANSWER_LINE)
    return "\n\n".join(parts)


def stage_repo_copy(task: dict, condition: str, rep: int, workdir: Path, embedder_url: str) -> Path:
    fixture_repo = ensure_indexed_repo(task, embedder_url)
    repo_copy = workdir / f'{task["id"]}-{condition}-{rep}'
    if repo_copy.exists():
        shutil.rmtree(repo_copy)
    if condition == "native":
        shutil.copytree(fixture_repo, repo_copy, ignore=shutil.ignore_patterns(".oxide"))
    else:
        shutil.copytree(fixture_repo, repo_copy)
        patch_index_root(repo_copy)
    return repo_copy


def first_tool_call_is_oxide(events: list[dict]) -> bool | None:
    """None if the run made no tool calls at all."""
    for e in events:
        if e.get("type") != "item.completed":
            continue
        item = e.get("item", {})
        kind = item.get("type")
        if kind == "mcp_tool_call":
            return item.get("server") == "oxide"
        if kind == "command_execution":
            return False
    return None


def oxide_context_chars(events: list[dict]) -> int:
    """Total size of content OXIDE's query/search tools actually returned --
    a rough proxy for "OXIDE context tokens returned" (chars/4). Failed calls
    (see oxide_call_outcomes) contribute 0, same as a genuinely empty result."""
    total = 0
    for e in events:
        if e.get("type") != "item.completed":
            continue
        item = e.get("item", {})
        if item.get("type") == "mcp_tool_call" and item.get("server") == "oxide":
            result = item.get("result") or {}
            for block in result.get("content", []) if isinstance(result, dict) else []:
                total += len(block.get("text", "")) if isinstance(block, dict) else 0
    return total


def oxide_call_outcomes(events: list[dict]) -> tuple[int, int]:
    """(ok, failed) among oxide mcp_tool_call items. A raw oxide_calls count
    conflates a successful retrieval with a rejected call (e.g. discovered
    empirically: the model guessing `path: ""` gets `-32602: path must not
    be empty` -- `path` isn't in the tool's `required` schema, so a model
    has no signal it needs a real value). Distinguishing ok/failed keeps
    that discoverability problem visible instead of counting it as usage."""
    ok = failed = 0
    for e in events:
        if e.get("type") != "item.completed":
            continue
        item = e.get("item", {})
        if item.get("type") == "mcp_tool_call" and item.get("server") == "oxide":
            if item.get("status") == "failed" or item.get("error"):
                failed += 1
            else:
                ok += 1
    return ok, failed


def sh(cmd, cwd=None, timeout=120):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def run_condition(task: dict, condition: str, rep: int, workdir: Path, embedder_url: str,
                   embedder_model: str, model: str) -> dict:
    repo_copy = stage_repo_copy(task, condition, rep, workdir, embedder_url)
    prompt = build_prompt(task, condition)

    events_path = OUT_DIR / "logs" / f'{task["id"]}-{condition}-{rep}.jsonl'
    answer_path = OUT_DIR / "logs" / f'{task["id"]}-{condition}-{rep}.answer.txt'
    events_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "codex", "exec", "-m", model,
        "--ignore-user-config", "--skip-git-repo-check", "--approve-for-me",
        "-C", str(repo_copy), "--json", "-o", str(answer_path),
    ]
    if condition != "native":
        # See run_pilot.py's run_condition: Codex does not inherit this
        # process's env into the MCP server subprocess, so the embedder
        # identity must be passed explicitly or `oxide mcp` silently falls
        # back to the default native embedder and every call fails with
        # `provider_mismatch` against this Jina-built index -- confirmed
        # empirically as the real cause of every failed oxide_calls so far.
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
    oxide_calls_ok, oxide_calls_failed = oxide_call_outcomes(events)
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
        "oxide_calls_ok": oxide_calls_ok,
        "oxide_calls_failed": oxide_calls_failed,
        "oxide_context_chars": oxide_context_chars(events),
        "first_call_is_oxide": first_tool_call_is_oxide(events),
        "unique_files_inspected": len(files),
        "files_inspected": sorted(files),
        "final_answer": final_answer,
        "events_path": str(events_path.relative_to(ROOT)),
        "error": None if r.returncode == 0 else (r.stderr or "")[-500:],
    }
    # See run_pilot.py's run_condition for why this cleanup happens per-run
    # rather than only at the end of the whole grid (tmpfs /tmp exhaustion).
    shutil.rmtree(repo_copy, ignore_errors=True)
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gpt-5.6-luna")
    ap.add_argument("--conditions", default=",".join(CONDITIONS))
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--tasks", default="")
    args = ap.parse_args()

    embedder_url = os.environ.get("OXIDE_EMBED_URL")
    assert embedder_url, "set OXIDE_EMBED_URL"
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
    # Not the default /tmp: it's an 8GB tmpfs on this machine and already
    # exhausted it once mid-grid (large Deno .oxide copies); see
    # run_pilot.py's main() for the same fix.
    tmp_base = HERE / ".run_tmp"
    tmp_base.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="oxide-agentbench-study2-", dir=str(tmp_base)))
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
                    print(f'{task["id"]:<40} {condition:<13} rep{rep} '
                          f'tools={rec.get("tool_calls_total")} '
                          f'oxide={rec.get("oxide_calls")}(ok={rec.get("oxide_calls_ok")}) '
                          f'first_oxide={rec.get("first_call_is_oxide")} '
                          f'wall={rec.get("wall_s")}s '
                          f'in_tok={rec.get("input_tokens")} '
                          f'err={rec.get("error") is not None}')
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
