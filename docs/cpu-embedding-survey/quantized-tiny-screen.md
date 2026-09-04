# Quantized tiny-embedder screen (in progress — Stage 1 partial)

Follow-up to `docs/cpu-embedding-survey/README.md` (Stage A, committed
2026-09-03). That report already covered fp32 `minilm-l6-v2`,
`arctic-embed-xs`, `bge-small-en-v1.5`, and `jina-code-v2` (native, in-process
fastembed) plus `qwen3-Q8_0`/`nomic-v2-moe` (HTTP). This screen asks a
narrower question: do the **quantized (`*Q`)** fastembed variants of the
three smallest candidates buy a real CPU speedup over their already-measured
fp32 counterparts, using `jina-code-v2` as the "expensive but good" reference
point (not `qwen3-Q8_0`, which is the actual shipped default — see caveat in
the parent README's "Correction" note added this session).

**Status: paused mid-run at the user's request ("commit, continue tomorrow").
No verdict yet.** Two of three quantized candidates have real-corpus numbers;
one (`bge-small-en-v1.5-q`) and the `jina-code-v2` real-corpus reference run
have not been measured yet.

## Code change

`src/embeddings.rs::native_model_spec` gained three new profile strings —
`minilm-l6-v2-q`, `bge-small-en-v1.5-q`, `arctic-embed-xs-q` — mapping to
fastembed 6.0.2's existing `AllMiniLML6V2Q` / `BGESmallENV15Q` /
`SnowflakeArcticEmbedXSQ` enum variants (confirmed present in the vendored
crate source before writing this; not guessed). Same pattern as the existing
`embeddinggemma-300m` / `embeddinggemma-300m-q4` pair. Quantization is
assumed not to change each model's documented query/document prompt or
pooling convention (mirrors the Gemma-q4 precedent) — not independently
re-verified against each model card for the `Q` variant specifically.
`open_embedder`/`configured_provider_name`/the CLI's provider-selection path
were **not** touched; these are additive `OXIDE_EMBED_NATIVE` profile
strings only, same as every other native profile in this survey.

## Corpus

`matplotlib` (from `~/.cache/oxide-contextbench/repos/matplotlib`, copied to
a scratch dir). Chosen as the closest available match to an unlocated
"~804 files/~3412 symbols" reference corpus from an earlier (undocumented —
searched and not found) session; user confirmed this choice.

**Actual scale, discovered when indexed — larger than the target**: 977
scanned files (895 `.py`/`.ts`), **18,532 symbols** — about 5.4x the ~3412
target. Not re-scoped mid-run; flagged here so tomorrow's continuation reads
the numbers at their true scale rather than assuming they match the smaller
target.

## Environment

- CPU: 13th Gen Intel Core i7-13620H, 16 logical cores
- RAM: 15Gi total, ~10Gi available at run time
- Load average during runs: 3.3–4.5 (not a fully idle machine — 2 sessions
  logged in; no other `oxide`/`contextbench` process was running, confirmed
  via `pgrep`, but this is not the "verified idle" bar Stage A held itself to)
- rustc/cargo 1.98.0, OXIDE commit `f3f86f0` + this session's uncommitted
  (now committed) `native_model_spec` addition
- Build: `cargo build --release --features native-embed`

## Results so far

### Synthetic microbenchmark (`examples/embedding_profile_probe`, sample_doc x500 batch)

| Profile | Dim | Cold init | `embed_query` p50 | `embed_query` p95 | Throughput (items/s) | Peak RSS |
|---|---:|---:|---:|---:|---:|---:|
| minilm-l6-v2 (fp32, Stage A reference) | 384 | 166.8ms | 1.95ms | 4.87ms | 310.5 | 629.4MB |
| **minilm-l6-v2-q** | 384 | 9818.3ms (incl. download) | 1.33ms | 1.51ms | **528.9** | 855.6MB |
| arctic-embed-xs (fp32, Stage A reference) | 384 | 177.8ms | 2.48ms | 2.97ms | 298.3 | 582.2MB |
| **arctic-embed-xs-q** | 384 | 96.6ms (cached) | 1.80ms | 2.17ms | **540.4** | 746.2MB |
| bge-small-en-v1.5 (fp32, Stage A reference) | 384 | 293.0ms | 4.47ms | 5.32ms | 156.2 | 640.2MB |
| **bge-small-en-v1.5-q** | 384 | 14599.8ms (incl. download) | 18.48ms (reproduced: 15.62ms) | 21.62ms | **130.7** (reproduced: 130.3) | 572.4MB |
| jina-code-v2 (fp32, Stage A reference, not rerun) | 768 | 1172.1ms | 11.71ms | 14.01ms | 35.1 | 1889.0MB |

**MiniLM-Q and Arctic-Q are faster than their fp32 counterparts** (as
expected for int8 CPU quantization). **BGE-small-Q is slower than its fp32
counterpart on every axis** (query p50 18.48ms vs fp32's 4.47ms, throughput
130.7 vs 156.2 items/s) — reproduced on a second run, not a fluke. Plausible
cause, not yet confirmed: BGE-small-Q's `Qdrant/bge-small-en-v1.5-onnx-Q`
repo uses a different quantization/optimization pipeline
(`model_optimized.onnx`) than MiniLM-Q/Arctic-Q's Xenova-style
`model_quantized.onnx`, possibly without fused int8 kernels for this op set
on this CPU's ONNX Runtime build. Not investigated further — out of scope
for a screening pass.

### Real-corpus full embedding pass (`oxide index`, matplotlib, 18,532 symbols)

Base parse+graph layer (offline `hashed-bow-256`, one-time, shared across all
embedder runs below): **5.7s cold**. Each embedder run below starts from
that already-parsed state and switches `OXIDE_EMBED_NATIVE`, which trips
`update_index`'s embedding-space-changed fingerprint check and re-embeds
every symbol — the same code path a real profile switch would take in
production, not a synthetic proxy.

| Profile | Wall time | Symbols | Items/s | Peak RSS (`/usr/bin/time -v`) |
|---|---:|---:|---:|---:|
| **minilm-l6-v2-q** | 1:50.44 (110.4s) | 18,532 | **167.8** | 233.2MB |
| **arctic-embed-xs-q** | 1:53.51 (113.5s) | 18,532 | **163.3** | 232.9MB |
| bge-small-en-v1.5-q | *not yet run* | — | — | — |
| jina-code-v2 (reference) | *not yet run* | — | — | — |

**Important finding — the synthetic probe overstates real-repo throughput by
~3.1-3.3x.** MiniLM-Q measured 528.9 items/s synthetic vs 167.8 items/s on
the real corpus; Arctic-Q measured 540.4 vs 163.3. Real `embed_text` strings
(`file + kind + qualified_name + signature + imports + references`,
`index.rs::embed_text`) are longer and more variable than the probe's fixed
5-line `sample_doc`, and the indexer's batching/SQLite-write path differs
from the probe's single 500-item `embed_documents` call — this gap has not
been decomposed further (which factor dominates is unknown). **Any Stage 5
early-stop verdict must use the real-corpus number, not the synthetic probe
number** — the synthetic number would have made both candidates look
~3x better than they actually perform end to end.

Peak RSS from `/usr/bin/time -v` on the real run (~233MB) is also much lower
than the synthetic probe's self-reported peak (746–856MB) for the same
profiles — likely measuring different things (whole-process RSS at a
different sample point vs the probe's own `VmHWM` read right after its
heaviest batch call). Not reconciled; flagged as an open methodology gap
rather than trusted at face value in either direction.

## Not yet done (continue here tomorrow)

1. Real-corpus run for `bge-small-en-v1.5-q` (expect ~140s based on its
   ~0.71x synthetic-throughput ratio relative to MiniLM-Q, extrapolated from
   the two completed real-corpus points — not measured).
2. Real-corpus run for `jina-code-v2` as the reference point (expect
   ~9 minutes at its Stage A synthetic 35.1 items/s rate, likely worse for
   the same reason the two quantized candidates were ~3x worse in practice —
   budget accordingly, this is the expensive one).
3. Stage 4 cheap retrieval sanity check — not started at all.
4. Stage 5 early-stop verdict and Stage 6 Pareto ranking — blocked on 1–3.
5. Decide whether the ~5.4x corpus-scale mismatch (18,532 vs target ~3412
   symbols) matters enough to rerun on a right-sized subset, or whether
   today's numbers are fine to reason about directly (throughput and RSS
   are per-symbol/steady-state figures that shouldn't be scale-sensitive;
   wall-clock totals obviously are).

## Provenance

Branch `embed-cpu-survey`, worktree `/home/khoi/Projects/oxide-embed-survey`,
commit adding this doc + the three `native_model_spec` profiles. Corpus
copies live in a scratch dir outside the repo and are not committed.
