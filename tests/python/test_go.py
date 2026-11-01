"""Go slice conformance (docs/go.md): identity, kinds, provenance, package and module
resolution, tests, cached facts and persisted incremental/full-rebuild equality."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from conftest import REPO, write
from test_incremental import BAD, _paths, _replace, apply, dump
from test_typescript import derived, resolved

import oxide
from oxide.capture import capture
from oxide.core.indexing import Derived, derive, dialect, validate
from oxide.core.languages import go, python

FIXTURE = REPO / "fixtures/go_repo"
MOD = "module example.com/m\n\ngo 1.22\n"


def unresolved(result: Derived, kind: str, source: str) -> set[str | None]:
    return {
        e.name
        for e in result.edges
        if e.kind == kind and e.source == source and e.resolution == "unresolved"
    }


def test_import_facts_are_in_source_order() -> None:
    parsed = go.parse(
        b'package p\nimport ("crypto/subtle"\n"encoding/base64"\n"net/http"\n'
        b'"strconv"\n\n"example.com/internal/bytesconv"\n)\n'
    )
    assert not isinstance(parsed, str)
    assert [item.path for item in parsed.imports] == [
        "crypto/subtle",
        "encoding/base64",
        "net/http",
        "strconv",
        "example.com/internal/bytesconv",
    ]


def test_fixture_entities_kinds_and_provenance() -> None:
    found = derived(FIXTURE)
    files = {f.path: f for f in found.files}
    assert all(
        f.language == "go" and f.coverage == "complete"
        for f in found.files
        if f.path.endswith(".go")
    )
    assert files["go.mod"].coverage == "unsupported"
    entities = {e.key: e for e in found.entities}
    kinds = {k: e.kind for k, e in entities.items() if e.category == "symbol"}
    assert kinds["symbol:store/store.go#Store"] == "struct"
    assert kinds["symbol:store/store.go#Backend"] == "interface"
    assert kinds["symbol:store/store.go#Backend.Get"] == "method"
    assert kinds["symbol:store/store.go#Key"] == "type"
    assert kinds["symbol:store/store.go#Store.Get"] == "method"
    assert kinds["symbol:store/config.go#ModeRead"] == "constant"
    assert kinds["symbol:store/config.go#ErrClosed"] == "variable"
    assert kinds["symbol:internal/util/util.go#Map"] == "function"
    # Equal segments take source-order ordinals; a method's segment is Receiver.Name.
    assert kinds["symbol:store/path_windows.go#init~1"] == "function"
    assert entities["symbol:store/store.go#Store.Get"].name == "Get"
    assert entities["symbol:store/store_test.go#TestGet"].test
    assert entities["symbol:store/api_test.go#TestOpen"].test
    assert not entities["symbol:store/api_test.go#Testable"].test
    assert not entities["symbol:store/store_test.go#helper"].test
    modules = {e.key: e.name for e in found.entities if e.category == "module"}
    assert modules == {
        "module:go:cart/cart": "example.com/shop/cart",
        "module:go:cmd/shop/main": "example.com/shop/cmd/shop",
        "module:go:internal/util/util": "example.com/shop/internal/util",
        "module:go:store/store": "example.com/shop/store",
        "module:go:store/store_test": "example.com/shop/store_test",
        "module:go:tools/tools": "example.com/shop/tools",
    }
    # Byte-exact provenance: a grouped entry spans its spec, a single one its declaration.
    data = {f.path: f.data for f in capture(FIXTURE).files}
    for key, text in [
        ("symbol:store/config.go#ModeWrite", "ModeWrite = 2"),
        ("symbol:store/store.go#DefaultLimit", "const DefaultLimit = 10"),
        ("symbol:store/store.go#Resource.ID", "ID() uint32"),
        ("symbol:store/store.go#Key", "type Key string"),
    ]:
        source = entities[key].source
        assert source is not None and source.digest == files[source.path].digest
        assert data[source.path][source.start : source.end].decode() == text
    signature = entities["symbol:store/store.go#Store.Get"].signature
    assert signature == "func (s *Store) Get(key Key) (string, bool)"


def test_fixture_relations() -> None:
    found = derived(FIXTURE)
    assert resolved(found, "imports", "file:cart/cart.go") == {
        "module:go:internal/util/util",
        "module:go:store/store",
        "module:go:tools/tools",  # through the nested module's own go.mod
    }
    assert resolved(found, "calls", "symbol:cart/cart.go#NewCart") == {
        "symbol:store/store.go#New"  # through the alias st
    }
    assert resolved(found, "calls", "symbol:cart/cart.go#Cart.Names") == {
        "symbol:tools/gen.go#Generate",
        "symbol:internal/util/util.go#Map",  # explicit instantiation
    }
    assert resolved(found, "references", "symbol:cart/cart.go#Cart") == {
        "symbol:store/store.go#Store"  # embedded *st.Store
    }
    assert resolved(found, "references", "symbol:store/store.go#Backend") == {
        "symbol:store/store.go#Resource"
    }
    assert resolved(found, "references", "symbol:store/store.go#Store.Get") == {
        "symbol:store/store.go#Store"  # the receiver type
    }
    assert resolved(found, "calls", "symbol:store/store.go#Store.MustGet") == {
        "symbol:store/store.go#Store.Get"  # through the receiver
    }
    assert resolved(found, "calls", "symbol:store/config.go#Open") == {"symbol:store/store.go#New"}
    assert resolved(found, "calls", "symbol:store/store.go#DefaultStore") == {
        "symbol:store/store.go#New"
    }
    # Promoted methods, parameters and the standard library stay unresolved.
    assert "c.Get" in unresolved(found, "calls", "symbol:cart/cart.go#Cart.Add")
    assert unresolved(found, "calls", "symbol:store/config.go#shadowed") == {"New"}
    assert "fmt" in unresolved(found, "imports", "file:cmd/shop/main.go")
    # Build constraints are not evaluated: platform files are not chosen between.
    ambiguous = [(e.source, e.targets) for e in found.edges if e.resolution == "ambiguous"]
    assert ambiguous == [
        (
            "symbol:store/path_windows.go#init~1",
            ("symbol:store/path_linux.go#dataPath", "symbol:store/path_windows.go#dataPath"),
        )
    ]
    assert resolved(found, "tested_by", "symbol:store/config.go#Open") == {
        "symbol:store/api_test.go#TestOpen"  # external test package, inside t.Run
    }
    assert resolved(found, "tested_by", "symbol:store/store_test.go#helper") == {
        "symbol:store/store_test.go#TestGet"
    }
    defines = resolved(found, "defines", "module:go:store/store")
    assert "symbol:store/store.go#Store.Get" in defines
    assert "symbol:store/store.go#Backend.Get" not in defines  # inside its interface


def test_search_and_context(tmp_path: Path, fts: Path) -> None:
    with oxide.open_repository(FIXTURE, data_dir=tmp_path / "data") as repo:
        repo.index()
        result = repo.search("MustGet store entries", symbols=["MustGet"])
        assert result.entries[0].entity.key == "symbol:store/store.go#Store.MustGet"
        bundle = repo.build_context("open the store", symbols=["Open"], budget=1024)
        assert 0 < bundle.used <= 1024
        assert "func Open(mode int)" in bundle.payload


def test_scoping_is_conservative(tmp_path: Path) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "go.mod": MOD,
                "a.go": "package a\n\nfunc f() {}\nfunc g() {}\ntype T struct{}\n"
                "func (t T) m() {}\nfunc (t T) n() { t.m(); g() }\n"
                "func (t T) r() { t := T{}; t.m() }\n"
                "func (T) anon() {}\n"
                "func blocks() { if x := 1; x > 0 { f := g; f() }; for f := range 3 { _ = f } }\n"
                "func closure() { func(g int) { _ = g }(1); g() }\n"
                "func typed[f any]() { f() }\n"
                "func asm(x int) int\n"
                "func conv() { _ = T(T{}); _ = T.m; g := T.m; g(T{}) }\n",
            },
        )
    )
    assert resolved(found, "calls", "symbol:a.go#T.n") == {"symbol:a.go#T.m", "symbol:a.go#g"}
    assert not resolved(found, "calls", "symbol:a.go#T.r")  # a rebound receiver is a local
    assert not resolved(found, "calls", "symbol:a.go#blocks")  # bound in any block: opaque
    assert not resolved(found, "calls", "symbol:a.go#closure")  # a literal's parameter g
    assert not resolved(found, "calls", "symbol:a.go#typed")  # a type parameter
    assert {"symbol:a.go#T.anon", "symbol:a.go#asm"} <= {e.key for e in found.entities}
    # A conversion calls its type; a method expression is not a call, nor its value's use.
    assert resolved(found, "calls", "symbol:a.go#conv") == {"symbol:a.go#T"}
    assert unresolved(found, "calls", "symbol:a.go#conv") == {"g"}


def test_receiver_type_parameters_are_local_to_methods(tmp_path: Path) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "a.go": "package a\nfunc X(i int) int { return i }\n"
                "type S[X ~int] struct{}\nfunc (s S[X]) M() { _ = X(1) }\n",
            },
        )
    )
    assert not resolved(found, "calls", "symbol:a.go#S.M")
    assert unresolved(found, "calls", "symbol:a.go#S.M") == {"X"}


def test_file_scope_closure_bindings_are_opaque(tmp_path: Path) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "a.go": "package a\nfunc F() {}\n"
                "var _ = func(F func()) int { F(); return 0 }(func(){})\n",
            },
        )
    )
    assert not resolved(found, "calls", "file:a.go")
    assert unresolved(found, "calls", "file:a.go") == {"F"}


def test_receiver_comments_do_not_change_method_identity(tmp_path: Path) -> None:
    found = derived(
        write(
            tmp_path,
            {"a.go": "package a\ntype S struct{}\nfunc (/* receiver */ s S) M() { s.M() }\n"},
        )
    )
    assert "symbol:a.go#S.M" in {e.key for e in found.entities}
    assert resolved(found, "calls", "symbol:a.go#S.M") == {"symbol:a.go#S.M"}


def test_indexed_function_values_are_not_calls_but_generic_calls_are(tmp_path: Path) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "a.go": "package a\nvar F []func()\nvar Fn func()\n"
                "func Generic[T any](value T) {}\n"
                "func G() { F[0](); Fn(); Generic[int](1) }\n",
            },
        )
    )
    assert resolved(found, "calls", "symbol:a.go#G") == {"symbol:a.go#Generic"}
    assert unresolved(found, "calls", "symbol:a.go#G") == {"Fn"}


def test_package_and_module_boundaries(tmp_path: Path) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "go.mod": MOD,
                "lib/lib.go": "package lib\n\nfunc Exported() {}\nfunc hidden() {}\n",
                "lib/lib_internal_test.go": "package lib\n\nfunc TestOnly() {}\n",
                "lib/gen.go": "//go:build ignore\n\npackage main\n\nfunc Exported() {}\n",
                "two/a.go": "package two\n\nfunc Two() {}\n",
                "two/b.go": "//go:build ignore\n\npackage other\n\nfunc Two() {}\n",
                "lib/use_test.go": "package lib\n\nfunc TestUse() { TestOnly(); hidden() }\n",
                "only/only_test.go": "package only\n\nfunc Only() {}\n",
                "x/x_test.go": "package x_test\n\nfunc X() {}\n",
                "testdata/t/t.go": "package t\n\nfunc T() {}\n",
                "_skip/s.go": "package s\n\nfunc S() {}\n",
                "sub/go.mod": "module other.org/sub\n",
                "sub/s.go": "package sub\n\nfunc S() {}\n",
                "dot/dot.go": "package dot\n\nfunc Dotted() {}\n",
                "use/use.go": "package use\n\nimport (\n"
                '\t"example.com/m/lib"\n\t"example.com/m/only"\n\t"example.com/m/x"\n'
                '\ttwo "example.com/m/two"\n'
                '\t"example.com/m/testdata/t"\n\t"example.com/m/_skip"\n'
                '\t"example.com/m/sub"\n\tsub2 "other.org/sub"\n\t. "example.com/m/dot"\n)\n\n'
                "func Use() { lib.Exported(); lib.hidden(); lib.TestOnly(); only.Only(); "
                "t.T(); s.S(); sub2.S(); Dotted(); two.Two() }\n",
            },
        )
    )
    imports = {
        e.name or e.targets[0]: e.targets
        for e in found.edges
        if e.kind == "imports" and e.source == "file:use/use.go"
    }
    # Build constraints are not evaluated, but main is never importable. Two other
    # package clauses in one directory are ambiguous. The go tool's ignored
    # directories, test-only and external test packages, and a path owned by a
    # nested module are not importable.
    assert imports["module:go:lib/lib"] == ("module:go:lib/lib",)
    assert imports["example.com/m/two"] == ("module:go:two/other", "module:go:two/two")
    for path in ("only", "x", "testdata/t", "_skip", "sub"):
        assert imports[f"example.com/m/{path}"] == ()
    assert imports["module:go:sub/sub"] == ("module:go:sub/sub",)
    calls = resolved(found, "calls", "symbol:use/use.go#Use")
    assert calls == {"symbol:lib/lib.go#Exported", "symbol:sub/s.go#S", "symbol:dot/dot.go#Dotted"}
    two = {e.targets for e in found.edges if e.name == "two.Two"}
    assert two == {("symbol:two/a.go#Two", "symbol:two/b.go#Two")}  # an alias binds both
    assert resolved(found, "calls", "symbol:lib/use_test.go#TestUse") == {
        "symbol:lib/lib_internal_test.go#TestOnly",  # test files see each other
        "symbol:lib/lib.go#hidden",
    }


def test_without_go_mod_only_packages_resolve(tmp_path: Path) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "a/a.go": "package a\n\nfunc A() {}\n",
                "a/b.go": "package a\n\nfunc B() { A() }\n",
                "c/c.go": 'package c\n\nimport "a"\n\nfunc C() { a.A() }\n',
            },
        )
    )
    assert resolved(found, "calls", "symbol:a/b.go#B") == {"symbol:a/a.go#A"}
    assert not resolved(found, "calls", "symbol:c/c.go#C")
    assert {e.name for e in found.entities if e.category == "module"} == {"a", "c"}


def test_identity_is_local_and_deterministic(tmp_path: Path) -> None:
    files = {"go.mod": MOD, "a.go": "package a\n\nfunc F() {}\nfunc (T) F() {}\ntype T int\n"}
    first = derived(write(tmp_path / "one", files))
    second = derived(write(tmp_path / "two", {**files, "b.go": "package a\n\nfunc F() {}\n"}))
    keys = {e.key for e in first.entities}
    assert {"symbol:a.go#F", "symbol:a.go#T.F", "symbol:a.go#T"} <= keys
    assert keys <= {e.key for e in second.entities}
    assert derived(tmp_path / "one") == first
    assert dialect("dir/x_test.go") == "go"


def test_coverage_cache_validation_and_limits(tmp_path: Path) -> None:
    root = write(
        tmp_path,
        {
            "ok.go": "package p\n\nfunc Ok() {}\n",
            "broken.go": "package p\n\nfunc Broken( {\n\treturn\n}\n\nfunc After() {}\n",
            "latin.go": b"package p\n// \xff\n",
        },
    )
    full = derived(root)
    files = {f.path: f for f in full.files}
    assert files["broken.go"].coverage == "partial" and files["broken.go"].diagnostics
    assert files["latin.go"].coverage == "unsupported"
    assert "symbol:broken.go#After" in {e.key for e in full.entities}
    assert set(full.facts) == {"go"}
    for f in capture(root).files:
        parsed = go.parse(f.data)
        if not isinstance(parsed, str):
            assert go.decode(json.loads(json.dumps(go.encode(parsed))), len(f.data)) == parsed
    assert derive(capture(root), "test-repo", full.facts).reused == 2
    cached = copy.deepcopy(full.facts)
    for key in cached["go"]:
        cached["go"][key] = ["damaged"]
    assert derive(capture(root), "test-repo", cached) == full
    deep = ("package p\nvar x = " + "(" * 70000 + "1" + ")" * 70000 + "\n").encode()
    assert go.parse(deep) == python.TOO_DEEP


def test_unsound_cached_facts_are_parsed_never_trusted_or_fatal(tmp_path: Path) -> None:
    source = (
        'package p\n\nimport f "fmt"\n\ntype I interface { M() }\ntype S struct{ I }\n'
        "func (s *S) N(x int) { s.N(x); f.Println(S{}) }\nvar V, W = 1, 2\n"
    )
    snapshot = capture(write(tmp_path / "repo", {"p/a.go": source}))
    full = derive(snapshot, "r")
    ((key, good),) = full.facts["go"].items()
    size = len(snapshot.files[0].data)
    rejected = 0
    for at in _paths(good):
        for bad in BAD:
            entry = _replace(good, at, bad)
            try:
                go.decode(entry, size)
            except ValueError:
                rejected += 1
                result = derive(snapshot, "r", {"go": {key: entry}})
                assert result == full and result.reused == 0, (at, bad)
                continue
            # Well-formed facts parse could have produced: they may differ, never fail.
            validate(derive(snapshot, "r", {"go": {key: entry}}))
    assert rejected > len(_paths(good)) * len(BAD) // 2


def test_persisted_incremental_equals_full_rebuild(tmp_path: Path) -> None:
    root = write(
        tmp_path / "repo",
        {
            "go.mod": MOD,
            "a/a.go": "package a\n\nfunc A() {}\n",
            "b/b.go": 'package b\n\nimport "example.com/m/a"\n\nfunc B() { a.A() }\n',
        },
    )
    steps: list[dict[str, str | bytes | None]] = [
        {"a/more.go": "package a\n\nfunc More() { A() }\n"},
        {"go.mod": "module example.com/renamed\n"},  # metadata only: resolution changes
        {"go.mod": MOD, "a/a.go": "package a\n\nfunc A() {}\nfunc (T) A() {}\ntype T int\n"},
        {"a/more.go": None, "b/b_test.go": "package b\n\nfunc TestB() { B() }\n"},
        {"a/a.go": "package renamed\n\nfunc A() {}\n"},
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
