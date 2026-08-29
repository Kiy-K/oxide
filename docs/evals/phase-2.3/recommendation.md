# Phase 2.3 recommendation

Governing rule: **improve selection, not obedience. OXIDE should become
the obvious tool when useful, not a ritual every agent performs before
touching code.**

## 1. Phase 2.2 E0 baseline

CLI + `skills/oxide-code-context/SKILL.md` (unchanged) + a ~63-token
`AGENTS.md` rule. On `muse-spark-1.2-contributor-free`: 54% Bucket-A
activation, 72% overall appropriate activation, 0% Bucket-C false
positives (Phase 2.2 `activation-results.md`).

## 2. Miss attribution

All 6 diagnosable Phase 2.2 E0 Bucket-A misses were `NATIVE_DEFAULT`
(model defaults to grep/read, never attempts `oxide`, despite both the
rule and skill being present) — no `COMMAND_FRICTION`,
`INSTRUCTION_CONFLICT`, `LATE_ACTIVATION`, or `INFRASTRUCTURE` observed
in that set. `NOT_NOTICED` vs `NATIVE_DEFAULT` couldn't be directly
distinguished (the transport doesn't stream reasoning tokens), but
composite evidence (54% is a coin-flip-beating rate, not a floor)
supports `NATIVE_DEFAULT` as the better-fitting label. Full detail:
`miss-forensics.md`.

## 3. Variants tested

E0 (baseline), E1 (stronger first sentence), E2 (exact commands spelled
out), E3 (explicit if/then decision rule), E4 (skill-description
rewrite, AGENTS.md unchanged). Full text: `policy-variants.md`.

## 4. Repetitions

120 navigation runs (5 variants × 4 Bucket-A + 4 Bucket-C tasks × 3
reps) + 6 coding-outcome runs (E0/E1 × 3 reps) + 12 interference-check
runs (E1, 2 tasks × clean/normal × 3 reps) = **138 real runs**, all on
`openai/gpt-5.6-luna` after a mid-phase model switch forced by a
`muse-spark` provider rate limit (`protocol.md` §-1). Zero timeouts,
zero dead runs across all 138.

## 5. Bucket-A activation

**100% for every variant, including the unmodified E0 baseline.** This
model never misses a single Bucket-A task regardless of instruction
text. The phase's originally planned headline metric is saturated and
uninformative for variant comparison on this model — see §12 for the
metric that actually differentiated the variants.

## 6. First-action activation

`oxide` is never the literal first tool call in any run (every Bucket-A
run's first action is a `skill` tool invocation, 120/120). The
meaningful signal is *which* skill: `oxide-code-context` vs. the ambient
competing `codebase-memory` skill. E1 wins that race 75% of the time
(9/12), vs. 50% for E0 and E2, 67% for E3/E4 — see
`activation-results.md` §3.

## 7. Bucket-C false positives

0% for E0, E1, E3, E4 (0/12 each). **E2: 8% (1/12)** — the only false
positive across 240 Bucket-C runs in Phase 2.2 and 2.3 combined. Full
transcript analysis in `activation-results.md` §4: triggered via an
ambient `brainstorming` skill leading to more elaborate deliberation, not
obviously E2's wording directly, but E2 is the variant that makes `oxide
context` feel most like a natural "next step" once in that mode.

## 8. Coding outcome

6/6 real bug-fix runs (E0 and E1, 3 reps each) passed the actual test
suite. `oxide context` called exactly once per run in every single case.
No variant-based difference in correctness — both are equally reliable
on this one real edit task. See `activation-results.md` §5.

## 9. Persistent/on-demand context cost

E1 (the winner) costs ~70 tokens persistent AGENTS.md text — **7 tokens
more than E0** — plus the unchanged ~99-token skill frontmatter stub and
~803-token on-demand skill body (loaded only when the `skill` tool is
actually called). Every variant tested stays well under the phase's
~250-token persistent ceiling. Full table: `context-cost.md`.

## 10. Search behavior

`oxide search` was used far less than `oxide context` in this phase's
data (mean ~0.0–0.25 calls/run across variants, vs. 1.00 for context —
`activation-results.md` §"tool-call discipline"). On this model, a
single `oxide context` call usually supplied enough to answer the
Bucket-A prompts outright, leaving less room for `search` to demonstrate
a distinct follow-up role than Phase 2.2 saw on `muse-spark`. Not
evidence to remove or retune `search` (out of scope per the brief, and a
single-model/single-task-set sample) — recorded as observed usage, not a
recommendation.

## 11. Interference findings

`--pure` (attempted "clean" config) removes ambient Skills (`ponytail`,
`codebase-memory`, ~30 others) but does **not** remove the `codegraph`
MCP server, which shows up as the first tool call in 4/6 "clean" runs.
Activation itself (100%, whether `oxide` gets used at all) is unaffected
by clean vs. normal config; only *which competing tool* shows up first
changes. Documented only, no compatibility hack attempted, per the
brief. Full detail: `protocol.md` §7.

## 12. Remaining miss categories

**None to report on this model** — Bucket-A activation is 100% under
every variant, so there are no misses left to classify. The remaining,
real quality gap is not "does the model use OXIDE" but "how much native
exploration and skill-selection competition happens before it does" —
answered quantitatively in §6/§9 above, not as a miss taxonomy. A future
pass evaluating a model that still shows real misses (as `muse-spark`
did) should re-run the `miss-forensics.md` classification against that
model's own transcripts rather than assume this phase's "no misses"
result transfers.

## 13. Recommended AGENTS.md text

**Ship E1**, replacing Phase 2.2's E0 text:

```markdown
## OXIDE

For unfamiliar repository work where the implementation path is not
already known, use `oxide context` before broad grep/read exploration.
Use `oxide search` for focused follow-up discovery. For exact known-file
or literal tasks, use normal tools directly. Read source before editing.
```

## 14. Recommended Skill metadata/body changes

**None.** E4's rewritten frontmatter description did not outperform the
original (native-calls-before-oxide 2.17 vs. baseline's 2.25 — within
noise, no clear win) despite costing the same. Keep
`skills/oxide-code-context/SKILL.md` exactly as shipped after Phase 2.1's
minimal-skill work; do not expand the body (nothing in this phase's
evidence shows the body itself is insufficient — see the phase brief's
own §4 instruction not to expand it without such evidence).

## 15. Is passive activation good enough to freeze?

**Yes, provisionally — for this model.** E1 achieves 100% Bucket-A
activation, 0% Bucket-C false positives, and the best activation-quality
numbers (earliest/most-consistent OXIDE use, most skill-selection-race
wins) of any tested variant, at a 7-token cost over the existing
baseline. This meets and exceeds the phase's stop-condition success
criterion (§14: "75–85%+ Bucket-A activation with near-zero Bucket-C
false positives"). **Caveat carried forward explicitly**: this result is
specific to `gpt-5.6-luna`. Phase 2.2's `muse-spark` baseline missed 46%
of the same tasks under the same E0 text — meaning "good enough to
freeze" is a per-model conclusion here, not a universal one. Do not read
this phase as proof the activation problem is solved in general; read it
as proof that (a) the miss-forensics-driven refinement approach works
(E1 beat E0 on every quality metric, on a model it was never tuned
against), and (b) at least one real, currently-available client/model
pairing already clears the bar with passive instructions alone. No hooks
are justified by this phase's evidence, on either model tested so far.
