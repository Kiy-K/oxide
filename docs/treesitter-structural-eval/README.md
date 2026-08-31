# Direct Tree-sitter queries vs ast-grep as OXIDE's structural backend

> **Superseded.** The recommendation below (replace ast-grep with
> Tree-sitter) shipped, and then the whole query-time backend was migrated
> again to precomputed relations — see
> `docs/precomputed-relations-migration/README.md`. `TreeSitterStructuralProvider`
> and `AstGrepProvider` are both gone; `tree_sitter_structural.rs`'s query
> substrate survives as the extraction engine for
> `structural_relations.rs`. Kept as-is below for historical record.

Evidence for whether `TreeSitterStructuralProvider` (`src/tree_sitter_structural.rs`,
new in this pass) can reach `AstGrepProvider` (`src/structural.rs`, production)
parity with less integration risk, per the task brief. Purely additive:
`AstGrepProvider` is untouched, still the only backend `context.rs:224` calls.

## What was built

`TreeSitterStructuralProvider` implements `StructuralSearchProvider`
(`find_implementors`, `find_callers`) using raw `tree_sitter::Query` matching
instead of `ast-grep-core` pattern strings. Four new `.scm` files under
`src/languages/queries/` (`python_callers.scm`, `python_implementors.scm`,
`typescript_callers.scm`/`typescript_implementors.scm`, the latter pair
shared by TypeScript and TSX exactly as `TS_TAGS` already is for
`TYPESCRIPT_PROFILE`/`TSX_PROFILE`) declare capture shapes only
(`@name`, `@base`, `@call`, `@class`); the target symbol/function name is
compared against captured text in Rust after matching, never spliced into
query source. This is a deliberate difference from `AstGrepProvider`, whose
patterns are `format!`-built with the caller-supplied name embedded directly
into pattern syntax — harmless today since `type_name`/`function_name`
ultimately come from indexed symbol names, but it means `TreeSitterStructuralProvider`
has no query-injection surface at all, by construction, rather than by
argument provenance.

Compiled queries are cached in `OnceLock`s (`queries_for`/`compiled_callers`/
`compiled_implementors`), mirroring `tags.rs::TagsExtractor::config`'s
precedent — that pass measured ~15x slower indexing from recompiling a query
per file, and the same cost shape applies here.

