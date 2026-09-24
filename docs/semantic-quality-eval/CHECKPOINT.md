# Semantic-quality research checkpoint (resume here)

Baseline SHA: `cce3907` (src/ identical to `5c62edd`; corpora built at
`5c62edd` remain valid). Scratch: `~/.cache/oxide-semantic-eval/`
(scripts `build_corpora.sh`, `run_variant.sh`, `run_cb.sh`, `queue2.sh`,
`cbqueue.sh`; `wt/` corpora, `dumps/`, `logs/`). Pinned measurement
binaries: `bin/` + `bin/SHA256SUMS` (oxide `1f16ba1c…`, semantic_variant
`c5a6f72e…`, fusion_dump `c24a35ed…`), built from `cce3907` + the harness
extractor fix. Scripts call the pinned copies, never `target/`.

## Hypotheses
- H0 (bug): embedding reuse key is a proxy for `symbol_embed_text`.
- H1 (text): identifier-only document text causes description-query route loss.
- H2 (model): a different small native model recalls more on the same text.

## Completed
- H0 **reproduced then fixed** (uncommitted): two new tests in
  `tests/embedding_staleness.rs` failed on `cce3907` (incremental != clean
  rebuild after import-only edit / new same-file definition). Fix:
  `index.rs::embedding_input_hash` folds `symbol_embed_text` into every
  symbol's `content_hash`. Full suite 497/0; conformance goldens regenerated,
  diff = `content_hash` lines only (240/240). AGENTS.md invariant updated.
  No forced re-embed on upgrade (only reparsed files re-key).
- Harness: extractor fixed (Rust `#[..]` / C `#include` no longer "doc";
  Python header with trailing `# comment`); `--dump-texts`, `--emit-vectors`,
  `--local-model/--onnx/--pooling` added. Text spot-check py/rs/ts OK.
- D0 self-check: ripgrep-00e501b5 (`--no-embed`) and httpx-336204f0
  (re-embed) identical to `fusion_dump` on lexical/semantic/fused/expanded/pack;
  D0 re-embed reproduced 1409/1409 stored vectors bit-for-bit.
- Leakage note: held-out corpora are at the task commit (post-change), so
  D1–D5 see the fixed body/doc. Bias favours text variants → a held-out
  *loss* is robust; a *win* must be confirmed on ContextBench (base commit).
- Granite 97M R2 (HF rev `835ad140`): fp32 `onnx/model.onnx` via fastembed
  user-defined CLS == sentence-transformers fp32 (min cos 0.9999998,
  top-1/top-5 identical, 31 probe texts incl. >512-token truncation) PASS.
  `model_quint8_avx2.onnx` min cos 0.941, top-1 0.58 → REJECT (correctness).
  Reference: a throwaway venv with torch 2.14 (CPU) + sentence-transformers 6.1, model loaded with dtype=float32.

## Results so far
- CB (21, base commit) D1 vs D0: sem@50 sym.cov −0.031 [−0.071, 0] (0/3),
  fused@10 file.cov −0.024, fused@5 file.cov +0.040 [−0.016, +0.111],
  pack line.cov ±0. Scorer `scripts/cb_variants.py` reproduces the ranking
  report's production CB numbers exactly (0.685/0.771/0.713/0.044/0.591).
- CB D3 vs D0 (drop imports/references, add doc+body+path words): sem@50
  sym.cov −0.219 [−0.380, −0.087] (1/9), fused@10 sym.cov −0.160
  [−0.300, −0.038], fused@5 file.cov −0.167, pack line.cov −0.136
  [−0.258, −0.020], gold lines/1k pack tok 28.8→18.6. **D3 fails the gate**;
  import/reference name lists carry signal (refutes the "dilution" arm).
- CB D5 vs D0 (doc+body only): sem@50 sym.cov −0.248 [−0.424, −0.099]
  (1/9), fused@5 file.cov −0.139 [−0.298, −0.012], fused@10 sym.cov −0.062,
  pack line.cov −0.089, gold lines/1k 19.9. **Fails.**
- CB D0 bare query (no Arctic prefix): sem@50 −0.051 [−0.117, +0.003] (1/4),
  fused@10 sym.cov +0.020 [−0.019, +0.070], pack line.cov −0.066
  [−0.188, +0.038], gold lines/1k 23.9. No win → keep the prefix.
