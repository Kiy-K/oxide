# ADR-0009: Optional JEV-assisted ordering of admitted context candidates

## Status

**Proposed** — 2026-10-24. This proposes how an optional JEV judge would order context
candidates, and the smallest milestone to implement it. It is not Accepted. Nothing is
implemented, no public configuration is added and the production default does not
change. Accepting it, implementing the milestone, and exposing or enabling the judge are
three separate decisions.

This is SPEC's candidate ADR-0009 ("DecisionProvider and optional JEV integration"), cut
down to the one policy the evidence supports: ordering candidates for selection. Branch
judgments inside TreeRouter, calibration heads and local judges are out of scope.

## Context

ADR-0011 § 5 keeps decision models optional and outside the engine, and names
`packing.value` as the one place a judge's estimate may enter. The pieces now in place
are as follows (all measured, `docs/phase-6/`):

| Step | What it established |
| --- | --- |
| [decision-seam](../phase-6/decision-seam/README.md) (`83c8f17`, `fef45e5`) | `DecisionProvider` behind `packing.value`; `oxide-capsule-v1` (bounded, digest-correlated); `CapsuleJudge` with one adapter call under `capsule.DEADLINE` (2.0 s); `Recorded` replay; any failure leaves the heuristic. No judge configured: byte-identical bundles, both frozen gates reproduce. The seam costs about 25 µs a request |
| [Decision Study v1](../phase-6/decision-study-v1/README.md) | **REJECT.** Used as a direct `value`, JEV's calibrated probabilities fall below `INCLUDE_FLOOR`, so recall drops (0.593 vs 0.659 @4096). Its ranking is strong (within-task AUROC 0.878 vs 0.716, about the same without rank). Compact rendering is the only one that fits on every request |
| [Decision Study v2](../phase-6/decision-study-v2/README.md) | **INCONCLUSIVE.** Preregistered hybrid (heuristic admits, JEV orders). Held-out passed every quality criterion (0.235 → 0.360 @4096), but 10 of 29 primary requests got HTTP 529 |
| [JEV reliability](../phase-6/jev-reliability/README.md) | **PASS under immediate fallback.** 0/408 first-attempt failures, 1 deadline miss (2.14 s), p50 0.90 s. Retry and breaker policies untested (no failures to test on). Outages are episodic |
| [Decision Study v3](../phase-6/decision-study-v3/README.md) | **PASS.** v2's frozen policy with the 2.0 s deadline enforced, on 52 new tasks from 8 new repositories, end to end: 0.316 → 0.476 @4096 (Δ +0.159, CI +0.103…+0.224), positive at every budget, 33 wins / 0 losses @4096, repeat Spearman 0.989 |

v3's preregistration allows exactly this step: proposing an ADR for an optional judge
adapter with that mapping. It also set out what remains open: availability, deadline
headroom, query clipping, small-budget regressions, and one model on Python only.

## Decision (proposed)

### 1. The policy is v3's, unchanged

A judged request runs this policy and nothing else. Changing any part of it is a new
ranking experiment with its own preregistration, not an implementation detail.

| Part | Fixed as validated |
| --- | --- |
| Asked set | The candidates `pack` already asks a judge about: has a source, not a container, pool order, at most `GRAPH_NODES` |
| Admission | Production's heuristic: admitted set A = asked candidates with `packing.value ≥ INCLUDE_FLOOR` (0.4). A judge never admits or excludes |
| Request | One call per `build_context`. Capsules (`oxide-capsule-v1`) of **every** asked candidate, not only A, in digest order, rendered `ds1-compact-v1` with rank (task once; a header line and an excerpt of at most 1,024 bytes per capsule). One `noul` question per capsule with v1's `QUESTION`/`CRITERIA` (`QUESTIONS_DIGEST`). JEV default context limits |
| Model | TypeSafe JEV `POST https://api.typesafe.ai/v1/systemone`, pinned `jev-1.14.0`. The reply must name that model |
| Mapping | Each `c ∈ A` gets `F + 0.59 p(c)` (in [0.40, 0.99], so A stays admitted). Every other candidate abstains and keeps its heuristic value below the floor |
| Fallback | Request level, all or nothing: unless **every** member of A has a sendable capsule and a valid `p`, the whole request uses the heuristic. Scales are never mixed within a request |
| Packing | Unchanged (`oxide-select-greedy-v4`): hinted first, value descending, ties by pool position, greedy first fit, `MAX_ITEMS` 24, prerequisite headers, reduced views, `used ≤ budget` |