One genuine query-side win: N-ary base/interface lists (`implements A, B`,
Python's `class X(A, B)`) need exactly **one** declarative pattern each,
because the tree-sitter query engine yields one match per base identifier
directly under `superclasses`/`implements_clause`. `AstGrepProvider` gets the
same correctness (see Correctness below) but only by hand-enumerating
first/middle/last-position pattern variants — 4 Python variants, 7 TypeScript
variants, in `src/structural.rs`.

## Correctness: bit-identical on the existing conformance suite

`tests/structural_conformance.rs` (new) reuses `structural.rs`'s exact
12-test suite verbatim, run against both providers via a small macro that
generates an `ast_grep`/`tree_sitter` test pair per shared assertion body —
so both backends are checked against literally the same expected values,
including the two "must return zero hits" malformed/empty cases (a
divergence there would show up as one half of the pair failing, not a
softened assertion):

```
cargo test --test structural_conformance
running 24 tests
test result: ok. 24 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
```

All 24 pass — **no divergence found**, including on malformed source
(`"class X implements { foo("`, `"function f( { return"`,
`"class X(:\n    pass"`, `"def f(:\n  return"`). In hindsight this isn't a
coincidence: `ast-grep-core`'s own pattern matching runs over a
`tree_sitter`-parsed `StrDoc` (`src/structural.rs`'s `impl_ag_lang!` wires
the exact same grammar), so both backends inherit the identical
error-tolerant parse tree (ERROR nodes, never a parse failure) — they only
differ in the pattern-matching layer on top, and neither layer's patterns
happen to match fragments of an ERROR-node subtree for these four inputs.

Separately: `AGENTS.md`'s structural-search note says the three
implementor-list gaps (TS multi-`implements`, Python multi-inheritance,
`extends`+`implements` both sides) were "found and pinned, not fixed" by the
hardening pass. That's stale — `docs/astgrep-hardening/README.md` and the
current `src/structural.rs` test suite (`cargo test --lib structural`, 12/12
passing before this pass touched anything) both show those three cases now
pass for `AstGrepProvider` via its expanded pattern lists. Not something
this pass set out to find, but worth fixing in `AGENTS.md` since it's a
load-bearing-invariants file people read in place of running the code.

## Codex review: 2 real coverage gaps found and fixed, 1 example panic fixed

Before finalizing, `src/tree_sitter_structural.rs`, all four new `.scm`
files, `tests/structural_conformance.rs`, and both new `examples/` scripts
went through a Codex review focused on capture-index correctness, UTF-8/byte
handling, query-cache safety across languages, TS/TSX grammar-divergence
risk, `finish()`'s sort/dedup parity with `structural.rs::hits_for`, and
reachable panics. Byte handling, `OnceLock` cache safety, and dedup parity
came back clean. Three real issues did not:

1. **`typescript_implementors.scm` missed `abstract class X implements Y`.**
   The TypeScript/TSX grammar represents `abstract class` as a distinct
   `abstract_class_declaration` node (confirmed via
   `tree-sitter-typescript`'s `node-types.json`; `typescript_tags.scm`
   already relies on this same node kind for `@definition.class`), not a
   flag on `class_declaration`. The query only had patterns for
   `class_declaration` and `class`, so an abstract-class implementor was
   silently invisible. **Fixed**: added a third top-level pattern for
   `abstract_class_declaration`.
2. **The anonymous class-expression pattern required a name it doesn't
   have.** `class` nodes have an optional `name` field (per
   `node-types.json`), but the query wrote `name: (type_identifier) @name`
   as a required match — so `const w = class implements Runnable {}` (no
   name at all) never matched the pattern at all, not just the capture.
   **Fixed**: `name: (type_identifier)? @name`.
3. **`examples/structural_backend_scale.rs` could panic on `&files[..1]`**
   if the caller-supplied repo had zero files matching the warm-up filter.
   Real for the example script, not `TreeSitterStructuralProvider` itself.
   **Fixed**: guarded with `if !files.is_empty()`.

Confirmed empirically (not just by grammar inspection) that both fixed
cases are genuine `TreeSitterStructuralProvider` wins, not shared gaps:

```
abstract class via ast-grep: 0 hits
anonymous class expr via ast-grep: 0 hits
```

`AstGrepProvider`'s patterns are literal `"class $NAME implements ..."`
pattern text — structurally unable to match either shape (a different node
kind for `abstract`, no node at all for a missing name), so this isn't
fixable by adding more `AstGrepProvider` pattern-string variants the way the
multi-`implements`/multi-inheritance gaps were; it would need matching
against `abstract_class_declaration` and an anonymous `class` shape
specifically. Pinned as `TreeSitterStructuralProvider`-only regression tests
(`abstract_class_implementors_are_found`,
`anonymous_class_expression_implementors_are_found` in
`src/tree_sitter_structural.rs`) rather than added to
`tests/structural_conformance.rs`, since that suite's whole point is
asserting identical behavior across both providers — these two are real,
intentional divergences in `TreeSitterStructuralProvider`'s favor, not
things `AstGrepProvider` was expected to also pass. All fixture-benchmark
and repo-scale numbers below were re-measured after these fixes; they were
unaffected (neither fixture nor synthetic repo uses `abstract class` or
anonymous class expressions), and the full gate (`cargo fmt --check`,
`cargo clippy --all-targets`, `cargo test`, `tests/benchmark_gate.rs`)
stayed green throughout.

## Benchmark: identical hits and recall on all 4 structural tasks

`examples/structural_backend_compare.rs` (new) runs both providers through
the same store/seeds/task list in one process
(`fixtures/structural_benchmark.json`), so provider-vs-provider variance
isn't confused with run-to-run noise:

```
task                          ag_hits  ts_hits  ag_recall  ts_recall      ag_ms      ts_ms
ts-implementors-retrypolicy         3        3      1.000      1.000      59.76       5.53
ts-callers-shouldretry              2        2      1.000      1.000      10.98       9.05
py-implementors-notifier            2        2      1.000      1.000      57.50       2.50
py-callers-should-retry             5        5      1.000      1.000      19.61       3.46
```

Identical hit counts, identical recall, zero `DIVERGENCE` lines (the script
diffs resolved symbol-id sets per task and would print them). These 7-file
fixtures are too small to be latency evidence on their own (see below), but
the `ag_ms`/`ts_ms` gap here previews the repo-scale result.

## Latency: repo-scale, not fixture-scale

`fixtures/{py,ts}_repo` are 6-8 files each — at that size, timing is
dominated by one-time costs (first `OnceLock` query compile for
`TreeSitterStructuralProvider`; nothing comparable for `AstGrepProvider`,
which has no compile step because it re-`format!`s and re-parses its pattern
strings on every call instead). To get a real signal,
`examples/structural_backend_scale.rs` (new) uses the same synthetic-repo
generator `scripts/perf.sh` already relies on
(`scripts/gen_bench_repo.py`, 300 modules/lang → 902 `.py` files) and warms
the query cache before timing, so both backends are measured at steady
state:

```
whole-repo candidate set: 902 .py files
bounded_6    files=   6  ast_grep:   10 hits in    11.87ms   tree_sitter:   10 hits in     0.74ms   speedup=16.0x
whole_repo   files= 902  ast_grep:  600 hits in   836.62ms   tree_sitter:  600 hits in    45.90ms   speedup=18.2x
```

(Reproduced 3x; hit counts identical every run, speedup stable at 16-18x.)
Same hit counts as `AstGrepProvider` at 902 files, not just the 6-8 file
fixtures — the correctness parity holds at scale, not only on the small
conformance/benchmark inputs. The asymmetry is architectural, not
implementation polish: `AstGrepProvider`'s `hits_for` rescans each file's
tree once *per pattern* (4-7 patterns for implementors, 2 for callers) and
re-parses each pattern string from scratch on every call; the Tree-sitter
provider does one compiled-query, one-cursor-pass per file regardless of
how many bases/interfaces it needs to match. `context.rs`'s bounded
expansion only ever calls `find_callers` on ≤3-6 files per request
(`RetrievalMode::structural_budget()`), so the `bounded_6` row is the
production-shaped number: ~12ms vs ~0.7ms per request, small in both cases
but a real 16x.

No `/tmp/darkreader_structural`-scale real-world repo was available in this
environment; the synthetic repo above is the best available substitute for
"more than fixture-scale," not a replacement for measuring against real,
irregularly-shaped source.

## Dependency and binary cost: zero new crates

`tree_sitter::StreamingIterator`/`StreamingIteratorMut` are re-exported at
the `tree_sitter` crate root (confirmed:
`grep streaming_iterator tree-sitter-0.27.0/binding_rust/lib.rs` →
`pub use streaming_iterator::{StreamingIterator, StreamingIteratorMut};`),
so `TreeSitterStructuralProvider` needed **zero** `Cargo.toml` changes —
`git diff --stat Cargo.toml Cargo.lock` is empty for this entire pass. This
beats `ast-grep-core`'s own footprint (`docs/astgrep-hardening/README.md`:
two genuinely new crates, `bit-set`/`bit-vec`, ~0.84% binary size) outright:
there was nothing to add.

