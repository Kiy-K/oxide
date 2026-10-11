# Code-body and field-aware lexical retrieval (rejected; lexical R&D closed)

Status: **all configurations rejected, 2026-10-11.** Production lexical
retrieval (LadybugDB FTS over name, signature and path) is unchanged. As
the task instructed, lexical retrieval R&D is closed. Gates were frozen
before any measurement in [preregistration.md](preregistration.md) (sha256
`14326d1d…d159`; it is committed together with the results, so git does
not attest the freeze). The harness is kept as `harness.patch` (applies to
`f9e8805`), the raw per-task results as `dev.json`, and the tables and
gates as `summary.py`. Numbers are measured unless marked *inferred*.

## Question

The semantic follow-up ([../semantic-eval/routing.md](../semantic-eval/routing.md))
saw, post hoc and on 4 tasks, a body-text BM25 channel match the LFM
channel at 4,096 units. Does indexing symbol bodies (B), or ranking with
fields (C), improve the final ContextBundle over production lexical (A)?

## Method

- Every configuration runs `build_context` with the frozen Phase 4 config
  (lexical limit 20, BFS 64 regions, heuristic judge, greedy selection,
  ContextPacker). Only the answer to the store's lexical request differs.
- The challengers are an in-memory BM25 behind a wrapping `ReadView`,
  because a body field in LadybugDB is a schema change, which was out of
  scope. It uses `oxide-terms-v1`, k1 = 1.2, b = 0.75, no stemming and no
  stopwords, unlike production (Porter, default stopwords). It scores the
  *distinct* query terms, while production sends every term, duplicates
  included, to the FTS. Whether the FTS weights those duplicates was not
  verified. `A0` is that scorer over production's own fields, the replica
  control, so A0 vs A mixes stemming, stopwords and query-term handling.
- Body: the symbol's digest-verified captured source, cut at a character
  boundary. A class's body is its first bytes (header and early methods).
  A symbol whose bytes failed to hydrate or verify got no body, the same
  as a symbol without source. Those failures were not counted.
- Dev split, sphinx (4 tasks) and keras (5): 39 verified gold, 33 of them
  symbols. Sympy does not fit the 3 GB harness cap and is excluded, as
  preregistered. Calibration and test were never opened.
- Recall is Phase 4 `required_symbol_recall` (all verified gold), task
  mean. Symbol-only recall is reported beside it.

## Results (dev, 9 tasks)

| config | recall @256 / @1,024 / @4,096 | entries R@20 / MRR | channel R@100 | gold in top 20, + / − vs A | routed coverage @1,024 | selection / packing loss @1,024 |
|---|---|---|---|---|---|---|
| **A** production | 0.019 / **0.304** / 0.359 | 0.474 / 0.473 | 0.630 | — | 0.663 | 0.341 / 0.019 |
| A0 replica, same fields | 0.000 / 0.222 / 0.241 | 0.356 / 0.444 | 0.633 | +0 / −5 | 0.444 | 0.204 / 0.019 |
| **B** fields + body (2 KB) | 0.000 / 0.222 / 0.407 | 0.452 / 0.569 | 0.567 | +3 / −4 | 0.730 | 0.378 / 0.130 |
| **C** BM25F, unit weights | 0.019 / 0.222 / 0.407 | 0.511 / 0.569 | 0.567 | +5 / −4 | 0.748 | 0.378 / 0.148 |
| C-rrf per-field RRF | 0.019 / 0.185 / 0.296 | 0.452 / 0.415 | 0.552 | +3 / −4 | 0.711 | 0.452 / 0.074 |
| B512 (body cap 512 B) | 0.000 / 0.241 / 0.407 | 0.480 / 0.602 | 0.554 | +4 / −4 | 0.556 | 0.278 / 0.037 |
| Bfull (cap 64 KiB) | 0.000 / 0.222 / 0.370 | 0.539 / 0.460 | 0.594 | +6 / −4 | 0.630 | 0.278 / 0.130 |
| B-joined (identifiers unsplit) | 0.000 / 0.204 / 0.407 | 0.474 / 0.519 | 0.567 | +4 / −4 | 0.693 | 0.341 / 0.148 |
| *post hoc* A0-qtf | 0.000 / 0.222 / 0.384 | 0.496 / 0.480 | 0.707 | +6 / −5 | 0.625 | 0.384 / 0.019 |
| *post hoc* B-qtf | 0.019 / 0.259 / 0.389 | 0.585 / 0.576 | 0.607 | +8 / −4 | 0.748 | 0.452 / 0.037 |
| *post hoc* C-qtf | 0.019 / 0.259 / 0.389 | 0.557 / 0.583 | 0.626 | +7 / −4 | 0.767 | 0.433 / 0.074 |

`-qtf` weights each query term by how often it occurs in the issue text
(the preregistered rows use distinct terms). It was added after the
preregistered rows were read, to locate the replica gap. It cannot be
promoted.

