# Provenance

- `lscpu*.txt`, `cpu-isa-flags.txt`: the measurement machine (i7-13620H; P-cores = CPUs 0–11, E-cores = 12–15; AVX2/FMA/AVX-VNNI present, no AVX-512/AMX).
- `latency_cpu_m7a_xlarge_20260924.json`: upstream Laya's own CPU latency benchmark (NandhaKishorM/laya `research/results/`, fetched at upstream HEAD `4066d5d5fbf08b66c6757ddeedbd797bd7655bc0`), same checkpoint revision `55cf4c4e` as this study.
- `export_onnx.py`: upstream `scripts/export_onnx.py` at the same HEAD, used unmodified for the ONNX row.
- Context7 (`/nandhakishorm/laya`, queried 2026-09-25): BENCHMARKS.md "Themes" table — "RAG passage relevance | laya 0.625 | laya-multilingual 0.657 | laya-typed-decisions 0.625 | in training"; README "Honest limits": base checkpoints are "a fast base to specialise, not a zero-shot decision engine".
- Baseline ContextBench dump at the pin: re-dumped with `target/release/examples/fusion_dump <repo> <task.jsonl>` per `cb-tasks.jsonl` line; decompressed sha256 `94a4dcc0a2d02bc36abeb74fbee3f8b5bf677059e517e2b228f6e4dff104903b`, identical to decompressed `docs/ranking-fusion-eval/results/dump-contextbench.jsonl.gz` (so the duplicate is not kept).
- `results/inputs/cb-shortlists-first3.jsonl` / `-first5.jsonl` are `head -3` / `head -5` of `cb-shortlists.jsonl` (first5 was re-created after the runs that used it; content identical by construction).
