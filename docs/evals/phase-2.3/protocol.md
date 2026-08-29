# Phase 2.3 — activation miss forensics + minimal policy refinement

## -1. Model switch mid-phase (read this before the numbers below)

Phase 2.2 and the miss-forensics work in §1 below used
`opencode/muse-spark-1.2-contributor-free`, matching every prior
evaluation in this repo. Partway through this phase's variant batch, that
model's provider returned `AI_APICallError: Rate limit exceeded` on every
call (confirmed via `opencode run --print-logs`, not inferred from
timeouts alone) and stayed down through repeated health probes. The user
was asked how to proceed and chose to switch models rather than wait or
stop with partial data.

**All variant-comparison results in `activation-results.md` (E0–E4) use
`openai/gpt-5.6-luna` instead**, discovered via `opencode models`. Two
consequences:

1. **E0 was re-run fresh on the new model**, not carried over from Phase
   2.2's muse-spark numbers — this phase's E0 is its own baseline, on its
   own model, and is not directly comparable to Phase 2.2's `activation-
   results.md` percentages. The miss-forensics in §1 (which diagnosed
   *why* muse-spark misses happened) still stands as the reasoning behind
   which variants to test; it is not re-validated against gpt-5.6-luna's
   own miss patterns in this phase.
2. **`gpt-5.6-luna` requires `opencode run --auto`** (auto-approve
   permissions) to make any tool call at all — without it, every run
   hangs indefinitely after `step=1` (no permission granted, no timeout,
   no error — confirmed via `--print-logs`, the session log shows no
   further activity after the first LLM step). `muse-spark` did not need
   this flag. `raw/run_variants.py` was updated to always pass `--auto`.
   The 28 muse-spark records collected before the switch (1 valid, 27
   timeouts against a now-known-unrelated rate limit) are preserved at
   `raw/results.muse-spark-partial.jsonl.bak` /
   `raw/logs-muse-spark-partial/` rather than discarded, per the phase's
   own "do not rewrite negative results" instruction — they are not used
   in any table in this phase's `activation-results.md`.

This is itself a finding worth carrying forward: **the exact same
CLI+skill+AGENTS.md integration surface behaves differently across
client/model pairings in ways that have nothing to do with OXIDE** (a
permission-flag requirement, a provider rate limit). Any future
activation-layer work should treat "does this client/model need `--auto`
or an equivalent" as a setup check, not an assumption inherited from the
last model tested.

## 0. Starting point

Phase 2.2 is committed at `be0dc09` (`docs: Phase 2.2 activation-layer
evaluation (CLI transport, not MCP)`), worktree clean, verification gate
passing before this phase's work began (re-run and confirmed: fmt/clippy/
test/benchmark all clean, `git diff --check` clean). No product code
changes in Phase 2.2 or Phase 2.3.

**E0** = Phase 2.2's winning condition, taken as the frozen baseline for
this phase: CLI (`oxide` on `PATH`) + `skills/oxide-code-context/SKILL.md`
(unchanged) at `.opencode/skills/oxide-code-context/SKILL.md` + the tiny
`AGENTS.md` rule:

```markdown
## OXIDE

