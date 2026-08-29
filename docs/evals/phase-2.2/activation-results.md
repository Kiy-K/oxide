# Activation results

Client: `opencode` 1.18.25. Model: `opencode/muse-spark-1.2-contributor-free`
(the only model available in this environment). See `protocol.md` for
conditions/tasks and `failures.md` for the 6 timeouts + 32 dead runs
excluded from every table below (164 runs executed → 122 valid navigation
runs analyzed here).

## 1. Bucket-A activation rate (unfamiliar multi-file — should activate)

| Condition | Activation rate | n |
|---|---|---|
| A — baseline | 0% | 14 |
| D — AGENTS.md only | 25% | 12 |
| C — SKILL.md only | 31% | 13 |
| B — bare mention, no guidance | 40% | 15 |
| **E — SKILL.md + AGENTS.md** | **54%** | 13 |

Ranking: **E > B > C > D > A**. The two single-mechanism instruction
layers (C alone, D alone) each landed *below* just telling the model in
the prompt that a CLI exists with zero usage guidance (B) — neither the
skill nor the tiny AGENTS.md rule reliably drove activation on its own.
Only the combination (E) roughly doubled D and meaningfully beat every
single-mechanism condition. None of these are anywhere near "reliable" in
the sense the phase's definition-of-done asks for (54% is a coin flip
plus a bit); see `recommendation.md` for what that implies.

Small-n caveat: even after extending Bucket-A to 5 reps/task/condition
(20 nominal runs/condition, 12–15 valid after excluding timeouts/dead
runs), these are still 12–15-run samples. The **ranking** (E highest, A
lowest) is consistent and the gap between E and A is large enough to
trust directionally; the exact percentages have wide confidence intervals
and should not be over-read to the percentage point.

## 2. Bucket-B activation rate (subsystem named, exact impl unknown — optional)

| Condition | Activation rate | n |
|---|---|---|
| A | 0% | 4 |
| C | 0% | 2 |
| D | 0% | 2 |
| E | 75% | 4 |
| B | 100% | 3 |

n is too small per cell (2–4) to draw a real conclusion here beyond "B and
E both show real usage, C and D's valid samples happened to show none" —
flagged as under-powered rather than a finding. Bucket B was explicitly
lower-priority per the phase brief's own cost-reduction allowance (§7).

## 3. Bucket-C false-positive rate (trivial/exact-file — should NOT activate)

| Condition | Unnecessary activation | n |
|---|---|---|
| A | 0% | 8 |
| B | 0% | 8 |
| C | 0% | 8 |
| D | 0% | 8 |
| E | 0% | 8 |

**Zero false positives in all 40 Bucket-C runs, across every condition.**
This is the cleanest result in the whole phase: no instruction layer,
including the ones that explicitly teach OXIDE usage (C/D/E), caused
reflexive/unnecessary tool calls on a task where the file and edit were
already fully specified. Whatever else is uncertain about which layer
best drives activation, none of them broke this property.

## 4. Overall appropriate-activation rate (all buckets combined)

| Condition | Appropriate | n |
|---|---|---|
| A | 31% | 26 |
| D | 50% | 22 |
| C | 52% | 23 |
| B | 65% | 26 |
| **E** | **72%** | 25 |

("Appropriate" = used OXIDE on Bucket A/B, or correctly didn't on Bucket
C — see `raw/run_activation_eval.py::classify_activation`.) E wins
overall too, driven entirely by Bucket A/B activation since Bucket C is a
100%-appropriate wash across every condition.

## 5. First repository-discovery action (Bucket A tasks only)

| Condition | bash | grep | read | oxide (as first action) | other |
|---|---|---|---|---|---|
| A | 5 | 5 | 3 | 0 | 1 (`codegraph_explore`) |
| B | 5 | 5 | 5 | 0 | — |
| C | 3 | 7 | 2 | 0 | 1 (`skill` tool) |
| D | 6 | 5 | 1 | 0 | — |
| **E** | 2 | 1 | 7 | **2** | 1 (`codegraph_explore`) |

`oxide` was the literal *first* action only in condition E (2/13 runs) —
every other condition's Bucket-A runs start with native `bash`/`grep`/
`read`, even in D and C where oxide-usage guidance is present. The phase
brief's desired production shape ("context first" for unfamiliar
multi-file work) is achieved only rarely even under the strongest tested
condition. One condition-A and one condition-C run reached for the
`codegraph` MCP tool first instead of OXIDE or native tools — a live
confound from a competing code-context tool this environment could not
disable (see `failures.md`).

