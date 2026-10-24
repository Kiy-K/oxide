# graph-v2 preregistration: routing over dispatch edges

Frozen 2026-10-24, before any arm below was run. Code at this freeze: the
`oxide-python-v5` / `oxide-ladybug-py-v2` working tree, where receiver-aware method edges
(`self.m()`, `cls.m()`, `super().m()`, inherited `Class.m()`, `x = C(); x.m()`) carry
`basis="heuristic"` and the router does not follow them (`routing.follows`). Held-out
tasks are **not** used; only Python dev (15 verified tasks) runs.

## Disclosure

Seen before this file was written:

- The v11 → v12 frozen results: the 6 verified spans graph-v1 newly routed are all lost
  after routing at 4,096 units (3 `selection: value below floor`, 3 `budget`).
- graph-v1's arm D (admit the top half of routed-only symbols at every depth) moved no
  `line_recall` on Python or TypeScript dev (`../graph-v1/results/ppr-gate.txt`).
- Edge precision of the dispatch edges (jedi) and their counts per corpus; no routing or
  packing number of the arm below.

## Study: does following dispatch edges deliver gold?

Information hypothesis: gold reached only through a method call on `self`/`cls`/`super()`
or a constructed instance is out of the router's reach today; following those edges
reaches it. Decision: whether `routing.follows` should accept heuristic `calls` and
`references` edges (a router change; retrieval, selection and packing unchanged).

Arms (`scripts/dispatch_arm.py`), Python dev, verified gold:

- **B**: the working tree as is. Its dev results must equal baseline-v13's frozen dev
  results (`compat` hashes); otherwise the study is void.
- **T**: `routing.follows(kind, heuristic)` returns `kind in FOLLOW`, so heuristic
  `calls`/`references` edges are walked like resolved ones, under the same caps
  (`MAX_REGIONS`, `MAX_DEPTH`, `MAX_FANOUT`, `MAX_EDGES`).

Primary metric: macro `line_recall@4096`. Secondary: `line_recall@1024`,
`span_recall@4096`, `gold_unit_share@4096`, `routed_coverage`. Tolerance 0.02.

Verdict:

- **ADVANCE** (to a held-out confirmation and a promotion proposal, a separate step) only
  if T − B ≥ +0.02 on `line_recall@4096`, no budget's `line_recall` falls more than 0.01
  below B, and `gold_unit_share@4096` is not more than 0.01 below B.
- **REJECT** otherwise. Dispatch edges stay heuristic and unrouted; they still serve
  `Repository.relations()` and `impact()` (as unproven hops).

Diagnostic reported whatever the verdict (it decides nothing): for each verified span
whose `routed` flips between B and T, its 4,096-unit loss stage under T (`entry`,
`routing`, `selection`, `budget`, `complete`). A span routed only in T but lost at
`selection` or `budget` is a selection-policy loss; a span routed in neither is a
candidate-discovery loss.
