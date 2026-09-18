# CodeRankEmbed CPU experiment

## Verdict

**Reject `nomic-ai/CodeRankEmbed` as an OXIDE runtime/default candidate.** FP32 is
correct but far too slow and large for the product's local-first UX. Dynamic
INT8 cuts the model to 138.5 MB, but its fixture fidelity is weak, indexing was
4.2–4.9x slower than Arctic XS-Q on the two controlled Dark Reader commits, and
it did not produce a consistent retrieval/context-quality gain. Static INT8 is
incorrect enough to reject before retrieval testing.

Stop rule: after FP32 failed the UX gate, dynamic INT8 was run on the two
TypeScript tasks. The second task showed one budgeted-recall gain but otherwise
flat or worse ranks; the remaining Python/Rust/Go CodeRank runs were cancelled
rather than spend hours confirming an already failed promotion case. Their
CodeRank metrics are **not measured**, not zero.

No provider-selection or production runtime code changed.

## 1. Exact reference and export pipeline

Pinned upstream:

- model: `nomic-ai/CodeRankEmbed`
- revision: `3c4b60807d71f79b43f3c4363786d9493691f8b1`
- upstream `model.safetensors` SHA-256:
  `827529bcd58aef0d9082e66eeff7e7d53a02f62bd005f841a26b3d3e2fb17ebe`
- embedding dimension: 768
- maximum sequence length: 8192
- query prefix, verbatim:
  `Represent this query for searching relevant code: `
- document prefix: none
- tokenizer: upstream tokenizer, right padding, right truncation
- pooling: CLS token (`1_Pooling/config.json` enables CLS and disables mean)
- normalization: L2 after pooling

The 12-case deterministic fixture covers natural-language queries, Python,
TypeScript/TSX, Rust, Go, short and long symbols, Unicode, empty/minimal input,
and a retry hard negative. The reference records input hashes, token counts,
embeddings, and full nearest-neighbor ordering.

Reproduction environment:

```text
Python 3.11
torch 2.6.0+cpu
sentence-transformers 2.7.0
transformers 4.45.1
onnx 1.17.0
onnxruntime 1.20.1
tokenizers 0.20.3
```

```bash
hf download nomic-ai/CodeRankEmbed \
  --revision 3c4b60807d71f79b43f3c4363786d9493691f8b1 \
  --local-dir target/coderankembed-model

uv venv --python 3.11 target/coderankembed-py311
uv pip install --python target/coderankembed-py311/bin/python \
  torch==2.6.0 sentence-transformers==2.7.0 transformers==4.45.1 \
  onnx==1.17.0 onnxruntime==1.20.1 tokenizers==0.20.3

target/coderankembed-py311/bin/python docs/coderankembed-eval/pipeline.py all \
  --model target/coderankembed-model \
  --fixture docs/coderankembed-eval/fixture.json \
  --out docs/coderankembed-eval/raw --threads 4
```

The export uses `AutoModel(..., add_pooling_layer=False)`, emits dynamic
`input_ids`/`attention_mask -> last_hidden_state` at opset 17, then takes
`last_hidden_state[:, 0, :]` and L2-normalizes outside the graph. This is the
target Rust runtime boundary: HF Tokenizers, ORT, CLS pooling, L2; neither
SentenceTransformers nor Python is required in a production implementation.

No official upstream ONNX artifact exists. A pinned community FP32 artifact
from `jamie8johnson/CodeRankEmbed-onnx` matched the reference, but its card
incorrectly says mean pooling, so it is not trustworthy as documentation. The
local reproducible export is the source of truth. The community INT8 artifact
from `mrsladoje/CodeRankEmbed-onnx-int8` was worse than the local dynamic INT8
artifact (minimum cosine 0.86702 versus 0.89590).

## 2. FP32 parity

Artifact:

```text
size     547,637,417 bytes (522.3 MiB)
SHA-256  2b3c282e9a61101f0290d393922fa3e8f1e395ac0092c8925ee7bb04aaf62ba2
```

Across all 12 fixture inputs, including the 2,522-token long input:

| Measure | Result |
|---|---:|
| Maximum absolute element difference | 5.96e-7 |
| Minimum cosine vs upstream | 0.99999988 |
| Exact top-1 agreement | 100% |
| Exact top-5 set agreement | 100% |

FP32 therefore preserves upstream behavior. It is rejected for runtime UX,
not correctness.

## 3. Quantization