Binary size, measured the same way `docs/astgrep-hardening/README.md` did
(build release twice, once with `pub mod tree_sitter_structural;` commented
out in `lib.rs`, reverted before anything was committed):

```
with tree_sitter_structural:    14,453,112 bytes
without tree_sitter_structural: 14,452,920 bytes
delta:                          192 bytes (0.0013%)
```

Effectively free — no new grammar crates, no new dependency, just Rust code
reusing APIs (`Query`, `QueryCursor`, `Parser`) the binary already links for
`tags.rs` and `structural.rs`.

## Code/query complexity

- **Query file line count**: 4 new `.scm` files, 10-42 lines each (see
  `src/languages/queries/{python,typescript}_{callers,implementors}.scm`;
  `typescript_implementors.scm` is the 42-line outlier, after the Codex-review
  fix added a third top-level pattern for `abstract_class_declaration`). Each
  implementors query is one declarative pattern per clause type per
  class-declaration node kind, independent of how many bases/interfaces a
  given class has.
- **`AstGrepProvider::find_implementors`** (`src/structural.rs:196-235`)
  hand-lists 4 Python and 7 TypeScript pattern-string variants to cover
  first/middle/last base-list positions — correct (see Correctness above),
  but the pattern count scales with position-enumeration, not with the
  underlying grammar shape.