**Gates.** B and C both score −0.081 at 1,024 against A, with bootstrap
[−0.193, 0.000]. That fails gate 1 (needed ≥ +0.03 and a lower bound
> 0). B also fails gate 2 (−0.019 at 256). Latency and resource gates
pass. No diagnostic exceeds A at 1,024 either; the best, B-qtf and C-qtf,
score −0.044.

**Costs** (per task, p50; A's index time includes capture, derive, load
and the FTS build):

| | A production | in-memory B/C (2 KB body) |
|---|---|---|
| index / rebuild | 4.2 s whole generation | about + 1.1 s (0.8–1.2 s to fetch and tokenize every entity's fields, 0.23 s to hydrate and tokenize bodies, 12 ms to build postings); within the 50% gate (checked by hand: summary.py computes only the latency part of gate 3) |
| retrieval warm / cold | 22 / 23 ms (FTS, all matches ranked) | 3.4 / 3.7 ms. A different engine, so not a like-for-like latency claim |
| memory | — | +5 MiB RSS (max 19) for all fields; harness peak 632 MiB |
| index size | store dir 90 MiB | estimated 2.8 MiB of body postings vs 1.5 MiB for the other fields (in-memory estimate; LadybugDB FTS on-disk size not measured) |

Body text: 79,698 of 94,367 entities have one. The median body is 371
bytes, 8% are cut at 2 KB and 37% at 512 B, and a 2 KB body averages 74
terms. A query has a median of 107 distinct terms.

## What the evidence shows

1. **No configuration improves the bundle at 1,024 units.** The result
   rests on few tasks. Three tasks are zero in every configuration and
   three keras tasks are identical or at ceiling, so the bootstrap
   intervals come from one or two tasks. Every 1,024 loss comes from two
   sphinx tasks, `5462d419` and `f0418d70`, and
   production wins both. The replica A0 loses the same two tasks with no
   body field. So the loss comes from the in-memory scorer (*inferred*:
   no stemming or stopwords, since qtf did not close it), not from
   bodies.
2. **Bodies do lift gold, but at 1,024 it is planned as a header.**
   Against the replica (B vs A0): entries MRR 0.569 vs 0.444, R@20 0.452
   vs 0.356, routed coverage +0.286, and recall at 4,096 +0.166 (keras
   `31f7341a` +0.5, `d492f44e` +0.33). B even beats A on MRR (0.569 vs
   0.473) and routed coverage (0.730 vs 0.663). At 1,024, packing loss
   rises from 0.019 to 0.130 (B) and 0.148 (C). On `5462d419`,
   `31f7341a` and `d492f44e`, gold the bodies promote is planned but not
   fully packed. The packer loses nothing (Phase 5 follow-up 5), so these
   are header-only plans: the budget fits the symbol's header, not its
   whole source (*inferred* from the metric definition). Bodies favour
   long symbols, which do not fit whole at 1,024, so the gain only shows
   at 4,096.
3. **Truncation and tokenization effects are not resolved at this
   sample size.** B512 / B / Bfull: recall at 1,024 is 0.241 / 0.222 /
   0.222, but routed coverage swings 0.556 / 0.730 / 0.630.
   Unsplit identifiers (B-joined) lower symbol recall at 1,024 to 0.204,
   against 0.278.
4. **Field-aware ranking changes entries, not the bundle.** C vs B: entries
   R@20 0.511 vs 0.452 (`57ad5598` 0.4 → 0.6, `d492f44e` 0.33 → 0.67),
   routed coverage +0.018. Bundle recall is the same except +0.019 at 256.
   RRF over fields is worse. Why BM25F moves entries was not tested.

## Remaining limitation

This design cannot say whether a body field would help **on top of
production FTS**. The in-memory replica already fails gate 1 with no body
field (−0.081, the same as B), and the preregistration had no rule for a
failed replica. The measured facts are narrower:

- no tested configuration beats production at 1,024;
- bodies lift gold into the entries and the route against the same
  scorer, and pay off at 4,096;
- at 1,024 that gold is planned as a header.

A real answer needs a body field in the LadybugDB lexical index (schema
and derivation change) and this comparison re-run against the real FTS,
on more tasks than 9. The selection budget's interaction with long
symbols would be part of that. Per the task's rule ("if no configuration
improves final ContextBundle quality, close lexical retrieval R&D"), that
work is not started and the current implementation stays. The closure
follows that rule. The data do not show that bodies are useless.

## Reproduce

`git apply docs/lexical-fields-eval/harness.patch`, then under the usual
caps (`systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0
-p CPUQuota=200%`, `-j 2`):

```text
OXIDE_CB_WORKTREES=<dir>:<dir> OXIDE_LEXICAL_SPLIT=dev OXIDE_SKIP_REPOS=sympy/sympy \
  cargo test --release -p oxide-runtime --test lexical_fields -- --ignored --nocapture
python3 docs/lexical-fields-eval/summary.py docs/lexical-fields-eval/dev.json
```

Recall, ranks and stages are deterministic (the preregistered rows
reproduced exactly across two runs); latencies vary.
