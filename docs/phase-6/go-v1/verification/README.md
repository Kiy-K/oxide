# Verification record

All heavy commands ran serially under
`systemd-run --user --scope -q -p CPUQuota=200% -p MemoryMax=3G -p MemorySwapMax=0`.
The TypeScript successor gate, Go real-repository evaluations and benchmarks, and both
ContextBench arms completed successfully; their outputs are retained in adjacent
directories. Both source-audit samples were checked against the pinned repository
files, with no contradiction found.

| Check | Result | Record |
| --- | --- | --- |
| TypeScript ContextBench successor gate | 17 passed | `contextbench-ts.log` |
| `mise run verify` | Ruff, formatting, mypy passed; pytest 265 passed, 7 TypeScript gate tests skipped by design | `mise-verify.log` |
| Wheel distribution smoke | Passed: indexing, Go search/context, MCP, lock ownership | `distribution-smoke.log` |
| Sdist distribution smoke | Passed: same checks | `distribution-smoke.log` |
| gin and Prometheus derivation/edit parity | Passed on corrected Go import ordering | `../real/*.json` |
| gin and Prometheus audits | 10 call and 10 import samples each; no contradictions | `../real/*-audit.json` |
| ContextBench before/after | Completed on identical 18 rows and corpora | `../context-before/`, `../context-after/` |

The wheel and sdist were installed into separate fresh Python 3.13 environments.
Build artifacts and those temporary environments were removed after both smokes;
the logs and result JSON remain. Windows and macOS runtime verification is pending
hosted CI. A later `go/types` oracle on gin is in [../README.md](../README.md#gotypes-oracle-gin). Four additional regressions covering receiver
type parameters, file-scope closure bindings, commented receivers and indexed
function-value calls were observed failing before their fixes, then passed with the
full suite.
