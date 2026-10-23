# ContextBench Rust diagnostic

Selected `22` rows from `9` repositories. `rust-rows.json` SHA-256:
`92f4a2cdab49cb6c53f5fe9021c35edfa3c212c00b1fe14d6f36e61c19551011`.
All `22` corpus directories were already present; `fetch` was skipped as directed.
The same rows and scorer were used for both arms.

| Verified metric | Before (b128a90) | After (Rust support) |
|---|---:|---:|
| Entry recall@20 | 0.292767 | 0.323178 |
| Routed coverage | 0.304131 | 0.626804 |
| Complete span recall@1,024 | 0.000000 | 0.005682 |
| Complete span recall@4,096 | 0.000000 | 0.104167 |
| Line recall@4,096 | 0.000000 | 0.096705 |

Gold-audit class counts were identical in both arms: `exact=11`, `path=114`,
`unresolved=13` (138 audited spans). Median index time was `0.335331 s` before
and `1.505749 s` after. Peak RSS was `154.324 MiB` before and `310.461 MiB`
after. These are observed measurements; no tuning was performed.

Artifacts: `../rust-rows.json`, `../context-before/`, and `../context-after/`.
The before evaluator's first attempt used a script path relative to the baseline
worktree, where the script is absent; the corrected invocation ran the evaluator
from this checkout with the baseline venv's interpreter.
