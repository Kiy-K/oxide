#!/usr/bin/env python3
"""Phase 2.3 §12: instruction-interference check for the winning candidate
(E1) -- clean/isolated opencode config (--pure, no ambient plugins/skills/
codegraph MCP) vs this machine's normal developer config. Documented only,
no compatibility hacks. gpt-5.6-luna (unlike muse-spark) does not hang
under --pure, so this comparison is actually achievable here."""
import json
import os
import sys
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_variants import (  # noqa: E402
    MODEL, TASKS, VARIANTS, setup_repo, build_env, sh, parse_events, analyze,
)

OUT_DIR = ROOT / "docs/evals/phase-2.3"
LOG_DIR = OUT_DIR / "logs"
RESULTS_PATH = OUT_DIR / "interference.jsonl"


def run_one(task, config, rep):
    run_dir = Path(tempfile.mkdtemp(prefix=f"p23-iface-{task['id']}-{config}-{rep}-"))
    try:
        repo, binpath = setup_repo(task, "E1", run_dir)
        env = build_env(binpath)
        cmd = ["opencode", "run", "--auto", "--format", "json", "--dir", str(repo), "-m", MODEL, task["prompt"]]
        if config == "clean":
            cmd.insert(2, "--pure")
        start = time.time()
        r = sh(cmd, cwd=str(repo), env={**env, "PWD": str(repo)}, timeout=200)
        wall = round(time.time() - start, 1)

        log_name = f"iface-{task['id']}-{config}-r{rep}.jsonl"
        (LOG_DIR / log_name).write_text(r.stdout or "")

        events = parse_events(r.stdout or "")
        analysis = analyze(events)
        return dict(task=task["id"], config=config, rep=rep, wall_s=wall, log=log_name, **analysis)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    tasks = [t for t in TASKS if t["id"] in ("A1", "A2")]
    done = set()
    if RESULTS_PATH.exists():
        for line in RESULTS_PATH.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                done.add((rec["task"], rec["config"], rec["rep"]))
    with RESULTS_PATH.open("a") as sink:
        for t in tasks:
            for config in ("clean", "normal"):
                for rep in (1, 2, 3):
                    if (t["id"], config, rep) in done:
                        continue
                    rec = run_one(t, config, rep)
                    sink.write(json.dumps(rec) + "\n")
                    sink.flush()
                    print(f"{t['id']} {config} r{rep} used_oxide={rec['used_oxide']} "
                          f"first={rec['first_action']} native_before={rec['native_calls_before_oxide']}")


if __name__ == "__main__":
    main()
