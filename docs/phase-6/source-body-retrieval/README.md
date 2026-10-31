# Source-body retrieval: preregistration (2026-10-17)

Question: does indexing symbol source bodies in the real LadybugDB FTS improve
natural-language entry retrieval *and* packed context, without regressing any
repository, VS Code in particular? It follows
[../entry-retrieval-diagnostic](../entry-retrieval-diagnostic/README.md), whose
in-memory replica predicted gains on Python and small TypeScript repositories and a loss
on vscode (14 → 6 dev spans in the top 20), and
[../../lexical-fields-eval](https://github.com/Kiy-K/oxide/blob/f79808a/docs/lexical-fields-eval/README.md), which made a real-FTS
body field the condition for reopening lexical work.

This section is written and committed before any arm below is run. Results are added
below it later and do not edit it.

## Arms (fixed; no sweep)

All arms run the production pipeline unchanged: `oxide-retrieval-v2` (one FTS column,
Porter, default stopwords, disjunctive BM25 k1 1.2 b 0.75, top 20), `oxide-router-bfs-v2`,
`packing.value` and the frozen scorers. The only change is the text stored in the
`terms` column that the FTS indexes, applied in the harness process
(`scripts/body_run.py`); `src/` is not edited.

- **`none`:** production, `document(e)` = terms of name, signature and path. Re-run
  under the same harness to establish the baseline and the costs; its compat hashes
  must equal the frozen dev results.
- **`full`:** `document(e)` plus `terms()` of the entity's whole source text, for
  symbols only. This is the diagnostic's `body` scorer in the real FTS.
- **`own`:** `document(e)` plus `terms()` of the entity's own source text: its bytes
  minus those of every symbol nested inside it, for symbols and files (a file's own
  text is its module-level code). Each source byte is indexed once, at its innermost
  entity, which bounds the index to about one copy of the source.

## Data

Dev splits only: ContextBench-py `baseline-v5` dev (15 tasks, 47 verified spans) and
TypeScript ContextBench `typescript-v2` dev (26 tasks, 115 verified spans). The harness
drops held-out tasks before indexing. Metrics are the frozen scorers' verified-gold
metrics, macro over tasks.

## Measures

- Entry: `entry_recall@5/@20`, `entry_mrr`; gold rank without the top-20 cut; gold-file
  rank by best entity.
- Routing: `routed_coverage`.
- Packed: `span_recall`, `line_recall`, `file_recall` at 256, 1,024 and 4,096 units.
- Cost: index time, store size (the sealed `graph.lbug` with its FTS index), FTS query
  latency, per-task scoring time, peak RSS, and whether vscode still indexes with the
  product's buffer pool (`default_index_failure`).
- Per repository (py: fixture, requests, seaborn, flask; ts: vscode, code-server,
  darkreader, fixture): the same metrics, macro over that repository's dev tasks.

## Promotion criteria (dev), per arm

An arm is a **candidate** only if all hold:

1. **Gain:** on at least one benchmark, dev macro `line_recall@1024` or
   `line_recall@4096` rises by more than the frozen tolerance (0.02).
2. **Coverage:** at least 5 distinct dev tasks (both benchmarks) improve in a packed
   metric (`span_recall` or `line_recall` at any budget), and more tasks improve than
   regress.
3. **No regression:** no gated metric (`entry_recall@5/@20`, `entry_mrr`,
   `routed_coverage`, `span_recall` and `line_recall` at every budget) falls by more
   than 0.02, either on a benchmark's dev mean or on any single repository's dev mean.
4. **Cost:** on vscode, index time and store size at most 2× `none`, median per-task
   scoring time at most 1.5× `none`, no new `default_index_failure`, and the run stays
   within the 3 GB memory cap.
5. **Determinism:** every bundle's `deterministic` flag holds, and an incremental
   re-index equals a full rebuild for the arm's `terms` column.

If both arms are candidates, the one with the smaller vscode store goes forward.

## Verdicts

- **PROMOTE:** a candidate also holds criterion 3 on the held-out splits (xarray,
  pytest; mui, vue), run once against the frozen held-out results, and shows no net
  packed loss there. Only then is it implemented in `src/` with a version bump and new
  baselines (a separate, explicit step).
- **REJECT:** criterion 3 or 4 fails on dev, or no arm shows any packed gain.
- **INCONCLUSIVE:** packed gains exist without regression but miss criterion 1 or 2
  (below what the dev split resolves).

The held-out splits are opened only for a candidate.

## Failure analysis (if an arm regresses)

Using dev evidence only, each lost or displaced gold span is classified as:

- **source-body noise:** the entities that now outrank the gold matched the query only
  through body text, or rose because of it (absent from, or below rank 1,000 in, `none`);
- **query discrimination:** the gold's own body adds no query term that is rare in the
  repository, so nothing new singles it out;
- **repository-level localization:** the gold file itself is not in the top 20 files
  under any arm.

No threshold, field weight or query pruning is tuned to repair a regression.

---

# Results (2026-10-17, dev only)

**Verdict: REJECT.** Both arms fail criterion 3 (regression) on both benchmarks and
criterion 4 (cost) on vscode. Neither is a candidate, so the held-out splits stay
unopened. Nothing in `src/` changes.

`none` reproduces the frozen compat hashes on 15/15 Python and 26/26 TypeScript dev
tasks. Every bundle in every arm is deterministic. Criterion 5's incremental check was
not run, because no arm reached it.

## Packed and entry metrics (dev macro, verified gold)

| ContextBench-py (15 tasks) | none | full | own |
|---|---:|---:|---:|
| entry_recall@5 | 0.608 | 0.461 | 0.606 |
| entry_recall@20 | 0.701 | 0.850 | 0.783 |
| entry_mrr | 0.584 | 0.561 | 0.584 |
| routed_coverage | 0.883 | 0.883 | 0.889 |
| span_recall@1024 | 0.508 | 0.443 | 0.457 |
| span_recall@4096 | 0.623 | 0.728 | 0.694 |
| line_recall@1024 | 0.548 | 0.503 | 0.524 |
| line_recall@4096 | 0.659 | 0.735 | 0.703 |
| tasks improved / regressed (packed) | | 8 / 5 | 6 / 7 |

| TypeScript (26 tasks) | none | full | own |
|---|---:|---:|---:|
| entry_recall@5 | 0.291 | 0.299 | 0.313 |
| entry_recall@20 | 0.383 | 0.376 | 0.392 |
| entry_mrr | 0.326 | 0.282 | 0.278 |
| routed_coverage | 0.468 | 0.407 | 0.446 |
| span_recall@4096 | 0.358 | 0.334 | 0.324 |
| line_recall@1024 | 0.330 | 0.320 | 0.323 |
| line_recall@4096 | 0.370 | 0.336 | 0.332 |
| tasks improved / regressed (packed) | | 2 / 4 | 1 / 4 |

| vscode (14 tasks) | none | full | own |
|---|---:|---:|---:|
| entry_mrr | 0.083 | 0.009 | 0.026 |
| routed_coverage | 0.128 | 0.038 | 0.094 |
| span_recall@4096 | 0.026 | 0.000 | 0.000 |
| line_recall@4096 | 0.045 | 0.000 | 0.007 |

Criterion-3 failures (falls of more than 0.02), from `scripts/body_sum.py`:

- **py `full`:** ALL `entry_recall@5`, `entry_mrr`, `span`/`line_recall@1024`; seaborn
  `entry_recall@5` and `span_recall@1024/4096`; requests `entry_mrr` and
  `routed_coverage` (0.944 → 0.778); fixture `entry_recall@5` (0.929 → 0.429) and
  `span`/`line_recall@1024`.
- **py `own`:** ALL `span`/`line_recall@1024`; seaborn `entry_recall@5` and
  `span_recall@1024/4096`; requests `entry_mrr` (0.600 → 0.492) and `routed_coverage`
  (0.944 → 0.778); fixture `entry_recall@5` (0.929 → 0.714) and
  `span`/`line_recall@1024`.
- **ts, both arms:** ALL `entry_mrr`, `routed_coverage`, `span`/`line_recall@4096`;
  code-server `span`/`line_recall@4096` (0.25 → 0 and 0.24 → 0); vscode `entry_mrr`,
  `routed_coverage` and `span`/`line_recall@4096`; fixture `entry_mrr` (0.822 → 0.697).

Small repositories gain entry rank: code-server `entry_mrr` 0.146 → 0.350 under `own`,
darkreader 0.100 → 0.250 under `full`. On Python, `line_recall@4096` rises by up to
0.077. In every case packing at 1,024 units or routing pays for it, and on TypeScript
no arm gains packed recall at all (best `line_recall` change −0.008).

## Cost (vscode, median of 14 dev commits)

| | none | full | own | limit |
|---|---:|---:|---:|---|
| index time | 25.4 s | 39.6 s (1.56×) | 37.8 s (1.49×) | 2× |
| store (`graph.lbug` + FTS) | 181 MiB | 473 MiB (2.61×) | 429 MiB (2.37×) | 2× ✗ |
| per-task scoring | 1.82 s | 2.31 s (1.27×) | 2.27 s (1.25×) | 1.5× |
| `default_index_failure` | 0/14 | 14/14 | 11/14 | none new ✗ |
| peak RSS (whole run) | 1.3 GB | 2.8 GB | 2.7 GB | 3 GB |
| FTS query median | 26 ms | 40 ms | 38 ms | |

With body text, the product's default LadybugDB buffer pool overflows while indexing
vscode ("buffer pool is full"). The quality numbers above come from the harness's
`EVAL_BUFFER_POOL` re-index, which changes no query result. A body field would
therefore ship as an indexing failure on large repositories, unless the pool sizing
added in `dd26e87` is grown with it. Python costs are small: seaborn's store goes from
19.5 to 31.5 / 27.3 MiB, and scoring median from 0.21 to 0.29 / 0.27 s.

## Why (failure classes, `scripts/body_why.py`)

Each displaced or still-lost gold span is classified at the lexical top 20 and top 5,
first match wins, per the preregistration:

| displaced + still lost, cut 20 | localization | discrimination | body noise |
|---|---:|---:|---:|
| py `full` / `own` | 0 / 0 | 15 / 16 | 0 / 2 |
| vscode `full` / `own` | 45 / 45 | 2 / 2 | 22 / 20 |
| code-server + darkreader `full` / `own` | 0 / 0 | 19 / 15 | 0 / 2 |

- **vscode: repository-level localization.** 67 of its 77 dev spans are below the top
  20 under both `none` and each arm. For 45 of them, the gold file is not among the top 20 files under
  any arm, and the body does not move it in. Under `full`, gold files in the top 20 fall
  from 6/24 to 4/24. Body text gives every one of vscode's tens of thousands of symbols
  more ways to match a natural-language query. The 22 body-noise spans have a
  distinctive query term in their own text and still lose to entities that rose past
  them on body matches. The replica predicted this loss (14 → 6 spans in the top 20).
  In the real FTS it is 10 → 8 under `full` and 10 → 10 under `own`, with no gain to
  offset it.
- **Small repositories and Python: query discrimination.** The body lifts gold into
  the top 20 (py 22 → 32 spans under `full`, 29 under `own`), but out of the top 5. At
  cut 5, 7 Python spans are displaced under `full` (fixture 4, seaborn 3). For displaced
  spans, the median share of the entities now above the gold that rose past it is 1.00.
  These gold spans contain no query term that is rare in the repository, so body text
  raises their neighbours as much as it raises them. Packing at 1,024 units reads only
  the top ranks, so the lift to the top 20 turns into a loss at 1,024 units.
- **Source-body noise** alone is the minority class outside vscode (at most 4 spans per
  arm and repository).

## Next experiment

None is justified on this evidence. The dominant vscode failure is localization: the
gold file is not reached at all. Any lexical field change, including field weights or
query pruning, would be tuning against the 45 spans whose file never surfaces, and the
preregistration rules that out. Work on vscode would start from file-level localization
(which files a query should reach). It needs its own preregistration and is not a
follow-up to this one. Lexical body indexing stays closed.

## Reproduce

```sh
export TMPDIR=~/.cache/oxide-ts-eval/tmp O=~/.cache/oxide-ts-eval/body
for b in py ts; do for a in none full own; do
  systemd-run --user --scope -q -p CPUQuota=200% -p MemoryMax=3G -p MemorySwapMax=0 \
    uv run python docs/phase-6/source-body-retrieval/scripts/body_run.py $b $a $O/$b-$a
done; done
uv run python docs/phase-6/source-body-retrieval/scripts/body_sum.py $O ts   # or py
uv run python docs/phase-6/source-body-retrieval/scripts/body_why.py $O ts \
  docs/phase-6/entry-retrieval-diagnostic/dev-ts.json 20                     # or 5
```

`results/<bench>-<arm>/` keeps each arm's `metrics.json`, `cost.json`, `results.json`
and `probe-spans.json` (per-span gold rank and gold-file rank). The per-task top-1000
key lists that `body_why.py` reads are only in the cache, about 1.5 MB per TypeScript
arm.
