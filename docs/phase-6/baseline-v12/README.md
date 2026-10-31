# baseline-v12: Python cross-file resolution (`oxide-python-v4`)

`oxide-python-v4` resolves three kinds of Python reference that `oxide-python-v3` left
unresolved (details and evidence: [../graph-v1/README.md](../graph-v1/README.md)):

1. **Nested import roots.** A file inside a package whose import root is a nested
   directory (the first ancestor without `__init__.py`, as pytest's `prepend` mode puts
   on `sys.path`) also answers to its root-relative name: `src/flask/app.py` is imported
   as `flask.app`. The repository-relative name keeps precedence, and module identities
   (`module:python:src.flask.app`) are unchanged.
2. **Re-exports.** A name a module binds by import (`from .core import Client` in
   `pkg/__init__.py`) resolves through that import for `from pkg import Client`. A cycle
   of re-exports resolves nothing; star re-exports stay unresolved.
3. **Module and class attributes.** `util.helper()`, `pkg.util.helper()` and
   `Class.method()` resolve when the head binds one in-repo module or class: module-level
   names and submodules of a module, the class's own body for a class. Inherited and
   instance attributes (`self.m()`, `obj.m()`) stay unresolved.

Facts are unchanged (`oxide-python-facts-v2`), so cached parse facts are reused. Every
edge `oxide-python-v3` resolved is still resolved to the same target (0 lost on flask,
requests, xarray, pytest); the change only adds resolved edges.

## Measured against baseline-v11

Same tasks, gold audit, scorer, budgets, corpora and tolerance. Retrieval, routing,
selection and packing are unchanged; the graph routing walks over is richer.

| Split / gold | Metric | v11 | v12 |
| --- | --- | ---: | ---: |
| all, verified (44 tasks) | `routed_coverage` | 0.673 | 0.730 |
| all, verified | `file_recall@4096` | 0.693 | 0.716 |
| all, verified | `line_recall@256 / @1024 / @4096` | 0.083 / 0.272 / 0.380 | unchanged |
| all, verified | `span_recall@1024 / @4096` | 0.245 / 0.330 | unchanged |
| all, verified | `gold_unit_share@4096` | 0.114 | 0.114 |
| all, unverified (19 tasks) | `routed_coverage` | 0.575 | 0.811 |
| dev, verified (15 tasks) | `routed_coverage` | 0.883 | 0.950 |
| held-out, verified (29 tasks) | `routed_coverage` | 0.565 | 0.617 |

Loss attribution at 4,096 units (all, verified spans): `entry` 35 → 32, `selection`
26 → 27, `budget` 45 → 47, `routing` 39, `complete` 40. Gold that was out of reach is
now reached by routing and lost later, at selection or budget: packed line and span
recall do not move. That is the router-reachability finding again (routed depth-2
symbols are valued below `INCLUDE_FLOOR`), so the reach is available to a selection
change, preregistered in [../graph-v1/preregistration.md](../graph-v1/preregistration.md).

`test_quality_does_not_regress` passes; `test_output_is_compatible` reported only the
derivation change before this freeze. Held-out tasks were scored as part of the standard
gate only; nothing was tuned on them.

Command: `uv run --locked python tests/python/contextbench.py run --out docs/phase-6/baseline-v12`.
