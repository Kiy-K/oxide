# Phase 4 — capsules, judgments, deterministic selection and packing

Status: **complete** (2026-10-09) on `rewrite/v2`. Phase 5: [phase-5.md](phase-5.md).
Decisions stay inside SPEC, [ADR-0002](adr/0002-ladybugdb-knowledge-store.md)
and [ADR-0010](adr/0010-domain-identity-and-source-capture.md); no ADR status
changed. Linux x86_64 only, as before.

> Routing and inclusion are separate decisions. TreeRouter (Phase 3,
> unchanged) builds the candidate universe; Phase 4 turns it into a bounded,
> source-backed ContextBundle.

```text
retrieve → route (Phase 3, frozen) → CandidateGraph → CandidateCapsules
  → DecisionProvider (heuristic | replay | optional JEV) → greedy inclusion
  + bounded expansion → SelectionPlan → ContextPacker → ContextBundle
```

## What exists

| Piece | Where | Tests |
| --- | --- | --- |
| Kernel SHA-256 (capsule digests, by-digest source verification) | `kernel/src/digest.rs` | FIPS vectors; parity with the runtime's `sha2` on every captured file |
| CandidateCapsule v2, canonical rendering, digest | `kernel/src/capsule.rs` | `kernel/tests/routing_boundary.rs`, `runtime/tests/context.rs` |
| DecisionProvider: judgment correlation by digest, evidence heuristic, `Replay` fake | `kernel/src/decision.rs` | `routing_boundary.rs`, `context.rs` |
| `SourceProvider` port and verified per-request `Sources` cache | `kernel/src/source.rs`, `runtime/src/storage/` | `context.rs` |
| CandidateGraph and the end-to-end pipeline (`build_context` → `ContextRun`) | `kernel/src/context.rs` | `context.rs` |
| Greedy inclusion, prerequisites, bounded expansion → `SelectionPlan` | `kernel/src/select.rs` | `context.rs` |
| ContextPacker, budget unit, payload, `ContextBundle` | `kernel/src/pack.rs` | unit tests there, `context.rs` |
| Optional JEV adapter (request mapping, strict parsing, deadlines, replay/recording) | `runtime/src/jev.rs` | unit tests there, `context.rs` |
| Frozen context-quality baseline | `runtime/tests/phase4_eval.rs`, `docs/phase-4/baseline-v1/` | reproduces byte for byte |

The Phase 3 harness moved its gold mapping and helpers into
`runtime/tests/common/` (shared with Phase 4). Its artifacts still reproduce
byte for byte; no retrieval or routing code changed.

## CandidateCapsule v2

One type for candidate and region subjects (`Subject::Candidate`/`Region`).
Fields: version, snapshot key, subject, question; task (query cut at 2,048
bytes, `query_truncated`, digest of the whole query); entity (kind, name,
signature cut at 512 bytes, kind and name at 256, `entity_truncated`,
source ref, test facet, file coverage, physical parent); snippet (`Text` cut at 2,048 bytes at the last fitting newline,
`Absent`, or `Unavailable` when bytes are missing, mismatched or not UTF-8);
retrieval (channel evidence, seeds); route (depth, origins); relations
(at most 16, evidence spans dropped, ambiguous targets at most 8); `provenance_truncated` and
`relations_truncated`. Each provenance list holds at most 8; list items hold
at most 256 bytes.

- **Canonical rendering**: `Capsule::render` writes JSON with a fixed key
  order and an injective entity encoding. It is what JEV receives as
  `state` and what a dataset record stores. `context.rs` asserts the JEV
  state equals the rendering. `render(true)` replaces the snippet and
  signature with `"withheld"` for judges not authorized to see source.
- **Digest**: `sha256` of the full rendering. Judgments now carry
  `capsule_digest`, and `decide` rejects an answer for other content
  (`Fallback::Invalid`). Subject, question and version alone would accept a
  replayed answer for an edited capsule.
- **No labels**: the rendering's top-level keys are fixed by a test. No
  gold, inclusion, policy outcome or downstream signal is in it.
- `capsules.jsonl` in the baseline holds every capsule of the heuristic run,
  canonical form and digest, for replay and as the future dataset input.

## Decisions

`Question` gains `Necessity` (relevance, necessity, neighbor value, branch
value; never correctness). The Phase 1 neutral heuristic is replaced by
`heuristic-evidence/1`, a pure function of the capsule:

