"""Rust slice conformance (docs/rust.md): identity, kinds, provenance, Cargo layout, module
trees, path resolution, tests, cached facts and persisted incremental/full-rebuild equality."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path

from conftest import REPO, write
from test_incremental import BAD, _paths, _replace, apply, dump
from test_typescript import derived, resolved

import oxide
from oxide.capture import capture
from oxide.core.indexing import Derived, derive, dialect, validate
from oxide.core.languages import python, rust

FIXTURE = REPO / "fixtures/rust_repo"
SHOP = "crates/shop/src/"
CART = f"symbol:{SHOP}cart.rs#"
STORE = f"symbol:{SHOP}store/mod.rs#"
MEMORY = f"symbol:{SHOP}store/memory.rs#"
PLATFORM = f"symbol:{SHOP}platform.rs#"
LIB = "crates/shop/src/lib.rs"
UTIL = "symbol:crates/util/src/lib.rs#"
CARGO = "[package]\nname = 'p'\nversion = '0.1.0'\nedition = '2021'\n"


def unresolved(result: Derived, kind: str, source: str) -> set[str | None]:
    return {
        e.name
        for e in result.edges
        if e.kind == kind and e.source == source and e.resolution == "unresolved"
    }


def crate(tmp_path: Path, files: Mapping[str, str | bytes], manifest: str = CARGO) -> Derived:
    return derived(write(tmp_path, {"Cargo.toml": manifest, **files}))


def test_use_trees_flatten_to_one_import_per_leaf() -> None:
    parsed = rust.parse(
        b"use std::fmt::{self, Display as D};\npub use crate::a::{b, c::*};\n"
        b"use ::std::io;\nuse super::*;\nuse {x, y::z as _};\n"
        b"fn f() { use inner::g; }\n"
    )
    assert not isinstance(parsed, str)
    leaves = [(i.path, i.alias, i.glob, i.public, i.scope) for i in parsed.imports]
    assert leaves == [
        (("std", "fmt", "self"), None, False, False, 0),
        (("std", "fmt", "Display"), "D", False, False, 0),
        (("crate", "a", "b"), None, False, True, 0),
        (("crate", "a", "c"), None, True, True, 0),
        (("::", "std", "io"), None, False, False, 0),
        (("super",), None, True, False, 0),
        (("x",), None, False, False, 0),
        (("y", "z"), "_", False, False, 0),
        (("inner", "g"), None, False, False, 1),  # local to the declaration holding it
    ]


def test_fixture_entities_kinds_and_provenance() -> None:
    found = derived(FIXTURE)
    files = {f.path: f for f in found.files}
    assert all(
        f.language == "rust" and f.coverage == "complete"
        for f in found.files
        if f.path.endswith(".rs")
    )
    assert files["Cargo.toml"].coverage == "unsupported"
    entities = {e.key: e for e in found.entities}
    kinds = {k: e.kind for k, e in entities.items() if e.category == "symbol"}
    assert kinds[STORE + "Store"] == "struct"
    assert kinds[STORE + "Mode"] == "enum"
    assert kinds[STORE + "Backend"] == "trait"
    assert kinds[STORE + "Backend.keys"] == "method"  # a provided method, nested in its trait
    assert kinds[STORE + "Key"] == "type"
    assert kinds[STORE + "MAX_KEYS"] == "constant"
    assert kinds[STORE + "NAME"] == "variable"
    assert kinds[STORE + "<impl Store>"] == "impl"
    assert kinds[STORE + "<impl Store>.new"] == "method"
    assert kinds[STORE + "<impl Backend for Store>.get"] == "method"
    assert kinds[UTIL + "square"] == "macro"
    assert kinds["symbol:crates/util/src/text.rs#pad"] == "function"
    # Equal sibling names take source-order ordinals; an inline module adds no segment.
    assert kinds[PLATFORM + "separator~1"] == "function"
    assert kinds[CART + "discount"] == "function"
    assert entities[STORE + "<impl Store>.new"].name == "new"
    assert entities[STORE + "<impl Backend for Store>"].name == "<impl Backend for Store>"
    assert entities[STORE + "Store"].signature == "pub struct Store"
    assert entities[STORE + "<impl Store>.must_get"].signature == (
        "pub fn must_get(&self, key: &Key) -> String"
    )
    source = entities[STORE + "<impl Store>.new"].source
    assert source is not None
    text = (FIXTURE / source.path).read_bytes()[source.start : source.end].decode()
    assert text.startswith("pub fn new(mode: Mode) -> Self {")
    tests = {k for k, e in entities.items() if e.test}
    assert tests == {
        MEMORY + "empty_memory_has_no_keys",
        MEMORY + "opens_a_store",
        "symbol:crates/shop/tests/api.rs#open_read",
    }
    modules = {e.key: e.name for e in found.entities if e.category == "module"}
    assert modules == {
        f"module:rust:{LIB}": "shop",
        f"module:rust:{LIB}::store": "shop::store",
        f"module:rust:{LIB}::store::memory": "shop::store::memory",
        f"module:rust:{LIB}::store::memory::tests": "shop::store::memory::tests",
        f"module:rust:{LIB}::cart": "shop::cart",
        f"module:rust:{LIB}::cart::pricing": "shop::cart::pricing",
        f"module:rust:{LIB}::platform": "shop::platform",
        "module:rust:crates/shop/src/bin/shop.rs": "shop (bin)",
        "module:rust:crates/shop/tests/api.rs": "api (test)",
        "module:rust:crates/util/src/lib.rs": "shop_util",
        "module:rust:crates/util/src/lib.rs::text": "shop_util::text",
    }


def test_fixture_relations() -> None:
    found = derived(FIXTURE)
    assert resolved(found, "imports", "file:" + LIB) == {
        CART + "Cart",
        STORE + "Backend",
        STORE + "Mode",
        STORE + "Store",  # a reexport names its target, not the module that reexports
    }
    assert resolved(found, "calls", "symbol:" + LIB + "#open") == {STORE + "<impl Store>.new"}
    assert resolved(found, "references", "symbol:" + LIB + "#open") == {
        STORE + "Mode",
        STORE + "Store",
    }
    assert resolved(found, "imports", f"file:{SHOP}cart.rs") == {
        f"module:rust:{LIB}::store",  # `store::{self, ..}`
        STORE + "Backend",
        STORE + "Store",
        UTIL + "map_all",  # through the workspace dependency
        UTIL + "square",  # a #[macro_export] macro, by path
        CART + "helper",  # `use super::helper` inside the inline module
    }
    assert resolved(found, "calls", CART + "<impl Cart>.total") == {
        UTIL + "square",  # `square!`
        CART + "discount",  # `pricing::discount`, an inline module
        CART + "Line",  # a tuple struct's constructor
    }
    assert resolved(found, "calls", CART + "<impl Cart>.names") == {UTIL + "map_all"}
    assert resolved(found, "calls", CART + "<impl Cart>.open") == {
        STORE + "<impl Store>.new",  # `store::Store::new`, through `use ...::{self}`
        STORE + "Mode",  # a variant constructor resolves to its enum
    }
    assert resolved(found, "calls", CART + "discount") == {CART + "helper"}  # `use super::..`
    assert resolved(found, "references", CART + "<impl Cart>.first_key") == {STORE + "Key"}
    assert resolved(found, "references", CART + "Cart") == {STORE + "Store", CART + "Line"}
    assert resolved(found, "references", CART + "<impl Cart>") == {CART + "Cart"}
    assert resolved(found, "references", STORE + "<impl Backend for Store>") == {
        STORE + "Backend",
        STORE + "Store",
    }
    assert resolved(found, "calls", STORE + "Backend.first") == {
        STORE + "Backend.keys"  # `self.keys()` in a trait's provided method
    }
    assert resolved(found, "calls", STORE + "<impl Store>.must_get") == {
        STORE + "<impl Store>.get"  # an inherent method shadows the trait's
    }
    assert resolved(found, "calls", STORE + "<impl Backend for Store>.get") == {
        STORE + "<impl Store>.get"  # `Store::get(self, ..)`
    }
    assert resolved(found, "calls", STORE + "<impl Store>.label") == {
        "symbol:crates/util/src/text.rs#pad"
    }
    assert resolved(found, "references", STORE + "<impl Store>.mode") == {STORE + "Mode"}
    assert resolved(found, "calls", "symbol:crates/util/src/text.rs#shout") == {
        "symbol:crates/util/src/text.rs#pad"
    }
    # The binary and the integration test see the library under its package name.
    for path in ("crates/shop/src/bin/shop.rs", "crates/shop/tests/api.rs"):
        assert resolved(found, "imports", f"file:{path}") == {
            "symbol:" + LIB + "#open",
            STORE + "Mode",
        }
    assert resolved(found, "calls", "symbol:crates/shop/src/bin/shop.rs#main") == {
        "symbol:" + LIB + "#open"
    }
    # Standard library items, std macros and methods on other receivers are unresolved.
    assert "std::collections::HashMap" in unresolved(found, "imports", f"file:{SHOP}store/mod.rs")
    assert unresolved(found, "calls", STORE + "<impl Store>.new") == {
        "HashMap::new",
        "Memory::default",
    }
    assert unresolved(found, "calls", PLATFORM + "join") == {"format!"}
    # `cfg` is not evaluated: both platform variants are candidates.
    ambiguous = [(e.source, e.targets) for e in found.edges if e.resolution == "ambiguous"]
    assert ambiguous == [(PLATFORM + "join", (PLATFORM + "separator", PLATFORM + "separator~1"))]
    # `use super::*` in a test module reaches the parent's items and the grandparent's.
    assert resolved(found, "tested_by", "symbol:" + LIB + "#open") == {
        MEMORY + "opens_a_store",
        "symbol:crates/shop/tests/api.rs#open_read",
    }
    assert resolved(found, "imports", f"file:{SHOP}store/memory.rs") == {
        f"module:rust:{LIB}::store",
        f"module:rust:{LIB}::store::memory",
    }
    assert resolved(found, "references", MEMORY + "Memory") == {STORE + "Key"}  # via the glob
    defines = resolved(found, "defines", f"module:rust:{LIB}::store")
    assert STORE + "<impl Store>" in defines
    assert STORE + "<impl Store>.new" not in defines  # nested in its impl
    assert STORE + "Backend.get" not in defines
    assert resolved(found, "contains_logical", f"module:rust:{LIB}::cart") == {
        f"module:rust:{LIB}::cart::pricing",
        f"file:{SHOP}cart.rs",
    }


def test_search_and_context(tmp_path: Path, fts: Path) -> None:
    with oxide.open_repository(FIXTURE, data_dir=tmp_path / "data") as repo:
        repo.index()
        result = repo.search("must get store entries", symbols=["must_get"])
        assert result.entries[0].entity.key == STORE + "<impl Store>.must_get"
        bundle = repo.build_context("open the store", symbols=["new"], budget=1024)
        assert 0 < bundle.used <= 1024
        assert "pub fn new(mode: Mode) -> Self" in bundle.payload
        by_kind = repo.search("Backend trait provided methods", symbols=["Backend"])
        assert STORE + "Backend" in {e.entity.key for e in by_kind.entries}


def test_impl_blocks_are_named_by_trait_and_type_only(tmp_path: Path) -> None:
    found = crate(
        tmp_path,
        {
            "src/lib.rs": (
                "pub struct S<T>(T);\n"
                "impl<T: Clone> S<T> where T: Copy { fn a(&self) {} }\n"
                "impl<T> S<T> { fn a(&self) {} }\n"
                "impl /* c */ std::fmt::Display for S<u8> { fn fmt(&self) {} }\n"
                "impl<T> From<T> for S<T> { fn from(t: T) -> Self { S(t) } }\n"
                "impl<'a> Tr for &'a S<u8> {}\n"
                "impl<T> Tr for T {}\n"
                "trait Tr {}\n"
            )
        },
    )
    keys = [e.key for e in found.entities if e.kind == "impl"]
    assert keys == [
        "symbol:src/lib.rs#<impl S<T>>",
        "symbol:src/lib.rs#<impl S<T>>~1",
        "symbol:src/lib.rs#<impl std::fmt::Display for S<u8>>",
        "symbol:src/lib.rs#<impl From<T> for S<T>>",
        "symbol:src/lib.rs#<impl Tr for &'a S<u8>>",
        "symbol:src/lib.rs#<impl Tr for T>",
    ]
    methods = {e.key for e in found.entities if e.kind == "method"}
    assert "symbol:src/lib.rs#<impl S<T>>.a" in methods
    assert "symbol:src/lib.rs#<impl S<T>>~1.a" in methods  # the ordinal belongs to the impl
    # A blanket impl's generic is not a reference; the trait's name is.
    blanket = "symbol:src/lib.rs#<impl Tr for T>"
    assert resolved(found, "references", blanket) == {"symbol:src/lib.rs#Tr"}
    assert unresolved(found, "references", blanket) == set()


def test_paths_crate_self_super_and_self_type(tmp_path: Path) -> None:
    found = crate(
        tmp_path,
        {
            "src/lib.rs": "mod a;\npub fn root() {}\npub struct Top;\n",
            "src/a/mod.rs": (
                "mod b;\npub fn up() { super::root(); crate::root(); self::here(); }\n"
                "fn here() {}\n"
            ),
            "src/a/b.rs": (
                "pub struct T;\n"
                "impl T {\n    fn new() -> Self { Self::make() }\n    fn make() -> T { T }\n}\n"
                "fn f() { super::super::root(); super::up(); crate::a::up(); }\n"
                "fn g() -> super::super::Top { Top }\n"
            ),
        },
    )
    b = "symbol:src/a/b.rs#"
    assert resolved(found, "calls", "symbol:src/a/mod.rs#up") == {
        "symbol:src/lib.rs#root",
        "symbol:src/a/mod.rs#here",
    }
    assert resolved(found, "calls", b + "f") == {"symbol:src/lib.rs#root", "symbol:src/a/mod.rs#up"}
    assert resolved(found, "calls", b + "<impl T>.new") == {b + "<impl T>.make"}
    assert resolved(found, "references", b + "g") == {"symbol:src/lib.rs#Top"}


def test_imports_aliases_reexports_and_globs(tmp_path: Path) -> None:
    found = crate(
        tmp_path,
        {
            "src/lib.rs": (
                "mod inner;\npub use inner::deep::Thing as Alias;\nuse inner::deep as d;\n"
                "use inner::*;\nuse inner::deep::*;\n"
                "fn caller() { Alias::go(); d::Thing::go(); shared(); only_deep(); }\n"
                "fn shadow() { shared(); }\nfn shared() {}\n"
            ),
            "src/inner/mod.rs": "pub mod deep;\npub fn shared() {}\n",
            "src/inner/deep.rs": (
                "pub struct Thing;\nimpl Thing { pub fn go() {} }\npub fn only_deep() {}\n"
                "pub fn shared() {}\n"
            ),
        },
    )
    thing = "symbol:src/inner/deep.rs#"
    assert resolved(found, "calls", "symbol:src/lib.rs#caller") == {
        thing + "<impl Thing>.go",  # through the alias, and through the module alias
        thing + "only_deep",
        "symbol:src/lib.rs#shared",  # the module's own item shadows both globs
    }
    assert resolved(found, "calls", "symbol:src/lib.rs#shadow") == {"symbol:src/lib.rs#shared"}
    # Two globs offering the same name make a use ambiguous.
    other = crate(
        tmp_path / "other",
        {
            "src/lib.rs": "mod a;\nmod b;\nuse a::*;\nuse b::*;\nfn f() { x(); }\n",
            "src/a.rs": "pub fn x() {}\n",
            "src/b.rs": "pub fn x() {}\n",
        },
    )
    (edge,) = [e for e in other.edges if e.kind == "calls" and e.name == "x"]
    assert edge.resolution == "ambiguous" and len(edge.targets) == 2


def test_glob_cycles_terminate_and_private_items_stay_private(tmp_path: Path) -> None:
    found = crate(
        tmp_path,
        {
            "src/lib.rs": "mod a;\nmod b;\nuse a::*;\nfn f() { from_b(); hidden(); }\n",
            "src/a.rs": "pub use crate::b::*;\nfn hidden() {}\n",
            "src/b.rs": "pub use crate::a::*;\npub fn from_b() {}\n",
        },
    )
    assert resolved(found, "calls", "symbol:src/lib.rs#f") == {"symbol:src/b.rs#from_b"}
    assert "hidden" in unresolved(found, "calls", "symbol:src/lib.rs#f")  # not `pub`


def test_enum_variants_and_associated_items(tmp_path: Path) -> None:
    found = crate(
        tmp_path,
        {
            "src/lib.rs": (
                "pub enum E { A, B(u8) }\n"
                "impl E { pub const MAX: u8 = 1; pub fn make() -> E { E::B(1) } }\n"
                "pub trait Tr { fn t(); }\nimpl Tr for E { fn t() {} }\n"
                "fn f() { E::make(); E::A; E::MAX; Tr::t(); E::t(); Self::x(); }\n"
            ),
        },
    )
    f = "symbol:src/lib.rs#f"
    assert resolved(found, "calls", f) == {
        "symbol:src/lib.rs#<impl E>.make",
        "symbol:src/lib.rs#Tr.t",
        "symbol:src/lib.rs#<impl Tr for E>.t",
    }
    # `E::A` is a variant: it resolves to its enum; `E::MAX` to the associated constant.
    assert resolved(found, "references", f) == {
        "symbol:src/lib.rs#E",
        "symbol:src/lib.rs#<impl E>.MAX",
    }
    assert unresolved(found, "calls", f) == {"Self::x"}  # no impl or trait around it
    assert resolved(found, "calls", "symbol:src/lib.rs#<impl E>.make") == {"symbol:src/lib.rs#E"}


def test_scoping_is_conservative(tmp_path: Path) -> None:
    found = crate(
        tmp_path,
        {
            "src/lib.rs": (
                "fn helper() {}\nstruct Item;\n"
                "fn shadowed(helper: fn()) { helper(); }\n"
                "fn closure() { let helper = || (); helper(); }\n"
                "fn pattern() { if let Some(helper) = x { helper(); } }\n"
                "fn matched() { match x { helper => helper() } }\n"
                "fn generic<Item>(i: Item) {}\n"
                "fn nested() { fn helper() {} helper(); }\n"
                "fn plain() { helper(); let _x: Item; }\n"
                "fn local_use() { use other::helper; helper(); }\n"
            )
        },
    )
    relations = ("calls", "references")
    for scope in ("shadowed", "closure", "pattern", "matched", "nested", "generic"):
        edges = [
            e
            for e in found.edges
            if e.source == f"symbol:src/lib.rs#{scope}" and e.kind in relations
        ]
        assert [e.name for e in edges if e.name in ("helper", "Item") or e.targets] == [], scope
    assert resolved(found, "calls", "symbol:src/lib.rs#plain") == {"symbol:src/lib.rs#helper"}
    assert resolved(found, "references", "symbol:src/lib.rs#plain") == {"symbol:src/lib.rs#Item"}
    # A `use` inside a function binds there only, and `other` is not a module: unresolved.
    assert unresolved(found, "calls", "symbol:src/lib.rs#local_use") == set()
    assert unresolved(found, "imports", "file:src/lib.rs") == {"other::helper"}


def test_macros_are_conservative(tmp_path: Path) -> None:
    root = write(
        tmp_path,
        {
            "Cargo.toml": "[workspace]\nmembers = ['m', 'n']\n",
            "m/Cargo.toml": CARGO.replace("'p'", "'m'"),
            "m/src/lib.rs": (
                "#[macro_export]\nmacro_rules! exported { () => {} }\n"
                "macro_rules! local { () => {} }\n"
                "pub fn f() {\n    local!();\n    exported!();\n    crate::exported!();\n"
                "    println!();\n    unknown!();\n}\n"
            ),
            "n/Cargo.toml": CARGO.replace("'p'", "'n'") + "[dependencies]\nm = { path = '../m' }\n",
            "n/src/lib.rs": (
                "use m::exported;\npub fn g() {\n    exported!();\n    m::exported!();\n"
                "    m::local!();\n}\n"
            ),
        },
    )
    found = derived(root)
    exported = "symbol:m/src/lib.rs#exported"
    assert resolved(found, "calls", "symbol:m/src/lib.rs#f") == {
        exported,
        "symbol:m/src/lib.rs#local",
    }
    assert unresolved(found, "calls", "symbol:m/src/lib.rs#f") == {"println!", "unknown!"}
    assert resolved(found, "calls", "symbol:n/src/lib.rs#g") == {exported}  # not the private one
    assert unresolved(found, "calls", "symbol:n/src/lib.rs#g") == {"m::local!"}
    # Macro arguments and bodies are token trees: their contents are not read.
    parsed = rust.parse(
        b"fn f() { assert_eq!(g(), h::<u8>()); }\nmacro_rules! m { () => { k() } }\n"
    )
    assert not isinstance(parsed, str)
    assert [u.name for s in parsed.scopes for u in s.calls] == ["assert_eq!"]


def test_test_attributes(tmp_path: Path) -> None:
    found = crate(
        tmp_path,
        {
            "src/lib.rs": (
                '#[test]\nfn a() {}\n#[tokio::test(flavor = "x")]\nasync fn b() {}\n'
                "#[cfg(test)]\nfn helper() {}\n#[test] // note\n/* c */ fn c() {}\n"
                "#[test]\nstruct NotAFunction;\nfn plain() {}\n"
                "#[test]\n\nconst GAP: u8 = 1;\nfn after() {}\n"
            )
        },
    )
    tests = {e.name for e in found.entities if e.test}
    assert tests == {"a", "b", "c"}


def test_module_files_follow_rust_rules(tmp_path: Path) -> None:
    found = crate(
        tmp_path,
        {
            "src/lib.rs": (
                "mod a;\nmod b;\nmod inline { mod c; }\nmod missing;\n"
                '#[path = "elsewhere/p.rs"]\nmod p;\n#[cfg(unix)]\nmod dup;\n'
                "#[cfg(windows)]\nmod dup;\n"
            ),
            "src/a.rs": "mod nested;\n",
            "src/a/nested.rs": "pub fn n() {}\n",
            "src/b/mod.rs": "mod leaf;\n",
            "src/b/leaf.rs": "pub fn l() {}\n",
            "src/inline/c.rs": "pub fn c() {}\n",
            "src/elsewhere/p.rs": "pub fn p() {}\n",
            "src/dup.rs": "pub fn d() {}\n",
            "src/orphan.rs": "pub fn o() {}\n",
        },
    )
    modules = {
        e.key.removeprefix("module:rust:"): e.name for e in found.entities if e.category == "module"
    }
    assert modules == {
        "src/lib.rs": "p",
        "src/lib.rs::a": "p::a",
        "src/lib.rs::a::nested": "p::a::nested",
        "src/lib.rs::b": "p::b",
        "src/lib.rs::b::leaf": "p::b::leaf",
        "src/lib.rs::inline": "p::inline",
        "src/lib.rs::inline::c": "p::inline::c",  # an inline module adds a directory
        "src/lib.rs::p": "p::p",  # `#[path]`
        "src/lib.rs::dup": "p::dup",  # one module for both cfg variants of the name
        "src/orphan.rs": "orphan",  # reached by no `mod`: a crate of its own
    }
    defined = resolved(found, "defines", "module:rust:src/lib.rs::a::nested")
    assert defined == {"symbol:src/a/nested.rs#n"}


def test_files_shared_by_library_and_binary_belong_to_the_first(tmp_path: Path) -> None:
    found = crate(
        tmp_path,
        {
            "src/lib.rs": "pub mod util;\n",
            "src/main.rs": "mod util;\nfn main() { util::u(); crate::util::u(); }\n",
            "src/util.rs": "pub fn u() {}\n",
        },
    )
    modules = {e.key for e in found.entities if e.category == "module"}
    assert modules == {
        "module:rust:src/lib.rs",
        "module:rust:src/lib.rs::util",  # one module for the shared file
        "module:rust:src/main.rs",
    }
    assert resolved(found, "calls", "symbol:src/main.rs#main") == {"symbol:src/util.rs#u"}


def test_cargo_targets_workspaces_and_editions(tmp_path: Path) -> None:
    root = write(
        tmp_path,
        {
            "Cargo.toml": (
                "[workspace]\nmembers = ['crates/*']\n[workspace.package]\nedition = '2018'\n"
                "[workspace.dependencies]\ncore-lib = { path = 'crates/core' }\n"
                "renamed = { path = 'crates/core', package = 'core-lib' }\n"
            ),
            "crates/core/Cargo.toml": (
                "[package]\nname = 'core-lib'\nedition.workspace = true\n"
                "[lib]\nname = 'corelib'\npath = 'src/kernel.rs'\n"
            ),
            "crates/core/src/kernel.rs": "pub fn run() {}\n",
            "crates/app/Cargo.toml": (
                "[package]\nname = 'app'\nedition = '2021'\n"
                "[dependencies]\ncore-lib = { workspace = true }\n"
                "[[bin]]\nname = 'tool'\npath = 'tools/tool.rs'\n"
            ),
            "crates/app/src/main.rs": (
                "fn main() { corelib::run(); core_lib::run(); renamed::run(); }\n"
            ),
            "crates/app/src/bin/extra.rs": "fn main() {}\n",
            "crates/app/src/bin/multi/main.rs": "fn main() {}\n",
            "crates/app/tools/tool.rs": "use corelib::run;\nfn main() { run(); }\n",
            "crates/app/tests/it.rs": "fn t() {}\n",
            "crates/app/examples/e.rs": "fn e() {}\n",
            "crates/app/benches/b.rs": "fn b() {}\n",
            "crates/app/build.rs": "fn main() {}\n",
            "crates/other/Cargo.toml": (
                "[package]\nname = 'other'\nedition = '2021'\n"
                "[dependencies]\nrenamed = { workspace = true }\n"
            ),
            "crates/other/src/lib.rs": "pub fn f() { renamed::run(); corelib::run(); }\n",
        },
    )
    found = derived(root)
    modules = {
        e.key.removeprefix("module:rust:"): e.name for e in found.entities if e.category == "module"
    }
    assert modules == {
        "crates/core/src/kernel.rs": "corelib",
        "crates/app/src/main.rs": "app (bin)",
        "crates/app/tools/tool.rs": "tool (bin)",
        "crates/app/src/bin/extra.rs": "extra (bin)",
        "crates/app/src/bin/multi/main.rs": "multi (bin)",
        "crates/app/tests/it.rs": "it (test)",
        "crates/app/examples/e.rs": "e (example)",
        "crates/app/benches/b.rs": "b (bench)",
        "crates/app/build.rs": "build (build)",
        "crates/other/src/lib.rs": "other",
    }
    run = "symbol:crates/core/src/kernel.rs#run"
    # Cargo's rule: an unrenamed dependency is named by its library target (`corelib`,
    # not the package name `core_lib`); `package = ".."` makes the key the name.
    assert resolved(found, "calls", "symbol:crates/app/src/main.rs#main") == {run}
    assert unresolved(found, "calls", "symbol:crates/app/src/main.rs#main") == {
        "core_lib::run",
        "renamed::run",
    }
    assert resolved(found, "imports", "file:crates/app/tools/tool.rs") == {run}
    assert resolved(found, "calls", "symbol:crates/app/tools/tool.rs#main") == {run}
    assert resolved(found, "calls", "symbol:crates/other/src/lib.rs#f") == {run}
    assert unresolved(found, "calls", "symbol:crates/other/src/lib.rs#f") == {"corelib::run"}


def test_extern_crate_reexports_and_declared_test_roots(tmp_path: Path) -> None:
    root = write(
        tmp_path,
        {
            "Cargo.toml": "[workspace]\nmembers = ['a', 'b', 'c']\n",
            "a/Cargo.toml": CARGO.replace("'p'", "'a'")
            + "autotests = false\n[[test]]\nname = 'all'\npath = 'tests/all.rs'\n"
            + "[dependencies]\nb = { path = '../b' }\n",
            "a/src/lib.rs": "pub extern crate b as inner;\nextern crate b;\n",
            "a/tests/all.rs": "mod util;\nmod case;\nfn root() { util::u(); }\n",
            "a/tests/util.rs": "pub fn u() {}\n",
            "a/tests/case.rs": "use crate::util::u;\nfn t() { u(); }\n",
            "b/Cargo.toml": CARGO.replace("'p'", "'b'"),
            "b/src/lib.rs": "pub fn f() {}\n",
            "c/Cargo.toml": CARGO.replace("'p'", "'c'") + "[dependencies]\na = { path = '../a' }\n",
            "c/src/lib.rs": "pub fn g() { a::inner::f(); }\n",
        },
    )
    found = derived(root)
    assert resolved(found, "calls", "symbol:c/src/lib.rs#g") == {"symbol:b/src/lib.rs#f"}
    # `autotests = false`: tests/util.rs and tests/case.rs are modules of the declared
    # root, not crates of their own, so `crate::util` is found.
    assert resolved(found, "imports", "file:a/tests/case.rs") == {"symbol:a/tests/util.rs#u"}
    assert resolved(found, "calls", "symbol:a/tests/case.rs#t") == {"symbol:a/tests/util.rs#u"}
    modules = {e.key for e in found.entities if e.category == "module" and "tests" in e.key}
    assert modules == {
        "module:rust:a/tests/all.rs",
        "module:rust:a/tests/all.rs::util",
        "module:rust:a/tests/all.rs::case",
    }


def test_cfg_variants_of_a_module_are_ambiguous_not_chosen(tmp_path: Path) -> None:
    found = crate(
        tmp_path,
        {
            "src/lib.rs": (
                '#[cfg(unix)]\n#[path = "unix.rs"]\nmod sys;\n'
                '#[cfg(windows)]\n#[path = "windows.rs"]\nmod sys;\n'
                "pub fn f() { sys::sep(); }\n"
            ),
            "src/unix.rs": "pub fn sep() {}\nfn only_unix() { sep(); }\n",
            "src/windows.rs": "pub fn sep() {}\n",
        },
    )
    assert {e.key for e in found.entities if e.category == "module"} == {
        "module:rust:src/lib.rs",
        "module:rust:src/lib.rs::sys",
        "module:rust:src/lib.rs::sys~1",
    }
    (edge,) = [e for e in found.edges if e.kind == "calls" and e.source.endswith("#f")]
    assert edge.resolution == "ambiguous"
    assert edge.targets == ("symbol:src/unix.rs#sep", "symbol:src/windows.rs#sep")
    # Each variant is a module of the crate: its own items resolve inside it.
    assert resolved(found, "calls", "symbol:src/unix.rs#only_unix") == {"symbol:src/unix.rs#sep"}


def test_cfg_wrapper_macros_are_read_as_their_items(tmp_path: Path) -> None:
    source = (
        "cfg_rt! {\n    pub mod runtime;\n    pub fn spawn() { runtime::run(); }\n}\n"
        "cfg_if! {\n    if #[cfg(unix)] {\n        mod sys;\n    } else if #[cfg(windows)] {\n"
        "        mod sys;\n    } else {\n        fn fallback() {}\n    }\n}\n"
        "struct S;\nimpl S {\n    cfg_x! { pub fn a(&self) {} }\n}\n"
        "cfg_outer! { cfg_inner! { fn nested() {} } }\n"
        "cfg_calc!(1 + 2);\nother! { fn not_passed_through() {} }\ncfg_bad! { fn broken( }\n"
    )
    found = crate(
        tmp_path,
        {
            "src/lib.rs": source,
            "src/runtime.rs": "pub fn run() {}\n",
            "src/sys.rs": "pub fn sep() {}\n",
        },
    )
    keys = {e.key for e in found.entities if e.category == "symbol"}
    assert {
        "symbol:src/lib.rs#spawn",
        "symbol:src/lib.rs#fallback",
        "symbol:src/lib.rs#<impl S>.a",
        "symbol:src/lib.rs#nested",
    } <= keys
    assert not keys & {"symbol:src/lib.rs#not_passed_through", "symbol:src/lib.rs#broken"}
    assert "module:rust:src/lib.rs::runtime" in {e.key for e in found.entities}
    assert resolved(found, "calls", "symbol:src/lib.rs#spawn") == {"symbol:src/runtime.rs#run"}
    # Offsets are the original file's: a symbol's span is its own text.
    entity = next(e for e in found.entities if e.key == "symbol:src/lib.rs#spawn")
    assert entity.source is not None
    original = source.encode()[entity.source.start : entity.source.end]
    assert original == b"pub fn spawn() { runtime::run(); }"


def test_edition_2015_use_paths_start_at_the_crate_root(tmp_path: Path) -> None:
    files = {
        "src/lib.rs": "mod a;\npub fn root() {}\n",
        "src/a.rs": (
            "use root;\nuse self::sub::x;\nmod sub { pub fn x() {} }\nfn f() { x(); root(); }\n"
        ),
    }
    old = crate(tmp_path / "old", files, "[package]\nname = 'old'\n")  # Cargo's default: 2015
    new = crate(tmp_path / "new", files)
    assert resolved(old, "imports", "file:src/a.rs") == {
        "symbol:src/lib.rs#root",
        "symbol:src/a.rs#x",
    }
    assert resolved(new, "imports", "file:src/a.rs") == {"symbol:src/a.rs#x"}
    # In 2015 `use root;` brings `root` into scope; in 2018 it names nothing.
    assert resolved(old, "calls", "symbol:src/a.rs#f") == {
        "symbol:src/a.rs#x",
        "symbol:src/lib.rs#root",
    }
    assert resolved(new, "calls", "symbol:src/a.rs#f") == {"symbol:src/a.rs#x"}
    assert unresolved(new, "imports", "file:src/a.rs") == {"root"}


def test_without_a_manifest_files_are_crates_of_their_own(tmp_path: Path) -> None:
    found = derived(
        write(tmp_path, {"a.rs": "mod b;\nfn a() { b::f(); }\n", "b.rs": "pub fn f() {}\n"})
    )
    assert resolved(found, "calls", "symbol:a.rs#a") == {"symbol:b.rs#f"}
    assert {e.key for e in found.entities if e.category == "module"} == {
        "module:rust:a.rs",
        "module:rust:a.rs::b",
    }


def test_a_manifest_that_does_not_parse_is_ignored(tmp_path: Path) -> None:
    found = crate(
        tmp_path,
        {
            "src/lib.rs": "pub fn f() {}\n",
            "sub/Cargo.toml": b"\xff[[[",
            "sub/src/lib.rs": "fn g() {}\n",
        },
        "[package\nname = ",
    )
    assert {e.key for e in found.entities if e.category == "module"} == {
        "module:rust:src/lib.rs",
        "module:rust:sub/src/lib.rs",
    }


def test_identity_is_local_and_deterministic(tmp_path: Path) -> None:
    files = {"src/lib.rs": "fn f() {}\nfn f() {}\nstruct S;\nimpl S { fn f(&self) {} }\n"}
    first = crate(tmp_path / "one", files)
    second = crate(tmp_path / "two", {**files, "src/other.rs": "fn f() {}\n"})
    keys = {e.key for e in first.entities}
    assert {"symbol:src/lib.rs#f", "symbol:src/lib.rs#f~1", "symbol:src/lib.rs#<impl S>.f"} <= keys
    assert keys <= {e.key for e in second.entities}
    assert derived(tmp_path / "one") == first
    assert dialect("dir/x.rs") == "rust"


def test_coverage_cache_validation_and_limits(tmp_path: Path) -> None:
    root = write(
        tmp_path,
        {
            "ok.rs": "pub fn ok() {}\n",
            "broken.rs": "fn broken( {\n    let = ;\n}\n\nfn after() {}\n",
            "latin.rs": b"fn f() {} // \xff\n",
        },
    )
    full = derived(root)
    files = {f.path: f for f in full.files}
    assert files["broken.rs"].coverage == "partial" and files["broken.rs"].diagnostics
    assert files["latin.rs"].coverage == "unsupported"
    assert "symbol:broken.rs#after" in {e.key for e in full.entities}
    assert set(full.facts) == {"rust"}
    for f in capture(root).files:
        parsed = rust.parse(f.data)
        if not isinstance(parsed, str):
            assert rust.decode(json.loads(json.dumps(rust.encode(parsed))), len(f.data)) == parsed
    assert derive(capture(root), "test-repo", full.facts).reused == 2
    cached = copy.deepcopy(full.facts)
    for key in cached["rust"]:
        cached["rust"][key] = ["damaged"]
    assert derive(capture(root), "test-repo", cached) == full
    deep = ("fn f() { let x = " + "(" * 70000 + "1" + ")" * 70000 + "; }\n").encode()
    assert rust.parse(deep) == python.TOO_DEEP


def test_adversarial_nesting_does_not_recurse(tmp_path: Path) -> None:
    modules = "".join(f"mod m{i} {{ " for i in range(1500)) + "pub fn f() {}" + " }" * 1500
    chain = "fn g() { a" + "::a" * 3000 + "(); }\n"
    found = crate(tmp_path, {"src/lib.rs": modules + "\n" + chain})
    assert {"symbol:src/lib.rs#f", "symbol:src/lib.rs#g"} <= {e.key for e in found.entities}
    assert len([e for e in found.entities if e.category == "module"]) == 1501


def test_untrusted_manifests_and_layouts_cannot_crash_indexing(tmp_path: Path) -> None:
    deep = "a = " + "[" * 5000 + "]" * 5000 + "\n"
    nested = crate(tmp_path / "deep", {"src/lib.rs": "pub fn f() {}\n"}, deep)
    assert "symbol:src/lib.rs#f" in {e.key for e in nested.entities}  # an unreadable manifest
    # A dependency whose root file another crate already claimed has no module to offer.
    claimed = derived(
        write(
            tmp_path / "claimed",
            {
                "Cargo.toml": "[workspace]\nmembers = ['b', 'z']\n",
                "b/Cargo.toml": CARGO.replace("'p'", "'b'")
                + "[dependencies]\nz = { path = '../z' }\n",
                "b/src/lib.rs": '#[path = "../../z/src/lib.rs"]\nmod inner;\nfn f() { z::g(); }\n',
                "z/Cargo.toml": CARGO.replace("'p'", "'z'"),
                "z/src/lib.rs": "pub fn g() {}\n",
            },
        )
    )
    assert unresolved(claimed, "calls", "symbol:b/src/lib.rs#f") == {"z::g"}
    # A very long chain of glob reexports is cut, not followed until the stack gives out.
    depth = 1500  # past Python's recursion limit if each hop recursed
    files = {f"src/m{i}.rs": f"pub use crate::m{i + 1}::*;\n" for i in range(depth)}
    files[f"src/m{depth}.rs"] = "pub fn deep() {}\n"
    files["src/lib.rs"] = (
        "".join(f"mod m{i};\n" for i in range(depth + 1)) + "use m0::*;\nfn f() { deep(); }\n"
    )
    chained = crate(tmp_path / "chain", files)
    assert unresolved(chained, "calls", "symbol:src/lib.rs#f") == {"deep"}
    ok = {f"src/m{i}.rs": f"pub use crate::m{i + 1}::*;\n" for i in range(10)}
    ok["src/m10.rs"] = "pub fn deep() {}\n"
    ok["src/lib.rs"] = (
        "".join(f"mod m{i};\n" for i in range(11)) + "use m0::*;\nfn f() { deep(); }\n"
    )
    near = crate(tmp_path / "near", ok)
    assert resolved(near, "calls", "symbol:src/lib.rs#f") == {"symbol:src/m10.rs#deep"}


def test_imports_that_name_themselves_resolve_to_nothing_and_terminate(tmp_path: Path) -> None:
    found = crate(
        tmp_path,
        {
            "src/lib.rs": (
                "fn a() { use clap; clap::x(); }\n"
                "fn b() { use other::thing as other; other::go(); }\n"
                "fn c() { use q::r as p; use p::s as q; p::x(); }\n"
                "use serde;\nfn d() { serde::x(); }\n"
            )
        },
    )
    for scope in "abcd":
        assert not resolved(found, "calls", f"symbol:src/lib.rs#{scope}")


def test_wrapped_trait_method_signatures_keep_every_line(tmp_path: Path) -> None:
    found = crate(
        tmp_path,
        {
            "src/lib.rs": (
                "pub trait T {\n    fn long(\n        &self,\n        a: u8,\n"
                '    ) -> Result<u8, ()>;\n}\nextern "C" {\n    fn c_abi(x: i32);\n}\n'
            )
        },
    )
    signatures = {e.name: e.signature for e in found.entities if e.category == "symbol"}
    assert signatures["long"] == "fn long( &self, a: u8, ) -> Result<u8, ()>"
    assert signatures["c_abi"] == "fn c_abi(x: i32)"


def test_unsound_cached_facts_are_parsed_never_trusted_or_fatal(tmp_path: Path) -> None:
    source = (
        "use std::fmt::{self, Display};\nmod m { pub fn f() {} }\n#[test]\n"
        "fn t<T: Display>(x: T) { m::f(); Self::g(); x.h(); self.k(); }\n"
        "enum E { A, B }\nimpl<T> Tr for S<T> { fn z(&self) { self.z() } const C: u8 = 1; }\n"
    )
    snapshot = capture(write(tmp_path / "repo", {"lib.rs": source}))
    full = derive(snapshot, "r")
    ((key, good),) = full.facts["rust"].items()
    size = len(snapshot.files[0].data)
    rejected = 0
    for at in _paths(good):
        for bad in BAD:
            entry = _replace(good, at, bad)
            try:
                rust.decode(entry, size)
            except ValueError:
                rejected += 1
                result = derive(snapshot, "r", {"rust": {key: entry}})
                assert result == full and result.reused == 0, (at, bad)
                continue
            # Well-formed facts parse could have produced: they may differ, never fail.
            validate(derive(snapshot, "r", {"rust": {key: entry}}))
    assert rejected > len(_paths(good)) * len(BAD) // 2


def test_persisted_incremental_equals_full_rebuild(tmp_path: Path) -> None:
    root = write(
        tmp_path / "repo",
        {
            "Cargo.toml": CARGO,
            "src/lib.rs": "pub mod a;\nmod b;\nuse a::A;\npub fn f() { A::new(); }\n",
            "src/a.rs": "pub struct A;\nimpl A { pub fn new() -> A { A } }\n",
            "src/b.rs": "use super::f;\nfn g() { f(); }\n",
        },
    )
    steps: list[dict[str, str | bytes | None]] = [
        {"src/c.rs": "pub fn c() {}\n", "src/lib.rs": "pub mod a;\nmod b;\nmod c;\n"},
        {"Cargo.toml": CARGO.replace("2021", "2015")},  # metadata only: resolution changes
        {"src/lib.rs": "pub mod a;\nmod c;\nuse a::A;\npub fn f() { A::new(); }\n"},  # no `mod b;`
        {"Cargo.toml": CARGO.replace("'p'", "'q'") + "[lib]\nname = 'renamed'\n"},
        {"src/a.rs": None, "src/a/mod.rs": "pub struct A;\n", "src/b.rs": "fn g() {}\n"},
        {
            "src/lib.rs": (
                "pub mod a;\n#[cfg(test)]\nmod t {\n    use super::*;\n"
                "    #[test]\n    fn t() { f(); }\n}\n"
            )
        },
        {"Cargo.toml": None, "src/lib.rs": "mod a;\n"},
    ]
    with oxide.open_repository(root, data_dir=tmp_path / "incremental") as repo:
        repo.index()
        for i, edits in enumerate(steps):
            apply(root, edits)
            report = repo.index()
            assert report.reused >= 1
            with oxide.open_repository(root, data_dir=tmp_path / f"full-{i}") as fresh:
                fresh.index()
                assert dump(repo) == dump(fresh)
