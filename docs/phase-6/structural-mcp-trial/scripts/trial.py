"""Structural-mode MCP trial (preregistration.md): the same agent on the same tasks with
OXIDE's one MCP tool, `oxide_search`, with (B) and without (A) its structural mode.

    trial.py prepare              copy and index every task repository
    trial.py run REP [WORKERS]    every task x arm once, in a seeded random order
    trial.py dev                  the unscored dev task, both arms (harness check)
    trial.py grade OUT.json       score every finished run
    trial.py verify               the repository copies still match their sources

Agent: Claude Code (`claude -p`), fixed model, an isolated config directory (no user
settings, hooks, memory or CLAUDE.md; see SETTINGS), MCP tool search off so the tool schema is
in the model's context (README § Discovery), read-only tools only. Arm A's server runs
with `ast_grep_py` blocked, as on an install without the `structural` extra, so its
schema has no structural mode; arm B's is the same server with the extra.
Data lives in $OXIDE_TRIAL_DATA (default ~/Projects/oxide-eval-data/oxide-structural-trial).
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts"))
from truth import REPOS  # noqa: E402

REPO = HERE.parents[2]
DATA = Path(
    os.environ.get(
        "OXIDE_TRIAL_DATA", Path.home() / "Projects/oxide-eval-data/oxide-structural-trial"
    )
)
PYTHON = str(REPO / ".venv/bin/python")
MODEL = "claude-sonnet-5-5"
TIMEOUT_S = 15 * 60
ARMS = ("A", "B")
SEED = 20261018
CAP = ["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=3G", "-p", "MemorySwapMax=0"]
CAP += ["-p", "CPUQuota=200%"]
READ_ONLY = ["rg", "grep", "find", "ls", "cat", "head", "tail", "wc", "sed", "awk", "sort"]
ALLOWED = ["Read", "Grep", "Glob", "mcp__oxide__oxide_search"]
ALLOWED += [f"Bash({c}:*)" for c in READ_ONLY]
ENV = {
    "HOME": str(DATA / "home"),
    "CLAUDE_CONFIG_DIR": str(DATA / "claude-config"),
    "ENABLE_TOOL_SEARCH": "false",
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "LANG": "C.UTF-8",
    "PATH": f"{Path(shutil.which('claude') or '/usr/bin').parent}:/usr/bin",
}
PROMPT = """You are in a checkout of the {repo} repository (the current directory). \
Answer the question below by inspecting the code. Do not modify any file.

{question}

OXIDE's MCP tool `oxide_search` can query an index of this checkout. Use it if it helps.

