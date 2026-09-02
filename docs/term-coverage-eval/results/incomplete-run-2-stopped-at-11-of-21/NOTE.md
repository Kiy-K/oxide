# Incomplete run — not evidence

11/21 pinned Tier A instances had been measured (55 rows = 11 tasks x 5
alphas) when this run was interrupted by unrelated infrastructure work
landing on `main` (scoped base-update/pending-embedding primitives, the
`oxide watch` filesystem watcher) mid-session. `results.jsonl` here is
preserved for audit only.

**Do not use these rows as experiment evidence.** Per the resumed
experiment's own instructions, the corroboration sweep must be rerun from a
fresh output path against the frozen post-infrastructure `main`, not resumed
from partial output that predates those landings. See
`docs/term-coverage-eval/README.md` for the actual result set.
