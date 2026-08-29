#!/usr/bin/env python3
"""Phase 2.3 coding-outcome tier: reuses eval-agent/tasks/py_bug_retry
verbatim (same real bug as Phase 2.2), run under E0 (baseline) and the
winning variant, with enough reps up front to absorb some attrition."""
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_variants import MODEL, OXIDE_BIN, VARIANTS, build_env, build_skill_file, sh  # noqa: E402
from run_variants import parse_events, analyze  # noqa: E402
import os  # noqa: E402

TASK_SRC = ROOT / "eval-agent/tasks/py_bug_retry"
OUT_DIR = ROOT / "docs/evals/phase-2.3"
LOG_DIR = OUT_DIR / "logs"
RESULTS_PATH = OUT_DIR / "coding_outcome.jsonl"

PROMPT = (
    "There's a bug reported against this repo's retry/backoff logic: "
    "clients are hammering the server harder on each retry instead of "
    "backing off. Find the bug and fix it. Do not change test files."
)


def run_one(variant, rep):
    run_dir = Path(tempfile.mkdtemp(prefix=f"p23-coding-{variant}-{rep}-"))
    try:
        repo = run_dir / "repo"
        shutil.copytree(TASK_SRC, repo, ignore=shutil.ignore_patterns("__pycache__"))
        binpath = run_dir / "bin"
        binpath.mkdir()
        os.symlink(OXIDE_BIN, binpath / "oxide")
        sh([str(OXIDE_BIN), "index", str(repo), "--json"], timeout=60)

        skill_dst = repo / ".opencode/skills/oxide-code-context"
        skill_dst.mkdir(parents=True)
        (skill_dst / "SKILL.md").write_text(build_skill_file(VARIANTS[variant]["skill_description"]))
        (repo / "AGENTS.md").write_text(VARIANTS[variant]["agents"])
        env = build_env(binpath)

        start = time.time()
        try:
            r = sh(["opencode", "run", "--auto", "--format", "json", "--dir", str(repo), "-m", MODEL, PROMPT],
                   cwd=str(repo), env={**env, "PWD": str(repo)}, timeout=280)
            stdout, rc, timed_out = r.stdout, r.returncode, False
        except subprocess.TimeoutExpired as e:
            out = e.stdout or ""
            stdout = out.decode("utf-8", "replace") if isinstance(out, bytes) else out
            rc, timed_out = -1, True
        wall = round(time.time() - start, 1)

        log_name = f"coding-{variant}-r{rep}.jsonl"
        (LOG_DIR / log_name).write_text(stdout or "")

        events = parse_events(stdout or "")
        analysis = analyze(events)

        verify = subprocess.run(["bash", "verify.sh"], cwd=repo, capture_output=True, text=True, timeout=60)
        tests_pass = verify.returncode == 0

        return dict(
            variant=variant, rep=rep, wall_s=wall, timed_out=timed_out, returncode=rc,
            tests_pass=tests_pass, log=log_name, **analysis,
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
                done.add((rec["variant"], rec["rep"]))
    with RESULTS_PATH.open("a") as sink:
        for variant in ("E0", "E1"):
            for rep in (1, 2, 3):
                if (variant, rep) in done:
                    continue
                rec = run_one(variant, rep)
                sink.write(json.dumps(rec) + "\n")
                sink.flush()
                print(f"{variant} r{rep} tests_pass={rec['tests_pass']} "
                      f"oxide_ctx={rec['oxide_context_calls']} oxide_search={rec['oxide_search_calls']} "
                      f"wall={rec['wall_s']}s")


if __name__ == "__main__":
    main()
