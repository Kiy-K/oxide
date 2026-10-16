# Phase 6 Python context-quality baseline v1 (`contextbench-py-v1`)

> Correction pointer (2026-10-16): superseded as the gate by
> [`baseline-v2`](../baseline-v2/README.md), which audits the gold and corrects the
> scorer (blank lines, module-level gold, loss stages) on the same engine output. The
> numbers below are v1's and stay as frozen.

The frozen quality baseline of the Python engine (ADR-0011) and the regression gate
that `mise run verify` runs against it. It is independent of the historical Rust
artifacts in `docs/phase-3/baseline-v1` and `docs/phase-4/baseline-v1`, which stay
untouched and keep their own exact-reproduction test (`tests/python/test_baseline.py`).

Claims: entry-point retrieval, TreeRouter coverage and packed-context quality of the
deterministic offline engine on 44 tasks with independently defined gold. It makes no
downstream-agent claim, and the 7-task fixture split is a regression floor, not a
generalization result (SPEC § Evaluation).

## Protocol (written before any real-repository result was produced)

Everything in this section was fixed before the harness first ran on the ContextBench
tasks. Changing it later needs a new baseline version, never an edit here.

### Tasks and gold

- **fixture** (7 tasks, dev): every Python query of `fixtures/benchmark.json` and
  `fixtures/structural_benchmark.json`, corpus `fixtures/py_repo`. The query is the
  task `text`; a structural task's `anchor_symbol` is passed as a symbol hint, as in
  Phase 3. Gold symbols (`file#A.b`) map to line spans with Python's own `ast` (the
  `def`/`class` line through `end_lineno`), never with OXIDE's parser or results.
- **ContextBench** (37 tasks): `EuniAI/ContextBench` `data/full.parquet` (sha256
  `2f56535bdc73eb8a68bf4ebb49789d8e9cd4f219ea60df6290b85278aee61ca8`), every
  `language == python` task whose repository has at most 250 tracked `*.py` files at
  every one of its base commits (a cost rule, measured from git trees without running
  OXIDE): psf/requests (6), pallets/flask (1), mwaskom/seaborn (1), pydata/xarray (17),
  pytest-dev/pytest (12). The query is the `problem_statement`, verbatim, without hints.
  Gold is the human-annotated `gold_context` line spans (1-based, inclusive). Each span
  stores the sha256 of its whitespace-normalized `content`; a span whose corpus lines
  do not match is reported as unverified, never dropped.
- Corpus: the tracked `*.py` files of the repository at `base_commit` (`git archive`),
  indexed whole. Non-Python files are left out, a declared difference from a full
  checkout.
- **Splits, by repository** (SPEC § Training-data implications): **dev** = fixture,
  requests, flask, seaborn (15 tasks); **heldout** = xarray, pytest (29 tasks), the two
  eligible repositories with the most tasks. Retrieval, routing or packing work is
  developed and inspected on dev. Held-out per-task results are recorded for the gate,
  but are not used to choose or tune a change; read them only to evaluate a finished
  change.

### Metrics

Overlap: an entity covers a gold span when they share a line of the same file. Only
symbols count for span metrics (a file entity would cover everything).

- `entry_recall@k` (k = 1, 5, 20): gold spans covered by a symbol among the first k
  `search` entries (seeds first, then lexical rank; default limit 20) / gold spans.
- `entry_mrr`: 1 / position of the first entry covering any gold span, 0 if none.
- `entry_file_recall`: gold files containing any entry (any category) / gold files.
- `routed_coverage`: gold spans covered by a TreeRouter candidate symbol (the
  inclusion ceiling) / gold spans.
- For each budget B in 256, 1024, 4096 `oxide-units-v1`:
  - `span_recall@B`: gold spans whose every line lies in packed items / gold spans;
  - `line_recall@B`: gold lines inside packed items / gold lines (distinct lines);
  - `file_recall@B`: gold files with a packed item / gold files;
  - `utilization@B`: used / budget (a diagnostic; filling the budget is not a goal);
  - `gold_unit_share@B`: units of items overlapping gold / used (labeled precision;
    incomplete gold makes the rest unlabeled, not irrelevant).