| Artifact | Size | Max abs error | Min cosine | Top-1 | Exact top-5 set | Decision |
|---|---:|---:|---:|---:|---:|---|
| FP32 | 547.6 MB | 5.96e-7 | 0.9999999 | 100% | 100% | correct, too slow |
| Dynamic INT8 | 138.5 MB | 0.12088 | 0.89590 | 100% | 83.3% | runtime/retrieval reject |
| Static INT8 QDQ | 208.9 MB | 0.48389 | 0.15523 | 83.3% | 25.0% | correctness reject |

```text
dynamic INT8 SHA-256  1622d679339c7d541622f54c2f9521a5ae24d7bf12763e3f62205b4ca3628873
static INT8 SHA-256   8b1c6a1dbfdfc866bc3a489cbe2e7249153aef83b782525649dcc5c1d527f25b
```

Dynamic INT8 used per-channel signed weights with reduced range. Static INT8
used QDQ, unsigned activations, signed per-channel weights, reduced range, and
fixture calibration up to 2,048 tokens. Quantizing all eligible operations
first failed in ORT's rotary `Slice`/`ReduceMax` path, so the defensible static
trial was restricted to `MatMul`/`Gemm`. Its long-input collapse and ranking
damage disqualify it; small average cosine error would not have been accepted.

Dynamic INT8 remained finite on every fixture, but the empty input fell to
0.8959 cosine and hard-negative ordering changed. The independently downloaded
community INT8 artifact was worse. These are material CPU-quantization
correctness warnings, not cosmetic numeric drift.

## 4. ORT and Tokenizers configuration

Recommended configuration **if CodeRankEmbed is used as an offline teacher or
quality ceiling**, not as OXIDE's interactive provider:

```text
tokenizers parallelism: off
ORT execution: sequential
ORT intra-op: 4 threads (UX-safe laptop setting)
ORT inter-op: 1 thread
ORT graph optimization: ORT_ENABLE_ALL
query cap: 128 tokens
document cap: 512 tokens
indexing batch: 8
session: resident
```

The raw throughput maximum was eight ORT threads and batch 64, but that is the
wrong local tradeoff: eight threads improved the 256-token INT8 median only
about 11% over four in the low-contention sweep, while batch 64 raised observed
RSS to about 2.0 GB. Batch 8 stayed near 0.94 GB at 512 tokens.

Four-thread laptop measurements were intentionally run under gaming load and
are noisy, but directionally decisive:

| Model | Tokens | Batch | Warm median | Throughput | Peak RSS |
|---|---:|---:|---:|---:|---:|
| FP32 | 128 | 1 | 185 ms | 5.39/s | 1.03 GiB |
| FP32 | 256 | 1 | 253 ms | 3.95/s | 1.04 GiB |
| FP32 | 512 | 8 | 7,674 ms | 1.04/s | 1.33 GiB |
| FP32 | 2,048 | 1 | 9,972 ms | 0.10/s | 1.43 GiB |
| INT8 | 128 | 1 | 116 ms | 8.60/s | 336 MiB |
| INT8 | 256 | 8 | 1,266 ms | 6.32/s | 431 MiB |
| INT8 | 512 | 8 | 1,392 ms | 5.75/s | 918 MiB |
| INT8 | 2,048 | 1 | 1,217 ms | 0.82/s | 1.04 GiB |

Tokenizer load was roughly 15–31 ms. INT8 session creation was roughly
0.3–0.7 s; FP32 was roughly 0.8–1.8 s. Saving an AOT optimized INT8 graph cut
one low-contention session creation observation from 439 ms to 226 ms without
changing warm inference (33.0 vs 32.6 ms), but ORT warned that the graph was
hardware-specific. This is useful only for a packaged per-target artifact.
Keeping the session resident in MCP matters more.

## 5. OXIDE quality/performance comparison

Protocol:

- OXIDE commit `65cf6fb40dcd16ae87e8ebe851889c0c06507849`, which includes the
  streaming bounded top-200 vector scan
- pinned ContextBench revision
  `c2855792b006af41c67202d33883fb9d46362853`
- identical commits, query text, `symbol_embed_text`, fusion, candidate depth,
  structural expansion, allocator, and 4,096-token context budget
- CodeRank query 128/document 512 caps; exact query prefix restored at the
  experiment HTTP boundary because production `open_embedder` currently wires
  HTTP providers to the Qwen3 prompt protocol
- one process/provider at a time; local CPUs 12–15 only, `nice 19`, idle I/O

Completed controlled comparison (two Dark Reader TypeScript tasks):

