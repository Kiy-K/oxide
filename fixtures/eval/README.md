# Frozen evaluation inputs

Read-only oracles for the quality gates. Never regenerate them to make a failure pass.
Experiments are only for claims a test can't settle (AGENTS.md § How to work); ordinary
implementation is accepted on tests plus `mise run verify`, and changes to retrieval,
routing or packing must also pass the ContextBench gates.

| Path | Read by |
|---|---|
| `structural-v1/` | `tests/python/test_baseline.py`: exact reproduction of the Rust structural baseline |
| `contextbench-py/{tasks,gold-audit}.json`, `contextbench-py/baseline-v21/` | `tests/python/contextbench.py` (`TASKS`, `AUDIT`, `BASELINE`), in `verify` |
| `contextbench-ts/{tasks,gold-audit}.json`, `contextbench-ts/baseline-v18/` | `tests/python/contextbench_ts.py`, `mise run contextbench-ts` |

Path labels inside frozen files (`docs/phase-6/...`) are their original locations,
kept byte-for-byte; the files themselves are history at `013e791`.

## ContextBench protocol

- **Tasks.** Fixture queries (`fixtures/benchmark.json`, `structural_benchmark.json`) plus
  `EuniAI/ContextBench` `data/full.parquet` tasks (sha256 `2f56535b…`) filtered by a
  file-count cost rule: 37 Python tasks (requests, flask, seaborn, xarray, pytest) and 81
  TypeScript tasks (mui, vue, vscode, code-server, darkreader). Gold is the
  human-annotated line spans; the audit marks each span verified or unverified, never
  dropped, and only verified gold is gated.
- **Splits are by repository.** Held-out is xarray and pytest (Python), mui and vue
  (TypeScript). Develop on dev; read held-out results only to evaluate a finished change.
- **Metrics** (macro means per split). Entry: `entry_recall@1/5/20`, `entry_mrr`,
  `entry_file_recall`. Routing: `routed_coverage`. Packing at 256/1,024/4,096 units:
  `span_recall`, `line_recall`, `file_recall`, `utilization`, `gold_unit_share`. Each loss
  is tagged with the stage that caused it.
- **Gate rules.** Per split, every gated metric stays at or above its frozen value minus
  0.02; bundles stay within budget, are deterministic and never fail; corpus identities
  match the manifest. Output compatibility (entry order, routed set, payload digests) is
  checked separately, and a version bump is a versioned difference, not a regression.
- **Freezing.** A failing gate is investigated, not re-frozen. An intended, reviewed
  change is frozen as a new `baseline-vN` beside the current one, with a README giving the
  reason and the diff against the previous version: run
  `uv run --locked python tests/python/contextbench.py run --out fixtures/eval/contextbench-py/baseline-vN`
  under the resource limits, switch `BASELINE`, and remove the superseded directory in the
  same change (git history keeps it).
- **Recording.** A verdict is one row in [`docs/decisions.md`](../../docs/decisions.md),
  plus a short report only if a gate, test or comment will cite it.

Commands, corpora and resource limits are in [workflow](../../docs/agents/workflow.md).
