# Read-only repository status intent

`oxide status [PATH]` reports the last published generation without capturing or indexing the worktree. It defaults to `.` and accepts `--data-dir` and `--json`. The public status operation raises `NotIndexed` when no snapshot exists; the CLI catches only that error and presents an unindexed valid directory as a successful, structured state with an indexing instruction. Invalid paths and damaged or incompatible stores retain typed errors.

A public typed status operation uses the existing pinned shared reader regardless of ownership. Every indexed result derives its snapshot identity, counts, coverage and diagnostics from one pinned view; it does not imply worktree freshness. Report stored skips and case collisions, file-level coverage and diagnostic details, and actual lexical availability. Existing API behavior and stored formats do not change. Reader pins and Windows reader copies are temporary infrastructure, not index generations.

Regression tests cover both output modes, unindexed and error cases, coverage and diagnostic problems, concurrent ownership, and no index build or published-generation mutation. Required verification is focused tests, tier-specific independent review, and `mise run verify`.
