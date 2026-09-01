#!/usr/bin/env python3
"""Term-coverage corroboration experiment: baseline vs several alphas.

Pure Rust feature (`$OXIDE_TERM_COVERAGE_ALPHA`, see src/retrieval.rs) — no
model, no venv beyond the pinned one this script itself needs, no network
call beyond the already-frozen embedder. Compares ranked-list metrics
(Precision@5, Recall@5, MRR, nDCG@10) from a hybrid search against
ContextBench gold spans, plus the actual bounded context pack
(gold-in-final-context, tokens, relevant-item count) at Balanced retrieval
mode — the mode any nonzero alpha would actually ship under if this
experiment earns a default change.

Per-task work runs through `examples/term_coverage_sweep` (built via `cargo
build --release --example term_coverage_sweep`), one process per task
instead of one `oxide` subprocess per (alpha, {search,context}) pair — see
that file's module doc for what redundancy this eliminates and why. This is
purely an eval-harness change: no retrieval/ranking/embedding-model/index/
context-allocation code is touched, and every alpha's search+context still
goes through the exact same production `RetrievalEngine::search`/
`build_context` entry points the CLI uses.

Usage:
    eval-agent/.venv/bin/python scripts/agent_eval/term_coverage_eval.py
"""
import json
import os
import sys
import tempfile
import time
from math import log2
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contextbench_run as cb  # noqa: E402

ROOT = cb.ROOT
SWEEP_BIN = ROOT / "target/release/examples/term_coverage_sweep"
ENV_BASE = {"OXIDE_EMBED_URL": os.environ.get("OXIDE_EMBED_URL", "")}
PIN = ROOT / "eval-agent/results/tier_a_instances.txt"
ALLOW = {i.strip() for i in PIN.read_text().splitlines() if i.strip()}
ALPHAS = ["0.0", "0.1", "0.2", "0.3", "0.5"]
K = 5
OUT_DIR = ROOT / "docs/term-coverage-eval/results"


def gold_lines(row) -> dict[str, list[tuple[int, int]]]:
    gold_data = {
        "init_ctx": json.loads(row["gold_context"]) if isinstance(row["gold_context"], str) else row["gold_context"],
        "repo_url": row["repo_url"],
        "commit": row["base_commit"],
    }
    gold = cb.Gold(gold_data)
    lines: dict[str, list[tuple[int, int]]] = {}
    for item in gold.init + gold.add:
        f = item.get("file")
        if f:
            lines.setdefault(f, []).append((item.get("start_line", 1), item.get("end_line", 1)))
    return lines


def overlaps_gold(item: dict, gold: dict[str, list[tuple[int, int]]]) -> bool:
    ranges = gold.get(item["file"])
    if not ranges:
        return False
    s, e = item["start_line"], item["end_line"]
    return any(s <= ge and gs <= e for gs, ge in ranges)


def gold_range_list(gold: dict[str, list[tuple[int, int]]]) -> list[tuple[str, int, int]]:
    return [(f, s, e) for f, ranges in gold.items() for (s, e) in ranges]


def matched_gold_indices(item: dict, gold_ranges: list[tuple[str, int, int]]) -> set[int]:
    """Which gold-range indices (by position in `gold_ranges`) `item` overlaps.
    Distinct from `overlaps_gold`'s boolean: multiple hits can legitimately
    overlap the same single gold range (e.g. two functions in one annotated
    region), so counting *hits* against a *gold-range* denominator (the
    original bug here) can exceed 1.0 for recall or blow nDCG's [0,1] bound —
    tracking which ranges are actually covered keeps both metrics bounded."""
    out = set()
    for idx, (f, gs, ge) in enumerate(gold_ranges):
        if item["file"] == f and item["start_line"] <= ge and gs <= item["end_line"]:
            out.add(idx)
    return out


