# graph-v1 preregistration: graph algorithms for selection and regions

Frozen 2026-10-23, before any D or P arm or any TypeScript community number was computed.
Code at this freeze: the `oxide-python-v4` working tree (baseline-v12 / typescript-v9 are
the controls). Held-out tasks are **not** used by either study; only dev splits run.

## Disclosure

Before this file was written, two smoke runs exposed dev data:

- `ppr_run.py py B` (arm B, the unpatched control), which reproduced baseline-v12 dev
  results byte-for-byte. No D or P number was seen.
- `community_probe.py py` on Python dev: community share 0.700, directory share 0.644,
  mean region 8.6 vs 12.4 files (15 tasks). The gate below was chosen before that run;
  it is recorded unchanged, and this Python result counts as seen, not as confirmation.

## Study 1: Personalized PageRank value for routed-only symbols

Information hypothesis: a routed symbol strongly connected to several high-ranked entry
points is more likely gold than one reached through a single edge. Decision: which
routed-only symbols clear `INCLUDE_FLOOR` and in which order. Today only depth-1 routed
symbols can be packed (0.45) and depth-2 never (0.3); the router-reachability study
(`docs/phase-6/router-reachability/`) showed routing gains reach only when selection can
use them.

Arms (`scripts/ppr_run.py`), each on Python dev (15 verified tasks) and TypeScript dev
(26 verified tasks):

- **B**: unpatched. Must equal the frozen dev results; otherwise the study is void.
- **D**: routed-only symbol value `0.3 + 0.2 q`, `q` its percentile by (depth, key). It
  admits the top half of routed-only symbols at every depth: the "more routed symbols"
  control.
- **P**: same formula, `q` by Personalized PageRank (damping 0.85, 50 iterations,
  restart at entry points weighted 1/rank, undirected routed subgraph over `contains`
  and the router's followed relations).

Primary metric: macro `line_recall@4096` over verified gold; secondary `line_recall@1024`,
`span_recall@4096`, `gold_unit_share@4096` (noise proxy). Tolerance: 0.02 (one task's
whole share moves a 26-task mean by about 0.013 to 0.04).

Verdict, per language and then combined:

- **ADVANCE to a held-out confirmation** (a separate, later study) only if, in both
  languages, P − D ≥ +0.02 and P − B ≥ +0.02 on `line_recall@4096`, no budget's
  `line_recall` falls more than 0.01 below B, and `gold_unit_share@4096` is not more than
  0.01 below B.
- **REJECT** PPR if P − D < +0.02 in either language: the graph ranking adds nothing over
  admitting more routed symbols in depth order. Report D − B separately as evidence about
  the floor, not as a promotion.
- **INCONCLUSIVE** if every difference is within one task's share and no gate is met.

Cost to report: added wall time per task (one adjacency read plus 50 iterations over
≤ 64 regions' members).

## Study 2: community detection as structural regions

Information hypothesis: Louvain/Leiden communities of the resolved file graph group
files that change together better than directories do, so they could serve as TreeIndex
regions or localization units. Decision (future): region membership for routing.

Probe (`scripts/community_probe.py`): per dev task, the anchor is the file of the top-1
search hit; for each gold file, whether it shares the anchor's community and the
anchor's directory. Louvain (networkx 3.5, seed 0, resolution 1) stands in for Leiden,
which refines Louvain partitions; LadybugDB's native Louvain is unavailable at the pin
(README § LadybugDB algo extension).

Verdict: worth building only if, on both Python and TypeScript dev, mean community
share − directory share ≥ +0.10 **and** mean community size ≤ mean directory size (in
files). Otherwise REJECT for this release; no implementation in `src/`.