- **Provider module size**: `tree_sitter_structural.rs` is 270 lines
  including doc comments and its own tests (compile-smoke plus the two
  Codex-review regression tests below), comparable to `structural.rs`'s
  ~260 (excluding its 12-test suite, now shared via
  `tests/structural_conformance.rs`).

## Where this pass did not look

- Real-world repos beyond the synthetic generator (see Latency).
- Languages beyond Python/TypeScript/TSX — neither backend covers more than
  that today.
- A dependency/security audit of `TreeSitterStructuralProvider` at the depth
  `docs/astgrep-hardening/README.md` gave `ast-grep-core` (full `cargo tree`
  duplicate-check history, an explicit version-bump upgrade rule). It
  doesn't need one yet — it added no dependency — but if it became
  production it should get the same documented upgrade contract
  `ast-grep-core` has, before it's trusted the same way.

## Recommendation: replace-with-Tree-sitter, as a follow-up migration

The evidence is one-sided and clean: bit-identical correctness on the full
existing conformance suite (24/24, including all four malformed-source
cases — no softened assertions), identical hits/recall on the benchmark and
at 902-file repo scale, a 16-18x latency win at the exact bounded-file-count
shape `context.rs` actually uses, zero added dependency or binary cost, and
two real coverage cases (`abstract class ... implements`, anonymous class
expressions) where `TreeSitterStructuralProvider` is now strictly *more*
correct than `AstGrepProvider` — not just equal — because those shapes are
structurally unreachable by ast-grep's literal `"class $NAME implements
..."` pattern text, not just missing pattern-string variants. There is no
axis in this pass's evidence where `AstGrepProvider` comes out ahead.

The Codex review that found those two gaps (plus an unrelated example-script
panic) is itself part of the evidence, not just cleanup: it's the first
external correctness check this module has had, the same category of scrutiny
`ast-grep-core` got across two prior passes before this task started, and it
came back with two real, fixed, now-regression-tested findings rather than
zero — a genuine (if small) signal about how much hardening a fresh backend
still needs relative to one that's already been through it.

Per the task brief this pass does not switch the production backend —
`context.rs:224` still hardcodes `AstGrepProvider`, unchanged. The honest
remaining cost is integration risk, not correctness or performance:
`ast-grep-core` has been through two prior hardening passes
(`docs/astgrep-structural-search/`, `docs/astgrep-hardening/`) with a
documented version-upgrade contract, and `TreeSitterStructuralProvider` has
had one review pass, not that track record, in production. A follow-up
commit that (1) swaps the `AstGrepProvider` call in `context.rs:224` for
`TreeSitterStructuralProvider`, (2) removes the `ast-grep-core` dependency
and `src/structural.rs`'s ast-grep-specific code once nothing references it,
and (3) gives `TreeSitterStructuralProvider` its own documented upgrade rule
in `AGENTS.md` (mirroring `ast-grep-core`'s) is the right next step — not
bundled into this evidence-gathering pass.
