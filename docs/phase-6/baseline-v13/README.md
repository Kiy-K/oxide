# baseline-v13: receiver-aware Python dispatch (`oxide-python-v5`)

`oxide-python-v5` (facts `oxide-python-facts-v3`) resolves Python instance, class and
`super()` method calls through a C3 method resolution order (details and evidence:
[../graph-v2/README.md](../graph-v2/README.md)):

1. **`self.m()` / `cls.m()`** in a method resolve to every in-repo definition of `m` the
   receiver's class or a subclass may dispatch to: one target is `resolved`, several are
   `ambiguous`. Lookup gives up at an unknown (external or computed) base, a metaclass or
   splat base, `__getattribute__`, or a name assigned as an attribute anywhere.
2. **`super().m()`** resolves to the next definition in each MRO that contains the class.
3. **`x = C(...); x.m()`**, with `x` bound once, resolves to `C`'s own MRO (no subclasses).
4. **`Class.m`** falls back to the MRO when the class body does not define `m`.

All these edges carry `basis="heuristic"` (they assume the in-repo class hierarchy is
closed), produce no `tested_by` edge, and the router does not follow heuristic
calls/references. Every edge `oxide-python-v4` produced is unchanged (0 lost, 0 changed
on flask, requests, xarray, pytest).

The store schema is `oxide-ladybug-py-v2` (`Unresolved` nodes keyed by the name's last
segment, for `Repository.impact`); existing stores rebuild once.

## Measured against baseline-v12

Same tasks, gold audit, scorer, budgets, corpora and tolerance. `metrics.json` and every
task's entries, routed set and payload are identical to baseline-v12; only the derivation
id (all component versions) differs. That is expected: routing skips the new heuristic
edges, so nothing reaches selection or packing. Whether following them helps is the
preregistered dev arm in [../graph-v2/preregistration.md](../graph-v2/preregistration.md).

`test_quality_does_not_regress` passes; `test_output_is_compatible` reported only the
derivation change before this freeze.

Command: `uv run --locked python tests/python/contextbench.py run --out docs/phase-6/baseline-v13`
(run once before the `BASELINE` switch; the manifest's `baseline` and `protocol` labels
were then set to v13, the only fields a rerun would change).
