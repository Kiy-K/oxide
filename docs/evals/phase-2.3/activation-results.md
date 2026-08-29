# Activation results (E0–E4, `openai/gpt-5.6-luna`, see `protocol.md` §-1)

120 runs, 5 variants × 4 Bucket-A + 4 Bucket-C tasks × 3 reps. **Zero
timeouts, zero dead runs** — a very different reliability profile from
Phase 2.2's `muse-spark` (which lost ~20% of runs to a client permission
bug). All 120 records are valid.

## 1. Headline: Bucket-A activation is saturated at 100% for every variant

| Variant | Bucket-A activation | Bucket-C false positives |
|---|---|---|
| E0 (baseline) | 100% (12/12) | 0% |
| E1 | 100% (12/12) | 0% |
| E2 | 100% (12/12) | 0% |
| E3 | 100% (12/12) | 0% |
| E4 | 100% (12/12) | 0% |

This is the single biggest surprise of the phase: on this model, **even
the unmodified E0 baseline never misses a Bucket-A task**. There is no
headroom left on the binary activation-rate metric for any variant to
show an improvement over any other — the ceiling effect the phase brief
warned about in its "stop condition" (§14 success criterion: "75–85%+")
was blown past by the baseline itself. This makes the phase's originally
planned primary metric (Bucket-A activation rate) uninformative for
variant comparison on this model/client pairing. **Every real
differentiator between variants in this phase comes from the
secondary/quality metrics below**, which is exactly why the brief's §8
("activation quality, not call count") turned out to be load-bearing
rather than a nice-to-have.

Bucket-C false positives are 0% for four of five variants — **except
E2 (8%, 1/12)**, discussed in §4.

## 2. Activation quality: native tool calls before `oxide` (lower is better)

