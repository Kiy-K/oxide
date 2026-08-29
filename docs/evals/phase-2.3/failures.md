# Failure attribution

## Two distinct failure episodes this phase, both infrastructure, both resolved

**1. `muse-spark-1.2-contributor-free` provider rate limit (28 runs, all
in the abandoned partial batch).** Confirmed via `opencode run
--print-logs`: `AI_APICallError: Rate limit exceeded. Please try again
later.` — a hard, explicit, server-side quota exhaustion, not activation-
policy-related, not fixable by retrying within this session. In
`--format json` mode (no `--print-logs`) this failure is **completely
silent**: empty stdout, empty stderr, the process just hangs until the
harness's own timeout — worth flagging as a real `opencode` UX gap (a
rate limit should surface as an error event in the JSON stream, not as
indistinguishable-from-a-hang silence). User was asked how to proceed and
chose to switch models (`protocol.md` §-1); the 28 records are preserved
at `raw/results.muse-spark-partial.jsonl.bak`, not deleted, per the
phase's "do not rewrite negative results" instruction, and are excluded
from every table in `activation-results.md`.

**2. `gpt-5.6-luna` hangs indefinitely without `--auto` (caught before any
real run, in the switchover smoke test).** No error, no timeout signal
in the logs — the session simply never advances past `step=1` in
`--print-logs` output. Root cause: without `--auto`, tool calls
(read/bash/edit) apparently wait on an interactive permission grant that
headless `opencode run` never provides for this model/provider pairing
(muse-spark's default permission set allowed these without `--auto`;
gpt-5.6-luna's did not). Fixed by adding `--auto` to every invocation in
`raw/run_variants.py`/`run_coding_outcome.py`/`run_interference.py`
before any of the 120+18 real runs in this phase's tables were executed
— not discovered mid-batch, so it did not contaminate any result.

## The main 120-run batch and both follow-on tiers: zero timeouts, zero dead runs

Once running on `gpt-5.6-luna` with `--auto`, this phase saw **none** of
Phase 2.2's client-side reliability problems: no permission-denied
`read("/")` session deaths (the dominant Phase 2.2 failure mode, ~20% of
runs there), no unexplained timeouts. 120/120 navigation runs + 6/6
coding-outcome runs + 12/12 interference-check runs all completed
normally with a real answer. This is itself informative: Phase 2.2's
dead-run bug was specific to `muse-spark`'s interaction with `opencode`'s
permission layer, not a general `opencode` client problem — a future pass
evaluating a new model/client pairing should treat reliability as a
per-pairing property to verify, not something Phase 2.2's findings
guarantee.

## No CODING/REASONING, RETRIEVAL, or VERIFICATION failures

All 6 coding-outcome runs (E0 and E1, 3 reps each) passed the real
`verify.sh` test suite regardless of variant or whether OXIDE was used —
see `activation-results.md` and the coding-outcome section for detail.
No evidence of a retrieval-quality problem in any transcript inspected
during miss forensics or spot-checks of the main batch (every `oxide
search`/`oxide context` call that was inspected returned results the
model then correctly used).

## MODEL_BEHAVIOR / TASK_CLASSIFICATION note carried from `miss-forensics.md`

Not a failure of this phase's runs, but the underlying reason Phase 2.2's
`muse-spark` E0 baseline missed ~46% of Bucket-A tasks while this phase's
`gpt-5.6-luna` E0 baseline missed 0%: different models weight the
"unfamiliar task → reach for a code-context tool" heuristic very
differently by default. Neither number should be read as "the AGENTS.md
policy is X% effective" in the abstract — both are conditional on the
specific model tested, which is exactly why `protocol.md` §-1 insists the
two phases' percentages are not comparable.