- Held-out D0 control (65 tasks, 65 per-commit corpora, 206,471 symbols,
  `dumps/baseline.jsonl`): all sem R@10/50/200 0.410/0.613/0.817, fused
  nDCG@10 0.397, rel tok/1k 110.5, losses route/order/alloc/hit 2/19/10/34.
  desc (n=40): sem R@10 0.272, R@200 0.720, fused nDCG@10 0.267, losses
  2/17/5/16. ident (n=25): nDCG@10 0.604. → On these corpora desc loss is
  mostly **ordering**, not route (README's R@10 0.071 came from older
  moving-HEAD corpora; not comparable).
- CB granite fp32 D0 vs xs-q D0: sem@50 +0.020 [−0.095, +0.139] (4/4),
  fused@10 sym.cov −0.102 [−0.256, +0.032] (2/5), pack line.cov −0.116
  [−0.327, +0.094] (4/8), gold lines/1k 20.3. No win on CB.
- CB arctic-embed-s D0 vs xs-q D0: sem@50 −0.040 [−0.194, +0.090] (4/4),
  fused@10 sym.cov −0.070 [−0.216, +0.057] (1/4), pack line.cov −0.044,
  gold lines/1k 23.2. No CB win → no held-out run (stop rule).
- Held-out D1 vs D0 (post-commit corpora, leakage-prone): all nDCG@10 +0.002
  [−0.039, +0.041]; desc sem R@50 +0.080 [+0.007, +0.176], desc nDCG +0.023
  [−0.018, +0.070]; ident nDCG −0.030 [−0.111, +0.038]; rel tok/1k
  110.5→142.1; gold_in_pack 0.538→0.600. Conflicts with CB (flat) →
  running parent-commit leakage control (D0 vs D1) on held-out.
- No independently labeled doc or multilingual retrieval set exists in the
  repo (no `.md` gold anywhere) → not measured.

## Stopped 2026-09-23 on user request (all jobs killed, nothing running)
- Parent-commit leakage control: `dumps/parent-baseline.jsonl` complete
  (63 tasks); `dumps/parent-xsq-D1.jsonl` partial (~20/63) — NOT scored.
  Resume: `cd ~/.cache/oxide-semantic-eval && ./run_variant_parent.sh
  tasks-parent.jsonl parent-xsq-D1 --variant D1` (skips finished corpora),
  then `semantic_eval.py tasks-parent.jsonl dumps/parent-baseline.jsonl
  D1=dumps/parent-xsq-D1.jsonl`.
- CB bge-small-en-v1.5-q: `dumps/cb-bge-q-D0.jsonl` partial (17/21), not scored.
- Held-out bare-query and idle-machine cost screen (`semantic_variant.v3
  --bench-batch`) not run.
- Uncommitted: H0 fix (src/index.rs, tests, goldens, AGENTS.md), harness
  changes (examples/semantic_variant.rs), scripts/cb_variants.py, this file.
  `mise run verify` NOT run yet (cargo test full suite: 497 pass / 0 fail).

## Embedding-path sweep (30-min box, 2026-09-23 evening)
- BUG 2 (fixed, uncommitted): dynamic-quant batch dependence. `arctic-embed-xs-q`
  single vs 7-text batch min cos 0.9975 (0/7 identical); minilm-q 0.9895;
  bge-q static and fp32 batch-invariant. `update_embeddings` batches <8-symbol
  updates via `embed_documents` → typical small incremental edits (2–7
  symbols) wrote off-space vectors ≠ clean rebuild. Fix: `NativeEmbedder`
  `batch_invariant` (from `get_quantization_mode`) → per-text ONNX calls.
  Test `dynamic_quant_batch_matches_single_text` failed before, passes after;
  httpx re-embed still 1409/1409 bit-identical to stored vectors; batch==single
  7/7 for both dynamic profiles. clippy (default + no-default) clean, 497/0.
  Existing indexes keep any old off-space vectors until those symbols re-embed.
- OPTIMIZATION (measured, NOT implemented — RSS trade-off, needs approval):
  httpx 1409 symbols, near-idle machine. Current (1 session, all cores,
  per-text, Mutex) 274 docs/s, 132 MB peak. Session pool, per-text, vectors
  bit-identical (200/200) in every config: 4×4 threads 520 docs/s (1.9×),
  414 MB; 8×2 564 docs/s, 635 MB; 4×1 333; 16×1 519. ≈80 MB per extra
  ORT session. Query embed p50 1.7 ms warm.