`oxide` was never the literal first tool call under any variant (every
Bucket-A run's first action was a `skill` tool invocation — see §3). The
real signal is *how many* other tool calls happen before OXIDE gets
reached:

| Variant | native calls before oxide (sorted, n=12) | mean |
|---|---|---|
| **E1** | 1,1,1,1,1,1,1,2,2,2,2,2 | **1.42** |
| E2 | 1,1,1,1,2,2,2,2,2,2,2,3 | 1.75 |
| E3 | 1,1,1,1,2,2,2,2,2,3,3,3 | 1.92 |
| E4 | 1,1,2,2,2,2,2,2,2,3,3,4 | 2.17 |
| E0 (baseline) | 1,1,1,2,2,2,3,3,3,3,3,3 | 2.25 |

**E1 is the clear winner**: earliest and most consistent activation
(max 2 calls before OXIDE, vs. E0's max of 3, E4's max of 4), at the
same 100%/0% activation/false-positive numbers as everyone else. This
directly answers the phase's core question: E1's more decisive first
sentence ("where the implementation path is not already known") measurably
tightens *when* the model reaches for OXIDE, even though it can't move
the already-saturated *whether*.

`late_activation` (≥2 prior tool calls, the brief's §8 flagged
anti-pattern) counts out of 12 oxide-using runs: E1 5, E2 8, E3 8, E0 9,
E4 10 — same ranking, same story.

## 3. First action is always a `skill` call — the real competition is skill-vs-skill

Every single Bucket-A run across all 120, in every variant, starts with
a `skill` tool invocation (0 exceptions). This environment has an
**ambient competing code-context skill, `codebase-memory`** (CodeGraph's
bundled skill, present in every run — see `protocol.md` §-1 and Phase
2.2's `failures.md` for the same confound with the MCP tool version of
this). The real mechanism this phase's variants are tuning is **which
skill wins the race to be called first**, `oxide-code-context` or
`codebase-memory`:

| Variant | `oxide-code-context` first | `codebase-memory` first | other |
|---|---|---|---|
| **E1** | **9/12 (75%)** | 3/12 | 0 |
| E3 | 8/12 (67%) | 4/12 | 0 |
| E4 | 8/12 (67%) | 3/12 | 1 (`systematic-debugging`) |
| E0 | 6/12 (50%) | 5/12 | 1 (`systematic-debugging`) |
| E2 | 6/12 (50%) | 6/12 | 0 |

Same ranking a third time. **E1's stronger first sentence doesn't just
speed up eventual OXIDE use — it measurably wins the skill-selection
race against a competing tool more often**, which is the actual
mechanism behind §2's lower native-calls-before-oxide numbers. This is a
much more concrete, actionable finding than "activation rate" alone would
have surfaced, and it directly validates the phase brief's insistence on
tracking first-use position and call-order quality instead of a single
binary rate.

On Bucket C (trivial tasks), the model correctly reaches for entirely
different, non-code-discovery skills instead (`git-workflow-and-
versioning`, the ambient `ponytail` persona skill, occasionally
`brainstorming`) — never `oxide-code-context` or `codebase-memory` as the
*first* action in any of the 60 Bucket-C runs. This is more evidence the
model's skill-selection is task-sensitive, not reflexive.

## 4. The one false positive: E2, and what it reveals

`C1-E2-r2` (rename `TTLCache`→`TimedCache` in one named file) is the only
unnecessary activation in all 240 Bucket-C-task-runs across Phase 2.2 and
2.3 combined. Full transcript (`logs/C1-E2-r2.jsonl`): the model called
the **`brainstorming`** skill first (an ambient Superpowers skill, not
oxide-related) "to confirm the smallest safe rename design," then
`glob`'d for the named file, then — despite already having the exact file
and a two-line change — ran `oxide context --task "Rename TTLCache to
TimedCache in oxidepy/cache.py, touching only that file" --json` anyway,
then read `AGENTS.md` explicitly (the only one of 120 runs to do so —
see `client-instruction-paths.md`) and the target file, and only then
proposed the edit. E2 is the variant that spells out `oxide context
--task "<task>" --json` as literal example syntax in the AGENTS.md text
— plausibly making `oxide context` feel like a natural "next step" once
the model was already in a more elaborate deliberative mode (triggered
by `brainstorming`, not by E2's wording directly), rather than E2 causing
reflexive overuse on its own. n=1 — treat as a concrete illustration, not
a statistically reliable 8% rate.

## 5. Coding outcome (§11)

`eval-agent/tasks/py_bug_retry` reused verbatim from Phase 2.2 (real
bug: `backoff_ms` shrinks instead of growing, pre-existing failing test),
3 reps each under E0 and the winning candidate E1:

| Variant | rep | tests pass | oxide context | oxide search | native calls before oxide | wall |
|---|---|---|---|---|---|---|
| E0 | 1 | ✅ | 1 | 1 | 3 | 62.7s |
| E0 | 2 | ✅ | 1 | 0 | 2 | 72.4s |
| E0 | 3 | ✅ | 1 | 0 | 3 | 63.6s |
| E1 | 1 | ✅ | 1 | 0 | 3 | 77.1s |
| E1 | 2 | ✅ | 1 | 0 | 4 | 66.9s |
| E1 | 3 | ✅ | 1 | 0 | 3 | 69.7s |

**6/6 pass, 0 timeouts, 0 dead runs.** Both variants reliably used `oxide
context` exactly once per run (never zero, never more than one) on a real
edit task with no location given — confirming the navigation-tier finding
holds for an actual coding task, not just report-only prompts. Unlike the
navigation tasks, `native_calls_before_oxide` doesn't clearly favor E1
here (E0 mean 2.67 vs E1 mean 3.33) — with a real edit task, both
variants show the model doing a few more exploratory reads before
committing to `oxide context`, and n=3/variant is too small to read
anything into the direction of that difference. What's consistent with
the navigation tiers: activation is universal (100%) and the model
always fixes the actual bug correctly (verified by the real test suite,
not by "did it touch the right symbol" — per the phase's own §11
caution against crediting a superficial edit).

## 6. Comparison to Phase 2.2 (different model — not the same experiment)

Phase 2.2's `muse-spark` E-condition Bucket-A activation was 54%, with
`NATIVE_DEFAULT` as the dominant miss cause. This phase's `gpt-5.6-luna`
E0 baseline is 100% on the same tasks, same skill, same AGENTS.md text.
**This is a model-behavior difference, not evidence the Phase 2.2
recommendation was wrong** — see `protocol.md` §-1 for why these two
numbers are not directly comparable (different model, forced by a
mid-phase provider rate limit). What *does* carry over: the miss-
forensics reasoning (§1 of this document, and `miss-forensics.md`)
correctly predicted which variants would help before any gpt-5.6-luna
data existed, on a completely different model. That's a stronger signal
that the underlying diagnosis (native-default / competing-tool-selection
is the mechanism, not "the model doesn't know OXIDE exists") generalizes,
even though the raw percentages don't.
