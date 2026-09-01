#!/usr/bin/env python3
"""Reranking experiment, phase 2+3: score, rerank, measure, decide.

Only runs after `reranker_ceiling.py` has shown gold is actually reachable
in the candidate pool — otherwise reranking has nothing to work with.

For each pinned Tier A task:
  1. Pull the exact pre-rerank `kept` candidate pool via
     `OXIDE_DEBUG_DUMP_KEPT` (see reranker_ceiling.py for why a huge
     budget alone is not faithful to it — the per-file/role diversity
     caps are budget-independent).
  2. Build one evidence bundle per candidate: path, qualified name,
     bounded source (read from the checked-out repo at the symbol's own
     line range), and relation context (the dump's own `reasons`) — never
     a bare symbol name.
  3. Score every bundle with each reranker model (own venv, see
     reranker_score.py), in both `transplant` (order-only) and `raw`
     (score-rewrite) modes — see context.rs::rerank_candidates for what
     those mean and why `raw` is the closest repro of the earlier Jina
     collapse.
  4. Re-run `oxide context` at the normal 4096-token budget for baseline
     and every (model, mode) arm, feeding OXIDE_RERANK_SCORES/MODE.
  5. Score every pack against ContextBench gold, plus a complementary-
     evidence-loss check modeled directly on the Jina incident: does an
     arm drop gold-overlapping symbols that baseline kept?

Usage:
    eval-agent/.venv/bin/python scripts/agent_eval/reranker_eval.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contextbench_run as cb  # noqa: E402

ROOT = cb.ROOT
OX = str(ROOT / "target/release/oxide")
ENV = {"OXIDE_EMBED_URL": os.environ.get("OXIDE_EMBED_URL", "")}
PIN = ROOT / "eval-agent/results/tier_a_instances.txt"
ALLOW = {i.strip() for i in PIN.read_text().splitlines() if i.strip()}
NORMAL_BUDGET = "4096"
OUT_DIR = ROOT / "docs/reranker-eval/results"
VENV_PY = str(Path(os.environ.get("OXIDE_RERANK_VENV", "")) / "bin/python")
MODELS = ["bge-v2-m3", "minilm-l6"]  # see reranker_score.py for why minilm-l6, not Qwen3-Reranker
MODES = ["transplant", "raw"]


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


def bundle_text(item: dict) -> str:
    reasons = "; ".join(item.get("reasons", [])) or "direct retrieval hit"
    return (
        f"{item['file']} :: {item['qualified_name']}\n"
        f"relation context: {reasons}\n\n"
        f"{item['snippet']}"
    )


def read_snippet(repo: Path, file: str, start: int, end: int, max_chars: int = 3000) -> str:
    path = repo / file
    if not path.exists():
        return ""
    lines = path.read_text(errors="replace").splitlines()
    return "\n".join(lines[max(0, start - 1):end])[:max_chars]


def flatten_candidate(repo: Path, c: dict) -> dict:
    """Adapts a dumped `Candidate` (nested `symbol`) to the flat
    file/qualified_name/snippet shape the rest of this module (and the
    final ContextPack items) already use."""
    sym = c["symbol"]
    return {
        "file": sym["file"],
        "qualified_name": sym["qualified_name"],
        "start_line": sym["start_line"],
        "end_line": sym["end_line"],
        "score": c["score"],
        "reasons": c.get("reasons", []),
        "snippet": read_snippet(repo, sym["file"], sym["start_line"], sym["end_line"]),
    }


def pool_items(repo: Path, problem: str) -> list[dict]:
    with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
        env = {**ENV, "OXIDE_DEBUG_DUMP_KEPT": tmp.name}
        cb.sh([OX, "context", "--task", problem, "--budget-tokens", NORMAL_BUDGET,
               "--retrieval-mode", "quality", "--json"], cwd=repo, env=env)
        raw = json.loads(Path(tmp.name).read_text())
    return [flatten_candidate(repo, c) for c in raw]


def context_pack(repo: Path, problem: str, extra_env: dict) -> dict:
    env = {**ENV, **extra_env}
    r = cb.sh([OX, "context", "--task", problem, "--budget-tokens", NORMAL_BUDGET,
               "--retrieval-mode", "quality", "--json"], cwd=repo, env=env)
    return json.loads(r.stdout)


def pack_metrics(repo: Path, row: dict, pack: dict, gold: dict, baseline_relevant_ids: set[str] | None) -> dict:
    items = [{"file": it["file"], "start_line": it["start_line"], "end_line": it["end_line"]} for it in pack["items"]]
    granularity = cb.evaluate_task(repo, row, items)
    relevant = [it for it in pack["items"] if overlaps_gold(it, gold)]
    relevant_ids = {f"{it['file']}#{it['qualified_name']}" for it in relevant}
    relevant_tokens = sum(it["est_tokens"] for it in relevant)
    m = {
        "used_tokens": pack["used_tokens"],
        "n_items": len(pack["items"]),
        "granularity": {g: granularity[g] for g in ("file", "symbol", "line")},
        "relevant_items": len(relevant),
        "relevant_tokens": relevant_tokens,
        "relevant_ids": sorted(relevant_ids),
    }
    if baseline_relevant_ids is not None:
        m["complementary_evidence_lost"] = sorted(baseline_relevant_ids - relevant_ids)
        m["complementary_evidence_gained"] = sorted(relevant_ids - baseline_relevant_ids)
    return m


def run_scorer(model: str, batch_path: Path, out_dir: Path) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [VENV_PY, str(Path(__file__).resolve().parent / "reranker_score.py"),
         "--model", model, "--batch", str(batch_path), "--out-dir", str(out_dir)],
        capture_output=True, text=True, timeout=3600,
    )
    if r.returncode != 0:
        raise RuntimeError(f"scorer failed for {model}: {r.stderr[-2000:]}")
    return [json.loads(line) for line in r.stdout.splitlines() if line.strip()]


def main():
    assert VENV_PY.endswith("/bin/python") and os.path.exists(VENV_PY), (
        "set OXIDE_RERANK_VENV to the venv containing torch/sentence-transformers/transformers"
    )
    tasks = [t for t in cb.load_tasks() if t["instance_id"] in ALLOW]
    missing = ALLOW - {t["instance_id"] for t in tasks}
    assert not missing, f"pinned instances missing: {sorted(missing)}"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bundles_dir = OUT_DIR / "bundles"
    bundles_dir.mkdir(exist_ok=True)

    # Phase A: build bundles for every task (needs oxide + the index, no torch).
    task_records = []
    for row in tasks:
        repo = cb.ensure_repo_checkout(row["repo_url"], row["base_commit"])
        cb.index_repo(repo, ENV["OXIDE_EMBED_URL"])
        problem = row["problem_statement"]
        items = pool_items(repo, problem)
        bundles = [{"id": f"{it['file']}#{it['qualified_name']}", "text": bundle_text(it)} for it in items]
        (bundles_dir / f"{row['instance_id']}.json").write_text(json.dumps(bundles, indent=2))
        task_records.append({"instance_id": row["instance_id"], "query": problem, "bundles": bundles, "repo": str(repo), "row": row})
        print(f"  bundled {row['instance_id']}: {len(bundles)} candidates", flush=True)

    batch_path = OUT_DIR / "scorer_batch.jsonl"
    with batch_path.open("w") as f:
        for t in task_records:
            f.write(json.dumps({"instance_id": t["instance_id"], "query": t["query"], "bundles": t["bundles"]}) + "\n")

    # Phase B: score with each model (torch venv, model loaded once per model).
    timing = {}
    for model in MODELS:
        print(f"scoring with {model}...", flush=True)
        events = run_scorer(model, batch_path, OUT_DIR / "scores" / model)
        timing[model] = events
        print(f"  {model}: {[e for e in events if e.get('event')]}", flush=True)

    # Phase C: rerank + measure.
    results_path = OUT_DIR / "results.jsonl"
    with results_path.open("w") as f:
        for t in task_records:
            repo = Path(t["repo"])
            row = t["row"]
            problem = t["query"]
            gold = gold_lines(row)

            baseline = context_pack(repo, problem, {})
            base_m = pack_metrics(repo, row, baseline, gold, None)
            f.write(json.dumps({"instance_id": t["instance_id"], "arm": "baseline", **base_m}) + "\n")

            for model in MODELS:
                scores_path = OUT_DIR / "scores" / model / f"{t['instance_id']}.scores.json"
                for mode in MODES:
                    pack = context_pack(repo, problem, {
                        "OXIDE_RERANK_SCORES": str(scores_path),
                        "OXIDE_RERANK_MODE": mode,
                    })
                    m = pack_metrics(repo, row, pack, gold, set(base_m["relevant_ids"]))
                    f.write(json.dumps({"instance_id": t["instance_id"], "arm": f"{model}:{mode}", **m}) + "\n")
            print(f"  measured {t['instance_id']}", flush=True)

    (OUT_DIR / "scorer_timing.json").write_text(json.dumps(timing, indent=2))
    print(f"\nwrote {results_path}")


if __name__ == "__main__":
    main()