## Docs check (Context7 fastembed-rs; TinyFish: IBM model card + HF blog)
- Granite runs on its official ONNX (`onnx/model.onnx`, ORT via fastembed,
  CLS, no prompt) — the card's documented ONNX path. PyTorch = reference only.
- IBM's CPU-optimized artifacts: `model_quint8_avx2.onnx` (98 MB; fails our
  fidelity gate, min cos 0.941) and OpenVINO INT8 (needs OpenVINO runtime →
  new dependency, out of scope). fp32 had no CB win, so quint8 not run.
- fastembed exposes `with_intra_threads` and batched `embed(texts, batch)`;
  OXIDE's `NativeEmbedder` Mutex + one-text-per-call worker path serializes
  embedding. Cost screen reports production-path AND batched ONNX throughput.

## Experimental pool — implemented 2026-09-24 (uncommitted, opt-in only)
`OXIDE_EXPERIMENTAL_EMBED_SESSIONS=N` (unset/1 = shipped path unchanged;
1..=16 else error). N sessions, intra-op threads = cores/N, slot 0 eager,
extra sessions lazy (only when all busy), Dynamic still one text per call,
name/fingerprint unchanged. `NativeEmbedder::with_sessions` for tests.
Tests (#[ignore], model): `session_pool_matches_single_session_bit_for_bit`
(4 threads × 16 texts, embed_documents, query, fingerprint) PASS.
End-to-end `oxide index -e` on scratch copies (16-core, release):
| corpus / condition | 1 session | 4-session pool | parity |
| httpx 1,409 idle | 14.1 s (100/s) | 5.0 s (284/s) | 1409/1409 |
| pytest 7,804 idle | 107.3 s (73/s) | 38.4 s (203/s) | 7804/7804 |
| httpx, 8 cores busy | 16.0 s | 7.6 s | — |
| httpx, 16 cores busy | 59.8 s | 13.9 s | — |
Peak RSS `index -e` httpx: 143 → 373 MB (+230; expected ≤ +282).
One-shot `oxide search`: 0.19 s / 73 MB both (lazy → no extra init).
Warm embed_query p50: 3.42 → 2.18 ms (session 0 uses cores/4 threads).

## Research-cost reduction — 2026-09-24
Audit (redundant work found): (1) every corpus re-embedded in full although
held-out has 45,157 unique texts of 206,471 (21.9%); (2) shipped single
session with 4 mutexed workers ~2.8× slower than the pool; (3) model load
per corpus process is small (0.1–2 s) — kept; (4) 3–4 query embeds per task
(ms) — kept; (5) corpus builds embed with production model — still needed
for the D0 baseline.
Changes (harness only, `bin/semantic_variant.v4`): `--cache <db>` wraps the
provider in `oxide::embedding_cache::SharedEmbeddingCache` (namespace = full
provider fingerprint; key = hash of the exact embedded text; queries never
cached); Granite LocalOnnx gets a 4-session pool and its fingerprint's
artifact field now carries the HF snapshot path; runs use
`OXIDE_EXPERIMENTAL_EMBED_SESSIONS=4`. Check: httpx-336204f0 cold cache
1409/1409 vectors == production; httpx-9fd6f0ca 1010 hits/393 misses,
1403/1403 == production.
Dev screen: `tasks-dev.jsonl` (70 tasks, 30,377 symbols, commit-disjoint
from held-out); `screen.sh` = 2×2 {D0,D1}×{xs-q, granite fp32};
`run_screen.sh`; cache at `~/.cache/oxide-semantic-eval/cache/emb.db`.

## Dev 2×2 screen result (2026-09-24, 1,034 s wall incl. Granite)
Dumps `dumps/dev-{xsq,granite}-{D0,D1}.jsonl`; baseline = xs-q D0 (stored).
| all (n=70) | sem R@50 | nDCG@10 | ΔnDCG [95% CI] | gold_in_pack | rel tok/1k |
| xs-q D0 | 0.503 | 0.405 | — | 0.586 | 143.2 |
| xs-q D1 | 0.545 | 0.402 | −0.003 [−0.044, +0.034] | 0.614 | 147.5 |
| granite D0 | 0.576 | 0.421 | +0.016 [−0.047, +0.077] | 0.700 | 176.6 |
| granite D1 | 0.590 | 0.426 | +0.020 [−0.043, +0.081] | 0.671 | 175.1 |
desc (n=49) nDCG: 0.328 / 0.344 (+0.016) / 0.329 (+0.001) / 0.336 (+0.008),
all CIs span 0. ident (n=21): D1 −0.047 [−0.105, +0.006]; granite +0.051.
Reading: text (D1) and model (Granite) each add a little semantic candidate
recall (+0.04/+0.07 sem R@50), neither moves desc fused ranking; granite's
dev pack utility (+23% rel tok/1k) contradicts CB (gold lines/1k 28.8→20.3).
The desc bottleneck is ordering under frozen RRF (held-out: 17/40 desc
losses are ordering, 2/40 route), not semantic candidate generation. Stop
rule: no challenger reaches held-out/CB validation.
Cost: xs-q variant on dev ≈ 2–2.5 min (pool + cache); Granite ≈ 6.5 min
(84 docs/s pooled, 7 s load/corpus). Cache 221 MB after 4 cells.

## Pool review (2026-09-24): no BLOCKER/MAJOR; MINORs resolved
- `VarError::NotUnicode` no longer defaults silently (errors).
- Bit-identity now tested for int8 AND fp32 (`arctic-embed-xs-q`,
  `arctic-embed-xs`) — both PASS; comment narrowed to measured profiles.
- Documented: process-wide setting (slot 0 also cores/N threads; lazy
  sessions stay resident in long-lived `oxide mcp`); `from_local_files`
  ignores it; first burst may wait on sequential lazy session loads.
- Pre-existing UX gap (not changed): the human CLI renders any provider
  construction error as "embedding provider could not be reached" without
  the cause (same for `OXIDE_EMBED_NATIVE=bogus`); `--json` carries it.
- Checks: clippy default + no-default clean; cargo test 497/0; ignored model
  tests (batch invariance, pool parity) PASS. `mise run verify` last run
  before the pool change (exit 0).

## State / next action
Uncommitted, awaiting approval: pool in `src/embeddings.rs` (opt-in env,
default unchanged); harness `examples/semantic_variant.rs` (cache, pools,
bench modes); `scripts/cb_variants.py`; this file.
Semantic track verdict so far: no text variant or small model improves
desc fused ranking (dev 2×2, CB, held-out); desc loss is fusion ordering,
which is frozen. Parent-commit D1 control not needed (D1 fails dev screen).
Smallest next action: decide whether to promote the pool (a default change
needing approval: +230 MB peak for 2.8–4.3× indexing) or keep it opt-in.

## Pool promoted to production default — 2026-09-24 (uncommitted, for review)
`OXIDE_EMBED_SESSIONS=auto|1..16` replaces the experimental env var (the
scratch scripts' `OXIDE_EXPERIMENTAL_EMBED_SESSIONS` is now ignored). Auto =
min(cores/4 ≤4, extras ≤¼ available mem incl. cgroup v2), 1 for models
>400 MB/session; growth only after 256 document embeds. Human CLI now shows
the cause of `embedder_unavailable`; model-load failures are actionable.
Bench (16 cores, release, single `=1` vs default): httpx full 15.1→6.5 s
(145→368 MB), -e 14.1→5.9 s, 3-sym update 0.32→0.31 s (79→78 MB), 30-sym
0.62→0.49 s (83→79 MB), one-shot search 0.20→0.19 s (74 MB both), MCP warm
p50 15.7→14.3 ms, 8 concurrent 0.07→0.05 s, peak 98 MB both; pytest full
119.4→46.9 s (189→446 MB); 8 busy cores 13.6→8.4 s; 16 busy 57.2→15.6 s;
taskset 4 cores: identical (auto=1, 141 MB); cgroup 500M: 218 MB (2 sessions).
Verification: mise run verify exit 0; cargo test 500/0; ignored model tests
(pool parity int8+fp32 incl. growth, batch invariance) PASS; review: no
BLOCKER/MAJOR, 4 MINOR/NIT resolved (docs + render test).