| Provider | Mode | R@1 | R@5 | R@10 | MRR | nDCG@10 |
|---|---|---:|---:|---:|---:|---:|
| Arctic XS-Q | vector | 0.000 | 0.125 | 0.250 | 0.167 | 0.167 |
| CodeRank INT8 | vector | 0.000 | 0.125 | 0.250 | 0.125 | 0.154 |
| Arctic XS-Q | hybrid | 0.000 | 0.250 | 0.250 | 0.250 | 0.221 |
| CodeRank INT8 | hybrid | 0.000 | 0.250 | 0.250 | 0.167 | 0.182 |
| Arctic XS-Q | budgeted | 0.000 | 0.125 | 0.125 | 0.250 | 0.123 |
| CodeRank INT8 | budgeted | 0.000 | 0.250 | 0.250 | 0.167 | 0.182 |

The single budgeted-recall improvement did not carry through to MRR and did
not outweigh the runtime/storage cost.

| Cost on Dark Reader | Arctic XS-Q | CodeRank INT8 |
|---|---:|---:|
| First commit index | 67.3 s | 281.0 s |
| Second commit with shared cache | 17.3 s | 84.2 s |
| First-commit symbols/s | 20.9 | 5.0 |
| Model bytes | 22,972,992 | 138,510,447 |
| Vector bytes, 1,408 symbols | 2,162,688 | 4,325,376 |
| Vector width | 384 | 768 |

CodeRank's resident-server query inference took 35–96 ms in these tasks; full
one-shot search/context commands took 79–137 ms. Arctic's one-shot commands
took 159–170 ms on the second task because each CLI invocation creates a native
session. That comparison does **not** establish an MCP win: MCP keeps Arctic's
session resident too, while CodeRank lacks a production native integration.

Arctic baseline-only evidence was also collected for Flask (Python) and two
Tokio (Rust) tasks before the stop decision. CodeRank Python, Rust, and Go were
not run. Therefore this experiment makes no claim of per-language CodeRank
quality outside TypeScript, no full Recall/MRR/nDCG aggregate, and no measured
single-edit latency. Continuing those measurements would not change the
runtime rejection.

HF Jobs `cpu-upgrade` ($0.03/hour) was attempted to keep work off the laptop.
It failed during setup because the current OXIDE repository is private; the
safety gate correctly refused uploading a private source archive without
explicit authorization. No GPU job was used.

## 6. Accept/reject decision

- **FP32 runtime:** reject.
- **Static INT8 artifact:** reject.
- **Dynamic INT8 runtime/default:** reject.
- **CodeRankEmbed as an offline teacher/ceiling:** retain as a useful option.
- **Change OXIDE's default:** no.

The candidate did not show the large, repeated context-quality improvement
needed to justify about 6x model bytes, 2x vector storage, materially weaker
quantized parity, and roughly 4–5x indexing time in the controlled slice. The
product objective is agent utility per context token at acceptable local cost,
not benchmark prestige.

## 7. Future training-data observations

- Preserve task-to-relevant-code labels at symbol/span granularity, plus final
  budgeted pack labels; file-only gold is too coarse to train context utility.
- Mine same-concept cross-language hard negatives (retry/backoff, parsing,
  lifecycle methods). The fixture shows these reorder under INT8.
- Record dependency/context files separately from primary edit targets so a
  teacher does not collapse “changed file” into “all useful context.”
- Balance Python, TypeScript/TSX, Rust, and Go explicitly. ContextBench had no
  Gin rows, so commit-derived Go labels were weaker and should not be treated as
  human gold.
- Keep empty/minimal, Unicode, very short, and long-symbol examples; empty and
  long inputs exposed the largest quantization failures.
- Use CodeRankEmbed FP32 offline to score hard negatives or generate candidate
  labels, then validate labels with human/task outcomes. Do not distill its
  mistakes blindly.
- Optimize the future dataset for relevant code delivered inside a fixed token
  budget and downstream agent success, not MTEB similarity alone.

## Artifacts

- `fixture.json` — deterministic reference cases
- `pipeline.py` — reference, export, quantization, parity
- `bench_runtime.py` — ORT/tokenizer microbenchmark
- `tasks.json` — pinned real-repository task slice
- `remote_benchmark.py` — controlled OXIDE runner (also supports capped local use)
- `raw/reference.json`, `raw/reference_embeddings.npz` — upstream reference
- `raw/parity.json` — FP32/dynamic/static comparison
- `raw/runtime_matrix.jsonl`, `raw/runtime_threads.jsonl` — CPU measurements
- `raw/aot_optimization.json` — saved-graph startup observation
- `raw/community_artifacts.json` — third-party artifact audit
- `raw/oxide_benchmark.jsonl` — completed partial OXIDE comparison

Large ONNX files are locally reproducible and ignored by Git.
