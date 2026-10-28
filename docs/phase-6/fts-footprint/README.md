# FTS footprint: build-time dead pages, and top-k lexical queries

Date: 2026-10-28. Branch `rewrite/v2` at `7666a3f` plus the working tree. Each claim is
labelled **measured** or **inferred**. Frozen baselines are untouched.

## Question

Earlier studies found two costs. `CREATE_FTS_INDEX` is the largest single statement in a
build (indexing-throughput § 1: 12–16% of total). The FTS index looked like 55–58% of
`graph.lbug` (leann-graph-storage § 1). The levers already tried are closed, because they
change BM25 scores (stemmer, stopwords, body terms) or are not exact (2 FTS threads,
in-place FTS updates). This study looks for gains that leave every score and ranking
byte-identical.

## Setup

- The corpora are the same commits as leann-graph-storage: nushell@399319476aa4,
  teleport@c2ace99b1c8b, transformers@33cb21150c03 and vscode@2dff5deef71f.
- Every run happens under `systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0
  -p CPUQuota=200%`, one job at a time. CPython 3.13, LadybugDB 0.21.0, btrfs.
- Scripts are in `scripts/`. Raw JSONL is in
  `~/Projects/oxide-eval-data/oxide-fts-eval/` (`stages.jsonl`, `ab/`, `query*.jsonl`).
  - `stages.py` rebuilds one generation's rows with `Store._load`'s statements. After
    each stage it records the file size and the pages that `STORAGE_INFO` attributes to
    live columns and indexes.
  - `ab.sh` interleaves HEAD and the working tree through indexing-throughput's
    `warm.sh`.
  - `query.py` times lexical queries.

## 1. Most of the "FTS share" is dead pages (measured)

`stages.py`, bytes. The rebuild reproduces the product's `graph.lbug` byte count exactly
(nushell 37,318,656).

| | nushell | vscode |
|---|---:|---:|
| after COPY + CHECKPOINT: file | 15.75 M | 77.98 M |
| after FTS + CHECKPOINT: file | 37.32 M | 179.06 M |
| live pages, all tables | 23.31 M | 108.17 M |
| · FTS tables (`0_lexical_docs`, `_terms`, `_appears_in`) | 7.59 M | 30.23 M |
| **pages no table owns** | **14.00 M (38%)** | **70.89 M (40%)** |

- The FTS index itself is 7.6 M and 30.2 M: 28–33% of the live data, not 55–58% of the
  file. The rest of the "FTS share" in leann-graph-storage was space left behind by the
  build.
- **Cause (inferred from which setting removes it).** `CREATE_FTS_INDEX` runs as
  several internal statements that build temporary tables (`0_lexical_appears_info`
  shows up in the catalog strings). The default `auto_checkpoint` (16 MiB WAL threshold)
  fires partway through and writes those tables to the file. They are dropped later.
  LadybugDB's free-space manager only truncates free pages at the end of the file, so
  they stay as holes.

### The fix: `auto_checkpoint=False` on the staging database (measured)

The fix is one keyword in `Store._load`. The only CHECKPOINT left is the one `_load`
already runs at the end.

| `stages.py`, 1 run | nushell auto → off | vscode auto → off |
|---|---:|---:|
| file | 37.32 M → **26.05 M** | 179.06 M → **120.68 M** |
| live pages | 23.31 M, unchanged | 108.17 M, unchanged |
| `CREATE_FTS_INDEX` s | 1.04 → 1.04 | 6.55 → 6.24 |

## 2. Product A/B: HEAD `7666a3f` vs the fix (measured)

`ab.sh`: 4 corpora, 3 interleaved rounds. Each round is a cold index, a one-file edit,
then a warm re-index. Medians; sizes come from `du -sm`.

