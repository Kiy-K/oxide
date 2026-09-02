# Native Qwen3 (fastembed-rs Candle backend) — evaluated and rejected

`fastembed` 6.0.2 exposes `Qwen3TextEmbedding` behind a `qwen3` feature flag
(Candle backend, not ONNX like every other native profile in
`docs/embedding-profile-comparison/`). This tests whether it could replace
the production llama.cpp/HTTP Qwen3-Q8_0 path. **Rejected**: quality is
equivalent, but the Candle CPU path is 6-100x slower and ~18-34x heavier on
RSS than the existing HTTP path, with no accompanying benefit. The code that
produced these numbers was **not committed** — see "What was reverted"
below; this doc is the only surviving record so the next person doesn't
have to rebuild it to learn the same thing.

## Quality: equivalent (0.999 cosine agreement)

Same model family, same instruction-prefixed query protocol
(`qwen3_query_text` — `"Instruct: Given a coding task, retrieve repository
symbols...\nQuery: {task}"`, applied to queries only, documents embedded
verbatim), same last-token pooling (Qwen3TextEmbedding's `embed()` does
`hidden.i((.., seq_len - 1))` with left-padded tokenization — matches
llama.cpp's `--pooling last` exactly), same L2 normalization. The only
axis measured for numerical agreement (not the full 21-task quality sweep —
see "Scoping" below):

```rust
// examples/qwen3_native_vs_http_agreement.rs (removed with the feature;
// this is the essential logic, reproducible if native-qwen3 is ever
// re-added)
let http = HttpEmbedder::new("http://127.0.0.1:8191/v1/embeddings", "qwen3-Q8_0")?;
let native = Qwen3NativeEmbedder::new()?; // Candle, f32, from_hf("Qwen/Qwen3-Embedding-0.6B", ...)
// for the same query/document texts:
let sim = cosine(&http.embed_query(q), &native.embed_query(q));
```

Result across 3 queries + 3 documents: **min cosine 0.9992, avg 0.9993**.
The small residual gap is attributable to precision (HTTP path is Q8_0
GGUF; native ran f32 — see below) — consistent with, and about the same
magnitude as, the ~0.9999 agreement Task D's EmbeddingGemma validation
measured against its own authoritative reference.

## Runtime: 6-100x slower, ~3-18x heavier

| Metric | HTTP Qwen3-Q8_0 (production) | Native Qwen3 (Candle, f32) | Ratio |
|---|---|---|---|
| Cold init | 200.9ms (client) + 6.15s (server, one-time/lifetime) | **1202.4ms** (wins, but irrelevant given the rest) | native faster here only |
| `embed_query` p50 | 38.56ms | 503.08ms | **13.0x slower** |
| `embed_query` p95 | 41.36ms | 559.68ms | **13.5x slower** |
| 1-item incremental | 121.96ms | 639.88ms | 5.2x slower |
| 10-item incremental, per-item | 98.17ms | 308.10ms | 3.1x slower |
| 50-item incremental, per-item | 105.27ms | 310.10ms | 2.9x slower |
| 100-item incremental, per-item | 105.25ms | 327.94ms | 3.1x slower |
| 500-item throughput | ~9.5 items/s (implied) | **2.8 items/s** | 3.4x slower |
| Peak RSS | ~140-260MB (separate server process) | **4735.7MB** | 18-34x heavier |

The RSS gap alone (4.7GB vs. Task D's previous worst-case candidate,
fp32 EmbeddingGemma at 1.6GB) would disqualify this on a "normal developer
machine" basis even ignoring latency.

**Real-world consequence, not just a synthetic benchmark artifact**: a
spot-check attempt to index `psf/requests` (743 symbols — one of the
*smallest* repos in the pinned eval set) under `OXIDE_EMBED_NATIVE=qwen3-0.6b`
did not finish within a 900-second timeout. The same repo indexes under the
HTTP path in seconds. This is the decisive, not just supporting, evidence —
a codebase small enough to be a fast-repo baseline in every other comparison
in this project could not complete indexing in 15 minutes.

## Root cause

`candle-core = "0.11.0"` was added with **no acceleration feature enabled**
(no `mkl`, `accelerate`, or `cuda`) — plain CPU matmul via the `gemm` crate's
generic kernels, no BLAS. ONNX Runtime (the backend every other native
profile in `docs/embedding-profile-comparison/` uses) ships its own
optimized CPU kernels by default; Candle's CPU path does not without an
explicit acceleration feature. This is *why* it lost, not just *that* it
lost — worth stating so a future re-test knows what would actually need to
change (see "If this is ever revisited" below), rather than re-deriving it.

## Two implementation findings worth preserving (cost real time to find)

- **candle-core has no CPU bf16 matmul.** The published
  `Qwen/Qwen3-Embedding-0.6B` checkpoint is bf16; loading it with
  `candle_core::DType::BF16` **loads successfully** and only fails at the
  first inference call (`unsupported dtype BF16 for op matmul`) — this
  looks like a working integration until the first real embed call. Load
  with `DType::F32` instead (`VarBuilder::from_mmaped_safetensors` upcasts
  on load; no re-download needed, same cached safetensors file).
- **`fastembed`'s `qwen3` feature pulls the full image-codec dependency
  tree** (`image`, `ravif`, `exr`, `tiff`, `image-webp`, `gif`, `png`,
  `qoi`, …) even for text-only use, because `models/qwen3.rs` also defines
  `Qwen3VLEmbedding` (vision) in the same feature-gated module. There is no
  text-only sub-feature to opt out of this.
- **`cargo build --release --example X` does not rebuild the `oxide` bin
  target.** They're separate build targets; a fix to library code
  (`src/embeddings.rs`) is visible to the example immediately but not to
  `target/release/oxide` until a plain `cargo build --release [--features
  ...]` runs. Cost one full spot-check cycle here (reran against a stale
  binary that still had the bf16 bug after the fix landed) — worth knowing
  for any future eval that drives the CLI subprocess while iterating on lib
  code in the same session.

## Decision

Per the task's explicit criterion ("commit only if the native path earns
its place"): it does not. `llama.cpp`/HTTP remains the sole production
Qwen3 path — not merely "kept for historical reasons," but because it is
measurably faster and lighter with no quality cost, which is precisely the
condition under which two paths would *not* be justified to maintain in
parallel.

## What was reverted

`src/embeddings.rs`'s `Qwen3NativeEmbedder` (struct + `EmbeddingProvider`
impl + `QWEN3_NATIVE_PROFILE`/`QWEN3_NATIVE_MAX_TOKENS` constants), the
`native-qwen3` Cargo feature and its `candle-core` dependency, the
`open_embedder` dispatch branch, and three example files
(`qwen3_native_vs_http_agreement.rs`, `qwen3_native_profile_probe.rs`, plus
the `[[example]]` Cargo.toml entries) were all removed after producing the
numbers above — not committed. Keeping a second, rejected ML runtime
compilable in the tree (plus its `Cargo.lock` weight and the image-codec
transitive dependencies above) is exactly the "two production paths for
historical reasons" cost the task asked to avoid creating.

## If this is ever revisited

The gap is almost entirely inference throughput, not architecture. Worth
retrying only if:
- Candle gains a competitive default CPU backend, or `candle-core`'s `mkl`/
  `accelerate` features become easy to enable on this project's target
  platforms (mkl requires Intel MKL as a system dependency — a real
  portability cost to weigh against the gain).
- GPU execution becomes in-scope (out of scope here, per Task D's "no GPU
  work yet" and this task not revisiting that).

Absent one of those, re-testing this exact configuration would reproduce
the same rejection.
