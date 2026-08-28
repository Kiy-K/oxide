# Retrieval ceiling — frozen OXIDE 0.6B stack

Status: **frozen** at `qwen3-Q8_0` (0.6B Q8) with the settings in [`src/config.rs`](../src/config.rs), `HashedEmbedder` offline.
Do not change `src/retrieval.rs` / `src/context.rs` / `src/index.rs` / `src/lexical.rs` without new evidence.

## Measured ceiling (21 pinned tasks, 48 gold files)

* hit (budgeted): 23
* route loss: **17** (gold absent from lexical+semantic+hybrid even @10)
  * 16/17 absent from semantic even @50 (`sem rank None`, score 0.0 vs top 0.016)
  * 1/17 rank 16 (margin top1 0.0032, top5 0.0022 - not flat)
* fusion loss: 3
* corpus/path mismatch: 3 (no symbols, `exists False`)
* allocation loss: 2

Universal misses (all routes miss @10): ~20, gap experiments reverted.

## Negative evidence (all reverted, baseline preserved)

* candidate widening, fusion weight changes, adaptive lex/sem cutoffs, query grounding, progressive retrieval, deterministic multi-view (body/identity structural) — no universal miss reduction, Pareto degraded or token cost +95% without gain.

## Unresolved issue

`semantic / retrieval-route ceiling — stronger or task-specialized retriever diagnostic deferred`

The 17 route losses are not candidates near the cap whose gap cutoff would rescue them; they are not retrieved by any route at all (16 absent @50). Per advisory diagnose-first: dominant class is **route loss (genuinely wrong ordering, A)**, not "ranks just below cap" or score crowding (flat 0). Replacing the primary cap with a gap cutoff would not move universal-miss count.

## Production constraint

Keep `qwen3-Q8_0` as current production constraint. Do not attempt 4B locally (hardware). Held-out stronger model noted as diagnostic control only, not shipped.

## Next step per gate

External benchmark vs 4 meaningfully different retrieval signals (lexical/grep, repo-map symbol-structure, dependency-aware, dense/file-level) under same pinned conditions, with failure-overlap focus `OXIDE miss + competitor hit` and signal attribution.
