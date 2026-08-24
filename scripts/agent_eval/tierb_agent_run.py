#!/usr/bin/env python3
"""Tier B: same coding agent across context conditions on ContextBench tasks.

For a small sample of ContextBench issues, runs `opencode run` (fixed model)
in the checked-out repository under four conditions:
  stock     task text only
  vec       top-8 vector-only retrieval injected
  hybrid    top-10 hybrid retrieval injected
  budgeted  budgeted OXIDE context pack injected

Measures: gold-file utilization of the final diff, unnecessary-edit files,
shell-tool-call proxy, wall time, injected tokens. No end-to-end solve claims
(test-env setup is out of scope here); Tier A carries retrieval quality.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from contextbench_run import (  # noqa: E402
    ensure_contextbench,
    ensure_repo_checkout,
    index_repo,
    est_tokens,
)

MODEL = "opencode/x-preview-f-free"


def sh(cmd, cwd=None, env=None, timeout=900):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, **(env or {})})


def render_search_context(indexed: Path, condition: str, problem: str) -> tuple[str, int, set[str]]:
    ox = str(ROOT / "target/release/oxide")
    mode = "semantic" if condition == "vec" else "hybrid"
    r = sh([ox, "search", problem, "--mode", mode, "--limit", "8" if condition == "vec" else "10",
            "--json"], cwd=indexed)
    hits = json.loads(r.stdout)
    parts = []
    files = set()
    for h in hits:
        files.add(h["file"])
        lang = h["file"].rsplit(".", 1)[-1]
        header = (f"`{h['file']}#{h['qualified_name']}` ({h['kind']}, "
                  f"lines {h['start_line']}-{h['end_line']}) why: {'; '.join(h['reasons'])}")
        parts.append(f"{header}\n```{lang}\n{h['snippet']}\n```")
    text = "\n\n".join(parts)
    return text, est_tokens(text), files


def render_pack_context(indexed: Path, problem: str) -> tuple[str, int, set[str]]:
    ox = str(ROOT / "target/release/oxide")
    r = sh([ox, "context", "--task", problem, "--budget-tokens", "4000", "--json"], cwd=indexed)
    pack = json.loads(r.stdout)
    parts = []
    files = set()
    for it in pack["items"]:
        f = it["file"]
        files.add(f)
        lang = f.rsplit(".", 1)[-1]
        header = (f"[{it['role']}] `{f}#{it['qualified_name']}` ({it['kind']}, "
                  f"lines {it['start_line']}-{it['end_line']}) why: {'; '.join(it['reasons'])}")
        parts.append(f"{header}\n```{lang}\n{it['snippet']}\n```")
    text = "\n\n".join(parts)
    if items := pack["items"]:
        text += "\n\nPack contents: " + ", ".join(
            f"{i['file']}#{i['qualified_name']}({i['est_tokens']})" for i in items)
    return text, pack["used_tokens"], files


def run_condition(task: dict, condition: str, workdir: Path, log_dir: Path) -> dict:
    fixture_repo = ensure_repo_checkout(task["repo_url"], task["base_commit"])
    index_repo(fixture_repo, os.environ.get("OXIDE_EMBED_URL", ""))
    repo = workdir / f'{task["instance_id"][:40]}-{condition}'
    if repo.exists():
        shutil.rmtree(repo)
    shutil.copytree(fixture_repo, repo, ignore=shutil.ignore_patterns(".oxide"))

    ctx_text, ctx_tokens, _ = ("", 0, set())
    if condition != "stock":
        if condition == "budgeted":
            ctx_text, ctx_tokens, _ = render_pack_context(fixture_repo, task["problem_statement"])
        else:
            ctx_text, ctx_tokens, _ = render_search_context(fixture_repo, condition,
                                                            task["problem_statement"])
    prompt = f"# Task\n\n{task['problem_statement']}\n"
    if ctx_text:
        prompt += ("\n\n# Relevant repository context (pre-retrieved)\n\n"
                   "The following symbols were surfaced by static analysis of this repo;\n"
                   "they may or may not all be relevant.\n\n" + ctx_text)
    prompt += "\n\nWhen done, reply with DONE."

    start = time.time()
    # opencode (node) trusts $PWD over getcwd(): pin it to the task repo.
    r = sh(["timeout", "900", "opencode", "run", "-m", MODEL, prompt],
           cwd=repo, timeout=960, env={"PWD": str(repo)})
    wall = time.time() - start
    log = log_dir / f'{task["instance_id"][:50]}-{condition}.log'
    log.write_text((r.stdout or "")[-20000:] + "\n---STDERR---\n" + (r.stderr or "")[-2000:])
    tool_calls = (r.stdout or "").count("\n$ ")

    # diff footprint vs pristine checkout
    sh(["git", "add", "-A"], cwd=repo)
    diff = sh(["git", "diff", "--name-only", task["base_commit"], "--"],
              cwd=repo).stdout.split()
    touched = [f for f in diff if f != "_PROMPT.md"]
    gold_file = None
    gctx = json.loads(task["gold_context"]) if isinstance(task["gold_context"], str) else task["gold_context"]
    gold_files = {g.get("file") for g in gctx}
    used_gold = [f for f in touched if f in gold_files]
    unnecessary = [f for f in touched if f not in gold_files]

    return {
        "task": task["instance_id"],
        "repo": task["repo"],
        "language": task["language"],
        "condition": condition,
        "ctx_tokens": ctx_tokens,
        "wall_s": round(wall, 1),
        "tool_calls_proxy": tool_calls,
        "files_touched": len(touched),
        "gold_files_utilized": len(used_gold),
        "gold_files_total": len(gold_files),
        "unnecessary_edit_files": len(unnecessary),
        "unnecessary_list": unnecessary[:8],
        "agent_output_excerpt": (r.stdout or "")[-300:],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conditions", default="stock,vec,hybrid,budgeted")
    ap.add_argument("--limit-per-repo", type=int, default=1)
    ap.add_argument("--repos",
                    default="pallets/flask,mwaskom/seaborn,darkreader/darkreader,tailwindlabs/tailwindcss")
    ap.add_argument("--out", default=str(ROOT / "eval-agent/results"))
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
    print(f"{len(tasks)} agent tasks x conditions {args.conditions}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "logs").mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "agent_results.jsonl"
    done = set()
    if results_path.exists():
        for line in results_path.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                done.add((rec["task"], rec["condition"]))

    agg: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    tmp = Path(tempfile.mkdtemp(prefix="oxide-tierb-"))
    with results_path.open("a") as sink:
        for t in tasks:
            for cond in args.conditions.split(","):
                if (t["instance_id"], cond) in done:
                    continue
                try:
                    rec = run_condition(t, cond, tmp, out_dir / "logs")
                except Exception as e:
                    print(f"FAIL {t['instance_id'][:40]} {cond}: {e}")
                    continue
                sink.write(json.dumps(rec) + "\n")
                sink.flush()
                print(f"{rec['task'][:44]:<46} {cond:<9} gold {rec['gold_files_utilized']}/"
                      f"{rec['gold_files_total']} tools={rec['tool_calls_proxy']:>3} "
                      f"wall={rec['wall_s']:>6}s ctx={rec['ctx_tokens']:>5} bad={rec['unnecessary_edit_files']}")
                for k in ("ctx_tokens", "wall_s", "tool_calls_proxy", "files_touched",
                          "gold_files_utilized", "unnecessary_edit_files"):
                    agg[cond][k].append(rec[k])
    print("\n=== aggregate ===")
    print(f"{'condition':<10} {'gold_used':>10} {'bad_edits':>10} {'tools':>7} {'wall':>8} {'ctx':>7}")
    for cond, vals in agg.items():
        n = max(1, len(vals["wall_s"]))
        print(f"{cond:<10} {sum(vals['gold_files_utilized']) / sum(max(1,v) for v in vals['gold_files_utilized']) if any(vals['gold_files_utilized']) else 0:>10.2f} "
              f"{sum(vals['unnecessary_edit_files']) / n:>10.2f} "
              f"{sum(vals['tool_calls_proxy']) / n:>7.1f} "
              f"{sum(vals['wall_s']) / n:>7.1f}s "
              f"{sum(vals['ctx_tokens']) / n:>7.0f}")


if __name__ == "__main__":
    main()
