#!/usr/bin/env python3
"""OXIDE vs ContextBench: official gold-context retrieval metrics.

Samples Python/TypeScript tasks from the ContextBench dataset
(arXiv:2602.05892), indexes each task's repository at its base commit with
OXIDE, retrieves context for the issue text under several conditions, and
scores predictions against human-annotated gold contexts using ContextBench's
own metric code (coverage/recall and precision at file/symbol/span/line
granularity).

Run with the prepared venv:
    eval-agent/.venv/bin/python scripts/agent_eval/contextbench_run.py \
        [--limit-per-repo N] [--conditions lexical,vec,hybrid,budgeted] [--out DIR]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CB_DIR = ROOT / "eval-agent" / "third_party" / "ContextBench"
REPO_CACHE = Path(os.environ.get("OXIDE_CB_CACHE", Path.home() / ".cache/oxide-contextbench/repos"))
CHARS_PER_TOKEN = 4.0


def sh(cmd, cwd=None, env=None, timeout=1800):
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, **(env or {})},
    )


def ensure_contextbench():
    if not CB_DIR.exists():
        CB_DIR.parent.mkdir(parents=True, exist_ok=True)
        r = sh(["git", "clone", "-q",
                "https://github.com/EuniAI/ContextBench", str(CB_DIR)])
        assert r.returncode == 0, r.stderr


ensure_contextbench()
sys.path.insert(0, str(CB_DIR))

from contextbench.metrics.compute import (  # noqa: E402
    compute_granularity_metrics,
)
from contextbench.parsers.gold import Gold  # noqa: E402
from contextbench.core.fileio import line_to_byte  # noqa: E402
from contextbench.extractors.treesitter import extract_def_set_in_spans  # noqa: E402


def load_tasks(langs=("python", "typescript"), limit_per_repo=None):
    from datasets import load_dataset
    ds = load_dataset("Contextbench/ContextBench", "default")["train"]
    tasks = [r for r in ds if r["language"] in langs]
    if limit_per_repo:
        seen = defaultdict(int)
        kept = []
        for r in sorted(tasks, key=lambda t: t["instance_id"]):
            if seen[r["repo"]] < limit_per_repo:
                kept.append(r)
                seen[r["repo"]] += 1
        tasks = kept
    return list(tasks)


def ensure_repo_checkout(repo_url: str, base_commit: str) -> Path:
    REPO_CACHE.mkdir(parents=True, exist_ok=True)
    name = repo_url.rstrip("/").removesuffix(".git").split("/")[-1]
    dst = REPO_CACHE / name
    if dst.exists() and not (dst / ".git").exists():
        shutil.rmtree(dst)  # partial clone from an interrupted run
    if not dst.exists():
        print(f"    cloning {repo_url} (blobless)...", flush=True)
        r = sh(["git", "clone", "--filter=blob:none", repo_url, str(dst)])
        if r.returncode != 0:
            raise RuntimeError(f"clone failed: {r.stderr[:300]}")
    have = sh(["git", "cat-file", "-e", f"{base_commit}^{{commit}}"], cwd=dst)
    if have.returncode != 0:
        r = sh(["git", "fetch", "-q", "origin", base_commit], cwd=dst)
        if r.returncode != 0:
            raise RuntimeError(f"fetch {base_commit[:10]} failed: {r.stderr[:200]}")
    cur = sh(["git", "rev-parse", "HEAD"], cwd=dst).stdout.strip()
    if cur != base_commit:
        r = sh(["git", "checkout", "-q", base_commit], cwd=dst)
        if r.returncode != 0:
            raise RuntimeError(f"checkout failed: {r.stderr[:200]}")
    return dst


def index_repo(repo_dir: Path, embedder_url: str) -> None:
    r = sh([str(ROOT / "target/release/oxide"), "index", "."],
           cwd=repo_dir, env={"OXIDE_EMBED_URL": embedder_url})
    if r.returncode != 0:
        raise RuntimeError(f"oxide index failed: {r.stderr[:300]}")


def retrieve(indexed: Path, condition: str, problem: str) -> tuple[list[dict], int]:
    """Returns oxide hits/pack items plus estimated token cost."""
    ox = str(ROOT / "target/release/oxide")
    env = {"OXIDE_EMBED_URL": os.environ.get("OXIDE_EMBED_URL", "")}
    if condition == "budgeted":
        r = sh([ox, "context", "--task", problem, "--budget-tokens", "4096", "--json"],
               cwd=indexed, env=env)
        pack = json.loads(r.stdout)
        items = [
            {
                "file": it["file"],
                "start_line": it["start_line"],
                "end_line": it["end_line"],
            }
            for it in pack["items"]
        ]
        return items, pack["used_tokens"]
    mode = {"lexical": "lexical", "vec": "semantic", "hybrid": "hybrid"}[condition]
    r = sh([ox, "search", problem, "--mode", mode, "--limit", "10", "--json"],
           cwd=indexed, env=env)
    hits = json.loads(r.stdout)
    items = [
        {"file": h["file"], "start_line": h["start_line"], "end_line": h["end_line"]}
        for h in hits
    ]
    text = "\n".join(h.get("snippet", "") for h in hits)
    return items, est_tokens(text)


def est_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN)


def to_spans(items: list[dict]) -> dict[str, list[tuple[int, int]]]:
    spans: dict[str, list[tuple[int, int]]] = {}
    for it in items:
        spans.setdefault(it["file"], []).append((it["start_line"], it["end_line"]))
    return spans


def evaluate_task(repo_dir: Path, row: dict, items: list[dict]) -> dict:
    """Score one prediction against gold using ContextBench's metrics."""
    gold_data = {
        "init_ctx": json.loads(row["gold_context"]) if isinstance(row["gold_context"], str) else row["gold_context"],
        "repo_url": row["repo_url"],
        "commit": row["base_commit"],
    }
    gold = Gold(gold_data)
    gold_files = set(gold.files())
    gold_lines = {}
    for item in gold.init + gold.add:
        f = item.get("file")
        if not f:
            continue
        gold_lines.setdefault(f, []).append((item.get("start_line", 1), item.get("end_line", 1)))

    # byte spans for span-granularity and def extraction
    def lines_to_bytes(spans_by_file):
        out = {}
        for f, intervals in spans_by_file.items():
            abs_path = repo_dir / f
            if not abs_path.exists():
                continue
            for (s, e) in intervals:
                b = line_to_byte(str(abs_path), s, e)
                if b:
                    out.setdefault(f, []).append(b)
        return out

    gold_spans = lines_to_bytes(gold_lines)
    pred_spans_dict = to_spans(items)
    pred_spans = lines_to_bytes(pred_spans_dict)

    # defs are byte-based in the evaluator: convert both sides' line spans first
    gold_def_input = {
        f: [line_to_byte(str(repo_dir / f), s, e) for (s, e) in ints
            if (repo_dir / f).exists()]
        for f, ints in gold_lines.items()
    }
    gold_def_input = {f: [b for b in bs if b] for f, bs in gold_def_input.items()}
    gold_defs = extract_def_set_in_spans(gold_def_input, str(repo_dir))
    pred_def_input = {
        f: [line_to_byte(str(repo_dir / f), s, e) for (s, e) in ints
            if (repo_dir / f).exists()]
        for f, ints in pred_spans_dict.items()
    }
    pred_def_input = {f: [b for b in bs if b] for f, bs in pred_def_input.items()}
    pred_defs = extract_def_set_in_spans(pred_def_input, str(repo_dir))

    return compute_granularity_metrics(
        pred_files=set(pred_spans_dict.keys()),
        pred_defs=pred_defs,
        pred_spans=pred_spans,
        gold_files=gold_files,
        gold_defs=gold_defs,
        gold_spans=gold_spans,
        pred_lines=pred_spans_dict,
        gold_lines=gold_lines,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conditions", default="lexical,vec,hybrid,budgeted")
    ap.add_argument("--limit-per-repo", type=int, default=3)
    ap.add_argument("--langs", default="python,typescript")
    ap.add_argument(
        "--repos",
        default="",
        help="comma-separated owner/name allowlist; empty = all",
    )
    ap.add_argument(
        "--instances",
        default="",
        help="path to newline-separated instance_id allowlist; empty = all",
    )
    ap.add_argument("--out", default=str(ROOT / "eval-agent/results"))
    args = ap.parse_args()

    ensure_contextbench()
    embedder_url = os.environ.get("OXIDE_EMBED_URL", "")
    assert embedder_url, "set OXIDE_EMBED_URL (llama.cpp embeddings server)"

    if args.instances:
        # Pin mode: load unbounded, then filter; per-repo sampling would
        # truncate the pin (dataset drift drops 6/21 ids through it).
        allow = {
            i.strip()
            for i in Path(args.instances).read_text().splitlines()
            if i.strip()
        }
        tasks = [t for t in load_tasks(tuple(args.langs.split(","))) if t["instance_id"] in allow]
        found = {t["instance_id"] for t in tasks}
        missing = allow - found
        assert not missing, f"pinned instances missing from dataset: {sorted(missing)}"
    else:
        tasks = load_tasks(tuple(args.langs.split(",")), args.limit_per_repo)
        if args.repos:
            allow = {r.strip() for r in args.repos.split(",") if r.strip()}
            tasks = [t for t in tasks if t["repo"] in allow]
    print(f"{len(tasks)} tasks sampled")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "cb_results.jsonl"

    done_keys = set()
    if results_path.exists():
        for line in results_path.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                done_keys.add((rec["task"], rec["condition"]))

    agg = defaultdict(lambda: defaultdict(list))
    with results_path.open("a") as sink:
        for i, row in enumerate(tasks):
            key_prefix = row["instance_id"]
            todo = [c for c in args.conditions.split(",") if (key_prefix, c) not in done_keys]
            if not todo:
                continue
            try:
                repo_dir = ensure_repo_checkout(row["repo_url"], row["base_commit"])
                index_repo(repo_dir, embedder_url)
            except Exception as e:
                print(f"[{i+1}/{len(tasks)}] SKIP {row['instance_id']}: {e}")
                continue
            stats = sh([str(ROOT / "target/release/oxide"), "stats"], cwd=repo_dir)
            if "symbols:    0" in stats.stdout:
                print(f"[{i+1}/{len(tasks)}] SKIP {row['instance_id']}: no indexable sources at base commit")
                continue
            for cond in todo:
                t0 = time.time()
                items, used_tokens = retrieve(repo_dir, cond, row["problem_statement"])
                m = evaluate_task(repo_dir, row, items)
                rec = {
                    "task": row["instance_id"],
                    "repo": row["repo"],
                    "language": row["language"],
                    "condition": cond,
                    "items": len(items),
                    "used_tokens": used_tokens,
                    "retrieve_s": round(time.time() - t0, 2),
                    "metrics": m,
                }
                sink.write(json.dumps(rec) + "\n")
                sink.flush()
                lc = m["line"]
                print(f"[{i+1}/{len(tasks)}] {row['repo']} {cond}: "
                      f"file_cov={m['file']['coverage']:.2f} sym_cov={m['symbol']['coverage']:.2f} "
                      f"line_cov={lc['coverage']:.2f} line_prec={lc['precision']:.2f} tok={used_tokens}")
                for gran in ("file", "symbol", "span", "line"):
                    agg[cond][f"{gran}_cov"].append(m[gran]["coverage"])
                    agg[cond][f"{gran}_prec"].append(m[gran]["precision"])
                agg[cond]["tokens"].append(used_tokens)

    print("\n=== aggregate (mean over tasks) ===")
    header = f"{'condition':<10}" + "".join(f"{g:>14}" for g in
              ["file_cov", "file_prec", "sym_cov", "sym_prec", "line_cov", "line_prec", "avg_tok"])
    print(header)
    for cond, vals in agg.items():
        n = max(1, len(vals["tokens"]))
        cells = "".join(
            f"{sum(vals[k]) / len(vals[k]) if vals[k] else 0:>14.3f}"
            for k in ["file_cov", "file_prec", "sym_cov", "sym_prec", "line_cov", "line_prec"]
        )
        print(f"{cond:<10}{cells}{sum(vals['tokens']) / n:>14.0f}")


if __name__ == "__main__":
    main()
