# graph-v3 preregistration: parent-precedence order for routed neighbours

Frozen 2026-10-24, before the TypeScript coverage count below or any number of arm R was
computed. Code at this freeze: `910c696` (`oxide-select-greedy-v4`, `oxide-router-bfs-v2`);
controls are baseline-v13 (Python) and typescript-v10.

## Disclosure

Seen before this file was written (Python dev only, `scripts/pool_trace.py`, no arm):

- Counterfactual ceilings on the same pool at 4,096 units: production 0.659; gold holders
  above the floor packed first 0.792; also below it 0.886; unlimited budget and item cap
  0.948 (floor kept) / 0.957 (floor 0, = routed coverage).
- Every depth-1 routed symbol has value 0.45, so among them `pack` follows pool position,
  which `build_context` sets to `(depth, key)`: alphabetical. In 4 Python dev tasks
  (seaborn-3069, requests-2317, -2931, -5414) a gold-holding depth-1 symbol is lost
  (budget, item cap or header view) while a non-gold depth-1 symbol reached from a
  *worse-ranked* entry was packed; each lost gold holder's best parent is entry 1 or 2.
- Gold rate of depth-1 call neighbours of entries 1–3 on Python dev: 3 of 25 (0.12),
  vs lexical ranks 4–10: 8 of 95 (0.084). Thin; it motivates, it does not decide.

## Mechanism

Information hypothesis: a routed neighbour of a higher-precedence entry point is more
likely relevant than one of a lower-precedence entry. Decision: the order among
candidates of equal value. Nothing else changes: values, `INCLUDE_FLOOR`, `MAX_ITEMS`,
`MAX_REGIONS`, `GRAPH_NODES`, the route, the pool's membership and the budgets are
identical, so context cost is matched by construction (`used <= budget` either way).

Not a rerun of a rejected study: PPR (graph-v1) replaced value with a propagated score and
admitted symbols below the floor; arm D admitted routed-only symbols; router-reachability
and graph-v2 changed reach (caps, fan-out, edge kinds). Here no candidate enters or
leaves the pool and no value changes.

Arms (`scripts/order_arm.py`):

- **B**: production. Must reproduce the frozen `compat` hashes of every task it runs.
- **R**: non-entry pool candidates are ordered by `(depth, p, key)` instead of
  `(depth, key)`, `p` the smallest position in `found.entries` of any region the
  candidate was reached from (`Routed.origins`), or `len(entries)` when none is an entry.
  Entry points keep their order.

## Coverage precondition

Count dev tasks (Python + TypeScript) where a gold-holding depth-1 candidate is not
packed whole at 4,096 while a non-gold depth-1 candidate with a strictly larger `p` is
packed (`scripts/order_precondition.py` over the `pool_trace.py` output). At least **5**
distinct tasks are needed (the routed-module-selection resolution argument); otherwise
**INCONCLUSIVE** and no arm runs. Python dev contributes 4 (above), one of which (seaborn
`Plotter`, 9,359 units) cannot fit at any order.

## Measurements and verdict

Per language and split, verified gold, macro over tasks: `line_recall` and `span_recall`
at 256 / 1,024 / 4,096, `gold_unit_share@4096`, mean `used`; determinism (two runs
equal). Pooled = macro over all tasks of both languages in that split.

1. **Dev gate:** pooled dev R − B `line_recall@4096` ≥ +0.010, and no language's dev
   `line_recall` at any budget falls more than 0.005 below B.
2. **Held-out confirmation** (run once, only if 1 passes): pooled held-out R − B
   `line_recall@4096` ≥ +0.005; no language's held-out `line_recall` at any budget below B
   by more than 0.005; `gold_unit_share@4096` not more than 0.01 below B in any
   language/split.
3. **PROMOTE** (as `oxide-select-greedy-v5`, new frozen baselines) only if 1 and 2 pass,
   and Go/Rust context results do not lose recall. **INCONCLUSIVE** if 1 passes and 2 is
   flat (no regression, gain below +0.005). **REJECT** otherwise; production unchanged.
