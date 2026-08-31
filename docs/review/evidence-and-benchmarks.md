# Evidence and benchmark review rules

Scope: any PR/commit/report citing a retrieval, ranking, or eval-harness
result. These rules are about how a *reviewer* should weigh evidence a
change presents, not about the code paths themselves.

---

### EVD-001 — Benchmark claims must state their provenance
**Severity:** MAJOR · **Scope:** any claim of the form "recall improved" /
"hybrid beats vector-only" / "X is faster now."

**Invariant:** a benchmark claim must say which config produced it: the
committed fixture (`fixtures/benchmark.json`, offline hashed embedder) vs.
a real-repo/ContextBench run, which embedder/model, and whether it
reproduces the committed `docs/canonical-baseline.md` ruler or is a new ad
hoc run. These are not interchangeable. `fixtures/benchmark.json` is small
and mostly saturated — 10 of its 11 tasks already score recall@5 = 1.000
under the existing pipeline (`docs/astgrep-structural-search/README.md`) —
so a fixture-only result cannot support a claim of broad retrieval
improvement the way a real-repo run can, and a real-repo run cannot replace
the fixture as the frozen regression gate (`tests/benchmark_gate.rs` only
runs the fixture).

**What constitutes a violation:** a PR/report claiming a ranking result
without naming the config, fixture, and model that produced it; treating a
`fixtures/benchmark.json` result as a stand-in for a canonical/real-repo
claim (or vice versa); updating `docs/canonical-baseline.md`'s numbers
without including the exact reproduction command, the way the existing doc
does ("Reproduced fresh (this commit, no code change): ...").

**Evidence required:** the reviewer must be able to locate the exact
command and config in the PR/doc. If it's missing, that absence is the
finding — don't accept the numeric claim on faith, and don't try to infer
the missing provenance yourself.

**Exceptions:** `tests/benchmark_gate.rs` passing/failing is a binary gate,
not a provenance claim in this sense — see EVD-003 for how to weigh it.

---

### EVD-002 — Incomplete or cancelled runs are not evidence
**Severity:** MAJOR · **Scope:** Tier A/B eval harnesses
(`eval-agent/`, `scripts/agent_eval/`), `coordinator_benchmark`,
`structural_benchmark`, any ContextBench sweep.

**Invariant:** a benchmark or eval run that was killed, timed out,
cancelled, or only partially completed must not be cited to support or
refute a regression/improvement claim. A partial `cb_results.jsonl` or a
Tier B run stopped mid-repo is missing data, not a smaller-but-valid
version of the final result.

**What constitutes a violation:** a PR citing "N of M tasks passed" from a
run that was interrupted, without either completing it or explicitly
caveating the numbers as provisional; treating an intermediate read of the
resumable, keyed-by-`(task, condition)` `cb_results.jsonl` (this format is
resumable by design, per `AGENTS.md`) as if it were the finished result.

**Evidence required:** check the run's own completion signal against what
was actually reported — e.g. `tests/benchmark_gate.rs`'s exact-count
assertion (`assert_eq!(report.per_query.len(), 22, ...)`), or the harness's
expected task/repo count for Tier A/B runs.

**Exceptions:** reading `cb_results.jsonl` mid-run for a status check is
fine — the violation is citing that read as a finished result.

---

### EVD-003 — A passing test is evidence, not automatic proof of coverage
**Severity:** MAJOR · **Scope:** any reviewer or PR claim of the form
"there's a test for this" / "existing tests still pass."

**Invariant:** a passing test supports only the specific invariant its
assertions actually check — not the general area of code it happens to
touch. Several of `AGENTS.md`'s own load-bearing invariants exist precisely
because a narrow, targeted regression test was the only thing that caught a
real bug (e.g. `languages::tags::tests::content_hash_matches_span_text_reconstruction`,
the `ts-default-policy-const` export-const-override regression in
`docs/treesitter-tags-parity/README.md`) — a broader "the test suite is
green" signal did not catch these on its own.

**What constitutes a violation:** accepting a diff to a load-bearing
invariant because "existing tests still pass" without checking whether any
existing test actually pins that specific invariant; a new test whose
assertion is weaker than its name implies (e.g. asserting a field is
non-empty when the invariant actually requires a specific value or
ordering).

**Evidence required:** quote the actual `assert!`/`assert_eq!` line being
relied on, not the test function's name or the fact that the suite passed.

**Exceptions:** none.