This fits behind the existing seam with no change to `packing.judged`. The mapping is a
`DecisionProvider` that raises on any incomplete answer, and `judged` already turns a
raise into the heuristic for every candidate. Its per-candidate fallbacks (`missing`,
`abstained`, `invalid`) must never apply to a member of A, because that would mix JEV's
scale with the heuristic's. The mapping is deterministic policy and lives in `core`. The
network call does not (§ 3).

### 2. Operations: one bounded attempt, then production

- **Deadline.** 2.0 s (`capsule.DEADLINE`, unchanged), wall clock, measured from before
  the call starts. A reply that arrives later is never read. This ADR does not change
  `DEADLINE`. Changing it would need its own evidence and decision.
- **The transport bounds itself.** `CapsuleJudge` only stops *waiting* (a daemon
  thread), so the adapter must also kill its own I/O at the deadline. The validated
  shape is a child process killed at the deadline (v3 `call.py`). No thread, socket or
  process may outlive the request.
- **No retry and no circuit breaker.** A retry after a typical 529 (0.72–1.23 s, v2)
  cannot also fit a ~1.0 s answer in 2.0 s, and the provider sends no `Retry-After`
  (*inferred* from measured timings, reliability study). A breaker is not adopted. If
  v2-like outages recur, the reliability study names K=3 consecutive failures with a 15 s
  open period as the policy to test, under its own preregistration.
- **Latency bound.** A judged `build_context` takes at most the heuristic path's time,
  plus capsule building and rendering (median 4.7 ms, max 13.5 ms on Python dev), plus
  2.0 s. Failure costs at most the deadline. It never costs quality below production's
  packed spans.
- **Failure reporting.** Each judged request ends in exactly one outcome: `judged`, or a
  fallback from a closed set: `not_authorized` (nothing sent), `unsendable` (a member of A
  has no capsule), `deadline`, `http <status>` (including 529 and `400
  max_tokens_exceeded`), `transport`, `wrong_model`, `unparsable`, `incomplete`. A
  fallback packs the same spans and units as production. Item and omission reasons
  carry `judged`'s existing suffix, and one `degradations` entry names the outcome.
  Success and fallback counts are observable per request and never silent.

### 3. Authorization before any source leaves the machine

A judged request discloses repository source to a third party: subject excerpts,
paths, names, signatures and the clipped query, all from the snapshot's captured bytes
and within repository scope (excluded files and neighbors' source never enter a
capsule, per `oxide-capsule-v1`). Following SPEC § Security and § Uncertainty, the adapter:

- sends nothing unless the caller passes an explicit authorization naming this
  repository. An API key in the environment is a credential, not consent, and never
  enables sending by itself;
- is never constructed on the default path, in offline/local-only use, in CI or in
  tests (tests use `Recorded` and localhost servers);
- reports `not_authorized` as a fallback instead of raising, so a missing consent
  degrades the request instead of failing it.

The provider's retention of request bodies is not verified by any study here. Whoever
authorizes a repository must be told that.

### 4. Identity and reproducibility

