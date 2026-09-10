# Language support

What OXIDE extracts per language, what it costs, and what it still misses.
Behavior is pinned by `tests/language_conformance.rs`'s committed goldens
(`fixtures/conformance/<lang>.golden.json`) — those files, not this page,
are the source of truth; regenerate them with `UPDATE_GOLDEN=1` and read
the diff.

Every language goes through the same path: a `LanguageProfile` (grammar +
`queries/*_tags.scm`) feeds `tree-sitter-tags`, and `languages/tags.rs`
normalizes the flat tag list into OXIDE's `Symbol` IR. There is no
per-language extractor and no language-specific retrieval behavior.

## Coverage matrix

| Dimension | Python | TypeScript | TSX | Rust | Go |
|---|---|---|---|---|---|
| definitions + stable ids | yes | yes | yes | yes | yes |
| kinds | class/function/method/constant | + interface/type_alias/enum | same as TS | class/enum/type_alias/interface(trait)/module(mod)/method | class/interface/method/function/constant |
| qualified names | containment | containment | containment | containment (incl. `impl` blocks) | receiver (`Store.Get`) |
| parent/child containment | yes | yes | yes | yes | interface members only |
| imports | `import` / `from … import` | `import` / `export … from` | same as TS | `use` trees | `import` specs |
| references | token intersection | token intersection | token intersection | token intersection | token intersection |
| calls | calls + attribute calls | calls + member calls | + JSX elements | + macro invocations | calls + selector calls |
| inheritance | base classes, incl. `abc.ABC` | `extends`/`implements`, incl. qualified + generic | same as TS | `impl Trait for T`, supertrait bounds | struct/interface embedding |
| decorator-inclusive spans | yes | yes | yes | n/a | n/a |
| broken files | module fallback | module fallback | module fallback | module fallback | module fallback |
| determinism | pinned | pinned | pinned | pinned | pinned |
| incremental single-file edit | pinned | pinned | pinned | pinned | pinned |

## What Python and TypeScript gained in this round

- **Decorated definitions span their decorators.** `@app.route`,
  `@pytest.fixture`, `@Injectable()` are inside the symbol's span, so they
  reach `content_hash`, `signature`, and the lexical index's body tokens. A
  decorator's own call now attributes to the decorated symbol instead of the
  file's module symbol.
- **Qualified and generic bases resolve.** `class Record(abc.ABC)`,
  `extends ns.Base`, `implements Iface<T>` — all previously produced no
  relation at all. `class Panel extends React.Component<Props>`, the most
  common class-heritage shape in a TSX codebase, produced nothing.
- **JSX element usage is a call.** `callers_of("Button")` returns the
  components that render `<Button />`. Intrinsics (`<div>`) are excluded by
  JSX's own lowercase rule.
- **`uses←` is narrowed to import-backed definitions.** Two files defining
  `Client`, one imported: the importer's `uses` edge points only at the one
  it imported.
- **The handwritten extractors are gone.** ~800 lines deleted; the tags path
  is the only extraction path.

Because all of that changes spans, hashes and relations for files whose
source never changed, `EXTRACTION_VERSION` is bumped to 2 and `update_base`
now forces a full reparse whenever the stored version is not this binary's.
An existing index repairs itself on the next plain `oxide index`; no `-a`
and no manual `.oxide` deletion.

## Performance

### Provenance

Everything in this section was measured on **2026-09-10** against the
`oxide` binary at commit **c0f5bcc** (`cargo build --release -j 2`), with
the offline hashed embedder — `OXIDE_EMBED_NATIVE=hashed`, with
`OXIDE_EMBED_URL` and `OXIDE_EMBED_MODEL` unset, which is what
`scripts/perf.sh` forces — so no model download or embedding server is
involved and the numbers are reproducible without one. These are indexing
and structural-retrieval measurements; they are **not** a retrieval-quality
claim and do not touch `docs/canonical-baseline.md`'s ruler. The
retrieval-quality gate for this whole round is
`./target/release/oxide eval --config fixtures/benchmark.json`, unchanged
throughout at vector-only recall@5 0.818 / hybrid 0.909.

Machine: Intel i7-13620H, a shared laptop, every run under `nice -n 10`.
**Each number is the median of 3 runs**, not a single run — single runs
taken a day apart on this machine differed by up to 1.7x in absolute terms
while their *ratios* held, so medians are the only comparable form. Do not
compare these absolutes against `docs/perf-baseline-v0.1.md`'s 2026-08-29
rows, which were single runs without `nice` on a differently loaded machine.