| Question | Value |
| --- | --- |
| BranchValue | 0.5 for every region, so heuristic routing is exactly unjudged routing (the Phase 3 harness still asserts it) |
| Relevance, Necessity | container (repository/module/file) 0.2; seed 0.9; lexical rank r: `0.85 − 0.025(r−1)`, at least 0.5; routed-only: depth 1 → 0.45, else 0.3 |
| NeighborValue | by the reaching relation: test of the seed 0.6, implementation link 0.55, callee 0.55, referenced definition 0.5, caller 0.45, else 0.35 |

Every fallback (no provider, unsupported, allowance exhausted, provider
failure, missing, duplicate, invalid, abstained, low confidence) now takes
this heuristic value instead of a neutral constant. Necessity has no
separate evidence yet. The baseline policy reads only Relevance and
NeighborValue.

`Replay` is the fake/replay provider: recorded judgments keyed by subject,
question and digest, answered under the recorded provider's identity.
`build_context` records every judgment a provider returns and every failed
call (`ContextRun.judgments`, `ContextRun.failures`). Feeding them back
through `Replay` rebuilds the same plan and bundle, fallback reasons
included (`recorded_judgments_replay_to_the_same_plan_and_bundle`, and the
failing-provider case in `context.rs`). Duplicate answers replay as
duplicates.

## Selection (`oxide-select-greedy-v1`)

The routed candidates become the CandidateGraph: at most 64 nodes in entry
order, then routed-only by depth, then domain order, each with up to 32
typed relations. Every node gets a Relevance capsule and decision. Then one
greedy loop runs over one pool:

1. Containers, entities without source, and values below `include_floor`
   (0.4) are excluded with a reason.
2. Take the best pending candidate. Order: value, then primaries before
   expansions, then pool order. Its **group** is the item plus a header of
   every enclosing symbol not yet shown (the declared prerequisite). Cost is
   the exact `oxide-units-v1` count. A part inside an already planned range
   (a method after its class body, a header already shown) costs only its
   name in the merged item's label, so the plan's estimate bounds the packed
   cost (the eval asserts it on every run). The
   group is taken in full if it fits the remaining budget, otherwise as its
   header view, otherwise omitted (`TooLarge` when even the header exceeds
   the whole budget). At most 24 items.
3. Expansion: a selected primary (depth < 1) reads up to 64 edges and
   follows calls (both ways), outgoing references, implements (both ways)
   and outgoing tested-by. It adds at most 4 new symbol neighbors per seed
   and 16 in total. Each neighbor gets a NeighborValue capsule and decision,
   and at or above `expand_floor` (0.4) it joins the **same pool and
   budget**. Known entities are counted as duplicates, which also ends
   cycles. Ambiguous and unresolved targets are counted, never guessed. A
   routed candidate excluded below the floor can be re-reached this way; it
   then keeps only its later outcome.

The `SelectionPlan` records items in selection order: view, `reduced`,
reason (primary with value, provider, fallback, entry/depth; expanded with
seed, relation, direction, depth, value), prerequisites and estimated cost.
It also records exclusions by stage and reason, neighbor capsules and
decisions, and an expansion trace (seeds, edges, considered, duplicates,
skipped, truncations, `stopped_by`). No TreeSelect or optimizer was
implemented; TreeSelect remains an unspecified future algorithm family.

## Packing

