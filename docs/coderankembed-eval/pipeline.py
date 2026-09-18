#!/usr/bin/env python3
"""Pinned CodeRankEmbed reference, ONNX export, quantization, and parity checks."""
import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np

QUERY_PREFIX = "Represent this query for searching relevant code: "


def rows(path):
    data = json.loads(path.read_text())
    for row in data:
        row["text"] += row.get("repeat_text", "") * row.get("repeat_count", 0)
    return data


def unit(vector):
    vector = np.asarray(vector, dtype=np.float32)
    norm = np.linalg.norm(vector)
    return vector / norm if norm else vector


def prepared(row):
    return QUERY_PREFIX + row["text"] if row["role"] == "query" else row["text"]


def neighbors(vectors):
    ids = list(vectors)
    matrix = np.stack([vectors[key] for key in ids])
    return {
        key: [ids[i] for i in np.argsort(-(matrix @ matrix[n]), kind="stable") if i != n]
        for n, key in enumerate(ids)
    }


def reference(args):
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(str(args.model), trust_remote_code=True, device="cpu")
    tokenizer = model.tokenizer
    vectors = {}
    metadata = []
    for row in rows(args.fixture):
        text = prepared(row)
        # Individual encoding avoids padding-dependent numerical drift in the fixture.
        vector = model.encode(text, normalize_embeddings=True, show_progress_bar=False)
        vectors[row["id"]] = vector
        encoded = tokenizer(text, truncation=True, max_length=model.max_seq_length)
        metadata.append({
            "id": row["id"], "role": row["role"],
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "tokens": len(encoded["input_ids"]),
        })
    args.out.mkdir(parents=True, exist_ok=True)
    np.savez(args.out / "reference_embeddings.npz", **vectors)
    (args.out / "reference.json").write_text(json.dumps({
        "model_revision": args.revision,
        "query_prefix": QUERY_PREFIX,
        "pooling": "CLS token",
        "normalization": "L2",
        "dimension": len(next(iter(vectors.values()))),
        "tokenizer": {"model_max_length": tokenizer.model_max_length,
                      "padding_side": tokenizer.padding_side,
                      "truncation_side": tokenizer.truncation_side},
        "inputs": metadata,
        "nearest_neighbors": neighbors(vectors),
    }, indent=2) + "\n")


def export(args):
    import torch
    from transformers import AutoModel, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True, local_files_only=True)
    model = AutoModel.from_pretrained(
        args.model, trust_remote_code=True, local_files_only=True, add_pooling_layer=False
    ).eval()

    class Encoder(torch.nn.Module):
        def __init__(self, inner):
            super().__init__()
            self.inner = inner

        def forward(self, input_ids, attention_mask):
            return self.inner(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state

    sample = tokenizer("export probe", return_tensors="pt")
    args.out.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        Encoder(model), (sample["input_ids"], sample["attention_mask"]), args.out / "model_fp32.onnx",
        input_names=["input_ids", "attention_mask"], output_names=["last_hidden_state"],
        dynamic_axes={"input_ids": {0: "batch", 1: "sequence"},
                      "attention_mask": {0: "batch", 1: "sequence"},
                      "last_hidden_state": {0: "batch", 1: "sequence"}},
        opset_version=17, do_constant_folding=True,
    )


def quantize(args):
    from onnxruntime.quantization import QuantType, quantize_dynamic

    quantize_dynamic(
        args.out / "model_fp32.onnx", args.out / "model_int8_dynamic.onnx",
        weight_type=QuantType.QInt8, per_channel=True, reduce_range=True,
    )


def quantize_static_model(args):
    from onnxruntime.quantization import (
        CalibrationDataReader, QuantFormat, QuantType, quantize_static,
    )
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True, local_files_only=True)

    class Reader(CalibrationDataReader):
        def __init__(self):
            self.data = iter([
                {k: v for k, v in tokenizer(
                    prepared(row), return_tensors="np", truncation=True, max_length=2048
                ).items() if k in {"input_ids", "attention_mask"}}
                for row in rows(args.fixture)
            ])

        def get_next(self):
            return next(self.data, None)

    quantize_static(
        args.out / "model_fp32.onnx", args.out / "model_int8_static.onnx", Reader(),
        quant_format=QuantFormat.QDQ, activation_type=QuantType.QUInt8,
        weight_type=QuantType.QInt8, per_channel=True, reduce_range=True,
        op_types_to_quantize=["MatMul", "Gemm"],
    )


def ort_vectors(model_path, args, threads):
    import onnxruntime as ort
    from transformers import AutoTokenizer

    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session = ort.InferenceSession(str(model_path), options, providers=["CPUExecutionProvider"])
    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True, local_files_only=True)
    vectors = {}
    for row in rows(args.fixture):
        inputs = tokenizer(prepared(row), return_tensors="np", truncation=True, max_length=8192)
        hidden = session.run(None, {k: v for k, v in inputs.items() if k in {"input_ids", "attention_mask"}})[0]
        vectors[row["id"]] = unit(hidden[0, 0])
    return vectors


def parity(args):
    reference = np.load(args.out / "reference_embeddings.npz")
    report = {}
    for name in ["fp32", "int8_dynamic", "int8_static"]:
        path = args.out / f"model_{name}.onnx"
        if not path.exists():
            continue
        vectors = ort_vectors(path, args, args.threads)
        per_input = {}
        for key, vector in vectors.items():
            expected = reference[key]
            per_input[key] = {"max_abs": float(np.max(np.abs(expected - vector))),
                              "cosine": float(expected @ vector)}
        expected_order = neighbors({key: reference[key] for key in reference.files})
        actual_order = neighbors(vectors)
        report[name] = {
            "size_bytes": path.stat().st_size,
            "max_abs": max(v["max_abs"] for v in per_input.values()),
            "min_cosine": min(v["cosine"] for v in per_input.values()),
            "top1_agreement": np.mean([expected_order[k][0] == actual_order[k][0] for k in actual_order]),
            "top5_agreement": np.mean([set(expected_order[k][:5]) == set(actual_order[k][:5]) for k in actual_order]),
            "per_input": per_input,
            "nearest_neighbors": actual_order,
        }
    (args.out / "parity.json").write_text(json.dumps(report, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["reference", "export", "quantize", "quantize_static_model", "parity", "all"])
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, default=Path(__file__).with_name("fixture.json"))
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("raw"))
    parser.add_argument("--revision", default="3c4b60807d71f79b43f3c4363786d9493691f8b1")
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    commands = ["reference", "export", "quantize", "quantize_static_model", "parity"] if args.command == "all" else [args.command]
    for command in commands:
        started = time.perf_counter()
        globals()[command](args)
        print(f"{command}: {time.perf_counter() - started:.3f}s")


if __name__ == "__main__":
    main()
