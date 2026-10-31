# Evidence and benchmark review rules (v2)

Scope: any PR/commit/report citing a retrieval, routing, packing or eval
result. These rules are about how a *reviewer* should weigh evidence a
change presents, not about the code paths themselves.

---

### EVD-001 — Benchmark claims must state their provenance
**Severity:** MAJOR · **Scope:** any claim of the form "recall improved" /
"routing beats retrieval" / "X is faster now."

**Invariant:** a benchmark claim must say which config produced it: the
frozen baseline (`tests/python/test_baseline.py` against
`docs/phase-6/baseline-vN/`), a ContextBench run
(`tests/python/contextbench.py` / `contextbench_ts.py`), or an ad hoc run;
which corpus and split (dev vs held-out); and the exact reproduction
command. These are not interchangeable. The frozen baseline is the gate;
ad hoc runs cannot replace it.

**What constitutes a violation:** a PR/report claiming a result without
naming the config, corpus, split and command; updating a frozen baseline
without a stated amendment and protocol note; tuning on held-out.

**Evidence required:** the reviewer must be able to locate the exact command
and config in the PR/doc. If missing, that absence is the finding.

**Exceptions:** `mise run verify` (lint/typecheck/tests) passing is a binary
gate, not a provenance claim.

---

### EVD-002 — Incomplete or cancelled runs are not evidence
**Severity:** MAJOR · **Scope:** ContextBench and any long-running eval.

**Invariant:** a run that was killed, timed out, cancelled or only partially
completed must not be cited to support or refute a claim. A partial result
file is missing data, not a smaller-but-valid version of the final result.

**What constitutes a violation:** citing "N of M tasks passed" from an
interrupted run without completing it or explicitly caveating the numbers
as provisional.

**Evidence required:** the run's own completion signal against what was
reported.

**Exceptions:** reading a result file mid-run for a status check is fine;
citing that read as finished is not.

---

### EVD-003 — A passing test is evidence, not automatic proof of coverage
**Severity:** MAJOR · **Scope:** any reviewer or PR claim of the form
"there's a test for this" / "existing tests still pass."

**Invariant:** a passing test supports only the specific invariant its
assertions actually check. Frozen artifacts must not be regenerated to make
a failure pass; a golden that changes without an intended cause is the
alarm.

**What constitutes a violation:** accepting a diff to a load-bearing
invariant because "existing tests still pass" without checking whether any
existing test pins that invariant; regenerating a frozen artifact to hide a
failure.

**Evidence required:** quote the actual assertion being relied on, not the
test name.

**Exceptions:** none.

---

### EVD-004 — Frozen baselines are not regenerated to pass
**Severity:** BLOCKER · **Scope:** `tests/python/test_baseline.py`,
frozen `docs/phase-6/baseline-vN/`.

**Invariant:** retrieval, routing and packing changes are evaluated against
the frozen baselines; never regenerate a frozen artifact to make a failure
pass. Legacy numbers are historical comparators, not v2 gates.

**What constitutes a violation:** editing a frozen baseline or golden to
make a test pass without a stated amendment and protocol review.

**Evidence required:** the diff to the frozen artifact and the amendment
note.
