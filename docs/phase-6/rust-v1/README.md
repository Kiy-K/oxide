# Rust source support milestone evidence

Implemented semantics and limits: [../../rust.md](../../rust.md). Rust adds a language
slice to the existing engine; retrieval, routing, packing, the public API and the MCP
surface (`oxide_search`) are unchanged. The implementation does not run cargo, rustc,
rust-analyzer, build scripts, proc macros or repository code.

## Grammar and fixture conformance

Selected **tree-sitter-rust 0.24.2** with grammar ABI 15; the locked **tree-sitter
0.26.0** accepts ABI 13–15. The grammar publishes `cp39-abi3` wheels for Linux
x86_64/aarch64, macOS x86_64/arm64 and Windows amd64/arm64. Cargo manifests are read
with `tomllib`. `fixtures/rust_repo` is a two-crate workspace (a renamed path dependency,
a binary, a `tests/` target, nested and `#[path]` modules, reexports, `cfg_*!` wrappers).
`tests/python/test_rust.py` passes all 29 tests: declarations and impl symbols,
`crate`/`self`/`super`, reexport chains and cycles, the Cargo target-naming rule, macro
and `cfg` ambiguity, self-naming imports, malformed and deep input, cache validation,
deterministic order, and persisted incremental/full-rebuild parity. `mise run verify`
passed: 294 passed, 7 skipped (`verification/mise-verify.log`).

## Pinned real repositories

Both checkouts were evaluated without running their code. All captured Rust files parsed
completely. Symbol counts are Rust-only; entities/relations include all captured files.

| Repository (commit) | Rust files | Symbols by kind | Imports: resolved / ambiguous / unresolved | Calls: resolved / ambiguous / unresolved | References: resolved / ambiguous / unresolved | Cold derive / reused derive | Cold index / unchanged / edit | Peak RSS |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ripgrep (`3fce3b5bb023`) | 110/110 | 768 function, 445 impl, 2,199 method, 309 struct, 76 enum, 7 trait, 37 macro, 50 constant, 7 variable, 7 type | 611 / 4 / 438 | 4,213 / 66 / 4,578 | 3,727 / 4 / 2,965 | 0.754 / 0.280 s | 2.184 / 0.052 / 1.484 s | 305 MiB |
| tokio (`17638a0df225`) | 814/814 | 3,297 function, 1,985 impl, 4,605 method, 691 struct, 105 enum, 67 trait, 178 macro, 214 constant, 21 variable, 71 type | 2,683 / 103 / 4,671 | 8,532 / 214 / 16,939 | 9,014 / 208 / 14,148 | 3.156 / 1.118 s | 7.844 / 0.210 / 5.767 s | 520 MiB |

Import ambiguity is 4/1,053 edges (0.38%) for ripgrep and 103/7,457 (1.4%) for tokio.
Ambiguity is expected where `cfg` variants declare the same name, because `cfg` is not
evaluated and every variant is a candidate (inferred, not counted per edge). Unresolved
edges include the standard library, registry dependencies and method calls on receivers
other than `self` (see "Not modeled"). An unchanged rebuild reused every Rust fact, and
both derivation and persisted edit/full-rebuild parity held (`real/*.json`). The
separate three-iteration `bench.py` reports cold-index medians of 2.002 s (ripgrep) and
6.743 s (tokio), unchanged-index medians of 0.034 s and 0.091 s, peak RSS medians of
172.9 MiB and 259.4 MiB, and warm context medians of 0.093 s and 0.113 s
(`SearcherBuilder`/`Matcher`, `spawn`/`Runtime`). These laptop measurements are
diagnostic, not a performance gate.

Ten resolved cross-file calls and ten resolved imports per repository were inspected
against the pinned sources (`real/spotcheck.md`): 40/40 agreed, no contradiction. This
bounded spot check is not a precision estimate.

## rust-analyzer oracle (ripgrep)

OXIDE's resolved ripgrep `calls`, `references` and `imports` edges were scored against
`rust-analyzer 1.98.0` go-to-definition at each edge's evidence identifier. The oracle
used a `rust-project.json` generated from `cargo metadata --no-deps --offline`, with
build scripts, proc macros, cache priming and `checkOnSave` off, for the default and
`test` cfg of linux/x86_64. It ran no repository code.

| Measured (ripgrep `3fce3b5bb023`) | Agree | Disagree (first pass) | No oracle answer |
| --- | ---: | ---: | ---: |
| calls | 3,724 | 1 | 488 |
| references | 3,595 | 4 | 128 |
| imports | 505 | 15 | 91 |
| overall | 7,824 | 20 | 707 |

The first-pass precision was 0.99745. I inspected all 20 disagreements against the
pinned source, and none is an OXIDE error:

