# Source-body retrieval: preregistration (2026-10-17)

Question: does indexing symbol source bodies in the real LadybugDB FTS improve
natural-language entry retrieval *and* packed context, without regressing any
repository, VS Code in particular? It follows
[../entry-retrieval-diagnostic](../entry-retrieval-diagnostic/README.md), whose
in-memory replica predicted gains on Python and small TypeScript repositories and a loss
on vscode (14 → 6 dev spans in the top 20), and
[../../lexical-fields-eval](../../lexical-fields-eval/README.md), which made a real-FTS
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
