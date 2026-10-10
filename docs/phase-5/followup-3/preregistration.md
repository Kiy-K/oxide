# Phase 5 follow-up 3 preregistration: routing budget

Frozen 2026-10-10, before any measurement of the configurations below. It
builds on follow-up 2 (`f20d1e3`) and changes none of its artifacts. Its
`lexical` verdict stays undecided. The calibration and test splits are not
used. Changing this file after results exist needs a new, dated version.

## Known before freezing

From follow-ups 1 and 2 (measured): every route loads exactly 64 regions,
and of 37 unrouted dev gold symbols, 5 are one hop from an entry point, 14
are two hops away and 18 are farther. From the code (read, not measured):
the 64-node graph cuts any routed candidate beyond 64, and the heuristic
ranks every lexical entry point (0.5–0.85) above any routed-only candidate
(0.45 / 0.3). So more routed candidates may not change a heuristic bundle.
That is the question here: whether extra candidates become usable context,
or whether selection or packing is the bottleneck.

## Configurations

Dev split only (14 tasks), offline, no JEV call. Budget N ∈ {64, 96, 128}
sets both `max_regions` and the graph's `max_nodes` to N. Everything else
is the Phase 4 baseline (depth 2, fanout 16, 2,048 edges, 20 entry points,
selection policy, 256-judgment allowance). N = 64 is the baseline and must
reproduce it exactly. The edge bound is kept; when it stops a route, that
is reported.

Context budgets: 256, 1,024 and 4,096 units, the same for every N.

Judges:
- **heuristic**: the offline default.
- **oracle** (evaluation only, never a product path): relevance and
  neighbor value 1.0 for a verified gold symbol, 0.5 for other verified
  gold (files), 0.0 otherwise, with confidence 1.0. Routing stays unjudged,
  so the route is the heuristic's. The oracle goes through the real
  selection and packing, so it is the packing ceiling for that candidate
  set.

## Measures, per task and N

- Routed gold-symbol recall: share of verified gold symbols routed.
- Gold symbols in the graph, planned, and fully packed (heuristic and
  oracle), at each context budget; fully packed recall (Phase 4 metric).
- Routing work: regions loaded, edges examined, maximum depth reached, the
  stopping bound; route latency (one timed route) and end-to-end heuristic
  latency.
- Memory: graph nodes, stored edges and rendered capsule bytes per
  request; process peak RSS is reported once (it is per process, not per
  configuration).
- Estimated JEV load: relevance capsules (graph nodes) plus the heuristic
  run's expansion capsules. Input tokens are estimated from the rendered
  request bytes at the recorded tokens-per-byte rate of the Phase 5
  sessions; cost at `USD_PER_INPUT_TOKEN`. Added latency at concurrency 16
  is estimated as extra 16-request waves × the recorded p50 round trip
  (306 ms), inferred, not measured.

## Decision rule

The default changes from 64 only if some N, against 64 at 1,024 units,
improves heuristic fully packed recall by a mean ≥ +0.02 with a 95%
task-bootstrap lower bound > 0, and its end-to-end heuristic p50 latency
is ≤ 1.5× the baseline's. The smallest such N is chosen. A gain visible
only under the oracle does not change the default: it is reported as
headroom that a judge (JEV) could reach, with its estimated cost. Its
JEV realization is not measured offline. With 14 tasks this rule detects
only large effects; a non-pass means "no gain detected".

Bottleneck reading, per N against 64: routed gain without a graph gain
means graph truncation; graph gain without oracle-packed gain means the
context budget (packing); an oracle-packed gain without a heuristic gain
means selection.
