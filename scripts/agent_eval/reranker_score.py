#!/usr/bin/env python3
"""Score (query, evidence-bundle) pairs with a local cross-encoder reranker.

Standalone by design: runs in its own venv (sentence-transformers/torch for
BGE, fastembed/onnxruntime for MiniLM — CPU-only, no GPU required for
either), never in eval-agent/.venv (pinned to Python 3.11 for
tree-sitter-languages — see AGENTS.md). Input/output are plain JSON files
so the OXIDE-side driver never needs torch or onnxruntime importable.

Two modes:
  --query/--bundles/--out : score one (query, bundle-list) pair, for smoke
    testing a model in isolation.
  --batch IN.jsonl --out-dir DIR : load the model ONCE, then score every
    {"instance_id", "query", "bundles"} line — realistic steady-state
    per-query latency (excludes one-time model load, the way a persistent
    rerank server would amortize it), plus peak RSS for the whole run.
    Writes DIR/<instance_id>.scores.json per task and prints one timing
    line per task plus a final summary line.

Bundle shape: {"id": "file#qualified_name", "text": "..."}
Score output: {"id": score, ...}
"""
import argparse
import json
import resource
import time


def load_bge():
    from sentence_transformers import CrossEncoder

    model = CrossEncoder("BAAI/bge-reranker-v2-m3", max_length=1024)

    def score(query: str, bundles: list[dict]) -> list[float]:
        pairs = [[query, b["text"]] for b in bundles]
        return [float(s) for s in model.predict(pairs)]

    return score


# Qwen3-Reranker-0.6B was the original second candidate (Apache-2.0,
# genuinely different generative/causal-LM architecture). Dropped: as a
# causal LM scoring a full "yes"/"no" vocab distribution per pair, it has
# no ONNX-optimized CPU path and was both far slower and, once, hung/crashed
# mid-batch under plain fp32 CPU torch — not something most OXIDE users
# (no dedicated GPU) could run in practice. Replaced with
# cross-encoder/ms-marco-MiniLM-L6-v2 (Apache-2.0): a 6-layer, ~22M-param
# classifier-head cross-encoder, run via ONNX Runtime (fastembed) instead of
# torch — no GPU, ~11s one-time load, ~4ms/pair on CPU in testing. Still
# "meaningfully different" from BGE-v2-m3 (568M, XLM-RoBERTa-large,
# multilingual): 25x smaller, classic MS MARCO lineage, English-only — a
# genuinely different point on the size/arch/speed Pareto frontier, and one
# realistic for CPU-only deployment the way BGE-v2-m3 itself barely is.
def load_minilm():
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    model = TextCrossEncoder(model_name="Xenova/ms-marco-MiniLM-L-6-v2")

    def score(query: str, bundles: list[dict]) -> list[float]:
        return [float(s) for s in model.rerank(query, [b["text"] for b in bundles])]

    return score


LOADERS = {"bge-v2-m3": load_bge, "minilm-l6": load_minilm}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, choices=LOADERS)
    ap.add_argument("--query")
    ap.add_argument("--bundles")
    ap.add_argument("--out")
    ap.add_argument("--batch")
    ap.add_argument("--out-dir")
    args = ap.parse_args()

    t_load0 = time.time()
    score = LOADERS[args.model]()
    load_seconds = time.time() - t_load0

    if args.batch:
        import os

        os.makedirs(args.out_dir, exist_ok=True)
        with open(args.batch) as f:
            tasks = [json.loads(line) for line in f if line.strip()]
        print(json.dumps({"model": args.model, "event": "loaded", "load_seconds": round(load_seconds, 3)}))
        for t in tasks:
            t0 = time.time()
            scores = score(t["query"], t["bundles"])
            seconds = time.time() - t0
            out = {b["id"]: s for b, s in zip(t["bundles"], scores)}
            with open(f"{args.out_dir}/{t['instance_id']}.scores.json", "w") as f:
                json.dump(out, f)
            print(json.dumps({
                "model": args.model, "instance_id": t["instance_id"],
                "n": len(t["bundles"]), "seconds": round(seconds, 3),
            }))
        peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        print(json.dumps({"model": args.model, "event": "done", "peak_rss_mb": round(peak_rss_mb, 1)}))
    else:
        bundles = json.loads(open(args.bundles).read())
        t0 = time.time()
        scores = score(args.query, bundles)
        seconds = time.time() - t0
        out = {b["id"]: s for b, s in zip(bundles, scores)}
        with open(args.out, "w") as f:
            json.dump(out, f)
        print(json.dumps({"model": args.model, "n": len(bundles), "load_seconds": round(load_seconds, 3), "seconds": round(seconds, 3)}))


if __name__ == "__main__":
    main()
