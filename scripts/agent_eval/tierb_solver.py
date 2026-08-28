#!/usr/bin/env python3
"""Tier B SOLVE: paired stock vs budgeted with native pytest + dead_test detection.

Builds on tierb_agent_run.py. Per run:
1. Run pytest on the pristine worktree to record a baseline (env + pre-existing).
2. Opencode writes its diff on top.
3. Run pytest on the patched worktree.
4. Classify:
   - dead_test       : baseline rc != 0 (env broken / no tests / pre-existing failure)
   - pass            : patched rc == 0 and baseline rc == 0
   - fail            : patched rc != 0 and baseline rc == 0
   - incomplete      : opencode produced no diff
   - provider_failed : opencode log shows API/rate errors
   - no_eval         : test files don't exist (handled by run_pytest)
Per task brief, dead_test/incomplete/provider_failed are "no evidence" and
excluded from the final aggregate.
"""
import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
EVAL_TASKS_LIMIT = 2  # matches prior 13-task paired set; CLI --limit-per-repo can override

from contextbench_run import (  # noqa: E402
    ensure_contextbench,
    ensure_repo_checkout,
    index_repo,
    est_tokens,
)
from tierb_agent_run import (  # noqa: E402
    MODEL, sh, render_pack_context, render_search_context, render_grep_context,
)

PYTEST_TIMEOUT = 600

def run_pytest(workdir: Path) -> tuple[int | None, str | None]:
    log = workdir.parent / f"{workdir.name}.pytest.log"
    try:
        r = sh([sys.executable, "-m", "pytest", "-x", "-q", "--no-header", "--tb=line",
                "--timeout=60", "-p", "no:cacheprovider"],
               cwd=workdir, timeout=PYTEST_TIMEOUT)
        log.write_text((r.stdout or "")[-4000:] + "\n---STDERR---\n" + (r.stderr or "")[-1000:])
        return r.returncode, str(log)
    except subprocess.TimeoutExpired:
        log.write_text("pytest timeout\n")
        return 124, str(log)
    except Exception as e:
        log.write_text(f"pytest failed to start: {e}\n")
        return None, str(log)


def classify_pytest(rc: int | None) -> str:
    if rc is None:
        return "no_tests"
    if rc == 0:
        return "pass"
    if rc == 5:  # pytest: no tests collected
        return "no_tests"
    if rc == 124:
        return "timeout"
    return "fail"


