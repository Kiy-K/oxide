# Semantic retrieval channel: LFM2.5-Embedding-350M pilot (rejected)

Status: **rejected for promotion, 2026-10-11; implementation reverted.**
Lexical-only stays the default; production code is unchanged. The full
implementation is kept as `semantic-channel.patch` (applies to `669ea4c`),
the raw per-task results as `pilot-lfm.json`, and the aggregation as
`scripts/summarize.py`. All numbers below are measured unless marked
*inferred*.

**Follow-up (same day):** channel-aware routing and selection, the channel
re-integrated without Rig, and a PPR best-first router were evaluated
against preregistered gates and also rejected. Semantic evidence now
reaches the bundle but displaces lexical evidence one for one, and it adds
nothing over a body-text BM25 control. See [routing.md](routing.md).

## Question

Does an optional embedding channel give OXIDE's real pipeline (lexical
retrieval → TreeRouter → heuristic selection → ContextPacker) evidence that
lexical retrieval misses, and does that evidence reach the ContextBundle at
an acceptable cost? DecisionProvider, TreeRouter and ContextPacker were held
unchanged by design.

## What was built (in the patch)

- **Kernel** (`retrieve.rs`, new `semantic.rs`): a `Semantic` input to
  `retrieve` (absent with a visible state, or exact-search hits checked
  against the pinned generation), two rank-only fusion rules (`Append`;
  weighted `Rrf` with a recorded cut), and the model-independent document
  recipe `oxide-semantic-recipe-v1` (file path + symbol source, 480 bytes;
  query 1,200 bytes). With the channel absent, retrieval is bit-for-bit the Phase 3
  rules (the frozen phase-3/4 baselines reproduce).
- **Runtime** (`embedding.rs`, new `semantic.rs`): a llama.cpp client (Rig
  0.44 OpenAI-compatible dialect for `/v1/embeddings`; `ureq` for `/props`
  and `/v1/models`) that pins the served model file's SHA-256, quantization,
  dimension, runner build, prompts, token window and a behavioural canary
  vector (pooling and normalization are not reported by the server), with
  explicit `Rejected`/`Cancelled`/`Timeout`/`Unavailable` failures; a
  persistent content-addressed vector cache per embedding space; a background
  index build (progress, cancellation, per-batch deadlines) so requests never
  wait; exact cosine search that refuses a model swapped behind the endpoint.
- **Harness** `runtime/tests/semantic_pilot.rs` (ignored, machine-local) and a
  body-text BM25 control (see Method).

## Model and runner

| | |
|---|---|
| Model | LiquidAI/LFM2.5-Embedding-350M-GGUF, Q4_K_M, sha256 `4d7aa9dc…f32a` (= HF LFS oid) |
| Format (model card + GGUF) | CLS pooling (`pooling_type=2`), 1,024-d, cosine on L2-normalized vectors, `query: ` / `document: `, 512-token window, BOS added, bidirectional |
| Runner | external llama.cpp `llama-server` build b11540, CPU only, `MemoryMax=1G`, `CPUQuota=200%` (2 cores) |
| License | LFM Open License v1.0: commercial use is not licensed for entities with ≥ US$10M annual revenue (non-profits exempt for research). Weights are never bundled or downloaded by OXIDE. |

## Method

- **Pilot scope**, fixed before results: the 4 sphinx dev tasks (21 verified
  gold symbols). Sympy was excluded on cost and memory (≈40k entities per
  task; the harness was OOM-killed at its 3 GB cap), keras on cost (1.5 h).
- **Configurations** (all through `build_context`, heuristic judge, budgets
  256/1,024/4,096): `lexical` (frozen baseline), `semantic` only, `lexical +
  semantic` append (20 + 20), `rrf` (k=60, 0.6/0.4, keep 20: equal entry
  count), and the same three fusions with a **control** channel: BM25 over
  the *identical* document text with OXIDE's own `terms()` and the production
  FTS k1/b. OXIDE's lexical document is name + signature + path only, so the
  control separates "the channel reads symbol bodies" from what the model adds.
  Caveat: `terms()` does not stem, while the production FTS uses Porter
  stemming, so the control is a somewhat weaker lexical scorer than
  production would be over the same text.
- **Metrics**: Recall@K and MRR of gold symbols in the channel and in the
  CandidateSet; gold symbols the channel's top 20 holds that lexical's 20 do
  not; gold reached by routing; Phase 4 `required_symbol_recall` of the
  bundle; gold packed with vs without the channel at 1,024 units.

## Results (sphinx dev, 4 tasks, 21 gold symbols)

| | channel R@20 | channel R@100 | channel MRR | gold in top 20 not in lexical's |
|---|---|---|---|---|
| LFM | 0.271 | 0.608 | 0.294 | **5** |
| body BM25 control | 0.146 | 0.271 | 0.253 | 3 |

| configuration | entries R@20 / R@40 | entries MRR | gold reached by routing (pooled) | bundle recall @256 / 1,024 / 4,096 | packed gold vs lexical @1,024 |
|---|---|---|---|---|---|
| lexical (baseline) | 0.183 / 0.183 | 0.175 | **0.524** | 0.042 / 0.183 / 0.267 | — |
| semantic only | 0.271 / 0.271 | 0.290 | 0.476 | 0.000 / 0.042 / 0.115 | — |
| lexical + semantic (append) | 0.183 / **0.413** | 0.191 | 0.476 | 0.042 / 0.183 / 0.267 | +0 / −0 |
| rrf | 0.183 / 0.183 | **0.286** | 0.429 | 0.042 / 0.183 / 0.267 | +0 / −0 |
| lexical + control (append) | 0.183 / 0.329 | 0.187 | 0.429 | 0.042 / 0.183 / 0.267 | +0 / −0 |
| rrf-control | 0.183 / 0.183 | 0.175 | 0.524 | 0.042 / 0.183 / 0.267 | +0 / −0 |

