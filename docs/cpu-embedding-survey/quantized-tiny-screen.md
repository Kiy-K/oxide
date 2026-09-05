# Quantized tiny-embedder screen (Step 1 — complete)

Follow-up to `docs/cpu-embedding-survey/README.md` (Stage A). That report
covered the **fp32** natives (`minilm-l6-v2`, `arctic-embed-xs`,
`bge-small-en-v1.5`, `jina-code-v2`) plus `qwen3-Q8_0`/`nomic-v2-moe` over
HTTP. This screen asks a narrower question, framed as step 1 of a possible
future dual-embedding (fast-pass → quality-pass) indexing strategy:

> Which **quantized** tiny model is fast enough, and plausibly good enough,
> to earn an expensive full-ContextBench quality run as a first-pass
> semantic embedder?

**No dual-embedding architecture was designed or implemented here.** No
retrieval, fusion, graph, allocator, budget, or default-provider-selection
semantics were changed. The only code change is three additive
`OXIDE_EMBED_NATIVE` profile strings.

## Correction to the task's framing

The task described "the existing Jina Code profile" as the production
reference. It is not: OXIDE's shipped default is `qwen3-Q8_0` over HTTP
(`open_embedder` → `HttpEmbedder`). `jina-code-v2` is a native fastembed
candidate introduced by the Stage A survey. It is used here as the
"expensive but good" reference point the task asked for, but it is **not**
the frozen production baseline, and nothing below should be read as a
comparison against production quality.

The task's cited prior numbers (~3412 symbols, ~1.75ms exact vector scan,
~93.1s Jina embedding wall) could not be located in any committed file,
worktree, or results directory. They are consistent with Stage A's measured
`jina-code-v2` throughput, so they are treated as plausible but unverified
context, not as a baseline anything here is scored against.

## Code change

`src/embeddings.rs::native_model_spec` gained three profile strings —
`minilm-l6-v2-q`, `bge-small-en-v1.5-q`, `arctic-embed-xs-q` — mapping to
fastembed 6.0.2's existing `AllMiniLML6V2Q` / `BGESmallENV15Q` /
`SnowflakeArcticEmbedXSQ` enum variants (verified present in the vendored
crate source before writing, not assumed). Same shape as the existing
`embeddinggemma-300m` / `-q4` pair. `open_embedder`,
`configured_provider_name`, and the CLI provider-selection path were not
touched.

Each `Q` variant keeps its fp32 counterpart's documented query/document
prefix and pooling convention. **Not independently re-verified** against the
quantized artifact's own model card — it follows the Gemma-q4 precedent and
the fact that quantization changes weight precision, not prompt semantics.
Note the `Q` variants do not all come from the same HF repo as their fp32
twins: `BGESmallENV15Q` pulls `Qdrant/bge-small-en-v1.5-onnx-Q`
(`model_optimized.onnx`) while MiniLM-Q/Arctic-Q use Xenova-style
`model_quantized.onnx`. That difference turns out to matter (see BGE below).

## Candidate semantics

| Profile | fastembed enum | Params | Dim | Quantization | Query prefix | Doc prefix | Pooling |
|---|---|---|---:|---|---|---|---|
| `arctic-embed-xs-q` | `SnowflakeArcticEmbedXSQ` | not verified | 384 | int8 | `Represent this sentence for searching relevant passages: ` | none | cls |
| `minilm-l6-v2-q` | `AllMiniLML6V2Q` | not verified | 384 | int8 | none | none | mean |
| `bge-small-en-v1.5-q` | `BGESmallENV15Q` | not verified | 384 | int8 | `Represent this sentence for searching relevant passages: ` | none | cls |
| `jina-code-v2` (ref) | `JinaEmbeddingsV2BaseCode` | not verified | 768 | fp32 | none | none | mean |

Parameter counts are deliberately left "not verified" — fastembed's
`ModelInfo` does not carry them and no model card was fetched for this
screen. Max sequence length likewise not captured. Model revision: fastembed
resolves HF `main` with no pinned revision (a known gap already recorded in
`NativeEmbedder::fingerprint`, unchanged here).

## Environment