- A judged bundle's `versions` records the policy and its identity: capsule schema,
  renderer, questions digest, provider, pinned model, deadline and retries. This is v3's
  manifest (`sha256:d9ae934c…`, v1's manifest with `deadline_s: 2.0`). Bundles built
  without a judge keep today's `versions` exactly, so the frozen baselines and versioned
  formats are untouched.
- Judgments are keyed by capsule digest. A request replays exactly through `Recorded`
  without the network: the same capsules and judgments give the same bundle. Keeping
  judgments by digest stores no source. Keeping capsules or request bodies is opt-in
  local trace export (SPEC § Observability).
- **Model-version changes.** The model is pinned, and a reply naming any other model
  is `wrong_model`, i.e. a fallback. A new JEV version, a "latest" alias, another
  provider, renderer, question or capsule version is a new policy identity. It is not
  used until it passes a preregistered confirmation like v3's. If the pinned model is
  retired, every request falls back, and the fallback is reported.

### 5. Internal and optional

The adapter is internal. It is not exported from `oxide/__init__.py`, the
`Repository.build_context` signature does not change, and the CLI and MCP server
neither construct nor mention it. It adds no public flag, environment variable,
configuration file or dependency (stdlib transport, as validated). Exposing it, its
configuration and authorization UX, and any change of default are later decisions with
their own validation. Evaluation and tests reach the judged path through an internal
entry point, as the studies do today by injecting a provider.

## Open operational limits and how this proposal treats them

| Limit (v3 and earlier) | Evidence | Treatment here |
| --- | --- | --- |
| Provider availability | v2: 529 on 10/29 requests in one morning. Reliability: 0/408 over 5 h of one day. v3: 0/52 primary, 0 529s (one ~2 min window). Outages are episodic | Immediate request-level fallback, which is production by construction. Report the per-request outcome. No retry. Breaker only after a new study. An outage costs the gain, never baseline quality |
| Deadline misses | v3 repeat: 2/52 killed at 2.01 s (3.8%, Wilson high 13%), p95 1.45 s. Reliability: max 2.14 s, p99 1.23 s. Little headroom at ~24k billed tokens | Keep 2.0 s, hard, counted as fallback. Report the miss rate. Do not raise the deadline in this ADR |
| Request size | Bounded by `GRAPH_NODES` and capsule bounds. Compact fit JEV's default context on every measured request (v1 15/15, v3 52/52). Larger pools and repositories over 3,000 files not measured | An over-limit request gets `400`, which falls back. Do not trim the request to fit: trimming changes the validated input |
| Query and excerpt truncation | The capsule clips the query at 1,024 bytes (v3: 40/52 clipped, median 1,509 bytes). Compact clips the excerpt at 1,024 bytes. v1 dev found no evidence lost. v3 *inferred* later requirements may be cut | Keep both bounds; truncation stays declared in the capsule's `truncated`. A longer clip or a full-query renderer is a new capsule/renderer version and a new study |
| Small-budget regressions | Macro Δ positive at all budgets (+0.013 @256, +0.125 @1,024, +0.159 @4,096), but 9 task-budget pairs regress (worst −0.375 @256, a task that gains +0.562 @4,096) | No budget gate. A threshold would be a new, unvalidated policy. Disclose that per-task harm at small budgets is possible. Budgets other than 256 / 1,024 / 4,096 are unmeasured |
| Model-version changes | One model, one snapshot (`jev-1.14.0`) | Pinned, wrong-model fallback, new identity → new confirmation (§ 4) |
| Uneven gain | v3 by repository: keras +0.391, langchain +0.011, yt-dlp 0. Without keras +0.117 | Report it. No per-repository policy |
| Language | Python only (all studies). TypeScript headroom was ≤ +0.058 (graph-v3) | Claims are Python-only. TypeScript (and any other language) needs its own study before being called a gain |

## What the evidence supports, and what it does not

**Supported (measured, three studies on the frozen policy):** that the ranking policy
in § 1 raises packed gold-line recall at matched budget on Python, beyond a random order
of the same admitted set, reproducibly. That is +0.124 on v1 dev (post hoc), +0.125 end
to end on v2 held-out, and +0.159 end to end on v3 new repositories. Also measured: a
fallback packs production exactly, the 2.0 s deadline can be enforced by killing a
child process, and a request costs about $0.0006 (~24k billed tokens) with p50 ≈ 1.0 s.

**Not verified (the production integration this ADR proposes):**

- Every call so far went through research scripts under `docs/phase-6/`
  (`adapter.py`, `render.py`, `call.py`, `harness.py`), not code in `src/oxide/`. A
  port must be shown to send the same bytes and pack the same bundles.
- The judged path inside `Repository.build_context`, its added latency in real use, and
  child-process lifecycle in long-running clients (the MCP server is stateless per
  request, but runs for a long time).
- Availability beyond the windows sampled (one day, at most ~5 h, concurrency ≤ 4).
- The authorization flow, key handling, and the provider's data retention.
- Budgets other than the three measured, languages other than Python, repositories over
  3,000 files, and any model other than `jev-1.14.0`.
- Downstream agent success. The studies measure context recall, not task outcomes.

## Alternatives considered

| Alternative | Why not now |
| --- | --- |
| JEV's probability as `value` directly | Rejected by v1: it drops below a floor tuned for the heuristic scale |
| Per-candidate fallback (judge some, heuristic for the rest) | Mixes two scales in one ordering. Not the validated policy |
| Retry within the deadline | No room for a second ~1 s answer after a 529; never shown to help |
| Circuit breaker | Untested (no failures to break on). Re-test only if outages recur |
| Raise `DEADLINE` | Would trade latency for availability without evidence. Separate decision |
| Send only A's capsules (fewer tokens) | Changes the validated request. A new experiment |
| Public flag or environment toggle now | No public configuration before the integration is implemented and validated |
| Default on | Discloses source and depends on a network service. Contrary to the local-first, offline default (SPEC) |

## Smallest implementation milestone (M1, not started)

Goal: show that an in-tree implementation is the validated policy. This uses offline
parity against recorded evidence, with no new ranking experiment and no new quality
claim.

1. **Mapping in `core`.** A `DecisionProvider` that applies § 1 over a `CapsuleJudge`:
   it computes A from `packing.value`, raises on any incomplete answer for A, maps
   `F + 0.59 p` and abstains elsewhere. No change to `packing.judged`, `pack` or
   `capsule`.
2. **Adapter outside `core`.** One internal, unexported module: `ds1-compact-v1`
   rendering and questions ported verbatim (asserting `QUESTIONS_DIGEST` and the v3
   manifest digest). The adapter holds the manifest, runs the stdlib transport in a
   child process killed at 2.0 s, checks the model, maps outcomes to § 2's closed set,
   and refuses without authorization (§ 3).
3. **Internal entry point.** The judged path is reachable from `build_context`'s
   internals with a provider. The public method passes none. `versions` gains the
   policy identity on judged bundles only (§ 4).
4. **Offline tests** (`mise run verify`, no network): no judge gives byte-identical
   bundles, and both frozen gates reproduce. Every fallback class (via localhost
   servers: hang, 529, wrong model, partial, late) packs production's spans, with one
   degradation. Without authorization, no connection is opened. No child process
   outlives the deadline. `Recorded` replay is deterministic.
5. **Parity with the evidence** (offline, needs `oxide-eval-data`):
   - the in-tree renderer produces the same request bodies (sha256) as v3's `call.py`
     for every v3 request;
   - replaying v3's recorded primary judgments through the in-tree path reproduces v3's
     primary arm exactly (0.0334 / 0.3026 / 0.4755);
   - replaying v2's dev judgments reproduces its dev gate.
   These sets are consumed, and that is acceptable: they check equivalence, not quality.
6. **Exit gate.** All of the above. No diff in `oxide/__init__.py`, the CLI, MCP,
   `pyproject.toml` or `uv.lock`. Every source file ≤ 500 LOC. M1 makes no live call.
   A live smoke call (public fixture only) would need its own spend and corpus
   authorization.

Out of M1: public configuration, CLI/MCP exposure, a default change, retries, a breaker,
a different deadline, other models, renderers or languages, and router branch judgments.

## Reconsider if

- the pinned model is retired or its behaviour changes, or the provider's data terms
  rule out sending source;
- a fallback-rate measurement in real use shows outages large enough that the expected
  gain no longer pays for the added latency;
- a new preregistered study changes the policy (budget gating, longer queries, another
  renderer or model), which supersedes § 1;
- a local judge reaches comparable ordering quality without disclosing source.