Repositories, at these exact revisions:

| repo | revision | how to get it |
|---|---|---|
| flask | `7ee9ceb71e868944a46e1ff00b506772a53a4f1d` | `~/.cache/oxide-contextbench/repos/flask` |
| darkreader | `a787eb511f45159c8869d30e5a6ba1f91cb67709` | `~/.cache/oxide-contextbench/repos/darkreader` |
| tokio | `1.52.1` | `~/.cargo/registry/src/*/tokio-1.52.1` |
| gin | `dcaa4296d111981ffb31ac3eba90bb63e1eb5ab9` | `git clone --depth 1 https://github.com/gin-gonic/gin` |

Commands, verbatim:

```bash
cargo build --release -j 2
nice -n 10 scripts/perf.sh <repo-path>     # real repos; copies the repo first
nice -n 10 scripts/perf.sh 200             # synthetic, all four languages
cargo build --release -j 2 --example structural_probe
nice -n 10 ./target/release/examples/structural_probe <repo-copy> <names...>
```

`scripts/perf.sh` copies the target repo to a temp dir before indexing and
editing it, so a real checkout is never mutated.

### Real repositories, one per language

| repo | language | files | symbols | cold index | no-change | 1-file edit | peak RSS | index size |
|---|---|--:|--:|--:|--:|--:|--:|--:|
| flask | Python | 80 | 1,755 | 865 ms | 31 ms | 180 ms | 28 MB | 6.9 MB |
| darkreader | TS + TSX | 197 | 1,356 | 1,021 ms | 29 ms | 190 ms | 28 MB | 5.4 MB |
| tokio | Rust | 547 | 7,155 | 5,666 ms | 131 ms | 315 ms | 51 MB | 33 MB |
| gin | Go | 99 | 2,076 | 1,338 ms | 38 ms | 520 ms | 34 MB | 8.6 MB |

Every single-file edit reported `+1 new, ~1 changed, 2 written, N reused` —
incremental re-embedding holds for all four. The edit appends a real
declaration rather than a comment: a trailing comment changes no symbol's
span, so nothing re-embeds and the measurement is vacuous.

gin's 520 ms edit is the outlier and is explained, not mysterious: the file
`perf.sh` picks is the largest in the repo, and gin's is `context.go` at
1,539 lines (with a 3,957-line `context_test.go` alongside), so the single
file being reparsed is unusually large relative to a 99-file repo.

### Synthetic scaling, and the cost of adding two languages

`scripts/gen_bench_repo.py` now emits Rust and Go modules alongside Python
and TypeScript. Both rows below were measured in the same session, medians
of 3:

| repo | files | symbols | cold index | ms/symbol | no-change | index size |
|---|--:|--:|--:|--:|--:|--:|
| N=200, Python + TS only | 804 | 3,412 | 1,072 ms | 0.314 | 45 ms | 9.4 MB |
| N=200, all four languages | 1,205 | 6,615 | 1,996 ms | 0.302 | 71 ms | 18 MB |

0.302 against 0.314 ms/symbol — adding Rust and Go costs the *same* per
symbol within the spread of the runs themselves, and certainly not more.
(An earlier measurement of the same pair at 8a4b02e read 0.333 vs 0.329,
the difference in the other direction and equally small; treat the claim as
"no per-symbol cost", not as a signed delta.) Files
grow more slowly than symbols because Rust and Go modules are denser. The
5x catastrophic-regression threshold in `docs/perf-baseline-v0.1.md` is
nowhere near.

(To reproduce the Python+TypeScript-only row: generate with
`python3 scripts/gen_bench_repo.py <dest> 200`, then delete `src/rs`,
`src/go` and `src/retry.rs` before pointing `perf.sh` at it.)

### Regression check against the pre-work binary

Both binaries, the same generated Python+TypeScript corpus, median of 3
cold indexes each, same session:

| binary | cold index | symbols | index size |
|---|--:|--:|--:|
| 43c9d1b (before this round) | 1,074 ms | 3,412 | 9.3 MB |
| c0f5bcc (after) | 1,072 ms | 3,412 | 9.4 MB |

Indistinguishable, with identical symbol counts. The individual runs for
43c9d1b were 1053/1074/1088 — the after figure sits inside that spread. The
decorator walk, the widened base-clause queries, the JSX patterns and the
attribution ladder cost nothing measurable. (The 43c9d1b row was measured
earlier the same day, same corpus, same methodology, from a worktree at
that commit.)

