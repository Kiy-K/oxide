# Coverage baseline: v0.1

Captured on 2026-08-30 at commit `145eae617b169257be44aea190b057c2eeb01f0a`,
with OXIDE `0.1.0`, Rust `1.98.0`, `llvm-tools-preview`, and
`cargo-llvm-cov 0.6.19`. The complete suite reported 95 executable tests,
including seven MCP e2e tests.

```bash
cargo llvm-cov --workspace --all-targets --json --summary-only \
  --output-path coverage/summary.json
```

| metric | covered | total | coverage |
|---|---:|---:|---:|
| Lines | 3,701 | 4,366 | 84.77% |
| Functions | 374 | 483 | 77.43% |
| Regions | 6,144 | 7,281 | 84.38% |

Coverage is a diagnostic signal, not a quality score. v0.1 intentionally has
no repository-wide percentage threshold: a stable baseline and risk coverage
are more useful than an arbitrary target. CI fails if coverage generation
breaks, not because a percentage changes.

## Risk audit

| Classification | Finding | Evidence / disposition |
|---|---|---|
| HIGH RISK | Incremental mutation, full/incremental parity, embedding invalidation | `incremental.rs`, `full_incremental_parity.rs`, and `embedding_staleness.rs` exercise mutations, deletes, renames, and reuse. No gap found. |
| HIGH RISK | Index version checks and interrupted SQLite metadata writes | `service_hardening.rs` and `interrupted_index_recovery.rs` cover incompatible metadata and atomic recovery. No gap found. |
| HIGH RISK | Concurrent access and deterministic ordering | `cli_e2e.rs` and `determinism_stress.rs` exercise concurrent indexing/reads and repeated mutation sequences. No gap found. |
| HIGH RISK | MCP initialization, two-tool schema, malformed arguments, service errors, deterministic and concurrent reads | `mcp_e2e.rs` has seven real-stdio protocol tests. No gap found. |
| MEDIUM RISK | Successful external HTTP-embedder responses | Excluded from required CI because it would require a network service; loopback failure behavior is covered. |
| MEDIUM RISK | Binary-process coverage attribution | `cli.rs` and `mcp.rs` are under-reported because e2e tests spawn the real binary as a child process. The tests still execute their transport contracts; do not replace them with unit tests solely to raise coverage. |
| LOW RISK | CLI rendering branches and error-display plumbing | Covered behaviorally by CLI e2e tests where relevant; remaining branches are presentation and conversion detail. |

No tests were added in Phase 3.2: the audit found no concrete untested
high-risk failure mode, and adding tests merely to inflate the percentage
would reduce signal. Remaining gaps are external-provider success behavior and
coverage attribution for child-process transports. Add focused tests only when
the project introduces a deterministic local HTTP fixture or a reproducible
child-process coverage collector.
