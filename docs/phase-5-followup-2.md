# Phase 5 follow-up 2: routing load order and JEV stability

Status: done (2026-10-10) on `rewrite/v2`, after `4582f6d`. No live JEV
call, no model training. No routing change was accepted (no gain
detected; the dev gate is underpowered, see below): the router stays
`oxide-router-bfs-v1`, and no Phase 3/4/5 artifact changed. Protocol,
frozen before any measurement:
[phase-5/followup-2/preregistration.md](phase-5/followup-2/preregistration.md).
Results: `phase-5/followup-2/{routing,stability}.json`.

## Routing: no gain detected from load order (underpowered)

The baseline is level-synchronous BFS. Layer 0 loads the 20 entry points,
then layer 1 uses the remaining 44 regions in discovery order, so it
expands only the first few entry points. The hypothesis was that spending
the same 64 regions differently would route more gold. Dev, heuristic,
1,024 units, 14 tasks. Every variant used the same limits, entry points,
graph and selection:

| Variant (layer load order) | Routed gold coverage | vs `bfs` [95% CI] | Packed recall | Route p50 / p95 |
| --- | --- | --- | --- | --- |
| `bfs` (baseline, discovery order) | 0.583 | — | 0.200 | 161 / 183 ms |
| `balanced` (round-robin over entry points) | 0.488 | −0.095 [−0.267, 0.010] | 0.200 | 152 / 195 ms |
| `lexical` (top-200 lexical hits first) | 0.588 | +0.004 [−0.077, 0.080] | 0.200 | 201 / 277 ms |
| `lexical+balanced` | 0.492 | −0.091 [−0.257, 0.018] | 0.200 | 175 / 253 ms |
| `lexical_only=64` (equal-work lexical) | 0.471 | −0.113 [−0.288, 0.033] | 0.200 | 154 / 224 ms |

All variants loaded 64 regions (edges examined 687–723; lexical-only 419).
None met the gate (mean ≥ +0.05, lower bound > 0), so calibration and
test were not run. The gate is underpowered at 14 tasks: interval
half-widths are about 0.08, so even a true +0.05 gain would have a lower
bound near −0.03 and fail. Read the result as "no gain detected", not
"no gain". `lexical` is undecided (+0.004, interval up to +0.080).
`balanced` and `lexical+balanced` lean negative, but their intervals
also include 0. Ruling `lexical` out would take a new, dated
preregistration with a pooled dev + calibration comparison. The current
protocol confirms only dev-accepted variants on calibration. Route latency is from one timed route per task under
a 200% CPU quota; the lexical-first request makes `lexical` about 1.25×
slower at p50, inside the 1.5× limit. Packed recall was identical in every
task (measured). The likely reason is in the code (inferred): the
heuristic ranks every lexical entry point (0.5–0.85) above any routed-only
candidate (0.45 / 0.3). Routing order can only help a judge that reads
routed candidates, and JEV answers for new capsules don't exist offline.

**Root cause: depth, not allocation (measured on dev, 37 unrouted gold
symbols; reachability with region, edge and fanout work unbounded):**
- only 5 are one hop from an entry point, 14 are two hops away, and 18
  are not reachable within two hops at all;
- level-synchronous BFS spends the whole 64-region budget on layers 0
  and 1, so no layer reordering can reach depth 2;
- the two-hop paths vary: same-class sibling (4), member of a called
  class (4), import chains (3), other (3). They start from entry ranks
  2–17, so they don't cluster on a few entry points;
- spreading layer 1 across entry points (`balanced`) lowered mean
  coverage, though its interval includes 0.

Reaching depth 2 within the same budget means choosing which depth-1
regions to expand. The heuristic has no signal for that: it values every
branch 0.5. The designed signal is a branch judge, and its effect can't be
measured offline. No single structural path is common enough on its own
to justify a hand-written navigation rule (14 symbols over 4+ path
shapes).

