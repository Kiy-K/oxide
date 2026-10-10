# Phase 5 follow-up 3: routing budget

Status: done (2026-10-10) on `rewrite/v2`, after `f20d1e3`. Dev split only
(14 tasks; only the JEV tokens-per-byte rate pools all recorded sessions),
offline, no JEV call, no model training. The 64-region default
is kept. No Phase 3/4/5 artifact changed. Follow-up 2's `lexical` verdict
stays undecided. Protocol, frozen before any measurement:
[phase-5/followup-3/preregistration.md](phase-5/followup-3/preregistration.md).
Results: `phase-5/followup-3/budget.json`, from
`runtime/tests/phase5_budget.rs` (opt-in, machine-local).

Budget N sets both the router's `max_regions` and the graph's `max_nodes`.
The oracle is an evaluation-only judge: it scores gold 1.0 (symbols) or 0.5
(files) and everything else 0. It runs through the real selection and
packing, on the heuristic's route.

## Result: more candidates are usable, but the heuristic cannot select them

60 verified gold symbols over the 14 dev tasks:

| N | Routed gold symbols | Heuristic recall @256 / 1,024 / 4,096 | Oracle recall @1,024 (vs 64) | Oracle recall @4,096 (vs 64) |
| --- | --- | --- | --- | --- |
| 64 (baseline) | 0.383 | 0.012 / 0.200 / 0.312 | 0.312 | 0.555 |
| 96 | 0.450 | 0.012 / 0.200 / 0.312 | 0.338 (+0.026 [0, 0.067]) | 0.581 (+0.026 [0, 0.067]) |
| 128 | 0.517 | 0.012 / 0.200 / 0.312 | 0.389 (+0.077 [0.014, 0.151]) | 0.655 (+0.101 [0.014, 0.211]) |

Recall is fully packed recall (Phase 4 metric, all verified gold), as a
mean over tasks with a 95% task-bootstrap interval.

- **The extra budget reaches depth 2 (measured).** At 128 regions every
  task explores depth 2. One task (sphinx `b8417a37`) runs out of frontier
  at 120 regions; the others stop at the region bound. At 64, three tasks
  never get past depth 1; at 96, one. Since `max_nodes` equals N, the
  graph keeps every routed candidate.
- **Heuristic gold recall, and gold planned and packed, are identical at
  every N and context budget (measured: paired difference 0.0 on all 14
  tasks).** The heuristic plans
  lexical entry points (0.5–0.85) before any routed-only candidate (0.45 /
  0.3), so the new candidates never enter a bundle (inferred from the
  code). **Selection is the bottleneck for the heuristic.**
- **For a judge that can rank them, the new candidates are usable
  context.** The oracle's packed gold rises with N. A small part comes
  from selection's neighbor expansion, which can add gold that is not in
  the graph (one task at N = 128). The oracle never fell back to the
  heuristic. At 4,096 units the
  oracle packs every gold symbol it plans (0.383 / 0.450 / 0.533 at N = 64
  / 96 / 128), so packing is not the limit there. At 1,024 units packing
  also binds: the oracle plans 0.533 of gold symbols at N = 128 but packs
  0.383. At 256 units almost nothing fits at any N.

## Cost

Per context, at 1,024 units:

| N | Regions / edges examined | Graph edges stored | Route p50 | Heuristic context p50 | Est. JEV requests | Est. JEV input tokens | Est. JEV cost | Est. added JEV latency at C-16 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 64 | 64 / 693 | 901 | 158 ms | 317 ms | 68 | 178k | $0.0075 | — |
| 96 | 96 / 971 | 1,266 | 212 ms | 452 ms | 100 (+32) | 258k | $0.0108 | +0.6 s |
| 128 | 127 / 1,234 | 1,616 | 292 ms | 599 ms | 131 (+63) | 333k | $0.0140 | +1.2 s |

- The JEV estimates count the relevance and expansion capsules the
  heuristic run would send, and convert rendered request bytes to tokens at
  the recorded rate (0.438 tokens per byte, pooled over every Phase 5
  session, all splits and question kinds). Added latency
  is extra 16-request waves × the recorded 306 ms p50 round trip
  (inferred). Expansion under JEV can differ from the heuristic's.
- Latencies are one run per task under a 200% CPU quota, with N = 64
  timed first on each fresh store (order-biased against 64).
- Memory, in-request: graph edges grow from 901 to 1,616 per request (1.8×
  at N = 128). The process peaked at 3.4 GiB RSS, dominated by indexing; it
  is per process, not per configuration.
- The 2,048-edge bound was never the stopping bound.

## Decision

No N passes the preregistered rule: heuristic recall gain +0.000. Even
ignoring that, the heuristic context p50 at 128 is 1.9× the baseline,
above the 1.5× limit (96: 1.43×). **The 64-region default is kept.**

The measured headroom is real but only reachable with a judge. At N = 128,
an oracle gains +0.077 packed recall at 1,024 units, for about 63 more JEV
requests (+$0.0065, about +1.2 s at C-16) per context. Whether JEV
realizes any of that gain needs live calls, so it is not measured.

## Limitations

- 14 dev tasks and 60 gold symbols: only large effects are detectable.
  The 96-region oracle gain (+0.026, interval touching 0) is undecided.
- The oracle is a ceiling for this candidate set and selection policy,
  not an achievable judge.
- Calibration and test were not used, except in the pooled
  tokens-per-byte rate.
- The per-task cache is keyed by the preregistration and the retrieval,
  router and selector versions.
