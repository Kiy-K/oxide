# Phase 5 follow-up — JEV orchestration latency and routing recall loss

Status: done (2026-10-10) on `rewrite/v2`, after Phase 5 (`1de6429`). No
model was trained and no live JEV call was made. The heuristic stays the
default judge. No Phase 3/4/5 artifact changed. Protocol, frozen before
any measurement:
[phase-5/followup/preregistration.md](phase-5/followup/preregistration.md).
Results: `phase-5/followup/{latency,selective,waterfall,routing}.json`,
from `runtime/tests/phase5_followup.rs` (opt-in, machine-local).

## JEV latency: the root cause is sequential requests

Measured on the Phase 5 live run (52 contexts, C at 256 units): p50 21.1 s
per context, with p50 66 JEV requests sent one at a time. Each added p50
316 ms, about one service round trip (p50 306 ms). The rest of the
pipeline took 0.33 s. JEV inference is not slow. OXIDE waited for each
request before sending the next.

Offline profile (1,024 units, 52 tasks; recorded answers served after
their recorded live round trips). The simulated sequential p50 is 22.4 s,
within 15% of live, so the simulation check passes.

| Per context | p50 | p95 |
| --- | --- | --- |
| retrieve | 20 ms | 88 ms |
| route | 142 ms | 176 ms |
| query graph | 127 ms | 156 ms |
| capsules, judgment application, select, pack | 47 ms | 94 ms |
| client work in the judge (request rendering, parsing, checks) | 16 ms | 20 ms |
| JEV requests | 68 | 80 |
| sequential judge calls (relevance, then neighbor rounds) | 3 | 6 |

Connection reuse was already in place: one `ureq` agent per transport,
reused across a sequential run. Retries and timeouts did not fire, so
they cost nothing. The API documents no batching across states, so none
was added.

**Change:** `Jev` sends a judge call's requests through a bounded worker
pool (`JevConfig::concurrency`, 1–16), on a shared deadline:
- requests still queued at the deadline are dropped, never sent;
- requests in flight are cut by the HTTP timeout;
- 429/529 retries run in rounds with backoff.

Admission under the request and token caps is decided in capsule order
before sending. Answers apply in capsule order, so completion order cannot
change a judgment, and recordings are written in that order. A test proves
this with reversed completion. `Http` pools up to 16 connections.
`concurrency: 1` keeps the previous results, with two documented
differences:
- the token cap reserves each body's bytes for the whole round rather
  than counting reported tokens as it goes, so it is more conservative;
- a 429 is retried after its round, not immediately.

Disclosure, caps and fallback reasons are unchanged. The latency runs had
no 429 or cap hit; the retry fix made after review does not touch that
path.

| Config | p50 | p95 | Requests | Input tokens | Cost | Fallbacks | Packed recall |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A heuristic | 0.34 s | 0.54 s | 0 | 0 | $0 | — | 0.118 |
| B JEV sequential | 22.4 s | 26.1 s | 68 | 187k | $0.0078 | 0% | 0.196 |
| C-4 | 6.4 s | 7.8 s | 68 | 187k | $0.0078 | 0% | 0.196 |
| C-8 | 3.8 s | 5.1 s | 68 | 187k | $0.0078 | 0% | 0.196 |
| C-16 | **2.6 s** | 3.8 s | 68 | 187k | $0.0078 | 0% | 0.196 |

C-k's decisions, plans and bundles were byte-identical to B's on every
task, so quality is preserved by construction. The process peaked at
3.3 GiB RSS, dominated by indexing. C-16 meets the 3 s p50 aspiration
offline (8.6× faster).

What is left at C-16:
- about 0.35 s of pipeline;
- the relevance batch, in about 5 waves of 16 round trips;
- 1–2 neighbor rounds. Each depends on the previous selection, so each
  costs at least one round trip.

The p50 context makes 3 sequential judge calls. Even with unlimited
concurrency, 3 round trips plus the pipeline leave a floor of about 1.3 s
(inferred).

The simulation replays latencies recorded one request at a time, and the
docs state no rate or concurrency limits. A capped live smoke check
followed (below).