| | graph.lbug MB | generation MB | total s cold | total s warm | peak RSS MB cold / warm |
|---|---:|---:|---:|---:|---:|
| nushell | 36 → **25** (−31%) | 50 → 39 | 7.37 → 7.33 | 4.97 → 4.72 | 299 → 309 / 327 → 335 |
| teleport | 48 → **34** (−29%) | 69 → 55 | 10.38 → 10.05 | 6.39 → 5.99 | 372 → 383 / 393 → 394 |
| transformers | 76 → **51** (−33%) | 129 → 103 | 21.64 → 20.72 | 14.01 → 13.65 | 644 → 640 / 677 → 677 |
| vscode | 171 → **115** (−33%) | 283 → 227 | 39.33 → 39.26 | 26.30 → 25.66 | 1,140 → 1,192 / 1,148 → 1,257 |

- **Outputs are identical.** All 24 cold and 24 warm digests (indexing-throughput
  `digest.py`) match between arms. They cover the manifest, facts, sources, every row
  of every table, and the exact `(key, score)` lists of 300 BM25 queries.
- **Disk −29 to −33%** per `graph.lbug`, and 20–22% per generation. Every retained
  generation and every copy without reflinks (indexing-throughput § 2) shrinks with it.
- **Time** is −0.2 to −4% cold and −3 to −6% warm, inside the run-to-run spread, so no
  speed claim is made. `CREATE_FTS_INDEX`'s own timer is flat on nushell and teleport,
  +2 to +6% on transformers and +9 to +11% on vscode. It now includes the WAL work the
  auto-checkpoints used to do elsewhere; publish as a whole is −2 to −6%, except vscode
  cold at +3%.
- **Peak RSS** is flat to +3% on three corpora. On vscode it is +52 MB cold and
  +109 MB warm (+9.5%), because the build's pages stay in the WAL until the final
  CHECKPOINT. The load pool (`load_pool`) was not raised, and vscode built within it
  under the 3 GB cap.
- **Gates.** `mise run verify` passes: 391 passed, 7 skipped by design (the TypeScript
  gate needs `OXIDE_CONTEXTBENCH_TS=1`). The frozen baselines are included.

## 3. Query latency: a wide `top := k` with an exact fallback (measured; adopted)

`query.py`, 400 queries per corpus: digest.py's 300 plus 100 agent-like 12-term
queries, run on the fixed file. Opening the database plus `LOAD EXTENSION` takes 0.04 s.
Changes are relative to the `full` arm of the same run.

| arm | product-visible diffs², nushell / vscode | nushell total / p95 | vscode total / p95 |
|---|---:|---:|---:|
| `full` (HEAD) | 0 / 0 | 4.2–5.0 s / 40–50 ms | 14.0–18.0 s / 155–209 ms |
| `top := limit+1` | 20 / 15 | −25..−30% / −32% | −34..−37% / −34% |
| guarded: rerun `full` when ranks `limit` and `limit+1` tie | **0 / 0** | −1..−7% / +33..+40% | −15% / −5..+7% |
| escalate: on a tie, `top := 8(limit+1)`, then `full` | **0 / 0** | +6% / +30% | −22% / −14% |
| **wide**: `top := 10(limit+1)`, `full` only on a tie at the cut | **0 / 0** | — | 14.63 → 10.28 s (−30%) / 168 → 118 ms |

² A difference in the first `limit` rows or in the "more matched" flag, which is what
`View.lexical` returns.

- `top` never changes a score. Every difference is which of several equal-score
  documents survive its cut, and `View.lexical` breaks those ties by key. So plain `top`
  would change the frozen lexical results.
- The cost `top` saves is not scoring. On the 40 slowest vscode queries, BM25 scoring
  took 4.49 s, and fetching and key-sorting every match added ~2.7 s.