### Structural retrieval on real repositories

`cargo run --release --example structural_probe <repo> <names…>`, same
revisions as above:

- **gin (Go)** — `implementors_of("RouterGroup")` = `gin.go#Engine` (struct
  embedding); `implementors_of("IRoutes")` = `routergroup.go#IRouter`
  (interface embedding); `callers_of("Context")` returns real
  `Context.ClientIP`, `Context.Deadline`, … method calls.
- **tokio (Rust)** — `implementors_of("AsyncRead")` returns 43 implementors
  including `fs::File`, `BufReader`, both `ReadHalf`s and `Stdin`, plus the
  supertrait `AsyncBufRead`. `implementors_of("Future")` returns 64.
- **OXIDE's own `src/` (Rust)** — 25 files, 611 symbols, 678 ms cold;
  `implementors_of("IndexBackend")` = `storage.rs#SqliteStore`;
  `implementors_of("LanguageExtractor")` = `languages/tags.rs#TagsExtractor`.

Two honest artifacts in the tokio list: `async_read.rs#Box` and `#Pin` are
`impl AsyncRead for Box<T>` / `Pin<P>` blocks, which survive as symbols in
their own right because no same-named struct in that file dedups them away
— they are real code regions implementing the trait, not false positives.

## Remaining unsupported constructs

| Language | Not extracted | Why |
|---|---|---|
| Go | import *resolution* | A Go import names a package *directory* of many files, and is usually module-qualified (`github.com/…`) or stdlib. `resolve_module`'s contract is "exactly one unambiguous file", so Go imports are recorded on the symbol but never produce an `imported-definition` edge. Rust `use` paths do resolve (`crate::backend::Backend` → `src/backend.rs`). |
| all | import *bindings* (the names, not the module) | `SymbolKind::Import` is never produced. The `uses` precision this was wanted for came from file-level import resolution instead; per-name bindings would only help when two *imported* files define the same name. |
| all | `exported` for Python/Rust/Go | The flag is derived only from TypeScript's `export` wrapper; Python is unconditionally `true`, Rust `pub` and Go's leading-capital convention are not read. |
| Python | cross-file reference staleness within one run | Known, accepted, architectural — see AGENTS.md. |
| TypeScript | `.d.ts` files | Excluded by the scanner denylist. |
| TSX | JSX member usage as a *distinct* relation | `<ns.Button />` is recorded as a call of `Button`, on the same bare-name tier as everything else. |
| Rust | `macro_rules!` definitions | Dropped rather than mislabelled — OXIDE has no macro kind. |
| Rust | `union` | Lands on `Class`. |
| Go | interface *satisfaction* | Structural and semantic in Go; `bases` means embedding only, by design. |
| Go | named types vs aliases | `type Key string` lands on `Class`; only `type X = Y` is a true alias and it is not distinguished. |
| Go | package `var` | Lands on `Constant` — OXIDE has no variable kind. |
| Rust, Go | rarer grammar shapes, found empirically | The base/call queries cover the shapes that have actually been exercised — bare, qualified, generic, and their compositions — but the set is empirical, not exhaustive. Eight adversarial review rounds each turned up narrower ones (`impl external::Trait<T> for Local`, Go's `*pkg.Base` embedding) and the last rounds were finding compositions of shapes already covered separately. Expect more; each is a one-line `.scm` alternative plus a regression test. |
| Java | everything | Not implemented. See `docs/java-feasibility/README.md`. |

## Next language worth adding

Not Java. The spike (`docs/java-feasibility/README.md`) found the grammar
builds cleanly against the pinned tree-sitter 0.27 and every `tags.scm` gap
is ordinary work — but Java overloading collides on OXIDE's
`(file, qualified_name)` symbol identity, and that identity is a pinned,
cross-language invariant whose change re-embeds every index in every
language. Settle the overload question on its own terms first; adding Java
before then ships a language whose most common construct is silently
truncated.

The cheaper next steps, in order: **C#** and **Java** share the overload
problem, so both wait on the same decision. **Ruby** and **PHP** have
upstream tags and no identity conflict, making them the closest thing to
mechanical additions left. **C/C++** has both the overload problem and a
preprocessor that makes byte-range containment unreliable — the expensive
one, and the least worth attempting under a no-semantic-resolution rule.
