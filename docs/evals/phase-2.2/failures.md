# Failure attribution

164 total runs executed (160 navigation-task runs + 4 initial coding-outcome
runs before the rep-3-5 extension). Every run is classified below; none
were discarded.

## INFRASTRUCTURE — provider timeout (6 navigation runs + 4 coding-outcome runs)

Navigation tier: `A3` (ts, token-refresh localization) and `A4` (ts,
backoff-delay localization) account for all 6: `A3/C/r2`, `A3/E/r1`,
`A3/E/r2`, `A4/A/r1`, `A4/A/r2`, `A4/B/r1`. Each was retried once (per
`run_activation_eval.py`'s `_invoke` retry-on-timeout) and still hung with
zero stdout for the full 200s budget both times. This clusters on the two
`ts_repo` tasks specifically, not randomly across all 10 — plausibly the
free-tier `muse-spark` provider is slower/more failure-prone on
TypeScript-flavored prompts in this environment, but n=6 is too small to
claim that with confidence.

Coding-outcome tier (§13, `activation-results.md` §8): 4 of 10 runs
(`A/r4`, `A/r5`, `D/r3`, `D/r4`, `D/r5` — 5 actually, one script run had
no retry-on-timeout logic) hit the full 280s timeout with zero output,
all clustered in the *second half* of that tier's run sequence — this
looks like cumulative provider degradation over this phase's total call
volume rather than a per-task pattern, unlike the navigation-tier
timeouts above. Both patterns are recorded as open questions about this
specific free-tier provider under this session's load, not claims about
`opencode` or the model in general.

Excluded from all activation-rate denominators in both tiers.

## TRANSPORT — opencode client bug: permission-denied `read("/")` kills the session (32 runs, 20%)

The dominant failure mode, and the most important operational finding in
this phase. In 32/160 navigation runs (and in 3/10 coding-outcome runs
before extension), the *entire* tool-call trace is:

```
1. a one-line text preamble ("Investigating the spurious retry on 4xx...")
2. tool_use: read(filePath="/")  ->  state.status: "error",
   error: "The user rejected permission to use this spec..."
3. (nothing else — session ends)
```

`opencode`'s headless `run` mode auto-denies a `read` on the literal root
path `/` (a reasonable safety default) but then simply **terminates the
run** on that single denial instead of letting the model recover and try
a real path. This is a client reliability bug, not an OXIDE issue, an
activation-layer issue, or a task-design issue — it happens in every
condition and at every bucket, and the pristine-repo/prompt pair is
identical to runs that succeed normally seconds later. Confirmed via
direct log inspection (`docs/evals/phase-2.2/logs/*.jsonl`, tool_use
event with `state.status:"error"`).

Rate by condition (of non-timed-out runs):

| Condition | Dead-run rate |
|---|---|
| A (baseline) | 4/30 (13%) |
| B (bare mention) | 5/31 (16%) |
| C (skill only) | 8/31 (26%) |
| D (AGENTS.md only) | 10/32 (31%) |
| E (skill + AGENTS.md) | 5/30 (17%) |

C and D show a noticeably higher dead-run rate than A/B/E. This could be
a real interaction (an extra file in the repo root nudging the model's
very first tool call toward a wrong path) or could be noise at n≈30 per
cell — flagged, not claimed as causal. It does **not** correlate with
task bucket or repo language the way the timeout failures do.

**Consequence for this phase's numbers**: every dead run is excluded from
activation-rate denominators (`analyze_results.py`'s `valid` set = not
timed-out AND not dead). Folding these into "missed activation" would
have inflated condition A's apparent failure rate specifically, since
dead runs necessarily show `used_oxide=False` — the corrected numbers in
`activation-results.md` are the ones to trust.

**Not fixed in this phase**: per the phase's own §20, only a proven
integration bug in *OXIDE's* protocol/instruction delivery licenses a
product fix; this is a bug in the `opencode` client's permission-recovery
path, out of OXIDE's control and out of this phase's scope.

## Competing code-context tool present in every condition (confound, not a failure)

This environment has a live `codegraph` MCP server attached to every
`opencode run` invocation (global config, `~/.config/opencode/opencode.json`
loads it via a shared hooks config — see `protocol.md` §6). The phase
brief (§5) asks to disable competing code-context tools; `opencode run
--pure` (which disables external plugins/MCP) reliably hung with zero
output in this environment, plausibly because the same layer also carries
provider auth — see `protocol.md` §6. `codegraph_codegraph_explore`
appears as the **first action** in 2 observed Bucket-A runs (conditions A
and C) despite the target repo never having a `.codegraph/` directory (so
the call would return nothing useful). This means at least some of
condition A's 0% Bucket-A activation reflects the model reaching for a
*different* code-context tool rather than plain grep/read — a genuine
confound this environment could not remove, recorded here rather than
silently absorbed into "OXIDE has no competition in condition A."

## No CODING/REASONING or VERIFICATION failures attributable to OXIDE

Zero Bucket-C runs (trivial, exact-file tasks) called OXIDE unnecessarily
in any condition — 0/8 per condition, 40 total Bucket-C runs, no false
positives. Whatever else varies across conditions, none of them broke the
"don't reach for OXIDE on a known-file task" property that
`docs/agent-usage-policy.md` and `skills/oxide-code-context/SKILL.md`
already prescribe.

## RETRIEVAL / ALLOCATION / FRESHNESS — none observed

Every real `oxide search`/`oxide context` invocation observed in the logs
returned results the model then used in its final answer (spot-checked
several transcripts; e.g. the retry-eligibility localization runs that
used `oxide search` converged on `RetryPolicy.should_retry` /
`oxidepy/retry.py`, matching the fixture's benchmark-pinned gold answer).
No evidence in this phase of a retrieval-quality problem — consistent
with retrieval being explicitly frozen and out of scope.