def run_condition(task: dict, condition: str, workdir: Path, log_dir: Path) -> dict:
    fixture_repo = ensure_repo_checkout(task["repo_url"], task["base_commit"])
    index_repo(fixture_repo, os.environ.get("OXIDE_EMBED_URL", ""))
    repo = workdir / f'{task["instance_id"][:40]}-{condition}'
    if repo.exists():
        shutil.rmtree(repo)
    shutil.copytree(fixture_repo, repo, ignore=shutil.ignore_patterns(".oxide"))

    # Baseline pytest on pristine worktree
    baseline_rc, baseline_log = run_pytest(repo)

    # Render context
    ctx_text, ctx_tokens, _ = ("", 0, set())
    if condition != "stock":
        ctx_text, ctx_tokens, _ = render_pack_context(fixture_repo, task["problem_statement"])
    prompt = f"# Task\n\n{task['problem_statement']}\n"
    if ctx_text:
        prompt += ("\n\n# Relevant repository context (pre-retrieved)\n\n"
                   "The following symbols were surfaced by static analysis of this repo;\n"
                   "they may or may not all be relevant.\n\n" + ctx_text)
    prompt += "\n\nWhen done, reply with DONE."

    # Opencode run
    start = time.time()
    agent_rc = None
    try:
        r = sh(["timeout", "900", "opencode", "run", "-m", MODEL, prompt],
               cwd=repo, timeout=960, env={"PWD": str(repo)})
        agent_rc = r.returncode
        stdout, stderr = r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        stdout, stderr = "", "opencode timeout"
    wall = time.time() - start
    log = log_dir / f'{task["instance_id"][:50]}-{condition}.log'
    log.write_text((stdout or "")[-20000:] + "\n---STDERR---\n" + (stderr or "")[-2000:])

    # Diff capture
    sh(["git", "add", "-A"], cwd=repo)
    diff_text = sh(["git", "diff", task["base_commit"], "--"], cwd=repo).stdout or ""
    diff_proc_names = sh(["git", "diff", "--name-only", task["base_commit"], "--"],
                          cwd=repo).stdout.split()
    touched = [f for f in diff_proc_names if f != "_PROMPT.md"]
    diff_path = log_dir / f'{task["instance_id"][:50]}-{condition}.diff'
    diff_path.write_text(diff_text)

    # Gold analysis
    gctx = json.loads(task["gold_context"]) if isinstance(task["gold_context"], str) else task["gold_context"]
    gold_files = {g.get("file") for g in gctx}
    used_gold = [f for f in touched if f in gold_files]
    unnecessary = [f for f in touched if f not in gold_files]

    # Provider-failure detection — include 429/Clinepass which the previous
    # run hit ("Error 429: monthly Clinepass limit") and was misclassified
    # as incomplete.
    provider_failed = any(s.lower() in (stderr or "").lower() for s in
                          ("rate limit", "unknownerror", "ai_apicallerror",
                           "no such model", "429", "clinepass", "quota exceeded"))
    patched_rc, patched_log = run_pytest(repo)

    # Solve classification
    baseline_status = classify_pytest(baseline_rc)
    patched_status = classify_pytest(patched_rc)
    # Order matters: an empty diff on a clean baseline is the agent doing
    # nothing — that's incomplete, not pass. Detect before assigning pass.
    if provider_failed:
        solve_status = "provider_failed"
    elif not diff_text.strip():
        solve_status = "incomplete"  # agent produced no diff at all
    elif baseline_status in ("no_tests", "timeout") and patched_status == "no_tests":
        solve_status = "no_eval"  # no test infrastructure to evaluate
    elif baseline_status != "pass":
        # baseline broken or missing tests → environment, not agent
        solve_status = "dead_test"
    else:
        # baseline clean + non-empty diff + pytest ran → classify patched
        solve_status = patched_status  # pass or fail

    return {
        "task": task["instance_id"],
        "repo": task["repo"],
        "language": task["language"],
        "condition": condition,
        "ctx_tokens": ctx_tokens,
        "wall_s": round(wall, 1),
        "agent_rc": agent_rc,
        "files_touched": len(touched),
        "touched_files": touched[:20],
        "gold_files_utilized": len(used_gold),
        "gold_files_total": len(gold_files),
        "unnecessary_edit_files": len(unnecessary),
        "unnecessary_list": unnecessary[:8],
        "baseline_pytest_rc": baseline_rc,
        "baseline_pytest_status": baseline_status,
        "patched_pytest_rc": patched_rc,
        "patched_pytest_status": patched_status,
        "incomplete": solve_status == "incomplete",
        "solve_status": solve_status,
        "agent_output_excerpt": (stdout or "")[-300:],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conditions", default="stock,budgeted")
    ap.add_argument("--limit-per-repo", type=int, default=EVAL_TASKS_LIMIT)
    ap.add_argument("--repos",
                    default="pallets/flask,mwaskom/seaborn,psf/requests,pylint-dev/pylint,"
                            "pytest-dev/pytest,darkreader/darkreader,coder/code-server,"
                            "tailwindlabs/tailwindcss")
    ap.add_argument("--out", default=str(ROOT / "eval-agent/results/tierb_solver"))
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    ensure_contextbench()
    assert os.environ.get("OXIDE_EMBED_URL"), "set OXIDE_EMBED_URL"
    from datasets import load_dataset
    ds = load_dataset("Contextbench/ContextBench", "default")["train"]
    allow = set(args.repos.split(","))
    by_repo: dict[str, list] = {}
    for row in ds:
        if row["repo"] in allow and row["language"] in ("python", "typescript"):
            by_repo.setdefault(row["repo"], []).append(row)
    tasks = []
    for repo_rows in by_repo.values():
        tasks.extend(repo_rows[: args.limit_per_repo])
    print(f"{len(tasks)} tasks x conditions {args.conditions} "
          f"(counterbalanced, seed={args.seed})")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "logs").mkdir(exist_ok=True)
    results_path = out_dir / "solver_results.jsonl"

    done = set()
    if results_path.exists():
        for line in results_path.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                done.add((rec["task"], rec["condition"]))

    rng = random.Random(args.seed)
    tmp = Path(tempfile.mkdtemp(prefix="oxide-tierb-solver-"))
    agg: dict[str, dict] = defaultdict(lambda: defaultdict(list))

    with results_path.open("a") as sink:
        for t in tasks:
            order = args.conditions.split(",")[:]
            rng.shuffle(order)
            for cond in order:
                if (t["instance_id"], cond) in done:
                    continue
                try:
                    rec = run_condition(t, cond, tmp, out_dir / "logs")
                except subprocess.TimeoutExpired as e:
                    kind = "index_timeout" if "index" in str(e.cmd) else "opencode_timeout"
                    gctx = json.loads(t["gold_context"]) if isinstance(t["gold_context"], str) else t["gold_context"]
                    rec = {
                        "task": t["instance_id"], "repo": t["repo"], "language": t["language"],
                        "condition": cond, "ctx_tokens": 0, "wall_s": float(e.timeout or 0),
                        "agent_rc": 124, "files_touched": 0, "touched_files": [],
                        "gold_files_utilized": 0, "gold_files_total": len(gctx),
                        "unnecessary_edit_files": 0, "unnecessary_list": [],
                        "baseline_pytest_rc": None, "baseline_pytest_status": "no_tests",
                        "patched_pytest_rc": None, "patched_pytest_status": "no_tests",
                        "solve_status": kind, "incomplete": False, "diff_path": "",
                        "agent_output_excerpt": f"{kind} {e.timeout}s",
                    }
                    print(f"FAIL {t['instance_id'][:40]} {cond}: {kind}")
                except Exception as e:
                    print(f"FAIL {t['instance_id'][:40]} {cond}: "
                          f"{type(e).__name__}: {e}")
                    continue
                sink.write(json.dumps(rec) + "\n")
                sink.flush()
                print(f"{rec['task'][:44]:<46} {cond:<9} solve={rec['solve_status']:<14} "
                      f"base={rec['baseline_pytest_status']:<8} "
                      f"patch={rec['patched_pytest_status']:<7} "
                      f"gold={rec['gold_files_utilized']}/{rec['gold_files_total']} "
                      f"wall={rec['wall_s']:>6.0f}s ctx={rec['ctx_tokens']:>5} "
                      f"bad={rec['unnecessary_edit_files']}")
                for k in ("solve_status", "baseline_pytest_status", "patched_pytest_status",
                          "wall_s", "ctx_tokens", "unnecessary_edit_files", "files_touched",
                          "gold_files_utilized", "language"):
                    agg[cond][k].append(rec[k])

    # Aggregate
    print("\n=== aggregate (excluding provider_failed, incomplete, dead_test, no_eval) ===")
    valid = {"pass", "fail"}
    header = f"{'condition':<10} {'n_valid':>8} {'pass_rate':>10} {'avg_wall':>9} {'avg_ctx':>8} {'avg_bad':>8} {'gold':>5}"
    print(header)

    def report(rows):
        kept = [(s, w, c, b, g)
                for s, w, c, b, g in zip(rows["solve_status"], rows["wall_s"],
                                          rows["ctx_tokens"], rows["unnecessary_edit_files"],
                                          rows["gold_files_utilized"])
                if s in valid]
        n = max(1, len(kept))
        if not kept:
            return None
        pass_rate = sum(1 for s, *_ in kept if s == "pass") / n
        return {
            "n_valid": n,
            "pass_rate": pass_rate,
            "avg_wall": sum(w for _, w, *_ in kept) / n,
            "avg_ctx": sum(c for _, _, c, *_ in kept) / n,
            "avg_bad": sum(b for _, _, _, b, _ in kept) / n,
            "avg_gold": sum(g for *_, g in kept) / n,
        }

    def fmt(d):
        if d is None:
            return f"{'(none)':<10} {'0':>8}"
        return (f"{'':10} {d['n_valid']:>8} {d['pass_rate']:>10.2f} "
                f"{d['avg_wall']:>9.0f}s {d['avg_ctx']:>8.0f} {d['avg_bad']:>8.1f} "
                f"{d['avg_gold']:>5.1f}")

    print("--- all languages ---")
    for cond, vals in agg.items():
        d = report(vals)
        print(f"{cond:<10}{fmt(d)}")

    # Per-language
    for lang in ("python", "typescript"):
        sub = defaultdict(lambda: defaultdict(list))
        for cond, vals in agg.items():
            langs = vals.get("language", [])
            for k in ("solve_status", "wall_s", "ctx_tokens", "unnecessary_edit_files",
                      "gold_files_utilized"):
                sub[cond][k] = [v for v, lv in zip(vals[k], langs) if lv == lang]
        if not any(sub[c]["solve_status"] for c in sub):
            continue
        print(f"--- {lang} only ---")
        for cond, vals in sub.items():
            d = report(vals)
            print(f"{cond:<10}{fmt(d)}")

    # Excluded
    print("\n=== excluded (per task brief: 'no evidence') ===")
    for cond, vals in agg.items():
        excluded = [s for s in vals["solve_status"] if s not in valid]
        if excluded:
            print(f"  {cond}: {dict(Counter(excluded))}")


if __name__ == "__main__":
    main()
