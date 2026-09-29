# Phase 4.1 tasks

Full task text lives in the scripts that consume it, not duplicated here, to
avoid the two ever drifting apart:

- **Tier 1** (34 prompts, zero agent runs): `raw/labeled_prompts.jsonl` — 14
  Bucket-A ("OXIDE should help": unfamiliar, multi-file, symptom/behavior-
  description discovery), 6 Bucket-B ("optional": subsystem named,
  implementation unknown), 14 Bucket-C ("should not activate": exact-file,
  literal, tiny edit). Taxonomy reused from
  `docs/evals/phase-3.1/tasks.md`; every prompt's wording is newly authored
  for this phase, not reused verbatim, per the roadmap's "fresh held-out
  tasks" instruction.
- **Tier 2** (3 tasks, real agent, small-n): `raw/tier2_agent_run.py`'s
  `TASKS` list — 2 Bucket-A, 1 Bucket-C, grounded in
  `fixtures/py_repo/oxidepy/notifiers.py` and
  `fixtures/ts_repo/src/auth/service.ts`, two real subsystems phase-3.1's
  own task set never exercised, chosen specifically so a model answering
  correctly can't be recalling this exact task text from a prior phase's
  eval logs.

See `protocol.md` §3 for why the split exists and §4 for how Tier 2's
per-condition fixtures are isolated.
