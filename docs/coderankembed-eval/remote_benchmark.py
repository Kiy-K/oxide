# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "numpy==2.1.3",
#   "onnxruntime==1.20.1",
#   "tokenizers==0.20.3",
# ]
# ///
"""Cheap HF CPU job for the controlled OXIDE CodeRankEmbed comparison."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer


INPUTS = Path(os.environ.get("CODERANK_INPUTS", "/inputs"))
WORK = Path(os.environ.get("CODERANK_WORK", "/tmp/coderank-bench"))
OUTPUT = Path(os.environ["CODERANK_OUTPUT"]) if os.environ.get("CODERANK_OUTPUT") else None
OXIDE_REVISION = "65cf6fb40dcd16ae87e8ebe851889c0c06507849"
QUERY_PREFIX = "Represent this query for searching relevant code: "
QWEN_PREFIX = (
    "Instruct: Given a coding task, retrieve repository symbols that are relevant "
    "to understand or change to complete it\nQuery: "
)


def run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None,
        timeout: int = 7200, check: bool = True) -> subprocess.CompletedProcess[str]:
    full_env = {**os.environ, "TOKENIZERS_PARALLELISM": "false", "OMP_NUM_THREADS": "4",
                **(env or {})}
    result = subprocess.run(cmd, cwd=cwd, env=full_env, text=True, capture_output=True,
                            timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f"{cmd!r} failed ({result.returncode})\n{result.stderr[-2000:]}")
    return result


def timed(cmd: list[str], **kwargs) -> tuple[subprocess.CompletedProcess[str], float, int]:
    started = time.perf_counter()
    result = run(["/usr/bin/time", "-f", "__OXIDE_COST__ %M", *cmd], **kwargs)
    elapsed = time.perf_counter() - started
    match = re.search(r"__OXIDE_COST__ (\d+)", result.stderr)
    return result, elapsed, int(match.group(1)) if match else 0


class CodeRankServer:
    def __init__(self, model_path: Path, port: int = 8192):
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(model_path), options,
                                            providers=["CPUExecutionProvider"])
        self.tokenizer = Tokenizer.from_file(str(INPUTS / "tokenizer.json"))
        self.stats: list[dict] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):
                length = int(self.headers.get("content-length", "0"))
                payload = json.loads(self.rfile.read(length))
                texts = payload.get("input", [])
                if isinstance(texts, str):
                    texts = [texts]
                vectors, stat = owner.embed(texts)
                owner.stats.append(stat)
                body = json.dumps({
                    "data": [{"index": i, "embedding": v.tolist()}
                             for i, v in enumerate(vectors)],
                    "model": payload.get("model", "CodeRankEmbed"),
                }).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = HTTPServer(("127.0.0.1", port), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def embed(self, raw_texts: list[str]) -> tuple[np.ndarray, dict]:
        is_query = [text.startswith(QWEN_PREFIX) for text in raw_texts]
        texts = [QUERY_PREFIX + text[len(QWEN_PREFIX):] if query else text
                 for text, query in zip(raw_texts, is_query)]
        max_length = 128 if texts and all(is_query) else 512
        started = time.perf_counter()
        parts = []
        for chunk_start in range(0, len(texts), 8):
            chunk = texts[chunk_start:chunk_start + 8]
            self.tokenizer.enable_truncation(max_length=max_length, strategy="longest_first")
            self.tokenizer.enable_padding(direction="right")
            encoded = self.tokenizer.encode_batch(chunk)
            ids = np.asarray([item.ids for item in encoded], dtype=np.int64)
            mask = np.asarray([item.attention_mask for item in encoded], dtype=np.int64)
            hidden = self.session.run(None, {"input_ids": ids, "attention_mask": mask})[0]
            parts.append(hidden[:, 0, :])
        vectors = np.concatenate(parts) if parts else np.empty((0, 768), dtype=np.float32)
        vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
        elapsed = time.perf_counter() - started
        return vectors, {
            "kind": "query" if all(is_query) else "document",
            "count": len(texts), "max_length": max_length,
            "elapsed_ms": elapsed * 1000, "inference_batch": 8,
        }

    def reset_stats(self):
        self.stats.clear()

    def close(self):
        self.httpd.shutdown()
        self.thread.join()


def install_and_build() -> tuple[Path, Path, Path]:
    if local_root := os.environ.get("CODERANK_OXIDE_ROOT"):
        root = Path(local_root).resolve()
        return (root / "target/release/oxide",
                root / "target/release/examples/term_coverage_index",
                root / "target/release/examples/embedding_profile_probe")
    run(["apt-get", "update"])
    run(["apt-get", "install", "-y", "--no-install-recommends",
         "ca-certificates", "curl", "git", "build-essential", "pkg-config", "time"])
    if not shutil.which("cargo"):
        run(["curl", "--proto", "=https", "--tlsv1.2", "-sSf",
             "https://sh.rustup.rs", "-o", "/tmp/rustup.sh"])
        run(["sh", "/tmp/rustup.sh", "-y", "--profile", "minimal",
             "--default-toolchain", "1.89.0"])
        os.environ["PATH"] = f"{Path.home() / '.cargo/bin'}:{os.environ['PATH']}"
    src = WORK / "oxide"
    if not src.exists():
        src.mkdir(parents=True)
        run(["tar", "-xf", str(INPUTS / "oxide-source.tar"), "-C", str(src)])
    run(["cargo", "build", "--release", "-j", "4", "--bin", "oxide",
         "--example", "term_coverage_index", "--example", "embedding_profile_probe"], cwd=src)
    return (src / "target/release/oxide",
            src / "target/release/examples/term_coverage_index",
            src / "target/release/examples/embedding_profile_probe")


def checkout(task: dict) -> Path:
    bare = WORK / "repos" / task["repo"].replace("/", "--")
    if not bare.exists():
        bare.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--filter=blob:none", task["repo_url"], str(bare)])
    run(["git", "fetch", "-q", "origin", task["base_commit"]], cwd=bare)
    dst = WORK / "checkouts" / f"{task['id']}@{task['base_commit'][:12]}"
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "worktree", "add", "--detach", "-q", str(dst), task["base_commit"]],
            cwd=bare)
    actual = run(["git", "rev-parse", "HEAD"], cwd=dst).stdout.strip()
    if actual != task["base_commit"]:
        raise RuntimeError(f"commit mismatch: {actual} != {task['base_commit']}")
    return dst


def provider_env(name: str) -> dict[str, str]:
    if name == "arctic-xs-q":
        return {"OXIDE_EMBED_NATIVE": "arctic-embed-xs-q", "OXIDE_EMBED_URL": "",
                "OXIDE_EMBED_MODEL": ""}
    return {"OXIDE_EMBED_NATIVE": "", "OXIDE_EMBED_URL": "http://127.0.0.1:8192/v1/embeddings",
            "OXIDE_EMBED_MODEL": f"CodeRankEmbed-{name}-cls-l2-q128-d512"}


def ranked_files(items: list[dict]) -> list[str]:
    seen: set[str] = set()
    out = []
    for item in items:
        file = item["file"]
        if file not in seen:
            seen.add(file)
            out.append(file)
    return out


def metrics(files: list[str], gold_files: list[str], k: int = 10) -> dict[str, float]:
    gold = set(gold_files)
    denom = max(len(gold), 1)
    recalls = {f"recall@{n}": len(gold.intersection(files[:n])) / denom for n in (1, 5, 10)}
    first = next((i + 1 for i, file in enumerate(files) if file in gold), None)
    gains = [1.0 if file in gold else 0.0 for file in files[:k]]
    dcg = sum(gain / math.log2(i + 2) for i, gain in enumerate(gains))
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(len(gold), k)))
    return {**recalls, "mrr": 1.0 / first if first else 0.0,
            "ndcg@10": dcg / ideal if ideal else 0.0}


def query(oxide: Path, repo: Path, task: dict, provider: str, mode: str) -> tuple[dict, float]:
    env = provider_env(provider)
    if mode == "budgeted":
        command = [str(oxide), "context", "--task", task["query"],
                   "--budget-tokens", "4096", "--json"]
    else:
        command = [str(oxide), "search", task["query"], "--mode", mode,
                   "--limit", "10", "--json"]
    result, elapsed, _ = timed(command, cwd=repo, env=env, timeout=300)
    payload = json.loads(result.stdout)
    items = payload["items"] if mode == "budgeted" else payload
    result_metrics = metrics(ranked_files(items), task["gold_files"])
    result_metrics["wall_ms"] = elapsed * 1000
    if mode == "budgeted":
        result_metrics["used_tokens"] = payload["used_tokens"]
    return result_metrics, elapsed


def index_stats(repo: Path, elapsed: float, peak_rss_kb: int) -> dict:
    db = repo / ".oxide/index.db"
    with sqlite3.connect(db) as connection:
        symbols = connection.execute("select count(*) from symbols").fetchone()[0]
        rows, vector_bytes = connection.execute(
            "select count(*), coalesce(sum(length(vec)), 0) from embeddings").fetchone()
    return {"wall_seconds": elapsed, "peak_rss_mb": peak_rss_kb / 1024,
            "symbols": symbols, "embedded_rows": rows,
            "symbols_per_second": symbols / elapsed, "index_db_bytes": db.stat().st_size,
            "vector_storage_bytes": vector_bytes}


def aggregate(rows: list[dict]) -> dict:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(row["provider"], row["language"])].append(row)
    out = {}
    for (provider, language), subset in sorted(grouped.items()):
        key = f"{provider}/{language}"
        out[key] = {"tasks": len(subset)}
        for mode in ("semantic", "hybrid", "budgeted"):
            for metric in ("recall@1", "recall@5", "recall@10", "mrr", "ndcg@10"):
                values = [row[mode][metric] for row in subset]
                out[key][f"{mode}.{metric}"] = sum(values) / len(values)
    return out


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    task_doc = json.loads((INPUTS / "tasks.json").read_text())
    if task_doc["oxide_commit"] != OXIDE_REVISION:
        raise RuntimeError("tasks.json and runner pin different OXIDE commits")
    oxide, indexer, native_probe = install_and_build()
    tasks = task_doc["tasks"]
    checkouts = {task["id"]: checkout(task) for task in tasks}
    rows = []
    if OUTPUT and OUTPUT.with_suffix(".jsonl").exists():
        rows = [json.loads(line) for line in OUTPUT.with_suffix(".jsonl").read_text().splitlines()
                if line.strip()]
    done = {(row["provider"], row["task"]) for row in rows}
    selected = set(os.environ.get(
        "CODERANK_PROVIDERS", "arctic-xs-q,fp32,int8-dynamic").split(","))
    provider_runtime = {}

    for provider, model_file in (
        ("arctic-xs-q", None),
        ("fp32", INPUTS / "model_fp32.onnx"),
        ("int8-dynamic", INPUTS / "model_int8_dynamic.onnx"),
    ):
        if provider not in selected:
            continue
        server = CodeRankServer(model_file) if model_file else None
        env = provider_env(provider)
        cache = WORK / f"embedding-cache-{provider}.db"
        for task in tasks:
            if (provider, task["id"]) in done:
                continue
            repo = checkouts[task["id"]]
            if (repo / ".oxide").exists():
                shutil.rmtree(repo / ".oxide")
            if server:
                server.reset_stats()
            _, elapsed, peak = timed([str(indexer), str(repo), task["base_commit"], str(cache)],
                                     cwd=repo, env=env)
            row = {"provider": provider, "task": task["id"], "repo": task["repo"],
                   "language": task["language"], "oracle": task["oracle"],
                   "gold_files": task["gold_files"],
                   "index": index_stats(repo, elapsed, peak)}
            if server:
                docs = [s for s in server.stats if s["kind"] == "document"]
                row["index"]["inference_embeddings_per_second"] = (
                    sum(s["count"] for s in docs) /
                    max(sum(s["elapsed_ms"] for s in docs) / 1000, 1e-12)
                )
            for mode in ("semantic", "hybrid", "budgeted"):
                before = len(server.stats) if server else 0
                row[mode], _ = query(oxide, repo, task, provider, mode)
                if server:
                    stats = server.stats[before:]
                    queries = [s for s in stats if s["kind"] == "query"]
                    row[mode]["embedding_ms"] = sum(s["elapsed_ms"] for s in queries)
            rows.append(row)
            print("RESULT " + json.dumps(row, separators=(",", ":")), flush=True)
            if OUTPUT:
                OUTPUT.parent.mkdir(parents=True, exist_ok=True)
                OUTPUT.with_suffix(".jsonl").open("a").write(json.dumps(row) + "\n")

        if server:
            server.close()
            provider_runtime[provider] = {"model_bytes": model_file.stat().st_size,
                                          "query_max_tokens": 128, "document_max_tokens": 512,
                                          "ort_intra_threads": 4, "ort_inter_threads": 1}
        else:
            probe, elapsed, peak = timed([str(native_probe), "arctic-embed-xs-q", "bare"],
                                         cwd=oxide.parents[2], env=env)
            provider_runtime[provider] = {"probe_stdout": probe.stdout,
                                          "probe_wall_seconds": elapsed,
                                          "probe_peak_rss_mb": peak / 1024}

    final = {"oxide_commit": OXIDE_REVISION, "dataset_revision": task_doc["dataset_revision"],
             "rows": rows, "per_language": aggregate(rows), "runtime": provider_runtime}
    if OUTPUT:
        OUTPUT.write_text(json.dumps(final, indent=2) + "\n")
    print("FINAL_JSON " + json.dumps(final, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
