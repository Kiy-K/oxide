# Integration-boundary hardening for `src/structural.rs`

> **Superseded.** `src/structural.rs` was removed and replaced with
> precomputed structural relations — see
> `docs/precomputed-relations-migration/README.md`. The "treat it as
> replaceable" conclusion below held: it was in fact replaced. Kept as-is
> for historical record.

Evidence for treating `ast-grep-core` as a reliable, replaceable OXIDE
implementation detail, ahead of expanding structural retrieval. Builds on
`docs/astgrep-structural-search/README.md` (the Phase 3.4b spike) and
`docs/retrieval-coordinator/README.md` (which wired bounded structural
expansion into `context.rs` — the earlier spike doc's "not wired in this
commit" note is superseded by that later work, not by this one). The
implementor patterns cover full Python base-class and TypeScript interface
lists; no public API was added.

## Dependency footprint

`cargo tree -p ast-grep-core` (current lock state):

```
ast-grep-core v0.45.3
├── bit-set v0.11.1 → bit-vec v0.10.1
├── regex v1.13.1 (+ aho-corasick, memchr, regex-automata, regex-syntax)
├── thiserror v2.0.20 (+ thiserror-impl, proc-macro2, quote, syn)
└── tree-sitter v0.27.0 (+ streaming-iterator, tree-sitter-language)
    [build-dependencies: cc, serde_json + indexmap/hashbrown]
```

Checked each transitive dependency against `cargo tree -i <crate>` to see
whether it's *new* or already required by OXIDE's existing dependencies
(`tree-sitter-tags`, `rmcp`):

| Crate | Also required by | Net-new because of ast-grep-core? |
|---|---|---|
| `tree-sitter` v0.27.0 | OXIDE directly, `tree-sitter-tags` | No — single resolved version, confirmed via `cargo tree --duplicates` (no second `tree-sitter` copy) |
| `regex` | `tree-sitter`, `tree-sitter-tags` | No |
| `thiserror` | `rmcp`, `tree-sitter-tags` | No |
| `bit-set` / `bit-vec` | nothing else | **Yes** — the only genuinely new runtime crates |
| `tree-sitter-language` | `tree-sitter` itself | No (tree-sitter's own dependency, not ast-grep-core's) |

**No grammar crates** (`tree-sitter-rust`, `tree-sitter-go`, etc.) appear
anywhere in the resolved graph — confirmed by `cargo tree | grep -i
tree-sitter` returning only `tree-sitter`, `tree-sitter-tags`,
`tree-sitter-python`, `tree-sitter-typescript`, all pins OXIDE already had
before `ast-grep-core` was added. This is the concrete confirmation of the
existing doc comment's claim ("no second source of truth for which grammar a
language uses"): `ast-grep-language` (the crate ast-grep's own CLI uses,
bundling ~23 grammar crates) is not a dependency at all, direct or
transitive — `ast-grep-core`'s `default-features = false, features =
["tree-sitter"]` genuinely has zero grammar crates of its own.

`hashbrown`/`indexmap` show a second version resolved via ast-grep-core's
`serde_json` **build-dependency** (used at compile time, not linked into the
shipped binary) — not a runtime duplication hazard.

## Binary size impact

Measured directly (`cargo bloat` unavailable in this environment), by
building the release binary twice from the identical working tree: once as
committed, once with `ast-grep-core` temporarily removed from `Cargo.toml`
and its one call site in `context.rs` stubbed out (`pub mod structural;`
also removed from `lib.rs`), then reverted via `git checkout` before
committing anything — the temporary build was never part of any commit.

```
with ast-grep-core:    14,454,264 bytes
without ast-grep-core: 14,333,592 bytes
delta:                 120,672 bytes (0.84%)
```

Confirms the Phase 3.4b spike's unmeasured claim ("minimal by construction")
with an actual number: the whole structural-search feature — the crate, its
`bit-set`/`bit-vec` dependency, the two `impl_ag_lang!` language wrappers,
and the wired `context.rs` call site — costs under 1% of the release binary.

## Conformance suite (`src/structural.rs`, `#[cfg(test)] mod tests`)

Grew from 4 to 12 tests. The original 4 pin regressions caught during the
Phase 3.4b spike (cross-file implementors, AST-precision vs. lexical
matching, Python subclassing, method-style calls). The 8 added here target
edges that spike's fixture-scale benchmark never exercised — each verified
empirically against the real `ast-grep-core` behavior, not assumed:

| Test | Confirms |
|---|---|
| `typescript_finds_method_style_calls_not_just_bare_calls` | TS/TSX had only bare-call coverage before; method calls (`client.shouldRetry(1)`) now confirmed working, matching the existing Python coverage |
| `tsx_finds_implementors_across_component_files` | TSX had **zero** test coverage despite being a fully wired `Language` variant in both `find_implementors`/`find_callers` |
| `tsx_finds_bare_and_method_calls_inside_jsx_expressions` | A call inside a JSX attribute expression container (`onClick={() => api.fetchData(1)}`) is found, not just top-level calls |
| `malformed_source_returns_empty_instead_of_panicking` | tree-sitter's error tolerance (ERROR nodes, never a parse failure) means ast-grep pattern matching over broken Python/TS source degrades to "no match," never panics, for both `find_implementors`/`find_callers` |
| `empty_file_list_returns_empty_instead_of_panicking` | The `files: &[]` edge case is safe |

The three previously documented implementor gaps are now positive
regressions: `typescript_implements_list_matches_every_interface`,
`python_multiple_inheritance_matches_every_base`, and
`typescript_extends_plus_implements_matches_both_sides`. The patterns still
match only class declarations in the caller-supplied bounded file list.

Run: `cargo test -j 2 --lib structural`.

## Request-time structural work stays bounded

Confirmed by direct code inspection, not just behavioral inference:
`context.rs`'s bounded ast-grep expansion loop builds `scope_files` with an
explicit, one-line invariant —

```rust
if scope_files.len() >= max_files {
    break;
}
```

— making it structurally impossible for the file list handed to
`AstGrepProvider::find_callers` to exceed `RetrievalMode::structural_budget()`'s
`max_files` (2 anchor seeds × 3 files in `Balanced`, 3 × 6 in `Quality`, none
in `Fast`). The existing `bounded_ast_grep_expansion_is_mode_gated` test
(`src/context.rs`) confirms the on/off mode-gating behaviorally. A
black-box test of the numeric file cap specifically was not added: once
`AST_GREP_HITS_PER_SEED` (2) truncates the results that flow into the
context pack, a properly-bounded 3-file scan and a hypothetically-unbounded
scan become indistinguishable from `build_context`'s public output alone —
proving the cap this way would need exposing an internal counter for tests
to observe, which is scope creep for a pass that must not redesign the
harness. The `docs/astgrep-structural-search/README.md` 10-70x
bounded-vs-unbounded latency measurement remains the standing empirical
evidence that this bound matters in practice.

## No hidden grammar bundle

Re-confirmed for this pass (see Dependency footprint above): the resolved
dependency graph contains exactly the same four grammar crates OXIDE already
pinned before `ast-grep-core` existed (`tree-sitter-python`,
`tree-sitter-typescript`, plus `tree-sitter`/`tree-sitter-tags` themselves),
and nothing from `ast-grep-language`'s bundled set. `Language`/`LanguageExt`
continue to be implemented directly against OXIDE's own
`languages::PYTHON_PROFILE`/`TYPESCRIPT_PROFILE`/`TSX_PROFILE` grammar
instances — still the single source of truth for which grammar a language
uses, unchanged by this pass.

## Upgrade rule

Changing `ast-grep-core`'s version pin (`Cargo.toml`, currently `=0.45.3`
exact) requires, before the pin changes:

1. `cargo test -j 2 --lib structural` — the 12-test conformance suite above
   must pass unmodified in behavior. `Language`/`LanguageExt` are unstable,
   pre-1.0 traits implemented directly (not just called) — a minor ast-grep
   version could change pattern-matching semantics without a compile error.
2. `cargo run --example structural_benchmark --release` — the structural
   recall benchmark (`fixtures/structural_benchmark.json`) must still show
   the same recall improvements documented in
   `docs/astgrep-structural-search/README.md`.
3. `tests/benchmark_gate.rs` (`cargo test`) — the frozen hybrid-retrieval
   gate, unaffected by structural search directly but a required sanity
   check that nothing else regressed.
4. Re-run the dependency audit above (`cargo tree -p ast-grep-core`,
   `cargo tree --duplicates`) — a version bump could introduce a new
   transitive grammar crate or duplicate `tree-sitter`, silently reversing
   the "no hidden grammar bundle" property this pass confirmed.

If any of the three list/clause regressions documented above changes
behavior as a side effect of the version bump, that's a material finding for
the bump's own commit message — not something to silently absorb.

## Verdict

The abstraction boundary holds: `ast_grep_core` types do not leak outside
`src/structural.rs` (confirmed structurally at v0.45.3, unchanged by this
pass), the dependency footprint is small and precisely accounted for
(two genuinely new crates, ~0.84% binary size), request-time work is
provably bounded by a one-line invariant, and the conformance suite now
exercises both languages, TSX (previously untested), and failure modes
(malformed/empty input), while the formerly missing implementor list shapes
are covered by positive regressions. **`ast-grep-core` is robust enough to treat as replaceable
infrastructure**: a future swap or upgrade has a concrete suite (conformance
+ structural benchmark + dependency audit) to validate against, not just
"it compiled."
