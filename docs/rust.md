# Rust source support

`oxide-rust-v1` adds Rust as a language slice of the existing engine, written in Python:
`core/languages/rust.py` extracts syntax facts with `tree-sitter-rust==0.24.2`
(`rust_facts.py` holds their types and cache codec, `rust_macros.py` opens
`cfg_*!` wrappers), `rust_cargo.py` reads the Cargo layout and builds module trees, and
`rust_resolve.py` resolves names over the whole capture. No Rust toolchain, `cargo`,
`rust-analyzer`, build script, proc macro or repository code runs while indexing or
querying; `Cargo.toml` files are read as data with the standard library. The ordinary
Python API, CLI, retrieval, graph, router and context packer serve these files unchanged,
and MCP remains exactly one tool, `oxide_search`, with `search` and `context` modes.

## Extraction and identity

`.rs` files report `FileInfo.language == "rust"`. These declarations become symbols:

| Rust declaration | Kind | Key segment |
| --- | --- | --- |
| `fn f`, a bodiless `fn f();` in an `extern` block | `function` | `f` |
| `struct S`, `enum E`, `union U` | `struct`, `enum`, `union` | `S` |
| `trait T` | `trait` | `T` |
| `fn m` or `fn m();` of a trait | `method`, nested in the trait | `T.m` |
| `impl Trait for Type`, `impl Type` | `impl` | `<impl Trait for Type>` |
| `fn m` of an impl | `method`, nested in the impl | `<impl Trait for Type>.m` |
| `const C` of an impl or trait | `constant`, nested | `<impl Type>.C` |
| `type A = ...;` | `type` | `A` |
| `const C`, `static S` | `constant`, `variable` | `C`, `S` |
| `macro_rules! m` | `macro` | `m` |

Keys are `symbol:<path>#<segment>`. Dotted symbol hints match this declaration path:
`new` or `<impl Store>.new`, not `Store.new` (an impl is a declaration, unlike Go's
receiver-named methods). An impl block is its own declaration site
(ADR-0010): its name is `<impl Trait for Type>` or `<impl Type>`, made of the trait
and self type as written (generic arguments kept, comments dropped, whitespace
squashed, `.` and `~` replaced by `_`, cut at 256 characters) and nothing else. The impl's
own generics (`impl<T: Clone>`) and `where` clause are not part of the name, so a comment
or a rewritten bound never changes identity. Equal sibling segments (several `impl Foo`
blocks, `cfg` variants of one function, an inline `mod` repeated) take `~n` in source
order; a method's ordinal belongs to its impl. `Entity.name` is the identifier
(`new`, not `Store.new`) or the impl's `<impl ...>` text; signatures are the declaration
header up to its body with whitespace squashed (`pub fn must_get(&self, key: &Key) ->
String`; a bodiless function keeps its whole declaration, a const or alias its first line). Attributes and doc comments are not part of a symbol's range.

Not symbols: enum variants and struct fields (a path to a variant resolves to its enum),
associated types, items declared inside function bodies or blocks, closures, `use`, `mod`
and `extern crate` declarations, and `_` items. Their code stays in the enclosing symbol or
the file's module-level view. Raw identifiers (`r#type`) are the plain name.

A function carrying `#[test]`, `#[bench]`, `#[rstest]`, `#[test_case]` or a path ending
in `::test` (`#[tokio::test]`) is a test. A resolved call made inside a test adds
`tested_by` to the callee, the same heuristic as the other slices; helpers inside
`#[cfg(test)] mod tests` are not tests.

## Crates and modules

Packages come from captured `Cargo.toml` files: `[package]` (name, edition, including
`edition.workspace = true`), `[lib]` (name, path), `[[bin]]`, `[[test]]`, `[[example]]`,
`[[bench]]`, `build`, the `auto*` target switches, and `[dependencies]`,
`[dev-dependencies]`, `[build-dependencies]` and `[target.*.*]` tables. A dependency with
a `path`, or `workspace = true` naming a `[workspace.dependencies]` path, links to that
package's library under Cargo's name for it: the library target's name (`[lib] name`, else
the package name with `-` as `_`), or the key when the entry renames it with
`package = "..."`. Dev-dependencies are visible to every target of the package (a superset
of Cargo's test, example and bench scope). Registry and git dependencies, features,
`[patch]` and `[replace]` are not modeled. A manifest that does not parse is
ignored. Cargo's own default edition (2015) applies to a package that names none.

A package's crates are its library, its binaries (`src/main.rs`, `src/bin/*.rs`,
`src/bin/*/main.rs`, declared paths), tests, examples, benches and build script. A
declared target replaces one inferred from the same file. A module tree starts at each
crate's root file and follows `mod x;` as rustc does: `x.rs` or `x/mod.rs` beside a
`mod.rs` or root file, `a/x.rs` under a file `a.rs`, inline `mod a { mod b; }` extending the
directory, and `#[path = "..."]`. A file belongs to the first crate that reaches it
(library, then binaries, tests, examples, benches, build script, in path order); a later
crate declaring the same file shares that module. A file no crate reaches (a missing
`mod` line, a tree without a manifest) becomes a crate of its own, with its package's
edition and dependencies when it has one (edition 2021 and none otherwise).