- CPU: 13th Gen Intel Core i7-13620H, 16 logical cores; AC power connected
- RAM: 15Gi total, ~10Gi available; swap 8Gi (945Mi in use at session start)
- OS: Linux 7.2.0-1-default; rustc/cargo 1.98.0
- OXIDE: branch `embed-cpu-survey`, `122f05e` + this doc
- Build: `cargo build --release --features native-embed`
- Machine freshly rebooted before the reported runs; load average 1.28 at
  rest. Every timed run self-saturates ~7 cores, so runs were strictly
  serialized — no two measurements overlap.

Absolute numbers are machine-specific. Read the ratios.

## Corpus

`matplotlib` (from the ContextBench repo cache), for the full-repo embedding
pass: **977 scanned files, 18,532 symbols**. This is ~5.4x the ~3412-symbol
scale the task described; the intended corpus could not be identified, and
matplotlib was chosen as the closest available file-count match before its
symbol count was known. Throughput and RSS are per-symbol/steady-state
figures and read fine at this scale; wall-clock totals obviously scale, so a
scaled-to-3412 column is given below for comparability with the task's own
framing.

Identical corpus, identical commit, identical symbol representation for
every profile. Base parse+graph layer was built once
(**5.77s**, offline `hashed-bow-256`) and reused; each profile run then
switches `OXIDE_EMBED_NATIVE`, which trips `update_index`'s
embedding-space-changed fingerprint check and re-embeds all 18,532 symbols —
the same code path a real profile switch takes in production. All runs
reported `embed_failures: 0` and `embedded_symbols: 18532`.

## CPU results

### Full-repo embedding pass (matplotlib, 18,532 symbols)

| Profile | Repo embed wall | Scaled to 3412 sym | Symbols/sec | Speedup vs Jina | CPU user | Peak RSS | Dim |
|---|---:|---:|---:|---:|---:|---:|---:|
| `jina-code-v2` (reference) | 1025.5s | ~189s | 18.1 | 1.00x | 11575.7s | 996MB | 768 |
| `bge-small-en-v1.5-q` | 485.4s | ~89s | 38.2 | **2.11x** | 4433.2s | 414MB | 384 |
| `minilm-l6-v2-q` | 117.3s | ~22s | 158.0 | **8.74x** | 787.9s | 233MB | 384 |
| `arctic-embed-xs-q` | 113.6s | ~21s | 163.2 | **9.03x** | 778.0s | 230MB | 384 |

Parsing/graph (5.77s, shared) is excluded from every row above — these are
embedding-stage figures, not total `oxide index` wall time. SQLite
persistence is *included* (it happens inside `update_embeddings`) and was not
separated further; at 18k symbols it is a small fraction of the whole, but
this screen did not measure it independently.

### Model startup and query latency (synthetic probe)

`examples/embedding_profile_probe`, unchanged, 50-rep warm query set.

| Profile | Cold init | `embed_query` p50 | p95 | Synthetic throughput | Probe peak RSS |
|---|---:|---:|---:|---:|---:|
| `jina-code-v2` (ref, Stage A) | 1172.1ms | 11.71ms | 14.01ms | 35.1/s | 1889MB |
| `bge-small-en-v1.5-q` | 14599.8ms (incl. download) | 18.48ms (rerun 15.62) | 21.62ms | 130.7/s (rerun 130.3) | 572MB |
| `minilm-l6-v2-q` | 9818.3ms (incl. download) | **1.33ms** | 1.51ms | 528.9/s | 856MB |
| `arctic-embed-xs-q` | **96.6ms** (cached) | 1.80ms | 2.17ms | 540.4/s | 746MB |

Cold-init figures are not comparable across rows: two include a first-run
model download. Arctic's 96.6ms is the only clean warm-cache number here;
Stage A's clean fp32 numbers (167–293ms for the 384d natives, 1172ms for
Jina) are the fair reference for what a cached cold start costs.

**The synthetic probe overstates real throughput by ~3.3x.** MiniLM-Q: 528.9/s
synthetic vs 158.0/s real. Arctic-Q: 540.4/s vs 163.2/s. Jina: 35.1/s vs
18.1/s (~1.9x). Real `embed_text` strings (`file + kind + qualified_name +
signature + imports + references`) are longer and more variable than the
probe's fixed 5-line sample doc, and the indexer's batching + SQLite path
differs from one 500-item `embed_documents` call. Which factor dominates was
not decomposed. **Every verdict below uses the real-corpus number**; the
synthetic column is kept only because query-latency p50/p95 has no
real-corpus equivalent here.