- Per gold span and budget: `complete`, `partial` or `missed`, with the stage that
  lost it (`entry`, `routing`, or the packer's omission reasons, or `graph-limit`).
- Diagnostics per bundle: items, header-only entities, omissions by stage and reason,
  degradations, route bounds (regions, deferred, fanout truncations, stop reason).

Aggregates are macro means over the tasks of a split.

### Gate rules (`tests/python/test_contextbench.py`)

1. **Quality.** For each split, every gated metric (`entry_recall@5`, `entry_recall@20`,
   `entry_mrr`, `routed_coverage`, `span_recall@B` and `line_recall@B` for every budget)
   must stay at or above its frozen value minus **0.02** absolute. The engine is
   deterministic, so there is no sampling noise to absorb; the tolerance only lets
   small line-level drift pass. Losing one span on the smallest split moves its mean
   by more than 0.02. Improvements pass.
2. **Invariants.** Every bundle has `used <= budget`, building it twice gives the same
   bundle, and no task fails. Corpus identities (commit and digest of the `*.py` tree)
   must equal the manifest; a changed corpus is reported as such, not as a quality change.
3. **Output compatibility, separately.** Per task, the entry order, the routed
   candidate set and each payload's sha256 must equal the frozen ones. When the engine
   versions (`retrieval`, `router`, `selector`, `payload`, `units`, derivation) changed,
   the failure says the output is versioned-different and points to the quality test;
   when they did not, it says the output changed without a version bump. A changed
   format is never reported as a quality regression, and a quality regression never
   hides behind a format change.

A failing gate is investigated, not re-frozen. An intended, reviewed change is frozen
as a new `baseline-vN` directory with its reason; this one is never regenerated.

## Reproduction

```bash
mise run bootstrap                     # also fetches the 37 corpora (git, pinned commits)
uv run python tests/python/contextbench.py fetch    # the corpus step alone
uv run python tests/python/contextbench.py run      # all tasks; prints the aggregates
uv run python tests/python/contextbench.py run --out /tmp/cb   # results/metrics/manifest
uv run pytest tests/python/test_contextbench.py     # the gate
```

`tasks.json` was generated once with
`uv run --with pyarrow python tests/python/contextbench.py tasks <full.parquet>`.
Corpora live under `$XDG_CACHE_HOME/oxide/contextbench` (default `~/.cache/oxide`), next
to the FTS extension; every run indexes into a fresh temporary data location.

## Results
Frozen from `cf6515c` plus this change's working tree (the manifest's `oxide_source`),
engine versions `oxide-retrieval-v2`, `oxide-router-bfs-v2`, `oxide-select-greedy-v2`,
`oxide-payload-v2`, `oxide-units-v1`; Python 3.13, ladybug 0.21.2, tree-sitter 0.26.0,
tree-sitter-python 0.25.0, Linux x86_64 (measured). Two independent runs produced
byte-identical `results.json`, `metrics.json` and `manifest.json`; every bundle stayed
within budget and rebuilt identically. A full run takes about 64 s under
`CPUQuota=200%` (measured; each bundle is built twice).

