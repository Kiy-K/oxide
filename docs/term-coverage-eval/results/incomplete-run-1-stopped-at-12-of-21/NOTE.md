# Incomplete run — not evidence

Stopped by explicit user instruction before completion: 12/21 pinned Tier A
instances had been measured (60 rows = 12 tasks × 5 alphas) when the run was
killed. `results.jsonl` and `run.log` here are preserved for audit only.

**Do not use these rows as experiment evidence.** The run was stopped
specifically because the eval harness itself was too slow (redundant
per-alpha subprocess + query-embedding work), not because of anything about
the retrieval results observed so far — no conclusions were drawn from this
partial data, and none should be. The corroboration experiment was rerun
from a fresh output path after the harness was optimized; see
`docs/term-coverage-eval/README.md` for the actual result set.