- **The exactness rule.** Let s be the score at rank `limit` in what `top := w`
  returned. If the last row `top` returned scores below s, every document scoring at
  least s is in the result, so sorting it by `(score DESC, key)` gives the true first
  `limit` rows. Otherwise, rerun without `top`.
  - guarded and escalate apply this rule with a narrow `w`. 12% of vscode queries tie
    at their cut and pay for a second query.
  - **wide** asks for `w = 10(limit+1)` in one statement, so a tie at its cut is rare.
    `View.lexical` implements it.
  - The test `test_bounded_lexical_hits_are_the_prefix_of_the_unbounded_ranking` checks
    every limit from 0 to the match count on the fixture, which exercises both branches.
- **Rejected, because they change scores:**
  - Entity rows written in key order would make the database's own tie order match
    `View.lexical`'s. But BM25's floating-point corpus statistics then drift by one ULP
    in 50 of 400 queries (`lexical_order.py`, `stages.py … sorted`).
  - Read views on 2 threads were −17%, but with a different digest.

### End to end: `repo.search` (measured)

`search_ab.py`: the same 400 queries through `repo.search(q)` on each corpus's indexed
store. HEAD's `src` vs the working tree, 3 interleaved rounds, medians.

| | total s | p95 ms | result digest |
|---|---:|---:|---|
| nushell | 6.62 → **5.63** (−15%) | 46.0 → **32.2** (−30%) | equal |
| teleport | 8.18 → **7.31** (−11%) | 50.8 → **41.0** (−19%) | equal |
| transformers | 12.85 → **10.92** (−15%) | 85.7 → **64.6** (−25%) | equal |
| vscode | 28.82 → **24.24** (−16%) | 206.5 → **157.0** (−24%) | equal |

- **Every round of every corpus is faster.** The ranges don't overlap: nushell
  6.35–7.22 vs 4.98–5.80 s, vscode 27.54–30.66 vs 23.55–24.82 s.
- The digest covers every result's entries, scores and lexical status. All 12 pairs are
  equal.
- The median query moves less (−5..+7%) than the tail. `top` pays off on the queries
  that match many documents.

## 4. Decision

- **ACCEPT, implemented (uncommitted):** `auto_checkpoint=False` on the staging
  `Database` in `Store._load`. Disk −29 to −33% per graph, identical outputs, time
  neutral, peak RSS up to +9.5% on the largest corpus.
  - There is no format, version, schema or API change.
  - The read side is unaffected, and generations built before the change stay readable.
  - They keep their dead pages until the next re-index.
- **ACCEPT, implemented (uncommitted):** the wide `top := k` with an exact fallback in
  `View.lexical` (§ 3). `repo.search` total −11 to −16%, p95 −19 to −30%, identical
  results.
- **Not a lever:** View open (0.04 s) and the compacted file's query speed (equal to
  the old file within noise).

## Limits

- `stages.py` sizes come from one build each. The A/B gives 3 rounds per arm.
- A repository much larger than vscode holds its whole FTS build in the WAL until the
  final CHECKPOINT, so peak memory grows with it. The fallback would be an explicit
  CHECKPOINT after COMMIT, before `CREATE_FTS_INDEX`. That point leaves no dead pages
  (§ 1: 15.75 M file, 15.72 M live). Measured on vscode, 3 builds each, it only moves
  time: the build takes 13.1–13.3 s either way, and the file is the same size.
- A buffer-pool overflow fails the build before publication, and the previous
  generation stays current (`_load` runs before seal). This is inferred from the code,
  not measured.
- The cause in § 1 is inferred from which setting removes the dead pages. LadybugDB's
  internals were not traced.

## Reproduce

```bash
S=docs/phase-6/fts-footprint/scripts E=~/Projects/oxide-eval-data/oxide-fts-eval
cap() { systemd-run --user --scope -q -p MemoryMax=3G -p MemorySwapMax=0 -p CPUQuota=200% "$@"; }
cap uv run python $S/stages.py GENERATION/graph.lbug $E/s noauto   # or auto
git archive HEAD src | tar -x -C $E/head
$S/ab.sh $E/ab $E/head/src 3 -- CORPUS...
cap uv run python $S/query.py GRAPH.lbug 3
```