For unfamiliar multi-file coding tasks, use `oxide context` before broad
repository exploration. Use `oxide search` for focused follow-up
discovery. For exact known-file or literal tasks, use normal tools
directly. Read source before editing.
```

This phase does **not** repeat the A–E comparison from Phase 2.2. Every
run in `results.jsonl` uses the skill and AGENTS.md present (no bare-CLI
or no-instruction condition retested) — the question is narrower: what
small change to the instruction text (and, for one variant, the skill's
frontmatter description) moves Bucket-A activation above E0 without
raising Bucket-C false positives.

## 1. Miss forensics (before touching anything)

See `miss-forensics.md`. Read all 6 valid Bucket-A/condition-E misses
from Phase 2.2's raw logs directly (not inferred from counts). Finding:
100% of diagnosable misses are `NATIVE_DEFAULT` (the model goes straight
to `grep`/`read` without ever attempting `oxide`, despite the rule and
skill both being present) — no instance of `COMMAND_FRICTION`,
`LATE_ACTIVATION`, `INSTRUCTION_CONFLICT`, or `INFRASTRUCTURE` in this
miss set, and no way to separate `NOT_NOTICED` from `NATIVE_DEFAULT` from
this transport's event stream (it doesn't carry model reasoning tokens —
documented as an explicit limitation, not glossed over). This is what
motivated testing E1/E3 (which target the native-default reflex
directly) alongside E2/E4 (which the miss data does not predict will help
as much, but the phase brief asks for all four to be tested — see §4).

## 2. Variants tested

All five keep the skill body, the CLI, and every other Phase 2.2
condition-E element fixed. Only the text below changes.

| Variant | AGENTS.md | Skill frontmatter `description` |
|---|---|---|
| **E0** (baseline) | original Phase 2.2 block (above) | unchanged |
| **E1** | stronger first sentence ("where the implementation path is not already known") | unchanged |
| **E2** | spells out the exact `oxide context --task "<task>" --json` / `oxide search "<question>" --json` commands | unchanged |
| **E3** | explicit if/then decision rule (`Unknown implementation path -> ... / Known exact file/literal target -> ...`) | unchanged |
| **E4** | same as E0 | rewritten to lead with "Use BEFORE grep/read when..." |

Full text of each: `raw/run_variants.py` (`E0_AGENTS`…`E3_AGENTS`,
`E4_SKILL_DESCRIPTION`) — kept next to the harness so it can't drift from
what was actually run.

## 3. Tasks

Same 4 Bucket-A + 4 Bucket-C tasks as Phase 2.2, copied verbatim
(`raw/run_variants.py::TASKS`), not redesigned after seeing Phase 2.2's
results. Bucket B is out of scope for this phase (the brief narrows scope
to Bucket-A activation improvement + Bucket-C false-positive control).

## 4. Reps

3 per (task, variant) = 120 nominal runs (5 variants × 8 tasks × 3 reps).
Extended to 5 for any variant that looks promising or ambiguous after the
first pass, per the brief's own allowance — see `activation-results.md`
for which variants got the extension and why.

## 5. Instrumentation

Same structured `opencode run --format json` event stream as Phase 2.2.
Two additions the miss-forensics work motivated:

- **`late_activation`**: `oxide` was called, but only after ≥2 prior tool
  calls (i.e. the model did real native exploration *before* reaching for
  OXIDE — the "grep → read → grep → oxide context" anti-pattern the
  brief's §8 explicitly asks to track separately from clean first-call
  activation).
- **`dead_run`** is now computed inline in the harness itself (exactly one
  tool call, and it errored) rather than as a separate post-hoc pass over
  logs — same detection logic as Phase 2.2's `analyze_results.py`, moved
  earlier so `results.jsonl` already carries it and `appropriate`/
  `missed`/`unnecessary` are `null` for dead/timed-out runs from the
  start (Phase 2.2 had to patch this in after the fact — fixed here).

## 6. Coding outcome

Reuses `eval-agent/tasks/py_bug_retry` from Phase 2.2 verbatim (see
`activation-results.md` for the run under the winning variant). Given
Phase 2.2's coding-outcome tier lost 70% of its runs to the same
client-side dead-run/timeout attrition, this phase runs it with enough
reps up front to budget for that, rather than needing a second
extension pass.

## 7. Instruction-interference check (§12, documented only)

Unlike Phase 2.2, `--pure` does **not** hang for `gpt-5.6-luna` (§-1) —
this made a real clean-vs-normal comparison possible for the first time.
12 runs (2 Bucket-A tasks × 2 configs × 3 reps, winning variant E1):
`interference.jsonl`.

**Activation itself is unaffected**: 12/12 runs used `oxide` regardless
of config (clean or normal), with similar native-calls-before-oxide
(clean mean 1.67, normal mean 1.33 — both small, same order of
magnitude).

**What `--pure` actually strips is narrower than "clean"**: it removes
the ~30 ambient Skills (`ponytail`, `codebase-memory`, etc.) but **not**
the `codegraph` MCP server — `codegraph_codegraph_explore` is the first
tool call in 4/6 "clean" runs (vs. 0/6 "normal" runs, where the model
always reaches for a `skill` tool first instead). This means `--pure` is
not actually a fully clean baseline in this environment: `codegraph`
(a competing code-context tool, exactly the kind of thing the original
Phase 2.2 brief's §5 asked to disable) persists through it regardless.
Recorded as a genuine environment/tooling limitation — per the brief,
documented only, no compatibility hack attempted.
