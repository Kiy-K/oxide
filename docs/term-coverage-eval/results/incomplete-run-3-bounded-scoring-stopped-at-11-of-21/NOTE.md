# Incomplete run — 11/21, not final, but not discard-worthy either

Different in kind from `incomplete-run-1` and `incomplete-run-2`: those two
were genuinely interrupted mid-flight by harness bugs / infra changes and
their rows should not be read at all. This run's 33 rows (11 tasks x 3
alphas — 0.0/0.05/0.1) are real, valid measurements against the bounded-
scoring fix (`src/config.rs::TERM_COVERAGE_MAX_BONUS_FRACTION`,
`src/retrieval.rs`'s bounded-additive reweight, `SymbolKind::Module`
exclusion) and the eval-side module-exclusion fix
(`scripts/agent_eval/term_coverage_eval.py::is_coarse_symbol`) — just
stopped early (by explicit user instruction, after the indexing time cost
of commit-keyed worktrees turned out to be too high without a cache) before
covering all 21 pinned tasks.

**What it does show**, per-task, all fully self-consistent (same process
per task, alpha 0.0/0.05/0.1 compared only against each other):

- 9 of 11 tasks completely flat across all three alphas — zero movement in
  any metric.
- 2 of 11 (pylint `...51b4c299`, `...049a7048`) show real positive MRR/nDCG
  movement at alpha=0.1, zero regressions.
- The two tasks that regressed under the original multiplicative formula
  (`...2e76c8cd` Blueprint, `...9cca0774` Session.request) show zero
  disturbance at alpha 0.05/0.1 here — consistent with (though not fully
  dispositive of, given embedder-jitter baseline drift documented in the
  session) the bounded-bonus fix doing its job.

**What it does NOT show**: zero TypeScript coverage (code-server,
darkreader), zero pytest coverage — 82% of the measured tasks are
requests/pylint. Not enough to call KEEP or REJECT.

Read `docs/term-coverage-eval/README.md`'s status line for the current
overall verdict; this file's rows are one input to a still-pending full
21-task rerun (`OXIDE_TERM_COVERAGE_OUT_NAME` env var / `ALPHAS` in
`scripts/agent_eval/term_coverage_eval.py`), not the final answer.
