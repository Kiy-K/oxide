#!/usr/bin/env python3
"""One-process ORT/Tokenizers benchmark; emit one JSON record."""
import argparse
import json
import resource
import statistics
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer


def elapsed(call):
    started = time.perf_counter()
    value = call()
    return time.perf_counter() - started, value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--threads", type=int, required=True)
    parser.add_argument("--sequence", type=int, default=256)
    parser.add_argument("--batch", type=int, default=1)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--save-optimized", type=Path)
    args = parser.parse_args()

    tokenizer_s, tokenizer = elapsed(lambda: Tokenizer.from_file(str(args.tokenizer)))
    tokenizer.enable_truncation(max_length=args.sequence)
    tokenizer.enable_padding(length=args.sequence)
    text = ("pub async fn retry_request context timeout backoff result error { value } " *
            (args.sequence // 8 + 2))
    tokenize_s, encoded = elapsed(lambda: tokenizer.encode_batch([text] * args.batch))
    feeds = {
        "input_ids": np.asarray([row.ids for row in encoded], dtype=np.int64),
        "attention_mask": np.asarray([row.attention_mask for row in encoded], dtype=np.int64),
    }

    options = ort.SessionOptions()
    options.intra_op_num_threads = args.threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    if args.save_optimized:
        options.optimized_model_filepath = str(args.save_optimized)
    session_s, session = elapsed(lambda: ort.InferenceSession(
        str(args.model), options, providers=["CPUExecutionProvider"]
    ))
    first_s, output = elapsed(lambda: session.run(None, feeds)[0][:, 0, :])
    warm = [elapsed(lambda: session.run(None, feeds)[0][:, 0, :])[0] for _ in range(args.runs)]
    vectors = output / np.linalg.norm(output, axis=1, keepdims=True)
    print(json.dumps({
        "model": args.model.name, "threads": args.threads,
        "sequence": args.sequence, "batch": args.batch,
        "tokenizer_load_ms": tokenizer_s * 1000,
        "tokenize_ms": tokenize_s * 1000,
        "session_create_ms": session_s * 1000,
        "first_ms": first_s * 1000,
        "warm_median_ms": statistics.median(warm) * 1000,
        "embeddings_per_s": args.batch / statistics.median(warm),
        "peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "finite": bool(np.isfinite(vectors).all()),
    }))


if __name__ == "__main__":
    main()
