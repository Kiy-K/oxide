# JEV Operational Reliability Study: preregistration (2026-10-24)

Frozen before any call of this study. It measures **provider behaviour only**: how often
JEV fails, how fast, in what pattern, under what concurrency, and what bounded retry or a
circuit breaker would buy within the existing deadline. It measures no ranking quality.
Nothing in `src/` changes, no judge is integrated, and no ADR is written, whatever the
verdict.

## What stays fixed

- Decision Study v2's verdict stays **INCONCLUSIVE**. Its held-out split (29 xarray/pytest
  tasks) is consumed: no held-out request is sent again, and nothing here is evidence
  about its quality result.
- The judge does not change. Each call is v2's primary body, byte for byte (JEV
  `jev-1.14.0`, `ds1-compact-v1` renderer with rank, capsules in digest order, v1's
  `QUESTION`/`CRITERIA`), built by v1's `adapter.py`. The mapping, floor, selector and
  packing are untouched and are not run.
- Production's `DEADLINE` (2.0 s, `src/oxide/core/capsule.py`) is the deadline every policy
  is judged against. Changing it is an ADR decision and out of scope.

## Disclosure (known before the freeze)

- v2 held-out calls, sequential (concurrency 1): 29 of 116 first attempts (10 of 29 in
  the primary arm) got HTTP 529 `system_overloaded`. The 529s took 0.72–1.23 s of client
  wall time, versus a median of about 1.1 s for answers. The v2 dev run an hour earlier
  had 0 of 60. In call order, failures often came in pairs (not tested).
- TypeSafe's docs (`/api/errors`, read 2026-10-24) say a 529 means "temporarily
  overloaded. Retry after a short delay", and recommend exponential backoff rather than
  an immediate retry. The SDK default is 2 retries with a 0.5 s initial backoff (±0.25 s
  jitter), with `Retry-After` respected. *Inferred*: a 0.5 s backoff after a ~0.8 s
  failure, followed by a ~1.0 s answer, cannot fit 2.0 s, so the only retry that can fit
  is an immediate one. Its effectiveness under correlated overload is exactly what is
  measured.
- `probe.py selfcheck` (localhost) measured the child-process client overhead at
  0.035–0.039 s per attempt, so a ~0.8 s 529 is provider time.
- `analyze.py` was exercised on a synthetic trace (not on JEV data), and R3 was set
  before any real call.

## Payload and authorization

The user authorized, for this study only, resending the **15 v2 dev request bodies** (the
9 public dev corpora already sent in v1/v2; `probe.py` asserts each corpus against this
list) under a **$0.40 cap** of billed spend, kept in a ledger outside the repository.
Attempts carry only status, times, headers and usage. Response bodies are not stored.

## Protocol (`scripts/probe.py`)

- **8 windows**, started about 40 minutes apart (about 5 hours in total), to sample
  provider load over time. Each window runs three blocks in order: concurrency 1, 2 and
  4, with 17 logical requests each, for 51 per window and **408 logical requests** in
  total. Requests cycle through the 15 dev bodies in a per-window seeded shuffle.
- Each logical request makes a first attempt. If that attempt is retryable (429, any
  5xx, a transport error, or killed), it is retried **once, immediately**, whatever time
  remains. That way the retry outcome is observed for every failure, and the deadline
  gate below is applied afterwards on the recorded times. Calls are killed at 60 s so
  that true tails are observed.
- Each attempt records its status, client wall time, network time inside the child,
  start offset, UTC wall clock, `Retry-After` and request-id headers if present, and billed
  input tokens.
- The spend cap stops sending. A request not sent because of the cap is excluded and
  reported.
- *Inferred, stated as a limit*: these calls are back to back, which is denser than an
  agent's real traffic. Ungated measurement retries add a little load that the policy
  itself would not.

## Policies evaluated (`scripts/analyze.py`, same logical requests, paired)

- **fallback** (provisional policy): one attempt. Any failure, or no answer by 2.0 s,
  falls back to the deterministic heuristic at once.
- **retry**: as fallback, except that a retryable failure is retried immediately once,
  only if at least **1.0 s** of the 2.0 s deadline remains (`NEED`, about v2's median
  answer latency). The retry counts only if its answer lands by 2.0 s from the first
  attempt's start. Time to decision is capped at 2.0 s.
- **breaker**: fallback plus a circuit breaker. After **3 consecutive** failed logical
  requests it opens for **15 s**, and requests during that time fall back without a
  call. The next request is a half-open probe: success closes the breaker, failure
  reopens it. It is simulated per window in start order, using the observed outcome of
  each request it would skip.

## Acceptance criteria

Every threshold below is fixed here and in `analyze.py`.

**Retry (R)**: the bounded retry is *recommended* only if R0–R4 all pass. Otherwise
immediate fallback stays.

- **R0** ≥ 20 retryable first-attempt failures; otherwise the retry comparison is
  inconclusive.
- **R1** Availability gain (retry − fallback) ≥ +0.03, with the circular block-bootstrap
  95% CI (block 10, 5,000 reps) lower bound > 0.
- **R2** Time to decision: p95 rises by ≤ 0.5 s, and p99 ≤ 2.0 s.
- **R3** Amplification: calls per logical request ≤ 1.25 pooled and ≤ 1.35 at
  concurrency 4.
- **R4** Cost: billed $ per answered request ≤ 1.05 × fallback.

**Breaker (B)**: *recommended* only if B0 and B1 pass.

- **B0** Failures are bursty: adjacent first-attempt failure pairs within windows exceed
  chance (within-window permutation, 10,000 perms, p < 0.05), and R0 holds.
- **B1** The breaker skips ≥ 20% of the calls that fail under fallback, while losing ≤ 1%
  of logical requests that would have been answered.

**Operational (O)**: judged under the recommended policy (retry if R passes, else
fallback), pooled over all windows and concurrencies.

- **O0** ≥ 300 logical requests evaluated.
- **O1** Fail rate (no answer by 2.0 s) ≤ 0.10, with Wilson 95% upper bound ≤ 0.15. This
  is v2's H7 threshold, with an interval.
- **O1w** Every window's fail rate ≤ 0.25.
- **O2** Deadline hits (answers slower than 2.0 s) ≤ 5% of requests, and median time to
  decision ≤ 1.2 s.
- **O3** No malformed or wrong-model reply.

**Verdict**:

- **PASS**: all O criteria pass.
- **FAIL**: the better of fallback and retry still fails more than 10% of requests, or
  any reply is malformed or from the wrong model.
- **INCONCLUSIVE**: anything else.

Concurrency effects (fail rates and answer latency at c = 1, 2, 4, with Wilson
intervals), per-window rates, latency distributions (p50/p90/p95/p99/max for answers and
for 529s) and `Retry-After` values are reported, not gated.

## Afterwards

- On **PASS**: propose (not run) a separate confirmatory quality study on **new**
  held-out tasks. The consumed 29 are never reused as fresh evidence.
- On **FAIL** or **INCONCLUSIVE**: report it. Availability then stays the blocker for any
  ADR.
- Either way: no production integration, no ADR, no change to the judge's ranking policy
  or the selection semantics.