- 15 imports (`use crate::gitignore::{self, ..}`, `doc::version`, `crate::sinks`): the
  oracle's definition is the module itself (its file at line 1, or `pub mod sinks {`), and
  that is the module entity OXIDE targets. The scorer matched only symbol spans.
- 4 `Self` references: rust-analyzer navigates `Self` to the impl header
  (`impl Default for GlobSet`), while OXIDE targets the impl's self type.
- 1 call (`search(&args, mode)`, `crates/core/main.rs:89`): the target is
  `fn search` at line 113, as OXIDE reports. The oracle returned the sibling
  `mod search` file.

After this classification there are 7,844 agreements and 0 disagreements (precision 1.0)
on resolved edges with an oracle answer. That covers resolved edges in one cfg pair of one
repository only. The 707 edges with no answer got an empty go-to-definition response and
are not scored. Of 73 ambiguous edges, 62 include the oracle's definition among their
candidates. The scorer was not rerun with these corrections: `run_oracle.py` was edited
after the scored run, and a second run timed out waiting for rust-analyzer. So
`report.md` and `ripgrep-oracle.json` hold the first-pass output, and the classification
above was done by hand. The program and its outputs are in [oracle/](oracle/report.md).

## ContextBench diagnostic

Both arms used the same 22 rows from 9 repositories (`rust-rows.json`, SHA-256
`92f4a2cdab49cb6c53f5fe9021c35edfa3c212c00b1fe14d6f36e61c19551011`), the same corpora,
gold and scorer. Before ran b128a90 in an isolated environment; after ran this checkout.
Gold-audit classes were identical in both arms: 11 exact, 114 path-only, 13 unresolved
(138 spans); the unresolved class is excluded from verified metrics.

| Verified macro metric (22 tasks, 125 spans, 14,229 lines) | Before | After |
| --- | ---: | ---: |
| Entry recall@20 | 0.292767 | 0.323178 |
| Routed coverage | 0.304131 | 0.626804 |
| Complete span recall@1,024 | 0 | 0.005682 |
| Complete span recall@4,096 | 0 | 0.104167 |
| Line recall@4,096 | 0 | 0.096705 |

Median index time per task was 0.335 s before and 1.506 s after; peak process RSS was
154.3 MiB and 310.5 MiB. No parameters were tuned. This is a small diagnostic (n=22),
not a gate or broad quality claim. Raw scores, audits and timings are in
`context-before/` and `context-after/`; the run summary is
`verification/contextbench-rust.md`.

## Successor baselines, distribution and limitations

[`baseline-v11`](../baseline-v11/README.md) and
[`typescript-v8`](../typescript-v8/README.md) are identity-only successors for the
derivation identity change: metrics are byte-identical and only derivation/identity
fields changed (`verification/baselines.md`). Wheel and sdist smoke passed in fresh
virtual environments, including a Rust impl-method search and context check
(`verification/distribution-*.log`).

Unsupported constructs are listed under "Not modeled" in [../../rust.md](../../rust.md):
method calls on non-`self` receivers and trait dispatch, type-alias owners, enum-variant
glob imports, macro arguments/bodies and macro-defined items, `cfg` evaluation, and
registry/git dependencies. Windows and macOS Rust runtime and artifact checks remain
pending hosted CI; wheel availability does not establish runtime support. The platform
matrix and existing LadybugDB limits still apply.

Reproduce from a locked checkout, under the resource caps recorded in the evaluation
logs:

```sh
mise run verify
uv run --locked python scripts/eval_rust.py real /path/to/ripgrep docs/phase-6/rust-v1/real/ripgrep.json
uv run --locked python scripts/eval_rust.py audit /path/to/ripgrep docs/phase-6/rust-v1/real/ripgrep-audit.json
uv run --locked python scripts/bench.py /path/to/ripgrep --iterations 3 --warm 3 --query SearcherBuilder --query Matcher
uv run --locked python scripts/eval_rust.py rows /path/to/contextbench.parquet /path/to/rust-rows.json
uv run --locked python scripts/eval_rust.py fetch /path/to/rust-rows.json /path/to/corpora
uv run --locked python scripts/eval_rust.py context /path/to/rust-rows.json /path/to/corpora docs/phase-6/rust-v1/context-after
# rust-analyzer oracle: needs the 1.98.0 toolchain's rust-analyzer; paths are set at the top of the script
uv run --locked python docs/phase-6/rust-v1/oracle/run_oracle.py
```

The previous-engine ContextBench arm requires its recorded isolated environment and the
same rows and corpora. Distribution smoke follows [platforms.md](../../platforms.md).
Next language: Java or C#, chosen against a scoped fixture and a resolution oracle
(javac/JDT or Roslyn) before implementation, since both need the type-directed member
resolution that the Rust and Go slices leave unmodeled.
