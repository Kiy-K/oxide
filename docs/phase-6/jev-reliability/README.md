# JEV Operational Reliability Study (2026-10-24): PASS (under fallback)

Provider behaviour only. No ranking quality is measured, nothing in `src/` changed, the
judge's ranking policy and selection semantics are untouched, and no ADR is written.
Decision Study v2 stays **INCONCLUSIVE**. Its consumed held-out split was not resent and
is not reinterpreted here.

- Preregistration: [`preregistration.md`](preregistration.md), frozen in `1deec3b`
  before any call.
- Scripts: [`scripts/probe.py`](scripts/probe.py) sends the calls;
  [`scripts/analyze.py`](scripts/analyze.py) analyses them offline.
- Full gate output: [`results/gate.txt`](results/gate.txt).
- Raw attempts (status, times and headers only, no bodies) and the spend ledger are
  outside the repository, in `~/Projects/oxide-eval-data/oxide-jev-reliability/`.

## Run (measured)

- 8 windows, 40 minutes apart, from 05:24 to 10:05 UTC (12:24 to 17:05 in Vietnam).
- Each window ran 17 logical requests at each of concurrency 1, 2 and 4: 408 logical
  requests in total, none blocked by the cost cap.
- Payload: v2's 15 dev request bodies (public dev corpora only, asserted in `probe.py`),
  at about 15.2k input tokens per call.
- Billed spend: **$0.2597** of the $0.40 cap ($0.000638 per answer).

## Results (measured)

| | c=1 | c=2 | c=4 | pooled |
|---|---|---|---|---|
| First-attempt failures | 0/136 | 0/136 | 0/136 | **0/408** (Wilson 95% upper bound 0.009) |
| Answer latency p50 / p95, s | 0.92 / 1.13 | 0.83 / 1.02 | 0.89 / 1.13 | 0.90 / 1.03 |

- Every one of the 408 attempts returned HTTP 200 from `jev-1.14.0`, with no malformed
  or wrong-model reply. There was no 529, no 429, no 5xx and no transport error, in any
  window or at any concurrency.
- Latency across all attempts, client wall time, p50 / p90 / p95 / p99 / max:
  0.90 / 1.02 / 1.03 / 1.23 / **2.14** s. Network time at p50 was 0.85 s; the child
  process accounts for the remaining ~0.04 s.
- Concurrency up to 4 changed neither the failure rate nor latency.
- **One deadline miss**: window 1, c=4, `mwaskom__seaborn-3069`, answered in 2.14 s
  (2.10 s of it network time) at 05:24:38 UTC. Under the 2.0 s deadline this counts as a
  failure. That gives a fail rate of 1/408 = 0.002 (95% CI 0.000–0.014) under every
  policy, and a median time to decision of 0.90 s.
- Response headers: only `date` is sent. There is no `Retry-After` and no request id,
  so client-side backoff cannot be tuned from server hints.

## Gates

| Gate | Result |
|---|---|
| O0 ≥ 300 requests | PASS (408) |
| O1 fail ≤ 0.10, Wilson high ≤ 0.15 | PASS (0.002, high 0.014) |
| O1w every window ≤ 0.25 | PASS (worst 1/51) |
| O2 deadline hits ≤ 0.05, ttd p50 ≤ 1.2 s | PASS (0.002, 0.90 s) |
| O3 no malformed or wrong-model reply | PASS |
| R0 ≥ 20 retryable failures | FAIL (0), so R1 is FAIL as well |
| R2–R4 latency, amplification, cost | PASS (trivially, since no retry was ever triggered) |
| B0 bursty failures / B1 breaker benefit | FAIL (no failures) |

**Operational verdict: PASS**, judged under the provisional policy (immediate
deterministic fallback, no retry).

## Recommendations

- **Retry: not recommended, keep immediate fallback.** No retryable failure occurred, so
  this run cannot show that a retry helps. *Inferred* from v2's 529s (0.72–1.23 s) and
  v2's median answer (~1.0 s): a retry after a typical 529 often lacks the 1.0 s it
  needs, and TypeSafe's docs advise backoff rather than an immediate retry, which cannot
  fit inside 2.0 s anyway.
- **Circuit breaker: not recommended now.** No failures occurred, so there were no
  bursts to break on. The v2 failure pairs remain untested. If outages like v2's recur,
  K=3 consecutive failures with a 15 s open period is the policy to re-test, using
  `analyze.py` unchanged.

## Limits

- **This run does not show that 529 outages are rare.** Five hours on one day were
  clean. v2's held-out run, earlier the same morning (v2 committed 04:57 UTC), saw 25% 529s. So
  overload is episodic, and this run sampled no episode. PASS means "operationally
  acceptable in the windows measured", not "the v2 outage rate was an anomaly". The
  windows sampled were 05:24 to 10:05 UTC on a single day.
- The calls were back to back, at concurrency ≤ 4, with dev bodies only (all about
  15k tokens). Larger payloads or higher concurrency were not measured.
- The deadline miss was a single slow answer. One case can't show whether slow answers
  have a tail.

## Next (proposed, not run)

Because reliability passed, the preregistration allows a **separate confirmatory quality
study**. It would need:

- **New** held-out tasks from corpora not used in v1 or v2. The consumed 29
  xarray/pytest tasks are never reused as fresh evidence.
- v2's primary arm and ranking policy, unchanged.
- Its own preregistration, its own spend authorization, and the same immediate-fallback
  policy.
- Because outages are episodic, its own 529s should be counted as fallbacks and reported
  next to the quality result, not rerun until they pass.

Any ADR or production integration remains a later, separate decision.