| Metric | dev (15) | heldout (29) | all (44) |
| --- | ---: | ---: | ---: |
| `entry_recall@1` | 0.263 | 0.101 | 0.157 |
| `entry_recall@5` | 0.574 | 0.245 | 0.358 |
| `entry_recall@20` | 0.646 | 0.388 | 0.476 |
| `entry_mrr` | 0.581 | 0.305 | 0.399 |
| `entry_file_recall` | 0.844 | 0.744 | 0.778 |
| `routed_coverage` | 0.839 | 0.559 | 0.654 |
| `gold_verified` | 0.829 | 0.770 | 0.790 |
| `span_recall@256` | 0.033 | 0.007 | 0.016 |
| `line_recall@256` | 0.134 | 0.027 | 0.063 |
| `file_recall@256` | 0.633 | 0.287 | 0.405 |
| `utilization@256` | 0.928 | 0.945 | 0.939 |
| `gold_unit_share@256` | 0.276 | 0.097 | 0.158 |
| `span_recall@1024` | 0.494 | 0.076 | 0.219 |
| `line_recall@1024` | 0.508 | 0.128 | 0.258 |
| `file_recall@1024` | 0.711 | 0.557 | 0.610 |
| `utilization@1024` | 0.942 | 0.992 | 0.975 |
| `gold_unit_share@1024` | 0.256 | 0.139 | 0.179 |
| `span_recall@4096` | 0.588 | 0.142 | 0.294 |
| `line_recall@4096` | 0.601 | 0.248 | 0.368 |
| `file_recall@4096` | 0.789 | 0.658 | 0.703 |
| `utilization@4096` | 0.660 | 0.988 | 0.876 |
| `gold_unit_share@4096` | 0.199 | 0.094 | 0.130 |

Harness cross-checks (measured): on the fixture split, `span_recall@256` is 0.0714
and `span_recall@1024` is 1.0, the Rust Phase 4 config B values (0.071, 1.0).
`entry_recall@5` (0.929) and `entry_mrr` (0.595) are higher than Phase 3's identity-based
0.857 and 0.516 because an enclosing class entry now covers a gold method by line
overlap; the two rulers are not comparable. Gate sensitivity (measured, then reverted):
`LEXICAL_LIMIT` 20 → 5 fails the quality test on both splits (`entry_recall@20`,
`routed_coverage`, held-out `entry_mrr`) with per-task attribution, and the
compatibility test as an unversioned change; a heading-only payload change
(`### ` → `%%% `, same units) with `PAYLOAD_VERSION` bumped passes the quality test and
fails only the compatibility test, as a versioned change.

Scorer correction before the freeze: the first runs counted entry positions without
module entries (which have no source). Fixed to count every `search` entry, as the
protocol defines; it changed only dev `entry_mrr` (0.581287 → 0.581111). No
retrieval, routing or packing code, threshold or task changed after results were
seen: every output digest and bundle diagnostic of the first run equals the frozen one.

### Where gold is lost (spans; held-out read only in aggregate)

| Split, budget | complete | partial | missed |
| --- | ---: | ---: | ---: |
| dev @1024 | 13 | 3 | 34 |
| dev @4096 | 19 | 2 | 29 |
| heldout @1024 | 9 | 17 | 141 |
| heldout @4096 | 19 | 25 | 123 |

- **Never reached by routing**: 13 of 50 dev spans and 76 of 167 held-out spans
  overlap no entry point or routed symbol (measured). Routing stopped at its depth
  bound in 12 dev and 12 held-out tasks and at its regions bound in 3 and 17 (measured).
  Inferred cause: a long problem statement is one disjunctive BM25 query over entity
  names, signatures and paths, never bodies (`retrieval.document`).
- **Reached but not packed** at 4096: packer budget omissions, enclosing classes shown
  only as headers, the 64-node graph limit and the 0.4 selection floor. Held-out
  bundles fill 99 % of 1024 and 4096 units while only 9–14 % of their units overlap
  gold. `lost` lists a reason for every routed symbol overlapping a span, so enclosing
  scopes appear next to the symbol itself.
- **Module-level gold** (imports, `if is_py2:` blocks) has no symbol entity and can
  never be packed by a symbol-only packer.

### Limitations

- 44 tasks, 5 real repositories, Python only; held-out is two repositories. A
  regression floor, not a generalization estimate; no confidence intervals are claimed.
- 44 of 208 ContextBench spans (21 %) do not match their annotated content at the
  base commit (off-by-one ends, content that is not the file at that commit, e.g.
  `pytest-dev__pytest-5787`); they are scored by their line ranges and flagged
  `verified: false`.
- Edit-locus and annotated gold is incomplete for read-required context;
  `gold_unit_share` is a lower bound on useful context, not a noise ratio.
- Corpora are `*.py` only, so non-Python files never compete as entry points.
- No downstream-agent result and no latency gate; `scripts/bench.py` covers latency.
