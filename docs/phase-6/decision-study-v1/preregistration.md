# Decision Study v1 preregistration: judge signal and input efficiency

Frozen 2026-10-24, before any judge number, control number or rendered-token count from
this study. Code at this freeze: `fef45e5` (`oxide-select-greedy-v4`, `oxide-capsule-v1`,
`DecisionProvider` seam). The seam and the capsule contract are not changed by this study.

## Question

Can a real judge, behind the existing `DecisionProvider` / `CapsuleJudge` seam, value
candidates well enough to raise packed gold-line recall at matched budgets by more than its
inference cost justifies? Also: which input representation is the most efficient, and does
the 1,024-byte query clip remove evidence a judge needs?

## Disclosure

Seen before this file was written:

- decision-seam § Cost on real pools (Python dev, canonical capsules): median 60 capsules
  and 38,349 o200k tokens a request, max 67,271; 35% of capsules carry a truncation, the
  query in 186 of them. 3 of the 15 dev queries exceed 1,024 bytes (1,188, 1,268 and
  1,428 bytes); the other 12 are ≤ 897.
- graph-v3 ceilings on the same pools (Python dev, macro verified `line_recall`):
  @1,024 production 0.548, `oracle_order` 0.682, `oracle_pick` 0.776; @4,096 0.659 /
  0.792 / 0.886. Gold rates by class (lexical ranks 1–5: 0.197; routed depth-1: 0.035).
- No model, prompt or renderer output has been run on these pools.

The ceilings are upper bounds on what a perfect judgment could do on the same pool, not an
expected model result.

## Scope

Python ContextBench **dev** (15 tasks, 9 corpora including `fixture:py_repo`) for every
development step. The Python held-out split (xarray, pytest) is sealed: the harness refuses
it unless a dev gate file records `DEV GATE: PASS`, and it runs once. TypeScript is out of
scope (graph-v3: ≤ +0.058 headroom, entry-bound).

## Contract boundary

Capsules are built by `capsule.build` exactly as at the freeze; identities, digests, digest
order and `Recorded` replay are unchanged. A **renderer** is a pure function of the
sendable capsules of one request (digest order) to one prompt. It may drop or re-encode
canonical fields but must not add evidence from outside the capsules. Each renderer has a
version string; the study writes a manifest (renderer version, model manifest digest,
instruction digest) next to every judgment file. Judgments are recorded by capsule digest
and replayed through `CapsuleJudge(…, Recorded(judgments))` into the unchanged `pack`.

| Renderer | Version | Encoding |
| --- | --- | --- |
| C `canonical` | `ds1-canonical-v1` | The existing representation: each capsule's canonical JSON, query repeated per capsule |
| S `shared` | `ds1-shared-v1` | The task once per request; per capsule its canonical fields minus `query`, `query_digest`, `schema`, `derivation`, `source_digest` |
| K `compact` | `ds1-compact-v1` | The task once; per capsule one header line (kind, name, path:lines, rank, score, seeds, depth, ≤ 3 origins) and the excerpt bounded to its first 1,024 bytes, cut after the last whole line |

Every renderer has a rank-removed variant (`…-norank`): `lexical_rank` and
`lexical_score` are absent. Batch ids are `c1…cN` by presentation position; presentation
order is digest order unless an order control says otherwise.

Diagnostic outside the contract (never promotable): S with the **full** task query instead
of the clipped capsule query (`ds1-shared-fullquery-v1`). It runs only if the truncation
measurement below finds at least 2 dev tasks where a gold holder is named in the query only
beyond the first 1,024 bytes.

## Labels

A candidate is positive when it holds at least one verified gold line, by graph-v3's
holder rule: module-level gold lines belong to the file's module-level view, all other gold
lines to the innermost pool symbol containing them. Labels are never written next to the
requests the adapter reads.

## Arms

Every arm is a `CapsuleJudge` adapter over the same pools and budgets 256 / 1,024 / 4,096.

| Arm | Judgments |
| --- | --- |
| H heuristic | `packing.value` of each candidate. **Validity: must reproduce production recall on every task and budget**, or the study is void |
| O1 `oracle_order` | 0.99 for holders whose heuristic value is ≥ the floor, others abstain. Validity: reproduces graph-v3 `oracle_order` |
| O2 `oracle_pick` | 0.99 for every holder, others abstain. Validity: reproduces graph-v3 `oracle_pick` |
| X random | uniform [0, 1) from `(seed, digest)`, seeds 0–4, mean reported |
| P permutation | a model arm's own values shuffled among the capsules of each request, seeds 0–4. Also applied to H |
| M model | one per renderer (C, S, K) with rank, plus the rank-removed variant of each |
| M-repeat | the gate renderer's request sent again, identical |
| M-reversed | the gate renderer with presentation order reversed (position-bias control) |

## Model configuration