**Live smoke check (2026-10-10, authorized).** `live_concurrency_smoke`
(opt-in, `OXIDE_FOLLOWUP_LIVE=1`) ran 3 public dev tasks at concurrency
16, one per dev repository: the task with the smallest recorded token use.
Caps were 240 requests, 750k input tokens and $0.10. Used: 211 requests,
488.6k tokens, $0.021. There were no 429s, retries, failed requests,
fallbacks or refused bodies (a guard refuses, without sending, any capsule
from outside the task's repository). Results:
`phase-5/followup/live-smoke.json`.

| Task | Live | Offline at recorded latency | Requests |
| --- | --- | --- | --- |
| keras `3f3ff585` | 2.41 s | 1.75 s | 64 |
| sphinx `b8417a37` | 2.79 s | 2.47 s | 71 |
| sympy `17c22a6f` | 3.86 s | 3.35 s | 76 |

The median context took 2.79 s live. Live runs were 0.3–0.7 s slower than
the simulation. A request's round trip was p50 312 ms, the same as one at
a time (306 ms), but p95 992 ms against 409 ms sequential: concurrency
widens the tail. The first context was the slowest (p95 992 ms against
543–566 ms for the others), likely while 16 new connections were set up
(inferred). The wait for a pool slot was p50 851 ms. Three contexts are a
smoke test, not a p95 benchmark.

Correctness held. Each live run equals a sequential replay of its own
answers, so completion order changed nothing. Bundles differ from the
frozen Phase 5 replay in all three tasks because JEV answered differently
this time: 52–59 of 64 relevance values changed, consistent with Phase 5's
13 of 100 bit-identical repeats. Packed recall was unchanged in all three.

**Rejected: selective judging (D).** Judging only the first K capsules in
graph order (the shared allowance) failed the preregistered rule on dev:

| | Packed recall vs C-8 | p50 latency |
| --- | --- | --- |
| D-16 | −0.048 [−0.128, 0.019] | 1.3 s |
| D-32 | −0.036 [−0.114, 0.029] | 1.9 s |

JEV's gain comes from re-ranking gold that the graph order puts low, so
cutting the tail loses it. No calibration confirmation ran.

## Routing recall loss: where gold disappears

All 52 tasks, frozen baseline, 1,024 units. There were 525 gold spans:

- 59 failed content verification;
- 84 map only to a file (57 verified file-level gold entities);
- 410 verified gold symbols, classified below. Routing is the same for
  both judges.

| Stage reached by gold symbols | Heuristic | JEV |
| --- | --- | --- |
| fully packed | 35 | 63 |
| packed inside an enclosing item | 10 | 22 |
| lost to budget (planned, not fully packed) | 16 | 27 |
| lost at selection (in graph, not planned) | 101 | 62 |
| never routed | 248 (60%) | 236 |
| routed but cut from the 64-node graph | 0 | 0 |

Why the 248 unrouted symbols were lost (first test that holds):

| Cause | Symbols | Share |
| --- | --- | --- |
| work limit: reachable within depth 2 of the 20 entry points, but the 64-region budget ran out first | 130 | 52% |
| entry limit: reachable only from lexical hits ranked 21–200 | 76 | 31% |
| lexical miss and graph gap: not a top-200 hit, not within depth 3 | 33 | 13% |
| depth limit: reachable at depth 3 only | 9 | 4% |

None of the unrouted gold ranks in the lexical top 20. 66 rank 21–200,
130 rank beyond 200, and 52 have no lexical hit. No reachability route
hit its 200,000-region safety bound. Per split (dev, calibration, test),
the unrouted share is 62%, 60% and 61%. The work limit is the largest
cause in dev and test; in calibration the entry limit is close behind
(26 vs 32).

So routing does not lose gold to pruning: with no branch judge the
heuristic values every branch 0.5, below no prune threshold (Phase 5,
measured). It mostly runs out of region budget: all 52 routes loaded
exactly 64 regions. Next come entry points that rank gold too low.

**Experiments (dev, heuristic, equal size, at most equal work):** no
variant was accepted.

| Variant | Routed gold coverage vs baseline (0.583) | Packed recall vs baseline |
| --- | --- | --- |
| `lexical_limit` 10 | −0.048 [−0.143, 0.024] | 0 |
| `lexical_limit` 40 | −0.088 [−0.250, 0.024] | 0 |
| `max_depth` 1 | −0.029 [−0.086, 0.000] | 0 |
| `max_depth` 3 | 0 (regions run out before depth 3) | 0 |
| `max_fanout` 8 | −0.086 [−0.252, 0.019] | 0 |
| `max_fanout` 32 | −0.095 [−0.267, 0.010] | 0 |
| lexical-only control (64 hits, depth 0) | −0.113 [−0.288, 0.033] | 0 |

At a fixed 64-region budget, every reallocation (more or fewer entry
points, wider or narrower fanout) has a lower mean coverage than the
baseline, and plain lexical top-64 has the lowest. Every interval
includes 0, though, so the measured result is "no improvement", not
"worse". Packed recall at 1,024 units was identical to the baseline's in
every dev task, for every variant. Inferred: the heuristic plans entry
points first, and those lead every variant's graph. `max_depth` 3 loaded
the same 64 regions as the baseline, which inferably means the region
budget runs out before depth 3. The routing baseline is unchanged. No
variant reached calibration confirmation.

## What would move recall (not done; needs a decision)

The measured bottleneck is the work budget itself. Up to 130 of 410 gold
symbols are within depth 2 of the current entry points. Raising
`max_regions` (and the graph's 64 nodes) is not an equal-work change. It
enlarges the candidate universe, and with it JEV requests and latency per
context: about one request per added node, about 0.3 s per 16 nodes at
C-16 (inferred). That trade (more work for recall) needs its own preregistered
experiment with cost limits.

The next lever is the 101 (heuristic) / 62 (JEV) routed gold symbols lost
at selection.

## Limitations

- Concurrent live behavior is a 3-context smoke test only (above).
- Latency was measured under a 200% CPU quota, as in Phase 5.
- Peak RSS is per process, not per configuration.
- Waterfall reachability uses resolved edges only (the router's policy).
  Gold reachable only through unresolved references counts as a graph
  gap.
