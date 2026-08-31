# Phase 3.4b evidence: ast-grep structural search

Raw data backing the keep/wire decision. `src/structural.rs` module doc has
the architecture summary.

## Dependency footprint

`ast-grep-core = "=0.45.3"` (`default-features = false, features =
["tree-sitter"]`) only — confirmed via a scratch probe that this pulls in no
extra tree-sitter grammar crates, unlike `ast-grep-language` (the crate
ast-grep's own CLI uses), whose `builtin-parser` feature bundles ~23 grammar
crates (rust, go, java, php, ruby, html, yaml, kotlin, ...) with no
per-language opt-in — all-or-nothing under one feature flag. `ast-grep-core`
alone builds cleanly against the exact `tree-sitter 0.27` / `tree-sitter-
python 0.25` / `tree-sitter-typescript 0.23.2` pins the Phase 3.4a tags
migration already established, with no version conflict — ast-grep 0.45.3
happens to already be on tree-sitter 0.27 core.

`Language`/`LanguageExt` are implemented directly (two ~15-line macros,
copied with attribution from `ast-grep-language` 0.45.3's `src/lib.rs`, MIT)
against OXIDE's existing `tree_sitter_python::LANGUAGE` /
`tree_sitter_typescript::LANGUAGE_TYPESCRIPT` / `LANGUAGE_TSX` instances — no
second source of truth for which grammar a language uses, and no new grammar
dependency at all.

**API stability**: ast-grep-core is pre-1.0 (0.45.3) and this module
implements its `Language`/`LanguageExt` traits directly — real coupling to
an unstable API surface. Pinned exactly (`=0.45.3`, not `"0.45"`) rather than
allowing minor-version drift, unlike OXIDE's other tree-sitter dependencies
which float on a minor version. A future ast-grep upgrade needs a deliberate
review of `Language`/`LanguageExt`'s shape, not just a `cargo update`.

## Structural strategies tested

Two symbol-anchored intents, both implemented (`src/structural.rs`,
`StructuralSearchProvider::find_implementors`/`find_callers`):

- **Implementors**: definitions that implement/extend a given type name.
  Python: `class $NAME(TypeName): $$$BODY`. TypeScript/TSX: `class $NAME
  implements TypeName { $$$BODY }` and `... extends TypeName { $$$BODY }`.
- **Callers**: AST-precise call sites of a given function name, both bare
  (`fn(...)`) and method/attribute (`obj.fn(...)`) forms — a bare-only
  pattern was tried first and empirically missed a real call site
  (`policy.should_retry(attempt, error)`), fixed and pinned by
  `structural::tests::finds_method_style_calls_not_just_bare_calls`.

Callers is evidence-only for this commit, not wired to replace anything:
`extract_references`/`embed_text`/`content_hash` stay frozen exactly as the
Phase 3.4b brief requires. Replacing lexical reference-matching with
AST-precise call matching is a real, separately-scoped follow-on — it would
change `Symbol.references`, which changes `embed_text`, which forces the
same kind of full re-embed the tags migration's constant-capture fix
produced. Named here, not attempted.

## Benchmark results

`fixtures/benchmark.json` (the frozen, gated benchmark) has no headroom to
show a structural-search win: 10 of its 11 tasks already score recall@5 =
1.000 with the existing hybrid pipeline. Confirmed unchanged after this
work — 0.818 vector-only / 0.909 hybrid, byte-identical to the Phase 3.4a
baseline; `tests/benchmark_gate.rs` (hybrid >= vector-only, exact 22-row
count) still passes.

New tasks (`fixtures/structural_benchmark.json`, 4 tasks — 2 implementor, 2
caller, one Python + one TypeScript pair each) target relationships the
existing regression set has none of: multi-implementor and cross-file
caller. New source added to support them: `fixtures/ts_repo/src/net/
policies.ts` (`LinearBackoff`, `NoRetryPolicy` implementing the existing
`RetryPolicy` interface, plus a cross-file caller of `shouldRetry`) and
`fixtures/py_repo/oxidepy/notifiers.py` (`Notifier` base class with two
subclasses, plus a cross-file caller of `should_retry`). Both files include
a decoy comment mentioning the target name as plain text, to test whether
structural matching (correctly) ignores it.

`cargo run --example structural_benchmark --release`:

| task | baseline recall@5 | + structural recall@5 | anchor in baseline top-5 |
|---|---|---|---|
| ts-implementors-retrypolicy | 0.000 | 1.000 | yes |
| ts-callers-shouldretry | 0.000 | 1.000 | no |
| py-implementors-notifier | 0.500 | 1.000 | yes |
| py-callers-should-retry | 0.000 | 1.000 | no |

All four: baseline hybrid retrieval finds the *type/function itself* (or, in
two cases, doesn't even need to — the query text alone is enough to run the
structural query) but never the implementor/caller symbols, because their
bodies don't share the query's vocabulary. Structural search, anchored on
the type/function name, finds them directly regardless. Structural search
also surfaced genuine callers I hadn't listed in the hand-curated gold set
(`ApiClient.request` calling `shouldRetry`, two more Python test-function
callers of `should_retry`) — undercounted recall, not a precision problem,
and a sign the four gold lists are conservative rather than inflated.

AST-precision, separately confirmed: `structural::tests::
call_matching_is_ast_precise_not_lexical` — a comment (`// call fetch(x)`)
and a string literal (`"fetch(y)"`) containing the target text produce zero
matches; only the real call expression does. OXIDE's existing lexical
`extract_references` would count all three.

## Latency / size cost

Fixture-scale (7-8 files): 5-23ms per structural query — noise-level.

Real-repo scale (darkreader, copied out of the shared ContextBench cache,
109 `.ts` files, 1355 symbols), `cargo run --example structural_cost
--release`, comparing structural queries **bounded** to the files of an
actual top-5 retrieval hit set vs **unbounded** (the whole repo):

| | files scanned | latency |
|---|---|---|
| bounded (retrieved-symbol files) | 5 | 17-149ms |
| unbounded (whole repo) | 109 | 1270-1320ms |

Unbounded is 10-70x slower and not viable per-query. This settles the design
question directly: `find_implementors`/`find_callers` must always be called
with a caller-supplied, explicitly bounded file list (the files already
surfaced by retrieval) — never a repo-wide scan. `FileSource`'s doc comment
states this as the contract, not just a benchmark footnote. ast-grep
re-parses every file it's handed on top of the parse OXIDE's own indexer
already did for the same file, so the unbounded cost is a real, structural
property of the design, not a missing cache — `TagsContext`-style caching
would not fix an O(repo size) scan.

Binary/dependency size: not separately measured (`cargo bloat` not
available in this environment); the dependency list itself
(`ast-grep-core` + its own small transitive set — no new tree-sitter
grammars) is the primary size signal and is minimal by construction.

## Failure analysis

- Bare-call-only pattern missed method-style calls — caught by the
  benchmark, not by inspection, exactly the same way the tags.scm
  constant-capture gap was caught in Phase 3.4a. Fixed before this commit.
- `find_implementors`'s TypeScript patterns require a single-line-shaped
  `class $NAME implements X { ... }`/`extends X { ... }` match against the
  class's full body — not yet tested against multiple interfaces
  (`implements A, B`) or deep interface hierarchies; out of scope for this
  spike, noted as an open gap rather than silently assumed to work.
- Unbounded structural search is a hard latency failure mode (>1s/query) at
  real repo scale, addressed by the bounded-file-list API contract rather
  than left implicit.

## Keep/revert recommendation

**Keep the isolated module, do not wire it into `context.rs`/retrieval/MCP
in this commit.** The evidence supports a real, meaningful improvement on
the narrow class of queries it targets (structural relationships: "what
implements this", "what calls this") with a bounded, acceptable latency
cost and a minimal dependency footprint. It does not yet meet the bar for
touching frozen production surfaces: the wins are demonstrated on hand-built
tasks engineered to need structural evidence, not on organic agent query
traffic, and the task's own constraints (frozen budget/weights/MCP surface,
no new public tool without clear evidence) are best honored by landing this
as tested, reusable infrastructure and revisiting production wiring once
there's evidence from real usage that natural-language agent queries
actually hit this gap often enough to justify spending context budget or
MCP surface on it.

## Update: wired in, then hardened

The "not wired into `context.rs`/retrieval/MCP in this commit" line above
was true when written; `docs/retrieval-coordinator/README.md` later wired
bounded ast-grep expansion into `context.rs`'s expansion loop. A subsequent
integration-boundary hardening pass — dependency/binary-size audit, a
12-test conformance suite (was 4), and three previously-undocumented known
gaps — is recorded in `docs/astgrep-hardening/README.md`. Both later docs
supersede this section's wiring status; the rest of this document (the
spike's raw benchmark evidence) still stands as the original record.

## Raw evidence locations

- `src/structural.rs` — adapter + `#[cfg(test)]` unit tests (4, including
  the method-call-pattern regression).
- `fixtures/structural_benchmark.json`, `fixtures/ts_repo/src/net/
  policies.ts`, `fixtures/py_repo/oxidepy/notifiers.py` — new gold tasks and
  supporting fixtures.
- `examples/structural_benchmark.rs` — baseline vs baseline+structural
  recall harness (`cargo run --example structural_benchmark --release`).
- `examples/structural_cost.rs` — bounded vs unbounded latency harness
  (`cargo run --example structural_cost --release -- <repo_dir>`).
