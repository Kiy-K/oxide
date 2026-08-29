# Miss forensics — E0 (Phase 2.2 condition E) Bucket-A misses

E0 = `E` from Phase 2.2 = CLI + `SKILL.md` (`.opencode/skills/oxide-code-context/`)
+ tiny `AGENTS.md` rule. Source data: `docs/evals/phase-2.2/results.jsonl`
+ `docs/evals/phase-2.2/logs/*.jsonl` (committed at `be0dc09`, unmodified).

13 valid Bucket-A/condition-E runs (after excluding the 2 timeouts and the
dead-run client bug documented in Phase 2.2's `failures.md`): 7 hits, 6
misses. Every miss's full raw transcript was read directly (not
sampled/inferred from aggregate counts).

## Epistemic limitation, stated up front

The `opencode --format json` event stream captures `text`, `tool_use`,
`step_start`/`step_finish` — it does **not** stream the model's private
reasoning/thinking tokens (checked: only those four event/part-type pairs
appear in any log — see `raw/`). This means `NOT_NOTICED` and
`NATIVE_DEFAULT` cannot be told apart from direct evidence of "the model
considered and rejected OXIDE" — there is no such evidence available in
this transport. The classification below infers `NATIVE_DEFAULT` over
`NOT_NOTICED` from indirect but fairly strong evidence: Phase 2.2 already
confirmed (`client-instruction-paths.md`) the AGENTS.md rule is
system-prompt-injected on every run regardless of outcome (not something
the model opts into reading), and the *same* task under the *same*
condition E hits 54% of the time — a rule that was genuinely never seen
would produce a rate indistinguishable from condition D-without-skill or
condition A, not a coin-flip that clearly beats every single-mechanism
condition (see Phase 2.2 `activation-results.md` §1). A rule the model
never sees can't produce that pattern; a rule it inconsistently applies
can. This is not certain, but it's the label the composite evidence
supports — recorded as an inference, not an observation.

## Miss-attribution table

| Run | Task | First 1-2 tool calls | Resolved via | Category |
|---|---|---|---|---|
| A1-E-r2 | 4xx retry eligibility | `grep("retry")` → `read(retry.py)` | native, 2 calls | NATIVE_DEFAULT |
| A1-E-r5 | 4xx retry eligibility | `read(repo/)` → `grep("retry")` → `read(retry.py)` | native, 3 calls | NATIVE_DEFAULT |
| A4-E-r2 | backoff delay growth | `read(repo/)` → `grep("retry")` → `grep("backoff")` → `read(retry.ts)` → `read(client.ts)` | native, 5 calls | NATIVE_DEFAULT + task-design confound (see below) |
| A4-E-r3 | backoff delay growth | `bash(grep -r "backoff" ...)` → `read(retry.ts)` → `read(client.ts)` | native, 3 calls | NATIVE_DEFAULT + task-design confound |
| A4-E-r4 | backoff delay growth | `read(repo/)` → `grep("backoff|retry|delay")` → `read(retry.ts)` | native, 3 calls | NATIVE_DEFAULT + task-design confound |
| A4-E-r5 | backoff delay growth | `read(repo/)` → `grep("backoff")` → `grep("retry")` → `read(retry.ts)` | native, 4 calls | NATIVE_DEFAULT + task-design confound |

**Every single miss is the same shape**: no attempt at `oxide` visible
anywhere in the transcript (not even a failed/malformed one — ruling out
`COMMAND_FRICTION`), no other instruction visibly cited or blamed (ruling
out `INSTRUCTION_CONFLICT` as an *observed* cause — the ambient
`ponytail`/`codegraph` layer is a standing confound per Phase 2.2 but
nothing in these 6 transcripts shows the model citing or acting on it),
`oxide` never appears at all so there's no "late" partial activation to
speak of (ruling out `LATE_ACTIVATION` — that category describes a
different failure than what happened here), and every run completed
normally with `returncode=0` (ruling out `INFRASTRUCTURE`). **Zero of the
6 misses show `SKILL_NOT_LOADED` as a distinct signal from plain
`NATIVE_DEFAULT`** — the skill was never invoked, but neither was
`oxide` attempted through any other path, so there's nothing to
distinguish "considered loading the skill and didn't" from "never
considered any OXIDE path."

## The A4 task-design confound (and A1's separate reliability problem)

Per-task E0 hit rate on valid (non-timed-out, non-dead) runs:

| Task | Valid n | Hits | Rate |
|---|---|---|---|
| A1 (4xx retry eligibility) | 2 | 0 | 0% |
| A2 (cache expiry) | 3 | 3 | 100% |
| A3 (token refresh) | 3 | 3 | 100% |
| A4 (backoff delay) | 5 | 1 | 20% |

4 of 6 misses (67%) are `A4`. Every `A4` miss resolves via a `grep` for
the literal word **"backoff"** — a rare, highly specific term that
happens to appear almost nowhere except the exact target file
(`src/net/retry.ts`, symbol `ExponentialBackoff.backoffMs`). This is not
true of `A2`/`A3`, whose 100% valid-run hit rate suggests the opposite
problem never occurs there. This means part of E0's overall 54% Bucket-A
number is **task-composition-dependent**: one of the four Bucket-A tasks
(`A4`) is disproportionately easy for native grep specifically because of
a wording choice in the task prompt, not because of anything about repo
size or unfamiliarity in general.

`A1` is a separate story: only 2 of its 5 nominal E0 reps survived as
valid runs — the other 3 (`A1-E-r1`, `A1-E-r3`, `A1-E-r4`) are the
permission-denied `read("/")` client-bug dead runs documented in Phase
2.2's `failures.md`, not real misses. `A1`'s 0/2 looks alarming in
isolation but n=2 is too small to trust as a real "A1 never activates"
signal — it's better read as "A1 has the worst dead-run attrition of the
four Bucket-A tasks under condition E specifically," which is itself
worth watching in Phase 2.3's reruns (same client bug, unrelated to which
AGENTS.md variant is active).

Phase 2.3 keeps both tasks unchanged per its own §6 ("do not redesign
tasks after seeing results") — recorded here so a future pass knows why
`A1` and `A4`'s numbers may move differently from `A2`/`A3` when testing
variants, for two unrelated reasons (client reliability vs. task
wording).

## What this implies for variant selection

All 6 diagnosed misses point to one mechanism — the model, even with the
rule and skill both present, defaults to native `grep`/`read` unless
something makes the "unfamiliar task → OXIDE first" branch more salient
at decision time. That is squarely the failure mode `E1` (a more decisive
first sentence) and `E3` (an explicit if/then decision rule) are designed
to fix. `E2` (spelling out the exact `oxide context`/`oxide search`
command) and `E4` (skill metadata refinement) target failure modes this
miss set did not actually exhibit — no run attempted and fumbled an
`oxide` invocation, and no run shows evidence of trying and failing to
discover the skill. Both are still tested per the phase brief (§4), but
this is the a priori expectation the diagnostic work supports: **E1/E3
are more likely to move Bucket-A activation than E2/E4**, because the
observed problem is "never considered/chose not to," not "tried and
struggled."