Modules are keyed `module:rust:<crate root path>` and `...::a::b`; the name is the path
from the crate (`corelib`, `shop::store`), with ` (bin)`, ` (test)` ... after the crate name
for crates other than a library. The repository contains each module, a module is the
logical parent (`contains_logical`) of its child modules and its file (an inline module has no
file of its own), and it `defines` the symbols declared in it, impl blocks included.
Same-named variants of a module (`#[cfg(unix)] mod sys;` and `#[cfg(windows)] mod sys;`) are
all mounted, the later with `~1` in its key, and a lookup through the name returns every
variant, so uses are ambiguous rather than chosen. `cfg` is not evaluated anywhere.

## Resolution

Everything below is syntactic: Rust's module system and scoping rules applied to the
captured files. Nothing needs a type checker.

- **Paths.** The first segment is `crate`, `self`, `super` (repeatable), `Self`, a local
  `use`, a name in the current module, or an extern crate; later segments are members of
  the modules, types and traits found so far. Lookups follow rustc's precedence: a
  module's own items and explicit imports, then its glob imports. A glob from an ancestor
  module (`use super::*`, the usual test module) sees private items and imports; from any
  other module only `pub` ones. Two globs offering a name, or a name in several modules,
  make a use ambiguous. Cycles of `pub use` globs terminate.
- **Imports.** Every `use` leaf is an `imports` edge from its file to what it names: a
  symbol (a reexport names its target, not the module that reexports), a module, or a
  macro. `as` aliases, `self` leaves, nested groups, absolute `::` paths, `use` inside a
  function (bound there only) and `extern crate x [as y];` are handled. Edition 2015 `use`
  paths start at the crate root.
- **Types and values.** `Type::assoc` resolves through the impl blocks of the type
  (inherent items shadow a trait impl's of the same name, otherwise several candidates are
  ambiguous), `Trait::m` through the trait, `Enum::Variant` to the enum, and `Self`
  to the impl's self type or the trait. An impl whose self type is not a plain path
  (`&T`, `[T]`, a blanket `impl<T> X for T`) attaches to no type.
- **Calls** cover a path or identifier call (turbofish included), `self.m()` inside an
  impl or trait (the self type's associated functions, then the trait's), tuple-struct
  and variant constructors, and `name!` macro invocations. A macro resolves through `use`,
  a path (`crate::m!`, `dependency::m!`) or, unqualified, to any `macro_rules!` of that
  name in the crate; `#[macro_export]` macros are items of their crate root. Calls inside
  macro arguments are not seen (below).
- **References** cover every type path (fields, signatures, bounds, generic arguments,
  impl headers, struct expressions) and value or pattern paths of two or more segments
  (`Kind::A`, `config::DEFAULT`).
- **Conservative locals.** A name bound anywhere in a declaration (parameters, patterns of
  `let`/`match`/`if let`/`for`/closures, generic parameters, nested items) is opaque in
  all of it and makes no relation, so shadowing never produces a wrong edge.

Pass-through macros: an item-position invocation of `cfg_*!` (tokio's convention) or
`cfg_if!` whose contents become error-free items once the macro name, its delimiters and
`cfg_if`'s `if #[cfg(..)]`/`else`/branch braces are blanked is read as those items. The blanks
are spaces, so offsets and evidence spans stay the original file's. Every other macro is
opaque.

## Not modeled

These need type information, a build configuration or macro expansion, and stay
unresolved (with their name) or absent rather than guessed:

- method calls on any receiver but `self` (`x.m()`, chains, closures), trait dispatch
  and `impl Trait` resolution, auto-deref, operators, method values, and associated types;
  there is no `implements` relation (an impl references its trait and type);
- type aliases as owners (`type A = B;` is a symbol, but `A::new()` does not look through
  it) and glob imports of an enum's variants (`use Kind::*;`);
- contents of macro arguments and bodies (`assert_eq!(f(), 1)`, `vec![g()]`), items and
  modules defined by macros (`pin_project!`, `bitflags!`, derives, proc macros, `include!`),
  and `macro_use` or textual macro scoping;
- `cfg`/`cfg_attr` evaluation and features (every variant is indexed, same names are
  ambiguous), build scripts and generated code that is not captured, `#[path]` outside the
  captured tree, and Rust's privacy checking beyond the `pub`/ancestor rule above;
- the standard library, registry and git dependencies, `[patch]`/`[replace]`, workspace
  `members`/`exclude` (every captured manifest counts), and the validity of the program;
- bare identifier uses of constants and statics (`return DEFAULT`), and block-level scoping
  (a shadowed name is opaque in the whole declaration).

Structural search (`Repository.structural_search`, ast-grep) remains Python and TypeScript
only.

## Reliability

The shared bounded native query, downward-only node reads, depth limit (65,535), UTF-8
check and bounded syntax diagnostics apply unchanged. Invalid UTF-8 or excessive depth is
unsupported file evidence; grammar recovery is partial and keeps recovered declarations.
Nothing recurses over syntax or module nesting (explicit stacks). Name lookup carries a
cycle guard and stops after 64 nested hops through imports and globs (a longer chain is
unresolved, never a crash); a manifest `tomllib` cannot read, such as one nested too deeply, is
ignored like a malformed one. `Cargo.toml` files stay unsupported file evidence and are read only during
resolution, which always reruns over the full capture, so a manifest-only change, or
removing a `mod x;` line, re-resolves correctly with all facts reused.

Facts are cached by `(rust, source digest)` under `oxide-rust-facts-v1`; the cache decoder
validates types, ranges, identifier names, impl names and the scope tree, and anything else is
parsed again. Derivation components add `rust` (`oxide-rust-v1`) and `rust-parser`;
older indices are re-indexed. Evidence and reproduction:
[phase-6/rust-v1](phase-6/rust-v1/README.md).
