# Phase 3.1 results

204 real runs total: 170 navigation runs (34 tasks × condition/rep cells,
`raw/run_matrix.py`) + 34 real bug-fix runs with actual test verification.
Per protocol.md §4.5, **Bucket A/B navigation is muse-spark-only** (a
clean single-model dataset); **Bucket C navigation and both coding tasks
are gpt-5.6-luna-only** (with 2 Bucket-C rows carried over from muse-spark
before the mid-batch switch, excluded from the tables below since they're
already-timed-out and contribute no activation signal). These two
sub-datasets are **never pooled into one percentage** anywhere in this
document.

Raw counts accompany every percentage per the phase brief's statistical-
honesty requirement (§9/§20 of the brief).

## 1. Infrastructure failures (retained, excluded from activation math)

| Condition | Nav timeouts | Coding timeouts |
|---|---|---|
| A | 6/30 | 0/6 |
| B | 4/30 | 0/6 |
| C | 3/30 | 0/6 |
| D | 3/30 | 0/6 |
| E | 5/50 | 0/10 |

All timeouts occurred during the muse-spark outage window documented in
protocol.md §4.5 (silent hang, no stdout/stderr, both the run and its
automatic retry exhausted the 200s timeout). Zero timeouts on gpt-5.6-luna
across all 34 coding runs and the post-switch nav runs. `codegraph`
leakage check: **0/204 runs** saw the ambient `codegraph` MCP server —
the isolation fix from protocol.md §2 held for the entire matrix.

## 2. Bucket-A activation (muse-spark, n=12 per condition except E n=20)

| Condition | Used OXIDE | Appropriate activation |
|---|---|---|
| A (native) | 0/12 (0%) | 0/12 (0%) — correct, nothing to activate |
| B (CLI) | 6/12 (50%) | 6/12 (50%) |
| C (MCP, minimal) | 3/12 (25%) | 3/12 (25%) |
| D (MCP + guidance) | 10/12 (83%) | 10/12 (83%) |
| E (CLI + MCP) | 17/20 (85%) | 17/20 (85%) |

**This is not a saturated metric on this model** (unlike Phase 2.3's
gpt-5.6-luna, which hit 100% on every variant) — it discriminates cleanly
between conditions, which is exactly why muse-spark was chosen for the
primary matrix (protocol.md §5). **D outperforms B by 33 percentage
points** (83% vs 50%) on the *same* underlying guidance principle (E1's
wording, transport-adapted) — MCP's self-describing `tools/list` schema
plus a short generic AGENTS.md rule activated far more reliably than the
CLI's Skill+AGENTS.md combination on this model. **C (MCP with no
guidance at all) badly underperforms both** (25%) — confirming that MCP's
own tool descriptions alone are not enough; an AGENTS.md rule still
matters even when the tool schema is self-describing.

## 3. Bucket-B activation (muse-spark, n=3 per condition except E n=5 — small-n, report with caution)

| Condition | Used OXIDE |
|---|---|
| A | 0/3 (0%) |
| B | 3/3 (100%) |
| C | 0/3 (0%) |
| D | 0/3 (0%) |
| E | 3/5 (60%) |

With only 3 reps per condition this is not a reliable percentage on its
own — but the pattern (CLI activates, both pure-MCP conditions don't) is
the opposite direction from Bucket A's D result, worth flagging rather
than smoothing over. Bucket B's two tasks ask the model to search a named
*subsystem* rather than described symptoms; it's plausible OXIDE's
`context` tool's task-description framing (built for "unfamiliar task"
phrasing) resonates less with "in subsystem X, find Y" phrasing than the
CLI's skill body (which explicitly walks through subsystem-style
examples) does — a hypothesis, not a conclusion, given n=3.

## 4. Bucket-C false-positive rate (mostly gpt-5.6-luna, 2 rows muse-spark)

| Condition | Unnecessary activation |
|---|---|
| A | 0/9 (0%) |
| B | 0/11 (0%) |
| C | 0/12 (0%) |
| D | 0/12 (0%) |
| E | 0/20 (0%) |

**Zero false positives across all 64 valid Bucket-C runs**, both
transports, both models. This matches Phase 2.1-2.3's CLI-only finding
(near-zero false positives) and extends it cleanly to MCP: neither
transport tempts the model into using OXIDE on an exact-file, literal,
tiny-edit task.

## 5. First meaningful discovery action, Bucket A (muse-spark) — REVISED