Amended the same day, before any number, after the user recommended JEV: the judge is
**TypeSafe JEV**, `POST https://api.typesafe.ai/v1/systemone`, model pinned to
**`jev-1.14.0`** (not an alias; the response's `model` must equal it, else the request
counts as failed). Sending repository source to it needs explicit authorization per corpus.
Fixed before the first call and recorded as a manifest digest:

- one call per request (the seam's contract): the renderer's output is the JEV `state`, and
  every candidate id `cN` is one `noul` question over that shared state with the same
  instructions and criteria (`render.QUESTION`, `render.CRITERIA`; their digest is
  recorded). The judgment is the returned `noul` probability. JEV offers no seed or
  temperature; repeatability is measured, not assumed;
- no long-context header: JEV's default limits (64k tokens per request, 32k per question,
  i.e. state plus one question) apply to every renderer alike. A request JEV rejects
  (`400 max_tokens_exceeded` or any non-200) is a request failure. A renderer that cannot
  fit is a cost result, not a reason to change the configuration;
- an **I/O deadline of 60 s per request, enforced by killing the child process that does
  the network call**, not by abandoning a thread; `CapsuleJudge`'s own deadline is set to
  61 s so the hard kill fires first. No retries. A killed, invalid or unparseable request
  is a request failure: heuristic for all of its candidates, counted;
- cost from the response's billed `usage.input_tokens` at the published $0.042 per
  million input tokens (output tokens are free).

## Measurements

Per arm, macro over dev tasks with verified gold:

1. **Packed recall**: verified `line_recall` @256 / 1,024 / 4,096; mean `used` and
   `gold_unit_share@4096`. Matched budgets by construction (`used <= budget` for every arm).
2. **Candidate quality** over the asked candidates, on the value `pack` actually uses
   (judgment or fallback): within-task AUROC (macro over tasks with both classes), pooled
   AUROC, within-task precision@5, Brier score and ECE (10 equal-width bins), with
   fallback, abstention and failure counts.
3. **Cost**: rendered prompt tokens per request with tiktoken `o200k_base` and `cl100k_base`
   for every renderer (dry run, no network); for model arms the provider's billed input
   and output tokens, wall-clock latency per request (median, p95, max) including process
   start, and price if the manifest declares one.
4. **Cost per useful improvement**: billed tokens per request ÷ (100 × Δ`line_recall@4096`
   vs H), i.e. tokens per request per +1 recall point; likewise latency and price.
   Undefined (reported as "no improvement") when Δ ≤ 0.
5. **Repeatability**: within-task Spearman between M and M-repeat (median over tasks), and
   the Δ@4096 difference. **Position bias**: the same between M and M-reversed.
6. **Query truncation** (model-free, run first): per dev task, the query's byte length,
   whether it is clipped, and for each gold holder whether its name (last qualified
   segment) or its file stem appears in the full query, in the first 1,024 bytes, or only
   beyond them; the same rate for non-holder candidates as a base rate.

## Decision rules

**Renderer for the gate:** the cheapest renderer (median o200k tokens a request) whose dev
Δ`line_recall@4096` vs H is within 0.010 of the best renderer's. Only it goes to
held-out.

**Dev gate** (all, for the gate renderer):

1. Δ`line_recall@4096` vs H ≥ +0.030, Δ@1,024 ≥ 0, and no budget below H by more than
   0.005.
2. `line_recall@4096` above both the random mean and its own permutation mean by ≥ +0.030.
3. Within-task AUROC ≥ 0.70 and ≥ H's within-task AUROC + 0.05.
4. Not rank copying: its rank-removed variant keeps ≥ 50% of its Δ@4096 and has
   within-task AUROC ≥ 0.65.
5. Operational: at most 1 failed request of 15; repeatability and position bias each with
   median within-task Spearman ≥ 0.90 and |ΔΔ@4096| ≤ 0.010.

**Held-out confirmation** (once, only after the dev gate passes): Δ`line_recall@4096` vs H
≥ +0.020, no budget below H by more than 0.005, within-task AUROC ≥ 0.65, rank-removed keeps
≥ 50% of the gain.

**Verdicts:**

- **SIGNAL** (meaningful, transferable ranking signal): dev gate and held-out pass. This
  justifies proposing a DecisionBench training study (not starting one) and an ADR for a
  judge adapter. It does **not** justify production integration.
- **Deployable** additionally needs p95 latency ≤ 2.0 s (the current `DEADLINE`) and the
  measured cost per useful improvement accepted in that ADR. If only latency fails, the
  report says so; raising `DEADLINE` is an ADR decision, not this study's.
- **INCONCLUSIVE**: dev gate passes, held-out flat (no regression, gain below +0.020).
- **REJECT**: the dev gate fails. Production unchanged; held-out stays sealed.

Whatever the verdict: no model in `src/`, no public judge configuration, no training.
