# Java ContextBench before/after

The fixed selection produced 16 rows from 6 repositories; rows SHA-256: `4041983b1e94233a2425963fadd0abcd39f13c36a0401d03998309776bf65430`. All 16 corpora fetched. Metrics below are the `heldout.verified` aggregate; gold classes are from the same gold audit in both arms.

| Metric | Before (3492b38) | After (Java support) |
|---|---:|---:|
| Entry recall@20 | 0.214658 | 0.310144 |
| Routed coverage | 0.214658 | 0.759177 |
| Complete span recall@1,024 | 0.000000 | 0.002604 |
| Complete span recall@4,096 | 0.000000 | 0.026042 |
| Line recall@4,096 | 0.000000 | 0.094019 |
| Gold-audit classes | exact 37; path 50; unresolved 10 | exact 37; path 50; unresolved 10 |
| Median index time | 0.3374 s | 1.7917 s |
| Peak RSS | 208.72 MiB | 850.55 MiB |

No tuning was applied; changes are reported as measured, including the higher after-arm median index time and RSS. Outputs: `context-before/`, `context-after/`.