### The BGE-Q anomaly

`bge-small-en-v1.5-q` is **slower than its own fp32 twin** on every axis —
synthetic throughput 130.7/s vs fp32's 156.2/s, query p50 15.6–18.5ms vs
fp32's 4.47ms — and reproduced on a second run. Its real-corpus 38.2 sym/s
is 4.3x worse than the other two quantized candidates. The likely cause is
the different quantization artifact noted above (`Qdrant/...-onnx-Q`
`model_optimized.onnx` vs Xenova-style `model_quantized.onnx`), plausibly
lacking fused int8 kernels for this op set on this ONNX Runtime build. Not
investigated further — a screen does not need the mechanism to reject on the
measurement.

## Cheap retrieval sanity

**Not a quality benchmark.** 12 hand-written module-responsibility queries
over `flask` (80 files, 1754 symbols), one gold file each, frozen in
`sanity_queries.json` **before any model was run**, phrased from what each
module does rather than by naming its symbols. Recall@5 == hit@5 by
construction (one gold per query). `hashed-bow-256` is included as a floor —
it has no learned semantics at all.

| Profile | Vector-only hit@5 | Vector-only MRR | Hybrid hit@5 | Hybrid MRR | Obvious failures |
|---|---:|---:|---:|---:|---|
| `hashed-bow-256` (floor) | 0.583 | 0.233 | 0.917 | 0.660 | 4 golds not in top-25 vector-only |
| `minilm-l6-v2-q` | 0.917 | 0.593 | **1.000** | 0.750 | "test client…" gold not in top-25 vector-only |
| `minilm-l6-v2` (fp32 control) | 0.917 | 0.661 | **1.000** | 0.708 | same single vector-only miss |
| `arctic-embed-xs-q` | 0.917 | 0.514 | **1.000** | 0.799 | same single vector-only miss |
| `arctic-embed-xs` (fp32 control) | **1.000** | 0.593 | **1.000** | 0.806 | none |
| `bge-small-en-v1.5-q` | **1.000** | 0.750 | **1.000** | 0.812 | none |
| `jina-code-v2` (reference) | 0.917 | **0.804** | **1.000** | **0.903** | same single vector-only miss |

Readings, in decreasing order of how much the evidence supports them:

1. **No candidate shows catastrophic semantic failure.** Every quantized
   candidate clears the hashed floor by a wide margin vector-only
   (0.917–1.000 vs 0.583) and saturates hybrid hit@5.
2. **Quantization's quality cost is small and not consistently negative
   here.** MiniLM-Q vs fp32: same vector hit@5, lower vector MRR (0.593 vs
   0.661), *higher* hybrid MRR (0.750 vs 0.708). Arctic-Q vs fp32:
   consistently but mildly worse (0.917/0.514/0.799 vs 1.000/0.593/0.806).
3. **The task's architectural hypothesis is directionally supported but not
   proven by this set.** Jina leads clearly on vector-only ranking
   (MRR 0.804 vs 0.514–0.750) yet every model ties at hybrid hit@5 1.000 —
   consistent with "lexical+structural preserves hybrid quality where
   vector-only quality degrades." The hashed floor makes the same point more
   sharply: 0.583 → 0.917 just by adding lexical+structural.
4. **Hybrid hit@5 is saturated and cannot discriminate at n=12.** Hybrid MRR
   still separates (Jina 0.903 > BGE-Q 0.812 ≈ Arctic-Q 0.799 > MiniLM-Q
   0.750 > floor 0.660), and Jina leads it. Treat that gap as the real
   signal, not the tied hit@5 column.

**The strongest caution against over-reading this screen** comes from
existing evidence, not from this run: the prior survey's full 21-task
ContextBench put fp32 `minilm-l6-v2` at hybrid R@5 **0.556** against
`qwen3-Q8_0`'s **0.679** — a substantial gap that a 12-query screen like this
one shows no trace of. A tiny model looking near-parity here is therefore
**not** evidence it will hold up at full benchmark scale. That is precisely
why this stage is a screen and Step 2 exists.