So the measured lever is still the budget size, not its order. Raising it
trades JEV requests and latency for recall (Phase 5 follow-up, "What
would move recall"), and needs its own capped experiment.

The rejected knobs (`RoutePolicy::balance`, `lexical_first`) and the
Part A harness are kept as
[phase-5/followup-2/routing-order.patch](phase-5/followup-2/routing-order.patch),
not in the kernel. `git apply` it on this commit to reproduce. It
applies cleanly and reproduces `routing.json` byte for byte from the
per-task cache. That cache is keyed by task and variant label only, so
clear `OXIDE_DECISIONBENCH_DATA/followup-2/routing/` after any router
edit.

## JEV stability (recorded answers only)

Harness: `runtime/tests/phase5_stability.rs` (opt-in, machine-local).
Value is `score / 2`. Selection includes a candidate at value ≥ 0.4, by
value, within the budget.

**Drift (B1).** 291 request bodies were answered 2–4 times: the 100
Phase 5 repeat capsules, plus the smoke run, which re-sent 211 Phase 5
capsules a day later. Over their 384 answer pairs:
- |Δ value| had p50 0.01, p90 0.035, p99 0.065 and max 0.09; 373 of 384
  pairs were within 0.05;
- |Δ confidence| had p50 0.02, p90 0.07 and max 0.22;
- only 30 of 291 bodies got identical answers every time, and 7 had
  answers on both sides of the 0.4 floor.

Same-session repeats (148 pairs) and day-apart answers (236 pairs) drift
about equally: max 0.085 and 0.09.

**Ranking (B2) and observed selection (B3).** 3 smoke tasks, 64 shared
relevance capsules each:

| Task | Kendall τ-b | Top-10 overlap | Floor flips | Plan / item Jaccard | Packed recall P5 → smoke |
| --- | --- | --- | --- | --- | --- |
| sympy `17c22a6f` | 0.918 | 10/10 | 0 | 0.78 / 0.78 | 0 → 0 |
| sphinx `b8417a37` | 0.936 | 10/10 | 3 | 1.0 / 1.0 | 0.167 → 0.167 |
| keras `3f3ff585` | 0.929 | 10/10 | 1 | 1.0 / 1.0 | 1.0 → 1.0 |

Rankings stay close (τ-b 0.92–0.94). One task's plan set changed, with no
recall change, and floor flips changed no plan. Three tasks are a small
sample. Top-10 overlap breaks ties by capsule order, and values are
quantized to 0.005, so it can overstate agreement at the rank-10
boundary.

**Simulated selection (B4).** All 52 tasks, 20 replicates each, every JEV
value shifted by a recorded drift delta:

| Outcome per replicate (1,040) | Count |
| --- | --- |
| plan identical | 237 (23%) |
| same plan set, reordered | 357 (34%) |
| plan set changed | 446 (43%) |
| fully packed recall changed | 94 (9%): 66 down, 28 up |

- Recall changed in 14 of 52 tasks.
- Mean recall change: −0.006 [−0.013, −0.0001].
- Mean plan Jaccard 0.86; bundle item Jaccard 0.80.
- 86 of the 94 recall changes came with no change in unrecorded-capsule
  fallbacks (per-task cache), so they are not a replay artifact.

My reading (inferred): drift-sized noise added to an already-noisy judge
weakens its ranking a little, which is why losses outnumber gains.

**Stability verdict.** By the preregistered definition, 57% of the
simulated replicates were harmless (plan set unchanged) and 43% were
consequential (set changed). Recall changed in 9%, with a mean of
−0.006. For scale, JEV's measured gain over the heuristic is +0.078
packed recall (Phase 5 follow-up, not re-measured here), so drift costs
about a twelfth of it (inferred). Live JEV bundles are not reproducible
from run to run; replays of recorded answers are. No change was made:
averaging repeated calls would multiply cost for an effect of about
0.006.

## Limitations

- Routing gates use routed coverage and heuristic recall only. Whether
  JEV would turn routed gold into packed gold for a new route needs live
  calls, so it is not measured here.
- The root-cause paths come from dev (37 unrouted symbols). The Phase 5
  follow-up waterfall covers all splits.
- B4 assumes drift is independent per capsule, drawn from 384 pairs: 236
  day-apart pairs (smoke run of 3 tasks vs Phase 5) and 148 same-session
  pairs (100 dev capsules). The B4 replay uses Phase 5 answers first; for the 3
  smoke tasks it can also serve smoke-only answers, which it never needs
  (all 211 smoke bodies also have Phase 5 answers).
- In `lexical+balanced`, lexical hits also advance their entry point's
  round-robin turn, so the remainder is not exactly the preregistered
  round-robin. The variant was rejected either way.
