# Structural router reachability: NEGATIVE (2026-10-17)

Question: after `oxide-typescript-v2` resolution, TypeScript ContextBench v2 attributes
120 verified spans to `router-bound` (64 before). Can a bounded router policy turn that
reachability into packed context? **No.** `oxide-router-bfs-v2` (64 regions, depth 2,
fan-out 16, 2,048 edges) stays the default. Nothing in `src/` changed, and the frozen
baselines are untouched. Only dev tasks were used (TypeScript dev: 26 tasks, 115 verified
spans; vscode, code-server, darkreader, fixture); held-out mui and vue were not opened.
Numbers are measured unless marked *inferred*.

## Why routed reach does not reach the bundle

Selection (`packing.value`, unchanged) decides what routing can contribute:

- a symbol routed at depth 2 is valued 0.3, below `INCLUDE_FLOOR` 0.4: never packed;
- a file reached only by routing is a container (0.2): never packed;
- only a depth-1 routed symbol (0.45) can be packed, after the entry points.

The `router-bound` cause (`contextbench_ts.attribute`) counts a span as router-bound when
it lies within two unbounded hops of an entry point. It therefore includes spans that no
router change can deliver under this selection policy.

## Corrected attribution (dev, the 46 `router-bound` spans)

Each span is classified by the shallowest depth at which an unbounded router (no region,
edge or fan-out limit) reaches a symbol holding one of its lines (`probe-dev.json`):

| Class | Spans | Packable under current selection |
| --- | ---: | --- |
| one-hop symbol | 6 | yes; 3 of them are already held at depth ≤ 1 by `bfs-v2` but still lost (*inferred*: the holder encloses the span, while the lost lines' own unit, e.g. module-level lines, is not routed) |
| two-hop symbol | 25 | no (0.3 < 0.4) |
| no symbol holds it (module-level lines of a non-entry file) | 15 | no (routed file = container) |

So at most **3** dev spans (6 %) are newly eligible, and all 3 are in one task
(`microsoft__vscode-136347`).

## Router variants (dev, gold reached at depth ≤ 1 out of 115 verified spans)

All variants keep retrieval, the store and selection. `base` reproduces `routing.route`
exactly (asserted per task).

| Variant | ≤ 1 hop | 2 hops | + / − vs base (≤ 1 hop) | vscode routed regions, median (max) |
| --- | ---: | ---: | --- | --- |
| base (`bfs-v2`) | 39 | 0 | — | 64 (64) |
| regions and edges unbounded | 39 | 22 | 0 / 0 | 272 (432) |
| fan-out unbounded | 41 | 0 | +3 / −1 | 63 (64) |
| all unbounded | 42 | 26 | +3 / 0 | 1,440 (8,036) |
| interleave layer across parents | 39 | 0 | 0 / 0 | 64 |
| cross-edges before children | 40 | 3 | +1 / 0 | 64 |
| ambiguous edges dropped | 39 | 0 | 0 / 0 | 64 |
| ambiguous edges ordered last | 39 | 0 | 0 / 0 | 64 |

Capacity (regions, edges) only adds two-hop reach. Prioritization (interleaving, edge
order) and the new ambiguity (declaration-merging edges) move nothing. On vscode a route
spends its 64 regions as 20 entry points, 35 one-hop regions (11 of them file scopes,
which are containers) and 9 two-hop regions.

## Downstream check: fan-out unbounded, all 14 vscode dev tasks

The only variant with one-hop gains, run through the full scorer
(`scripts/fanout_run.py`, `fanout-unbounded-vscode-dev.json`), against frozen
`typescript-v2`:

| Metric (vscode dev mean) | bfs-v2 | fan-out unbounded |
| --- | ---: | ---: |
| `entry_recall@20` | 0.044 | 0.044 |
| `routed_coverage` | 0.119 | 0.099 |
| `span_recall@256 / @1024 / @4096` | 0 / 0 / 0.024 | 0 / 0 / 0.024 |
| `line_recall@1024` | 0.0141 | 0.0148 |
| `line_recall@4096` | 0.0418 | 0.0418 |
| fan-out truncations (sum) | 208 | 0 |
| task time, median s | 1.82 | 1.75 |
| peak RSS, MB | 1,229 | 1,249 |

- Packed recall: one task (`136347`) gains 1 line at 1,024 units; nothing else moves.
- Displacement: `routed_coverage` falls 0.119 → 0.099. `136347` gains (0.44 → 0.67) and
  `135197` loses everything (0.5 → 0), because the wider neighbor lists spend the same
  64 regions elsewhere. Stops move
  from `depth`/`regions` to `edges` (7 of 14 tasks).
- Cost: no measurable change at a 64-region cap. Removing the region cap as well costs
  up to 8,036 regions per route, unbounded in practice.

## Verdict

**Negative; keep `oxide-router-bfs-v2`.** No bounded variant improves packed context on
dev, and the best upper bound (+3 eligible spans, one task) is below what the dev split
can resolve: one task in 26 moving by its whole share shifts a macro mean by ≈ 0.013,
under the 0.02 tolerance (*inferred* from the task count). Raising caps buys two-hop
reach that selection discards, and redistributing the fixed caps moves no gold.

The routing gain after resolution is structural reachability, not context quality.
Reopen routing only together with a selection policy that can use depth-2 or routed
file evidence (a selection change, preregistered separately), or once entry retrieval
places gold near the entry points: on vscode dev only 4.4 % of gold is an entry
within the top 20.

## Reproduce

From the repository root, with TypeScript v2 corpora fetched:

    uv run python docs/phase-6/router-reachability/scripts/route_probe.py OUT.json
    uv run python docs/phase-6/router-reachability/scripts/probe_sum.py OUT.json
    uv run python docs/phase-6/router-reachability/scripts/fanout_run.py 1000000000 OUTDIR --only microsoft__vscode