**Correction (post-review)**: the original version of this section
counted every native `read` before an OXIDE call as a competing,
avoidable action — including a `read AGENTS.md → load OXIDE skill →
oxide context` sequence, which is healthy activation, not a delayed-
activation failure. Recomputed directly from the raw per-run logs
(`raw/analyze_discovery_quality.py`, reading `logs/*.jsonl` — `results.jsonl`
itself was not touched) with reads split into `INSTRUCTION_READ`
(AGENTS.md/SKILL.md, or a `skill` tool call), `PROJECT_ORIENTATION_READ`
(README/manifest/bare-directory reads), `DIRECT_TARGET_READ` (a file the
task names explicitly — not applicable to Bucket A, which names no
files), and `IMPLEMENTATION_EXPLORATION_READ` (a source file opened
hunting for the implementation — the only `read` type that still counts
as competing with OXIDE). "First meaningful action" now skips
instruction/orientation/direct-target preamble and reports the first
OXIDE call or the first genuine exploration action, whichever comes
first:

| Condition | Refined distribution (n) |
|---|---|
| A | `OTHER_NATIVE_DISCOVERY:grep` 6, `preamble-only:PROJECT_ORIENTATION_READ` 3, `:glob` 1, `:bash` 2 |
| B | `oxide:bash` 4, `preamble-only:PROJECT_ORIENTATION_READ` 5, `OTHER_NATIVE_DISCOVERY:glob` 1, `:bash` 2 |
| C | `preamble-only:PROJECT_ORIENTATION_READ` 6, `OTHER_NATIVE_DISCOVERY:grep` 3, `oxide:oxide_search` 1, `:glob` 1, `:bash` 1 |
| D | `oxide:oxide_search` 7, `oxide:oxide_context` 2, `preamble-only:PROJECT_ORIENTATION_READ` 2, `OTHER_NATIVE_DISCOVERY:bash` 1 |
| E | `oxide:oxide_context` 9, `oxide:oxide_search` 8, `preamble-only:PROJECT_ORIENTATION_READ` 2, `OTHER_NATIVE_DISCOVERY:bash` 1 |