def sweep(repo: Path, problem: str) -> list[dict]:
    """Runs the full alpha sweep for one task in a single process. Returns
    one dict per alpha with `hits`, `pack`, `search_seconds`,
    `context_seconds`, `embed_query_calls_total`, `embed_query_cache_hits_total`."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(problem)
        task_file = f.name
    try:
        r = cb.sh([str(SWEEP_BIN), str(repo), task_file, ",".join(ALPHAS)],
                  cwd=repo, env=ENV_BASE, timeout=600)
        if r.returncode != 0:
            raise RuntimeError(f"term_coverage_sweep failed: {r.stderr[-2000:]}")
        return [json.loads(line) for line in r.stdout.splitlines() if line.strip()]
    finally:
        os.unlink(task_file)


def rank_metrics(hits: list[dict], gold: dict) -> dict:
    gold_ranges = gold_range_list(gold)
    n_gold = len(gold_ranges)
    per_hit_matches = [matched_gold_indices(h, gold_ranges) for h in hits]
    relevance = [1 if m else 0 for m in per_hit_matches]

    top_k = relevance[:K]
    precision_at_k = sum(top_k) / K
    # Distinct gold ranges covered by the top-K, not a count of matching
    # hits — multiple hits can cover the same range, which would otherwise
    # let recall exceed 1.0 (the bug this replaced).
    covered: set[int] = set()
    for m in per_hit_matches[:K]:
        covered |= m
    recall_at_k = len(covered) / max(1, n_gold)

    mrr = 0.0
    for i, rel in enumerate(relevance, start=1):
        if rel:
            mrr = 1.0 / i
            break

    # Standard nDCG@10: IDCG re-ranks the SAME window's relevance labels
    # ideally (all 1s first), so DCG <= IDCG always holds by construction —
    # unlike deriving IDCG from the gold-range count independently of what
    # the window actually contains, which could make DCG exceed it.
    window = relevance[:10]
    dcg = sum(rel / log2(i + 1) for i, rel in enumerate(window, start=1))
    ideal_window = sorted(window, reverse=True)
    idcg = sum(rel / log2(i + 1) for i, rel in enumerate(ideal_window, start=1))
    ndcg = dcg / idcg if idcg > 0 else 0.0

    return {
        "precision_at_5": precision_at_k,
        "recall_at_5": recall_at_k,
        "mrr": mrr,
        "ndcg_at_10": ndcg,
    }


def pack_metrics(pack: dict, gold: dict) -> dict:
    items = pack["items"]
    relevant = [it for it in items if overlaps_gold(it, gold)]
    return {
        "used_tokens": pack["used_tokens"],
        "n_items": len(items),
        "relevant_items": len(relevant),
        "gold_in_context": len(relevant) > 0,
        "relevant_ids": sorted(f"{it['file']}#{it['qualified_name']}" for it in relevant),
    }


def main() -> None:
    embedder_url = os.environ.get("OXIDE_EMBED_URL", "")
    assert embedder_url, "set OXIDE_EMBED_URL (llama.cpp embeddings server)"
    assert SWEEP_BIN.exists(), (
        f"{SWEEP_BIN} missing — build it first: "
        "cargo build --release --example term_coverage_sweep"
    )

    tasks = [t for t in cb.load_tasks() if t["instance_id"] in ALLOW]
    missing = ALLOW - {t["instance_id"] for t in tasks}
    assert not missing, f"pinned instances missing: {sorted(missing)}"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results_path = OUT_DIR / "results.jsonl"
    with results_path.open("w") as sink:
        for i, row in enumerate(tasks):
            repo = cb.ensure_repo_checkout(row["repo_url"], row["base_commit"])
            cb.index_repo(repo, embedder_url)
            problem = row["problem_statement"]
            gold = gold_lines(row)
            if not gold:
                print(f"[{i+1}/{len(tasks)}] SKIP {row['instance_id']}: no gold lines")
                continue

            t0 = time.time()
            arms = sweep(repo, problem)
            task_wall_s = time.time() - t0

            for arm in arms:
                rm = rank_metrics(arm["hits"], gold)
                pm = pack_metrics(arm["pack"], gold)
                rec = {
                    "instance_id": row["instance_id"],
                    "alpha": arm["alpha"],
                    "search_seconds": arm["search_seconds"],
                    "context_seconds": arm["context_seconds"],
                    "embed_query_calls_total": arm["embed_query_calls_total"],
                    "embed_query_cache_hits_total": arm["embed_query_cache_hits_total"],
                    "n_hits": len(arm["hits"]),
                    **rm,
                    **pm,
                }
                sink.write(json.dumps(rec) + "\n")
                sink.flush()
            print(f"[{i+1}/{len(tasks)}] measured {row['instance_id']} "
                  f"(task wall {task_wall_s:.2f}s, {arms[-1]['embed_query_calls_total']} real embed calls)",
                  flush=True)

    print(f"\nwrote {results_path}")


if __name__ == "__main__":
    main()
