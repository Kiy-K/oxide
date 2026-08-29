#!/usr/bin/env python3
"""Phase 2.2 §13 coding-outcome tier (reduced scope: one real bug-fix task,
conditions A and D only). Reuses eval-agent/tasks/py_bug_retry verbatim —
`app/retry.py` has a real bug (backoff_ms shrinks instead of growing) with
a pre-existing test that fails until it's fixed. Unfamiliar-repo bug-fix
task with no location given: a genuine Bucket-A-shaped edit task, not just
navigation.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_activation_eval import (  # noqa: E402
    AGENTS_BLOCK, MODEL, OXIDE_BIN, SKILL_SRC, build_env, parse_events, analyze, sh,
)

TASK_SRC = ROOT / "eval-agent/tasks/py_bug_retry"
OUT_DIR = ROOT / "docs/evals/phase-2.2"
LOG_DIR = OUT_DIR / "logs"
RESULTS_PATH = OUT_DIR / "coding_outcome.jsonl"

PROMPT = (
    "There's a bug reported against this repo's retry/backoff logic: "
    "clients are hammering the server harder on each retry instead of "
    "backing off. Find the bug and fix it. Do not change test files."
)


def run_one(condition, rep):
    run_dir = Path(tempfile.mkdtemp(prefix=f"p22-coding-{condition}-{rep}-"))
    try:
        repo = run_dir / "repo"
        shutil.copytree(TASK_SRC, repo, ignore=shutil.ignore_patterns("__pycache__"))
        binpath = run_dir / "bin"
        binpath.mkdir()
        if condition != "A":
            os.symlink(OXIDE_BIN, binpath / "oxide")
            sh([str(OXIDE_BIN), "index", str(repo), "--json"], timeout=60)
        if condition in ("C", "E"):
            skill_dst = repo / ".opencode/skills/oxide-code-context"
            skill_dst.mkdir(parents=True)
            shutil.copy(SKILL_SRC, skill_dst / "SKILL.md")
        if condition in ("D", "E"):
            (repo / "AGENTS.md").write_text(AGENTS_BLOCK)
        env = build_env(binpath, condition)

        start = time.time()
        try:
            r = sh(["opencode", "run", "--format", "json", "--dir", str(repo), "-m", MODEL, PROMPT],
                   cwd=str(repo), env={**env, "PWD": str(repo)}, timeout=280)
            stdout, rc, timed_out = r.stdout, r.returncode, False
        except subprocess.TimeoutExpired as e:
            out = e.stdout or ""
            stdout = out.decode("utf-8", "replace") if isinstance(out, bytes) else out
            rc, timed_out = -1, True
        wall = round(time.time() - start, 1)

        log_name = f"coding-{condition}-r{rep}.jsonl"
        (LOG_DIR / log_name).write_text(stdout or "")

        events = parse_events(stdout or "")
        analysis = analyze(events)

        verify = subprocess.run(["bash", "verify.sh"], cwd=repo, capture_output=True, text=True, timeout=60)
        tests_pass = verify.returncode == 0

        retry_after = (repo / "app/retry.py").read_text()
        touched_only_retry = "backoff_ms" in retry_after

        return dict(
            condition=condition, rep=rep, wall_s=wall, timed_out=timed_out, returncode=rc,
            tests_pass=tests_pass, touched_only_retry=touched_only_retry, log=log_name,
            **analysis,
        )
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    done = set()
    if RESULTS_PATH.exists():
        for line in RESULTS_PATH.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                done.add((rec["condition"], rec["rep"]))
    with RESULTS_PATH.open("a") as sink:
        for condition in ("A", "D"):
            for rep in (1, 2, 3, 4, 5):
                if (condition, rep) in done:
                    continue
                rec = run_one(condition, rep)
                sink.write(json.dumps(rec) + "\n")
                sink.flush()
                print(f"{condition} r{rep} tests_pass={rec['tests_pass']} "
                      f"oxide_ctx={rec['oxide_context_calls']} oxide_search={rec['oxide_search_calls']} "
                      f"wall={rec['wall_s']}s")


if __name__ == "__main__":
    main()