**Important artifact found while doing this per-call inspection, reported
here rather than silently absorbed into the numbers**: every
`preamble-only:PROJECT_ORIENTATION_READ` entry above (18/68 valid Bucket-A
runs total, 26% — spread across all five conditions roughly evenly, not
concentrated in any one) turned out on manual log inspection to be the
**same single-call degenerate session**: the model calls `read` with
`filePath: "/"` (the filesystem root), the harness's sandbox
auto-rejects it as an external-directory permission violation
(`external_directory (/*): auto-rejecting` in `opencode`'s stderr), and
the session ends there with no further tool calls. This is a muse-spark
model quirk unrelated to OXIDE, AGENTS.md wording, or transport — it
occurs at similar rates whether OXIDE is present or not (condition A, with
no OXIDE at all, has 3/12; conditions B–E range 2–6/12). It is **not**
genuine project-orientation reading and is **not** evidence of a
transport-specific problem; it inflates "missed activation" counts by the
same amount in every condition and does not bias the B-vs-D-vs-E
comparison. (Left as `activation_missed: true` in `results.jsonl`, exactly
as any other non-activation — this note only corrects how the
*discovery-action* tables should be read, not the raw activation
percentages in §2, which are unaffected.)

With that artifact identified: **D and E's genuine first meaningful
action is overwhelmingly an OXIDE MCP call** (9/12 for D, 17/20 for E, once
the degenerate sessions are set aside), while **B's genuine activations
mostly do go straight to the CLI** (4/12 as `oxide:bash`, i.e., no
avoidable exploration preceded it) — the original "B's first action is
usually a native `read`" claim doesn't survive contact with the raw logs;
most of what looked like delayed CLI activation was actually the `read /`
artifact, not the model exploring source before trying `oxide`.

## 6. Discovery efficiency, Bucket A (muse-spark, mean per run) — REVISED

**Correction (post-review)**: "mean native-explore calls" in the original
table counted every native `read` (before *or after* the OXIDE call) as
avoidable exploration. That conflates two very different things: reading
source *before* OXIDE activates (genuinely avoidable — the point of
discovery efficiency) and reading source *after* an OXIDE call to inspect
the file it pointed to (expected, healthy behavior — confirmed elsewhere
in this report, `failures.md`'s evidence-utilization finding that 68/68
OXIDE-using runs did a follow-up native read). Recomputed with three
figures: raw total calls (unchanged), total avoidable exploration
(`IMPLEMENTATION_EXPLORATION_READ` + grep/glob/bash, anywhere in the run),
and — the metric that actually measures discovery efficiency — avoidable
exploration **before** the first OXIDE call:

| Condition | Mean total tool calls | Mean avoidable exploration (whole run) | Mean avoidable exploration (pre-OXIDE only) |
|---|---|---|---|
| A | 4.9 | 4.2 | 4.2 (no OXIDE ever called) |
| B | 4.8 | 2.8 | 0.6 |
| C | 5.3 | 3.5 | 1.3 |
| D | 4.0 | 2.7 | **0.1** |
| E | 6.3 | 4.8 | **0.3** |

**D remains the most efficient condition** on the metric that matters —
almost no wasted exploration happens before it activates (0.1 avoidable
calls/run). **But E's original "least efficient" label was wrong**: once
pre- and post-activation exploration are separated, E's *wasted*
exploration before activating (0.3) is nearly as low as D's, and much
lower than B's (0.6) or C's (1.3). E's higher **total** call count (6.3)
is overwhelmingly legitimate follow-up reading after a single OXIDE call,
not redundant pre-activation searching and not a second OXIDE call on the
other transport (§transport-selection.md: 0/55 condition-E runs ever
called both transports). **This changes a conclusion this report
previously drew**: E is not a discovery-efficiency liability relative to
D — it activates just as cleanly as D does; its extra cost is the
persistent context tax documented in `context-economics.md`, not wasted
exploration. `recommendation.md` and `transport-selection.md` have been
corrected accordingly.

## 7. Coding-task outcomes (gpt-5.6-luna, real tests)

| Task | Condition | Success | Transport per rep |
|---|---|---|---|
| coding-py | A | 3/3 | NONE, NONE, NONE |
| coding-py | B | 3/3 | CLI_ONLY ×3 |
| coding-py | C | 3/3 | MCP_ONLY, NONE, MCP_ONLY |
| coding-py | D | 3/3 | MCP_ONLY ×3 |
| coding-py | E | 5/5 | CLI_ONLY, MCP_ONLY, MCP_ONLY, CLI_ONLY, CLI_ONLY |
| coding-ts | A | 3/3 | NONE ×3 |
| coding-ts | B | 3/3 | CLI_ONLY ×3 |
| coding-ts | C | 3/3 | NONE, MCP_ONLY, MCP_ONLY |
| coding-ts | D | 3/3 | MCP_ONLY ×3 |
| coding-ts | E | 5/5 | MCP_ONLY ×5 |

**34/34 (100%) real bug-fix success**, verified by each task's own test
suite (`verify.sh`), regardless of condition or transport. **Transport
choice has zero measured effect on coding correctness** on these two
tasks — matching Phase 2.2/2.3's own coding-outcome finding for CLI alone,
now extended to MCP: both transports (and no transport, for condition A —
the model solved both bugs via native grep/read alone too) reliably
produced a passing fix. This is a genuine "equal correctness, different
exploration cost" result, which the phase brief explicitly calls a valid
win — see discovery-efficiency data above for where the cost differs.
**No condition-E run combined both transports on the same coding task**
(0/10) — reinforcing the nav-task transport-selection finding
(`transport-selection.md`) on a second, independent task type.

## 8. Whole-session token totals (NOT decomposable into "OXIDE's share";
model-consistent groupings only, per protocol.md §4.5's non-pooling rule)

| Condition | Bucket A+B nav (muse-spark) | Bucket C nav (gpt-5.6-luna) | Coding (gpt-5.6-luna) |
|---|---:|---:|---:|
| A | 114,801 | 170,649 | 427,715 |
| B | 161,926 | 193,820 | 490,191 |
| C | 130,509 | 176,732 | 441,126 |
| D | 140,762 | 174,840 | 378,886 |
| E | 205,057 | 196,627 | 445,659 |

These are `measured` (OpenCode's own `step_finish.tokens.total`, summed
per run and averaged), whole-session totals that include everything
(system prompt, all native tool schemas, conversation) — not an OXIDE-
specific figure, and **not comparable across the two model columns** (the
absolute scale differs by model and by task type, not just by condition).
Within each column, condition E is consistently the most expensive or
near-most-expensive on nav tasks (matching its higher tool-call count from
§6), but D is actually the *cheapest* coding-task condition on gpt-5.6-luna
(378,886 vs A's 427,715) — MCP's efficiency advantage from §6 shows up in
whole-session cost too on real coding work, not just navigation.