- **Budget unit `oxide-units-v1`** (the canonical OXIDE unit; it claims no
  model's tokenizer): each run of alphanumerics or `_` costs
  `ceil(chars/4)`, each `\n` costs 1, other whitespace costs 0, every other
  char costs 1. `TokenCounter { name: "oxide-units-v1", strict: true }` is
  strict for this unit only. Items end in `\n`, so the payload's count is
  the sum of its items' counts. How it compares with real tokenizers is not
  measured.
- **Payload `oxide-payload-v1`**: `### <path>:<l1>-<l2> <names>\n<bytes>\n`
  per item, in plan order. A name marked `(header)` is shown only up to its
  declaration. An empty bundle has an empty payload and costs 0. Omissions,
  provenance, counts and degradations are envelope, outside the budget.
- **Hydration**: only through `SourceProvider` by `(path, digest)`. The kernel
  hashes the bytes itself, so changed worktree bytes are `SourceMismatch`
  and missing bytes are `SourceUnavailable`, never substituted. The LadybugDB
  view serves its retained generation bytes and only for its pinned manifest.
- **Views and reduction**: `Full` (the entity's range) or `Header` (from the
  start through the first line naming the entity, at most 8 lines, so a
  decorator stays with its `def`). No other cut exists. Non-UTF-8 bytes are
  `NotText`.
- **Overlap**: spans of one file that overlap merge into one item (the union
  range, counted once, with every entity and reason kept). `ItemEntity.complete`
  says whether the item covers that entity's whole range.
- **Groups**: a group is packed whole, reduced, or omitted. The packer
  recounts the whole payload, so it never exceeds the budget even if the plan
  were wrong.

`ContextBundle` holds the snapshot key, query digest, versions (retrieval,
router, capsule, selector, payload), budget, used and remaining units,
items (source ref and digest, lines, entities with completeness and reason,
tokens, text) and omissions (selection, expansion, packing). Its degradations
are channels that did not run, the route bound, graph truncation, judgment
fallbacks by reason, and the expansion bound.

## JEV: fixture-only

The wire contract was read from the live TypeSafe docs on 2026-10-09
(`docs.typesafe.ai/api`, `/confidence`, `/models`): `POST /v1/systemone`
with `{state, model, questions}`; Score answers return `score`, `legend`,
`probabilities` and `confidence`; the response names the versioned model.

- One request per capsule, which avoids batch position bias. One
  three-level Score per question; value = `score/2`; confidence = the
  answer's `confidence` (provider-reported, uncalibrated).
- The model is pinned to `jev-1.13.0`; `-latest`/`-preview` aliases are
  refused, and an answer from another model is malformed.
- Strict validation: one answer, `type: "score"`, score in [0, 2], three
  probabilities summing to 1 ± 1e-3, confidence in [0, 1].
- Deadline per `judge` call: late answers count as timeouts. There is a
  lifetime request cap. `Disclosure` must be chosen (no default):
  `Metadata` withholds snippet text and signatures.
- JEV never abstains, so a malformed, failed, late or skipped answer is
  missing and falls back per subject. Any confidence floor is Rust's
  `DecisionPolicy::min_confidence`; none is set, and no threshold was tuned.
- **No HTTP transport and no live call.** `Replay` serves captured exchanges
  keyed by the SHA-256 of the exact request body; a golden test pins those
  bytes. `Recording` captures any transport into `oxide-jev-replay-v1`,
  non-JSON bodies and HTTP statuses included. A request cap reports as
  unavailable, a passed deadline as a timeout.
- **The test responses are synthetic, written in the documented shape, not
  recorded from the service.** The tests show the mapping, validation,
  fallback and replay plumbing against the docs, not that today's live
  answers parse. Before any JEV result is trusted, record real exchanges
  with an HTTP transport and credentials and replay those.
- Context construction with a dead, abstaining or unrecorded JEV equals the
  offline payload (`failing_abstaining_or_absent_judges_cannot_break_context_construction`).

Evaluation config C (JEV) was not run: there are no captured live responses.
No JEV quality, coverage, disagreement, cost or latency is claimed.

## Evaluation baseline (frozen: `docs/phase-4/baseline-v1/`)

The fixture, snapshot and gold mapping are those of `phase3-baseline-v1` (7
tasks, 9 gold symbols), so routed coverage is the Phase 3 ceiling (1.0).
Budgets: 256 and 1,024 units (the fixture's 8 files are about 8 KB). Files:
`manifest.json`, `metrics.json` (macro means), `results.json` (per task ×
config × budget: plan, packed items, omissions, expansion trace,
degradations, payload SHA-256) and `capsules.jsonl`. Every run asserts
`used ≤ budget` and `count(payload) == used`. The heuristic run is rebuilt
and must be identical. **A 65-entity regression floor, not evidence of
generalization; no downstream agent success is measured.**

Configs: A = the routed universe with a constant 0.5 judgment for every
subject (greedy in pool order, no judgment); B = the heuristic offline path;
B without expansion; B without routing (entry points only, `max_depth` 0,
a diagnostic); B without either.

| Config @ budget | Symbol recall | Header only | File recall | Gold token share | Units used | Items | Planned | Expanded planned | Reduced | Budget omissions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A @1024 | 1.000 | 0 | 1.000 | 0.309 | 990.1 | 9.3 | 12.0 | 0.29 | 2.00 | 12.0 |
| B @1024 | 1.000 | 0 | 1.000 | 0.414 | 878.3 | 8.1 | 10.7 | 0 | 1.29 | 5.4 |
| B no expansion @1024 | 1.000 | 0 | 1.000 | 0.414 | 878.3 | 8.1 | 10.7 | 0 | 1.29 | 5.1 |
| B no routing @1024 | 1.000 | 0 | 1.000 | 0.414 | 878.3 | 8.1 | 10.7 | 0.57 | 1.29 | 3.0 |
| B neither @1024 | 1.000 | 0 | 1.000 | 0.417 | 856.6 | 7.7 | 10.3 | 0 | 1.00 | 1.9 |
| A @256 | 0.071 | 0.714 | 0.857 | 0.309 | 242.1 | 4.4 | 4.4 | 0 | 2.43 | 19.0 |
| B @256 | 0.071 | 0.714 | 0.857 | 0.341 | 234.1 | 4.1 | 4.1 | 0 | 2.14 | 12.0 |
| B no expansion @256 | 0.071 | 0.714 | 0.857 | 0.341 | 234.1 | 4.1 | 4.1 | 0 | 2.14 | 11.7 |
| B no routing @256 | 0.071 | 0.714 | 0.857 | 0.341 | 234.1 | 4.1 | 3.9 | 0 | 1.86 | 9.1 |

Reading (measured on this fixture only):

- Symbol recall counts gold whose whole range is inside one packed item. At
  1,024 units every configuration packs all gold. At 256 units the only
  gold packed whole is `EmailNotifier` (one of the notifier task's two),
  and 71% of gold is shown only as a header. Selection loss is 0.286 and
  packing loss (planned but not whole) is 0.643. 256 units is smaller than
  most gold symbols with their prerequisites here.
- Same recall, less noise: the heuristic (B) raises the gold token share
  over the no-judgment control (A) from 0.309 to 0.414 at 1,024 units, with
  112 fewer units used. At 256 it rises from 0.309 to 0.341. There are no
  irrelevance labels, so `1 − gold token share` is only an upper bound on
  noise and true noise is not measured.
- Expansion adds nothing under baseline routing. Depth-2 routing already
  holds the resolved neighbors (12 duplicates per task at 1,024), and the
  Python slice leaves attribute calls unresolved. B and B without expansion
  have identical recall and gold token share. Without routing, expansion
  plans 0.57 items per task and recovers B's exact numbers at 1,024. On this fixture neither routing
  nor expansion moves context metrics: the fixture is too small to separate
  them (the same caveat as Phase 3's no-routing ablation).

Latency and memory (debug build, not frozen; printed by the harness):
`build_context` for B@1,024 took 67–278 ms per task (median about 200 ms)
over LadybugDB, and the harness's peak RSS was about 125–130 MiB. Most of the
time is per-query store reads (routing's ~4.5 ms per region, Phase 3) plus
one adjacency read per graph node and per expansion seed.

## Review

An independent review (Sonnet 5.5) found no blocker or high issue. Its six
medium findings were fixed:

- The zero-cost estimate ignored label growth. It now charges the label,
  and the eval asserts that the plan estimate bounds the packed cost.
- Replay lost failures and duplicates. Both are now recorded and replayed.
- Ambiguous target lists, names and kinds were unbounded. They are now
  bounded and flagged.
- A non-finite score could render invalid JSON. It now renders as null.
- Replay keys depended on serde key order. A golden request-hash test now
  pins them.
- Recording lost non-JSON bodies and statuses. They now round-trip.

Also fixed: a request cap no longer reports as a timeout, and the heuristic
looks up the lexical channel by name. Remaining lows: a stale-plus-correct
answer pair falls back as `Duplicate`, JEV `score` is not cross-checked
against its distribution, and alias refusal checks suffixes only.

## Open (not Phase 4)

- A live JEV HTTP transport with recorded real exchanges, then the
  preregistered JEV comparison (config C), confidence-floor and calibration
  work: Phase 5.
- Irrelevance labels, so noise can be measured rather than bounded.
- Tokenizer comparison for `oxide-units-v1` against downstream model
  tokenizers; an estimated export mode if wanted.
- Coherence groups beyond enclosing-scope headers (for example a callee
  signature required by a selected caller).
- Expansion work per seed reads up to 64 edges; capsule relation lists for
  neighbors carry only the connecting edge (`relations_truncated`).
- A `build_context` service operation: the v1 protocol is unchanged.
- A larger mapped corpus to separate routing, expansion and the heuristic.
