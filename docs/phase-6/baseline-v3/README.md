# Phase 6 Python context-quality baseline v3 (`contextbench-py-v3`)

The engine change that baseline-v2's loss stages called `unsupported`: ContextPacker
can now pack module-level source evidence. Scorer (`contextbench-py-scorer-v2`), tasks
(`baseline-v1/tasks.json`), gold audit (`baseline-v2/gold-audit.json`), corpora,
splits, budgets, tolerance and gated metrics are [v2's](../baseline-v2/README.md);
only `oxide-select-greedy-v3` and `oxide-payload-v3` differ. Measured against v2 on
both splits, the quality gate passed and the compatibility gate failed only as a
versioned change, which this freeze records. v1 and v2 stay as frozen.

## The change

- **View.** A parsed Python file's view is its module-level source: runs of whole,
  non-blank lines that no top-level declaration touches (decorators belong to their
  declaration; a `def` nested in `if`/`try` is a top-level declaration and splits its
  block). Each run is a bounded source range of the file entity, rendered
  `### <path>:<l1>-<l2> (module-level)` with `complete: false`, never the whole file.
  Its header view is the first run (docstring, imports, constants). Runs never
  overlap declarations, so file and symbol items cannot duplicate source. The
  declarations come from the store (`contains` children of the file), not a re-parse.
- **Selection.** A file competes with its module-level view only when retrieval found
  the file itself (lexical rank or a hint seed), valued by the existing rules for
  those. A file reached only by routing (an entry's scope, an importer, or the root
  fallback) keeps the container value 0.2, below the floor, as before. The rule was
  fixed by a fixture case before any ContextBench run of this engine: valued by depth
  like a symbol, the root fallback of a query matching nothing packed nine module-level
  items (`test_mcp.py::test_empty_results_are_successes`).
- **Explicit omissions.** Unparsed or non-Python files keep `container`; a parsed file
  whose source is all declarations gets `no module-level source`; budget and floor
  omissions are reported as for symbols.

Unit and integration tests in `tests/python/test_context.py`: regions for imports,
constants, docstrings, decorators, nested declarations and class bodies; full view,
header view and `too large` at exact budgets; every omission reason; and a retrieved
file contributing bounded, attributed, non-overlapping, deterministic module-level
items within budgets 0–2,048.

## Results (verified gold; denominators as in v2)

Two runs gave byte-identical `results.json`, `metrics.json` and `manifest.json`; every
bundle stayed within budget and rebuilt identically. Entries and routes are v2's (no
retrieval or routing change); payloads changed in 15 of 45 dev bundles and 4 of 87
held-out bundles. The held-out column was produced once, after the rule was fixed, and
is diagnostic, not a new generalization result.

| Metric | v2 dev | v3 dev | v2 heldout | v3 heldout | v2 all | v3 all |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `entry_recall@5` | 0.608 | 0.608 | 0.238 | 0.238 | 0.364 | 0.364 |
| `entry_recall@20` | 0.701 | 0.701 | 0.375 | 0.375 | 0.486 | 0.486 |
| `entry_mrr` | 0.584 | 0.584 | 0.281 | 0.281 | 0.384 | 0.384 |
| `routed_coverage` | 0.883 | 0.883 | 0.565 | 0.565 | 0.673 | 0.673 |
| `span_recall@256` | 0.047 | 0.047 | 0.007 | 0.007 | 0.020 | 0.020 |
| `line_recall@256` | 0.183 | 0.180 | 0.033 | 0.033 | 0.084 | 0.083 |
| `file_recall@256` | 0.633 | 0.600 | 0.270 | 0.270 | 0.394 | 0.383 |
| `utilization@256` | 0.928 | 0.939 | 0.945 | 0.945 | 0.939 | 0.943 |
| `gold_unit_share@256` | 0.276 | 0.250 | 0.077 | 0.077 | 0.145 | 0.136 |
| `span_recall@1024` | 0.508 | 0.508 | 0.109 | 0.109 | 0.245 | 0.245 |
| `line_recall@1024` | 0.548 | 0.548 | 0.129 | 0.129 | 0.272 | 0.272 |
| `file_recall@1024` | 0.711 | 0.711 | 0.529 | 0.529 | 0.591 | 0.591 |
| `utilization@1024` | 0.942 | 0.948 | 0.992 | 0.992 | 0.975 | 0.977 |
| `gold_unit_share@1024` | 0.253 | 0.244 | 0.102 | 0.102 | 0.154 | 0.150 |
| `span_recall@4096` | 0.623 | 0.623 | 0.178 | 0.178 | 0.330 | 0.330 |
| `line_recall@4096` | 0.659 | 0.659 | 0.235 | 0.235 | 0.380 | 0.380 |
| `file_recall@4096` | 0.833 | 0.833 | 0.621 | 0.621 | 0.693 | 0.693 |
| `utilization@4096` | 0.660 | 0.667 | 0.988 | 0.989 | 0.876 | 0.879 |
| `gold_unit_share@4096` | 0.203 | 0.190 | 0.075 | 0.075 | 0.118 | 0.114 |

### Where verified gold is lost (spans)

| Split, budget | complete | entry | routing | unsupported | selection | budget |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| dev @256 | 2 | 4 | 5 | 0 | 3 | 33 |
| dev @1024 | 14 | 4 | 5 | 0 | 3 | 21 |
| dev @4096 | 20 | 4 | 5 | 0 | 7 | 11 |
| heldout @256 | 1 | 31 | 34 | 0 | 13 | 59 |
| heldout @1024 | 11 | 31 | 34 | 0 | 13 | 49 |
| heldout @4096 | 20 | 31 | 34 | 0 | 19 | 34 |

Unverified spans at 4096 (32): 6 complete, 9 entry, 6 routing, 3 selection, 8 budget.

- **No verified gold is unsupported any more, and none was gained.** The 8 v2
  `unsupported` spans are now `selection` losses: 7 lie in files reached only by
  routing (container value below the floor) and 1 (pytest-10051 `logging.py`) in a
  retrieved file that hit the 24-item cap. Packing them needs a selection-policy
  decision about routed files, which this change deliberately did not make.
- **Small-budget cost (dev, measured).** Retrieved files' module-level views now
  compete with symbols. At 256 one fixture task (`py-semantic-http-retries`) packs a
  module-level run instead of its partial gold (`line_recall@256` 0.183 → 0.180,
  `file_recall@256` 0.633 → 0.600); `gold_unit_share` drops by 0.01–0.03 on dev. Every
  gated metric stays within the 0.02 tolerance; no priority was tuned to avoid it.
- Remaining loss is upstream and in the budget: 74 of 185 verified spans are never
  reached (35 `entry`, 39 `routing`) and 45 lose to the budget at 4096.

## Reproduction

```bash
uv run python tests/python/contextbench.py run --out /tmp/cb
uv run pytest tests/python/test_contextbench.py tests/python/test_context.py
```

## Limitations

- v2's limitations stand.
- The module-level view of a large prologue (for example an imports block of 100+
  lines) is all-or-header; there is no finer view, and a header that does not fit is
  omitted as `too large`.
- Files reached only by routing never contribute module-level source; whether, and
  which, routed files should is a selection question for a dev-split experiment or a
  future decision provider.