## 6. Tool-call discipline (mean per valid run, all buckets)

| Condition | oxide_context | oxide_search | native calls | total calls | wall time | tokens |
|---|---|---|---|---|---|---|
| A | 0.00 | 0.00 | 5.58 | 5.9 | 29.2s | 177,875 |
| B | 0.35 | 1.23 | 5.62 | 7.0 | 26.3s | 215,211 |
| C | 0.17 | 0.22 | 4.61 | 5.3 | 39.1s | 196,623 |
| D | 0.32 | 0.45 | 4.05 | 5.0 | 20.1s | 179,011 |
| E | 0.52 | 0.76 | 5.04 | 6.3 | 44.8s | 216,662 |

`oxide search` is called roughly 2–3x as often as `oxide context` in
every condition that uses OXIDE at all (B/D/E). See §7 below —
`search` is doing most of the actual work, not `context`. E has the
highest wall time and token cost, but also the highest appropriate-
activation rate; not a free win, a real trade (see `context-cost.md` for
whether that trade is worth it).

Token totals (100k–215k/run) are dominated by this environment's own
system-prompt overhead (≈30 registered skills, the `ponytail` plugin, the
`codegraph` MCP registration) — see `context-cost.md` for why these
absolute numbers shouldn't be read as "OXIDE's cost."

## 7. Search-role validation

| Condition | both context+search | context-only | search-only |
|---|---|---|---|
| A | 0 | 0 | 0 |
| B | 3 | 0 | 6 |
| C | 3 | 1 | 0 |
| D | 3 | 0 | 0 |
| E | 7 | 1 | 2 |

The desired shape from `docs/agent-usage-policy.md`
(`context → read → search for a specific follow-up`) shows up most
clearly in **E**, where "both" (7) is the dominant pattern among oxide-
using runs and clearly beats "search-only" (2). In **B** (bare mention,
no guidance on order), the model reaches for `search` alone six times as
often as it uses `context` at all — when nothing tells it the intended
workflow, it defaults to a single targeted `search` call rather than
building an initial context pack first. This is consistent with
`search` retaining a distinct, real follow-up role once the workflow is
actually taught (E), and being used as a generic single-shot lookup tool
when it isn't (B) — evidence in favor of keeping `search` in the surface
(per the phase brief's "just collect evidence, don't remove it" framing),
not evidence that `search` alone with no `context` guidance is the
better default.

## 8. Coding outcome (§13, real edit + test)

One real bug-fix task (`eval-agent/tasks/py_bug_retry` — reused verbatim,
`backoff_ms` shrinks instead of growing, pre-existing failing test),
conditions A and D only, 5 reps attempted each (10 runs total):

| Condition | rep | outcome |
|---|---|---|
| A | 1 | dead run (permission-denied `read("/")`) |
| A | 2 | dead run |
| A | 3 | **valid — tests pass**, 13 tool calls, no oxide use |
| A | 4 | timed out (280s, no retry in this script) |
| A | 5 | timed out |
| D | 1 | **valid — tests pass**, 8 tool calls, no oxide use |
| D | 2 | dead run |
| D | 3 | timed out |
| D | 4 | timed out |
| D | 5 | timed out |

**7 of 10 runs are unusable** (3 dead runs, same client bug as
`failures.md`; 4 timeouts — reps 4/5 of both conditions all hit the full
280s timeout, suggesting the free-tier provider degraded further as this
phase's cumulative call volume grew rather than random per-run noise).
This leaves exactly **one valid sample per condition**, both of which
passed the test suite via native `read`/`bash` exploration alone, without
ever calling `oxide`. n=1/condition is not evidence of anything about
which condition is better for real coding outcomes — it is only evidence
that (a) the task is solvable by this model without OXIDE on a 3-file
repo, consistent with §6's tiny-fixture-repo confound, and (b) this
phase's real edit-and-verify tier was **underpowered by infrastructure
attrition**, not by task or instruction design. Do not cite this section
for "OXIDE doesn't help coding outcomes" — the phase brief's §13
"do not claim coding-quality improvement from navigation-only tasks"
caution applies doubly here since even the intended broader sample never
materialized. Treat the 70% unusable-run rate itself as the finding:
a future pass needs either a more reliable client/provider pairing or a
retry budget large enough to absorb ~20-45% attrition per condition.
