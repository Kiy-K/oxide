#!/usr/bin/env python3
"""Phase 4.1 Tier 2: small, real-agent hook-vs-skill-only comparison using the
actual `claude` CLI (headless `-p`), not a scripted stand-in for it. Explicitly
small-n and directional -- see docs/evals/phase-4.1/results.md for the caveat
this carries, matching how phase-3.1 treated its own thin buckets (n=3).

Held-out tasks (new phrasing, grounded in fixture subsystems phase-3.1's own
task set never touched: notifiers.py's final-failure notification path, and
service.ts's JWT payload decoding) so results aren't contaminated by a model
having seen this exact task text in a prior phase's eval logs.

Two conditions, isolated per run by copying the indexed fixture into a fresh
temp dir with only that condition's `.claude/` config:
  hook  - contrib/agent-hooks/claude-code/settings.snippet.json's hook wired
          in; no Skill file present.
  skill - skills/oxide-code-context/SKILL.md copied in; no hook configured.

Both conditions get the real `oxide` release binary on PATH and an
already-built index, so any OXIDE usage is real, not mocked.
"""
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OXIDE_BIN = ROOT / "target/release/oxide"
HOOK_SCRIPT = ROOT / "contrib/agent-hooks/claude-code/oxide_suggest.py"
SKILL_SRC = ROOT / "skills/oxide-code-context/SKILL.md"
OUT_DIR = Path(__file__).resolve().parent
RESULTS_PATH = OUT_DIR / "tier2_results.jsonl"

TASKS = [
    {
        "id": "T2-A1",
        "bucket": "A",
        "fixture": "fixtures/py_repo",
        "prompt": (
            "There's a report that customers don't get any alert when a "
            "retried operation ultimately fails and gives up. Find where "
            "that final-failure notification happens and report the file "
            "and function. Do not edit anything."
        ),
    },
    {
        "id": "T2-A2",
        "bucket": "A",
        "fixture": "fixtures/ts_repo",
        "prompt": (
            "Some users report seeing garbled profile data right after "
            "logging in. Find where the login token's payload gets decoded "
            "on the client and report the file and function. Do not edit "
            "anything."
        ),
    },
    {
        "id": "T2-C1",
        "bucket": "C",
        "fixture": "fixtures/py_repo",
        "prompt": (
            "In `oxidepy/notifiers.py`, rename the `SlackNotifier` class to "
            "`SlackChannelNotifier`. Only touch that one file."
        ),
    },
]

CONDITIONS = ["hook", "skill"]


def sh(cmd, cwd=None, env=None, timeout=180):
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, **(env or {})},
    )


def prepare_repo(fixture_rel: str, condition: str, run_dir: Path) -> Path:
    src = ROOT / fixture_rel
    dst = run_dir / f"{Path(fixture_rel).name}-{condition}-{uuid.uuid4().hex[:6]}"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".oxide"))
    # oxide's root discovery requires a git repository; the fixture dirs are
    # plain subdirectories of this repo, not their own git checkouts.
    sh(["git", "init", "-q"], cwd=dst)

    r = sh([str(OXIDE_BIN), "index", "--json"], cwd=dst, env={"OXIDE_EMBED_NATIVE": "hashed"})
    assert r.returncode == 0, f"index failed: {r.stdout}\n{r.stderr}"

    claude_dir = dst / ".claude"
    claude_dir.mkdir(exist_ok=True)
    if condition == "hook":
        settings = {
            "hooks": {
                "UserPromptSubmit": [
                    {"hooks": [{"type": "command", "command": f"python3 {HOOK_SCRIPT}", "timeout": 5}]}
                ]
            }
        }
        (claude_dir / "settings.json").write_text(json.dumps(settings))
    elif condition == "skill":
        skill_dir = claude_dir / "skills" / "oxide-code-context"
        skill_dir.mkdir(parents=True)
        shutil.copy(SKILL_SRC, skill_dir / "SKILL.md")
    else:
        raise ValueError(condition)
    return dst


def run_task(task: dict, condition: str, run_dir: Path) -> dict:
    repo = prepare_repo(task["fixture"], condition, run_dir)
    env = {"PATH": f"{OXIDE_BIN.parent}:{os.environ.get('PATH', '')}"}

    start = time.time()
    r = sh(
        ["claude", "-p", task["prompt"], "--output-format", "stream-json",
         "--verbose", "--allowedTools", "Bash,Read,Grep,Glob,Edit,Skill"],
        cwd=repo, env=env, timeout=180,
    )
    wall_s = round(time.time() - start, 1)

    tool_calls = []
    result_obj = None
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "assistant":
            for block in ev.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    tool_calls.append({"name": block.get("name"), "input": block.get("input")})
        if ev.get("type") == "result":
            result_obj = ev

    def is_oxide_call(tc):
        name = (tc.get("name") or "").lower()
        inp = json.dumps(tc.get("input") or {}).lower()
        return "oxide" in name or "oxide query" in inp or "oxide search" in inp

    used_oxide = any(is_oxide_call(tc) for tc in tool_calls)
    skill_loaded = any((tc.get("name") or "").lower() == "skill" for tc in tool_calls)

    rec = {
        "task": task["id"],
        "bucket": task["bucket"],
        "condition": condition,
        "wall_s": wall_s,
        "total_tool_calls": len(tool_calls),
        "tool_names": [tc["name"] for tc in tool_calls],
        "used_oxide": used_oxide,
        "skill_loaded": skill_loaded,
        "exit_code": r.returncode,
        "stderr_tail": (r.stderr or "")[-500:],
    }
    if result_obj:
        rec["cost_usd"] = result_obj.get("total_cost_usd")
        usage = result_obj.get("usage", {})
        rec["tokens_total"] = (
            usage.get("input_tokens", 0)
            + usage.get("output_tokens", 0)
            + usage.get("cache_read_input_tokens", 0)
            + usage.get("cache_creation_input_tokens", 0)
        )
        rec["is_error"] = result_obj.get("is_error")
        rec["result_text"] = (result_obj.get("result") or "")[:2000]
    else:
        rec["cost_usd"] = None
        rec["tokens_total"] = None
        rec["is_error"] = True
        rec["result_text"] = ""
        rec["parse_failure"] = True
    return rec


def main():
    run_dir = Path("/tmp") / f"oxide-phase4.1-tier2-{uuid.uuid4().hex[:8]}"
    run_dir.mkdir(parents=True)
    print(f"run dir: {run_dir}")

    assert OXIDE_BIN.exists(), "build the release binary first: cargo build --release -j 2"

    with RESULTS_PATH.open("a") as sink:
        for task in TASKS:
            for cond in CONDITIONS:
                print(f"--- {task['id']} / {cond} ---")
                try:
                    rec = run_task(task, cond, run_dir)
                except subprocess.TimeoutExpired:
                    rec = {"task": task["id"], "bucket": task["bucket"], "condition": cond,
                           "timed_out": True}
                sink.write(json.dumps(rec) + "\n")
                sink.flush()
                print(f"  {json.dumps(rec, indent=2)[:600]}")

    shutil.rmtree(run_dir, ignore_errors=True)
    print(f"\nWrote {RESULTS_PATH}")


if __name__ == "__main__":
    main()
