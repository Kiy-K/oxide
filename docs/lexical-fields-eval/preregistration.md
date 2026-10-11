# Preregistration: code-body and field-aware lexical retrieval

Frozen 2026-10-11, before any measurement of the configurations below.
Hypothesis source: the post-hoc body-BM25 observation in
`docs/semantic-eval/routing.md` (4 tasks, not preregistered). This is a new
test of it, not a confirmation.

## Setup

- Harness: `oxide_kernel/crates/runtime/tests/lexical_fields.rs`
  (ignored, machine-local). No JEV, embeddings, training or database
  change. Every configuration runs `build_context` with the frozen
  Phase 4 config: lexical limit 20, BFS 64 regions, heuristic judge,
  greedy selection, ContextPacker. Only the answer to the store's lexical
  request differs.
- **A** is production: the LadybugDB FTS index over `lexical::document`
  (name, signature, path; `oxide-terms-v1`; Porter stemming, default
  stopwords, BM25 k1 = 1.2, b = 0.75). The other configurations are an
  in-memory BM25 over the same pinned view, behind a `ReadView` wrapper
  that answers `lexical()`. They use `oxide-terms-v1`, no stemming, no
  stopwords, k1 = 1.2, b = 0.75, and the distinct query terms.
- Body = the symbol's verified captured source (digest-checked through
  `Sources`), cut at a character boundary to a byte cap. Only
  source-backed symbols have a body.

## Configurations

| label | fields | role |
|---|---|---|
| `A` | production FTS | baseline |
| `A0` | in-memory, one field: name + signature + path | replica control; A0 vs A = scorer gap |
| **`B`** | in-memory, one field: name + signature + path + body (cap 2,048 B) | **primary**: what reading bodies adds (B vs A0) |
| **`C`** | in-memory BM25F: name, signature, path and body (cap 2,048 B) as fields, unit weights, b = 0.75 per field, IDF over the union | **primary**: what field-aware ranking adds (C vs B) |
| `C-rrf` | per-field BM25 rankings fused by unweighted RRF (k = 60) | diagnostic |
| `B512`, `Bfull` | B with body cap 512 B / 65,536 B | diagnostic: truncation |
| `B-joined` | B, body identifiers kept whole (lowercased, underscores dropped), no part splitting | diagnostic: tokenization |

No field weights or boosts. Diagnostic rows cannot be promoted.

## Data

Dev split (`docs/phase-5/decisionbench-v1/splits.json`). The data are the
dev tasks that fit the 3 GB harness cap: sphinx (4) and keras (5). Sympy
was OOM-killed at that cap on 2026-10-11 and is excluded. Calibration and
test stay sealed unless a dev gate passes.

## Metrics

Primary: the mean over tasks of Phase 4 `required_symbol_recall` (all
verified gold, the Phase 4 definition). Symbol-only recall, entry
Recall@10/@20 and MRR, channel Recall@100, unique gold symbols, routed gold
coverage, selection and packing loss, index build time, cold/warm
retrieval latency, RSS and index size are reported alongside.

## Dev gates (each primary configuration against `A`, all required)

1. Mean recall at 1,024 ≥ +0.03, with a paired task-bootstrap (10,000
   resamples, seed 1) 95% lower bound > 0.
2. Mean recall at 256 and at 4,096 not lower by more than 0.01; mean routed
   gold coverage at 1,024 not lower by more than 0.02.
3. Cost: warm lexical retrieval p50 ≤ max(2 × A, A + 50 ms); added index
   build time ≤ 50% of A's median indexing time; the in-memory index's
   resident growth ≤ 200 MiB per task.

If both pass, the one with the higher mean recall at 1,024 is frozen
(B on a tie). It is then validated on the **calibration** split with the
same harness and gates 1–3, with nothing re-tuned. Test stays sealed. If
neither passes on dev, the current lexical retrieval is kept and lexical
retrieval R&D is closed.

## What a pass can and cannot mean

The challengers are an in-memory scorer, not production: no stemming, no
stopwords, and its own BM25 code. `A0` measures that gap for the current
fields only. A pass on dev and calibration therefore does not make anything
shippable. Production would need the body field in the LadybugDB lexical
index, which is a schema and derivation change excluded here. It would then
have to repeat this comparison against the real FTS before promotion.