## Pareto interpretation

Maximize symbols/sec and hybrid quality; minimize wall time, query latency,
peak RSS, dimension/storage, integration complexity. Integration complexity
is identical (zero) for all four — every one is an existing
`OXIDE_EMBED_NATIVE` profile string requiring no new runtime surface.

- **`arctic-embed-xs-q` — SURVIVOR.** Fastest full-repo pass (113.6s, 9.03x
  Jina), lowest peak RSS (230MB), lowest query p50 among candidates that are
  actually fast (1.80ms), smallest dimension tier (384d = half Jina's storage
  per vector), no catastrophic failure on the sanity set, and the best hybrid
  MRR of the three quantized candidates that are fast (0.799).
- **`minilm-l6-v2-q` — DOMINATED, thinly.** Slower (117.3s vs 113.6s), higher
  RSS (233 vs 230MB), higher query p50 (1.33ms is actually *better*), and
  lower hybrid MRR (0.750 vs 0.799). It loses on three of four axes but every
  margin except hybrid MRR is within measurement noise, and its query p50 is
  genuinely better. Calling it "dominated" is a ranking convenience, not a
  strong claim — it is effectively Arctic-Q's twin. Its one real asset:
  its fp32 lineage already has full 21-task numbers, so its quality ceiling
  is partly known (and it is not flattering — see the caution above).
- **`bge-small-en-v1.5-q` — REJECT, not materially faster.** Best tiny-model
  vector-only quality (hit@5 1.000, MRR 0.750) but only **2.11x** Jina on the
  real corpus, 4.3x slower than its quantized peers, ~9x worse query p50
  (15.6ms vs 1.80ms — bad for a first-pass model that must answer queries,
  not just index), and slower than its own fp32 twin. A fast-stage model
  exists to be dramatically cheaper; 2.11x does not clear the task's own bar,
  and its quality edge over Arctic-Q is one query's worth of difference at
  n=12.
- **`jina-code-v2` — reference, not a fast-stage candidate.** Best ranking
  quality on every metric that discriminates, at 1025.5s, 996MB, and 768d.
  Its role in a dual-stage design would be the quality pass, which is exactly
  what the framing assumed.

## Verdict

**KEEP FOR FULL CONTEXTBENCH: `arctic-embed-xs-q`** — one model, not two.
9.03x faster than the Jina reference on a real corpus (~189s → ~21s at the
task's ~3412-symbol framing; ~93s → ~10s against the task's own cited Jina
number), 4.3x less peak RSS, half the vector storage, zero integration work,
and no catastrophic semantic failure on the cheap screen.

**REJECT — not materially faster: `bge-small-en-v1.5-q`** (2.11x, and slower
than its own fp32 twin).

**KEEP AS SPEED FLOOR ONLY: `minilm-l6-v2-q`** — hold as a fallback if
Arctic-Q fails Step 2; do not spend a second expensive benchmark run on it
up front, since it is Arctic-Q's near-twin on every runtime axis and its
lineage's known 21-task quality is unimpressive.

Nothing about OXIDE's default embedding model, retrieval, or indexing
behaviour was changed by this work.

## One recommended next action

Run `arctic-embed-xs-q` through the frozen 21-task ContextBench (Tier A)
against the existing `qwen3-Q8_0` and fp32 `minilm-l6-v2` numbers already in
`docs/embedding-profile-comparison/README.md`, and compare **hybrid and
budgeted R@5** specifically — budgeted is what `oxide context` actually
serves an agent, and it is the condition where fp32 MiniLM's real gap
appeared. That single run decides whether a fast-stage tiny model is viable
at all; the dual-stage design question should not be opened before it lands.

## Known gaps in this screen

- n=12 on one small repo, self-authored gold set; hybrid hit@5 saturated.
- Corpus is 5.4x the intended scale; wall-clock totals scaled arithmetically,
  not re-measured at the target size.
- Cold-init numbers mix cached and download-inclusive runs.
- SQLite persistence not separated from embedding inference.
- Quantized artifacts' prompt/pooling conventions inherited from their fp32
  twins by precedent, not re-verified per model card.
- No model revision pinning (pre-existing `fastembed`/`hf-hub` gap).