- The channel finds gold lexical retrieval misses, more than the body-text
  control does (5 vs 3 symbols; n = 4 tasks, so indicative only).
- **None of it reaches the bundle**: bundle recall and packed gold are
  identical to lexical-only at every budget, and routed gold *drops*.
- Why (*inferred* from the code and the per-task traces): every run stops at
  the 64-region routing bound; appending semantic hits doubles the entry
  points (20 → 37–40), so they compete for the same regions (routed gold
  −2 on `f0418d70`, +1 on `9c4b27a6`). And the heuristic DecisionProvider
  values a candidate by its *lexical* rank only (`decision.rs`), so a
  semantic-only entry gets the 0.3 default and is not selected. The
  bottleneck is downstream of the embedding model, so a different model
  (EmbeddingGemma 2) was not run: it would face the same wall.

## Cost (measured, 2-core / 1 GB runner, laptop on AC)

| | |
|---|---|
| Query (embed ~400-token issue + exact search over ~7k vectors) | 779–961 ms (median per task); first query after each build 803–985 ms (runner already warm; a process-cold query was not measured) |
| Context pipeline overhead with the channel | +3–5 ms (≈300 ms total) |
| Cold embedding, one sphinx snapshot (7,077 symbol documents, 6,952 unique texts) | 23 min (≈5 documents/s) |
| Next snapshots of the same repository, cache hits | 4–16 min |
| Vector cache, 13,287 vectors | 55 MB; model file 229 MB |
| Runner RSS / harness peak RSS | ≈0.6 GB / 428 MiB |
| Lexical-only context while vectors build | ≈550 ms (first request on a fresh store; ≈300 ms later in the same run), channel `Unavailable("vectors building …")` set by the harness from `Build::pending`, not a live service. It read `0/0` before the worker knew its total; the patch reports "vectors building" until then |

Runner RSS, model size and the comparison against EmbeddingGemma 2 Q8_0 come
from the standalone runner benchmark earlier the same day (same caps; raw
numbers in `oxide-eval-data/oxide-semantic-eval/RESUME.md`, outside the repo):
LFM uses half the anonymous memory (346 vs 717 MiB) and loads 3× faster at
equal throughput.

## Engineering findings worth keeping

Measured during the session; the raw test output is not committed.

- **Rig changes serde_json crate-wide.** `rig-core` 0.44 enables
  `preserve_order` and `float_roundtrip` on serde_json; Cargo feature
  unification applies them to the whole runtime. Key order alone is
  recoverable (sorted writers), but exact float parsing changes the bytes of
  JEV requests that embed capsule state, so 8 of 222 recorded DecisionBench
  JEV answers stopped replaying and 73 fixture metrics changed. The patch
  carries a vendored `rig-core` with `float_roundtrip` removed (Rig needs
  `preserve_order`) plus sorted-key writers; with both, every frozen
  baseline and the DecisionBench fixture reproduce. Rig also adds 84 crates
  to the lockfile (146 with its default TLS stack).
- llama.cpp does not report pooling or normalization; identity needs the
  served file's digest plus a canary vector, compared within ±1e-3.
- A query longer than the model window is not truncated by `llama-server`
  below its batch size; the client enforces the window from the reported
  token usage.

## Known limits of the patch (for a re-application)

From the independent review; none affects the pilot's numbers.

- The per-query model-swap check compares only `/props` `model_path` and
  `build_info`; a different file at the same path after a server restart
  passes. The file digest is checked only when an index is built.
- Any failed batch (including a transient 5xx, mapped to `Rejected`) aborts
  the background build; the cache keeps finished batches, so a rerun resumes.
- The token window is enforced only for single-text calls that report usage;
  batched documents rely on the 480-byte recipe bound and a prompt of at most
  31 bytes.
- A query over the window disables the channel for that request instead of
  being cut to fit.
- Cached vectors are not re-validated on load (no per-record checksum,
  finiteness or norm check); only a torn last record is dropped.
- One exclusive lock per space directory: two concurrent builds in the same
  space fail with "in use".
- `LlamaCpp::embed` calls `block_on`; it must not run on a tokio runtime
  thread.
- Sorted-key writers cover the JEV sites and the test artifact writers that
  the suite checks; other `json!` output whose bytes matter would need the
  same under Rig's `preserve_order`.

## Not done, and why

keras, sympy, EmbeddingGemma 2 and held-out tasks: the pilot showed no
end-to-end value, so no gates were preregistered and no multi-hour run was
started. Changing DecisionProvider or routing to credit semantic evidence was
out of scope for this task.

## Reproduce

`git apply docs/semantic-eval/semantic-channel.patch`, copy crates.io
`rig-core` 0.44.0 to `oxide_kernel/vendor/rig-core` and drop
`"float_roundtrip"` from its serde_json features (`vendor/VENDORED.md` in the
patch), start the capped runner, pin the canary
(`OXIDE_SEMANTIC_CALIBRATE=1`, then `OXIDE_SEMANTIC_CANARY_LFM=496,-9,89,-42,-587,-555,116,-281,-219,47,219,607,-31,209,-76,564`)
and run the harness as its module docs show; then
`python3 docs/semantic-eval/scripts/summarize.py docs/semantic-eval/pilot-lfm.json`.