End your reply with a line `ANSWER:` followed by one `path:line` per line \
(repository-relative path, 1-based line number) and nothing after them."""
NO_EXTRA = (
    "import sys; sys.modules['ast_grep_py'] = None; "
    "from oxide.cli import main; sys.exit(main(sys.argv[1:]))"
)
ANSWER = re.compile(r"([\w./@+\-\[\]]+\.(?:py|tsx?)):(\d+)")


def load() -> dict[str, object]:
    return json.loads((HERE / "tasks.json").read_text())


def tasks() -> list[dict[str, str]]:
    return load()["tasks"]  # type: ignore[return-value]


def checkout(repo: str) -> Path:
    return DATA / "repos" / repo


def digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


# Account-synced plugins still load in a fresh config directory: their hooks are off,
# and without the Skill tool their skills cannot be invoked.
SETTINGS = {
    "disableAllHooks": True,
    "autoMemoryEnabled": False,
    "includeCoAuthoredBy": False,
    "attribution": {"commit": "", "pr": ""},
}


def cmd_prepare() -> None:
    config = DATA / "claude-config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "settings.json").write_text(json.dumps(SETTINGS))
    if not (config / ".credentials.json").exists():
        (config / ".credentials.json").symlink_to(Path.home() / ".claude/.credentials.json")
    for repo in sorted({t["repo"] for t in [*tasks(), load()["dev"]]}):  # type: ignore[list-item]
        target = checkout(repo)
        if not target.exists():
            shutil.copytree(REPOS[repo], target, symlinks=True)
        args = [*CAP, PYTHON, "-m", "oxide", "index", str(target), "--data-dir", str(DATA / "data")]
        print(repo, subprocess.run(args, capture_output=True, text=True, check=True).stdout.strip())


def server(arm: str, repo: str) -> dict[str, object]:
    serve = ["mcp", "--repo", str(checkout(repo)), "--data-dir", str(DATA / "data")]
    args = ["-m", "oxide", *serve] if arm == "B" else ["-c", NO_EXTRA, *serve]
    return {"mcpServers": {"oxide": {"command": PYTHON, "args": args}}}


def run_one(task: dict[str, str], arm: str, rep: str) -> None:
    out = DATA / "runs" / rep / arm / task["id"]
    if (out / "meta.json").exists():
        return
    out.mkdir(parents=True, exist_ok=True)
    (out / "mcp.json").write_text(json.dumps(server(arm, task["repo"])))
    prompt = PROMPT.format(repo=task["repo"], question=task["question"])
    (out / "prompt.txt").write_text(prompt)
    command = [
        "timeout", str(TIMEOUT_S), "claude", "-p", "--model", MODEL,
        "--output-format", "stream-json", "--verbose", "--no-session-persistence",
        "--strict-mcp-config", "--mcp-config", str(out / "mcp.json"),
        "--tools", "Bash", "Read", "Grep", "Glob",
        "--allowedTools", *ALLOWED,
    ]  # fmt: skip
    started = time.time()
    with (out / "events.jsonl").open("w") as events, (out / "stderr.txt").open("w") as err:
        code = subprocess.run(
            command,
            input=prompt,
            text=True,
            stdout=events,
            stderr=err,
            cwd=checkout(task["repo"]),
            env=ENV,
        ).returncode
    meta = {
        "task": task["id"],
        "arm": arm,
        "rep": rep,
        "model": MODEL,
        "exit_code": code,
        "timed_out": code == 124,
        "wall_s": round(time.time() - started, 1),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "claude_version": subprocess.run(
            ["claude", "--version"], capture_output=True, text=True
        ).stdout.strip(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started)),
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    print(task["id"], arm, rep, f"exit={code}", f"{meta['wall_s']}s", flush=True)


def cmd_run(rep: str, workers: int, todo: list[dict[str, str]] | None = None) -> None:
    jobs = [(t, a) for t in (todo or tasks()) for a in ARMS]
    random.Random(f"{SEED}-{rep}").shuffle(jobs)
    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(lambda job: run_one(job[0], job[1], rep), jobs))


def events(run: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in (run / "events.jsonl").read_text().splitlines() if line]


def answer(text: str, repo: str) -> list[tuple[str, int]]:
    tail = text.rsplit("ANSWER:", 1)[1] if "ANSWER:" in text else ""
    prefix = str(checkout(repo)) + "/"
    found = []
    for path, line in ANSWER.findall(tail):
        path = path.removeprefix(prefix).removeprefix("./")
        found.append((path, int(line)))
    return sorted(set(found))


def score(found: list[tuple[str, int]], truth: list[list[object]]) -> dict[str, float]:
    matched: set[int] = set()
    false = 0
    for path, line in found:
        hits = [i for i, (p, a, b) in enumerate(truth) if p == path and a <= line <= b]  # type: ignore[operator]
        if not hits:
            false += 1
        elif not set(hits) <= matched:
            matched.add(next(i for i in hits if i not in matched))
        # an answer inside an item already matched is a duplicate, not a false positive
    tp = len(matched)
    precision = tp / (tp + false) if tp + false else 0.0
    recall = tp / len(truth) if truth else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": false, "fn": len(truth) - tp, "precision": precision,
            "recall": recall, "f1": f1}  # fmt: skip


def usage(run: Path) -> dict[str, object]:
    calls, errors, modes = [], 0, {}
    pending: dict[str, str] = {}
    result: dict[str, object] = {}
    for e in events(run):
        message = e.get("message")
        content = message.get("content", []) if isinstance(message, dict) else []
        for c in content if isinstance(content, list) else []:
            if c.get("type") == "tool_use":
                name = c["name"]
                if name == "mcp__oxide__oxide_search":
                    name = f"oxide:{c['input'].get('mode', 'search')}"
                calls.append(name)
                pending[c["id"]] = name
            elif c.get("type") == "tool_result" and c.get("is_error"):
                if pending.get(c.get("tool_use_id"), "") == "oxide:structural":
                    errors += 1
        if e.get("type") == "result":
            result = e
    for name in calls:
        modes[name] = modes.get(name, 0) + 1
    u = result.get("usage", {})
    assert isinstance(u, dict)
    tokens = sum(int(u.get(k, 0)) for k in ("input_tokens", "output_tokens",
                 "cache_creation_input_tokens", "cache_read_input_tokens"))  # fmt: skip
    return {
        "calls": modes,
        "structural_calls": modes.get("oxide:structural", 0),
        "structural_errors": errors,
        "tokens": tokens,
        "cost_usd": result.get("total_cost_usd"),
        "turns": result.get("num_turns"),
        "result": result.get("result", ""),
        "is_error": result.get("is_error", True),
    }


def cmd_grade(out: Path) -> None:
    truth = json.loads((HERE / "results/truth.json").read_text())
    by_id = {t["id"]: t for t in [*tasks(), load()["dev"]]}  # type: ignore[list-item]
    rows = []
    for meta_path in sorted((DATA / "runs").glob("*/*/*/meta.json")):
        run, meta = meta_path.parent, json.loads(meta_path.read_text())
        task = by_id[meta["task"]]
        expected = task.get("truth") or truth[task["id"]]
        used = usage(run)
        found = answer(str(used.pop("result")), task["repo"])
        technical = bool(meta["timed_out"] or meta["exit_code"] or used["is_error"])
        rows.append({**meta, "kind": task["kind"], "answers": len(found),
                     "technical_failure": technical or not found, **used,
                     **score(found, expected)})  # fmt: skip
    out.write_text(json.dumps(rows, indent=1) + "\n")
    print(len(rows), "runs graded")


def cmd_verify() -> None:
    for repo in sorted({t["repo"] for t in [*tasks(), load()["dev"]]}):  # type: ignore[list-item]
        same = digest(checkout(repo)) == digest(REPOS[repo])
        print(repo, "unchanged" if same else "CHANGED")


def main() -> None:
    command = sys.argv[1]
    if command == "prepare":
        cmd_prepare()
    elif command == "run":
        cmd_run(f"r{sys.argv[2]}", int(sys.argv[3]) if len(sys.argv) > 3 else 3)
    elif command == "dev":
        cmd_run("dev", 2, [load()["dev"]])  # type: ignore[list-item]
    elif command == "grade":
        cmd_grade(Path(sys.argv[2]))
    elif command == "verify":
        cmd_verify()


if __name__ == "__main__":
    main()
