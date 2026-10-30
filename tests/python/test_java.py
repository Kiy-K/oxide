"""Java slice conformance (docs/java.md): identity, kinds, provenance, package, type and
member resolution, tests, cached facts and persisted incremental/full-rebuild equality."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from conftest import REPO, write
from test_go import unresolved
from test_incremental import BAD, _paths, _replace, apply, dump
from test_typescript import derived, resolved

import oxide
from oxide.capture import capture
from oxide.core.indexing import derive, dialect, validate
from oxide.core.languages import java, java_facts, python

FIXTURE = REPO / "fixtures/java_repo"
MAIN = "src/main/java/com/example/shop"
STORE = f"symbol:{MAIN}/store/Store.java#Store"
CART = f"symbol:{MAIN}/cart/Cart.java#Cart"
TEST = "symbol:src/test/java/com/example/shop/store/StoreTest.java#StoreTest"


def test_fixture_entities_kinds_and_provenance() -> None:
    found = derived(FIXTURE)
    files = {f.path: f for f in found.files}
    assert all(
        f.language == "java" and f.coverage == "complete"
        for f in found.files
        if f.path.endswith(".java")
    )
    assert files["pom.xml"].coverage == "unsupported"
    entities = {e.key: e for e in found.entities}
    kinds = {k: e.kind for k, e in entities.items() if e.category == "symbol"}
    store = f"symbol:{MAIN}/store"
    assert kinds[STORE] == "class"
    assert kinds[f"{store}/Backend.java#Backend"] == "interface"
    assert kinds[f"{store}/Backend.java#Backend.getOrDefault"] == "method"
    assert kinds[f"{store}/Key.java#Key"] == "record"
    assert kinds[f"{store}/Key.java#Key.Key"] == "constructor"  # compact
    assert kinds[f"{store}/Mode.java#Mode"] == "enum"
    assert kinds[f"{store}/Cached.java#Cached"] == "annotation"
    assert kinds[f"{store}/Cached.java#Cached.seconds"] == "method"
    assert kinds[f"{STORE}.Entry.value"] == "method"  # nested under its class
    # Overloads take source-order ordinals; constructors are named by their class.
    assert kinds[f"{STORE}.put~1"] == kinds[f"{STORE}.put"] == "method"
    assert kinds[f"{STORE}.Store~1"] == "constructor"
    assert entities[f"{STORE}.put~1"].name == "put"
    # Members of anonymous classes and enum constant bodies are not symbols.
    assert f"{store}/Mode.java#Mode.label~1" not in kinds
    assert not any(k.endswith(".run") for k in kinds)
    assert entities[f"{TEST}.getsWhatWasPut"].test
    assert entities[f"{TEST}.labelsWrites"].test  # a qualified @Test
    assert not entities[f"{TEST}.fixture"].test
    modules = {e.key: e.name for e in found.entities if e.category == "module"}
    assert modules == {
        f"module:java:com.example.shop.{p}": f"com.example.shop.{p}"
        for p in ("cart", "store", "util")
    }  # Main.java is in the default package: no module
    data = {f.path: f.data for f in capture(FIXTURE).files}
    for key, start, end in [
        (STORE, "@Cached\npublic class Store", "\n}"),
        (f"{STORE}.get", "@Override\n    public String get", "entry.value();\n    }"),
        (f"{store}/Backend.java#Backend.get", "String get(Key key);", "String get(Key key);"),
    ]:
        source = entities[key].source
        assert source is not None and source.digest == files[source.path].digest
        text = data[source.path][source.start : source.end].decode()
        assert text.startswith(start) and text.endswith(end), key  # annotations, no Javadoc
    signature = entities[STORE].signature
    assert signature == "public class Store extends AbstractStore implements Backend"
    assert entities[f"{STORE}.get"].signature == "public String get(Key key)"


def test_fixture_relations() -> None:
    found = derived(FIXTURE)
    store, util = f"symbol:{MAIN}/store", f"symbol:{MAIN}/util/Strings.java#Strings"
    assert resolved(found, "imports", f"file:{MAIN}/cart/Cart.java") == {
        "module:java:com.example.shop.store",  # on demand
        util,
    }
    assert unresolved(found, "imports", f"file:{MAIN}/cart/Cart.java") == {
        "java.util.ArrayList",
        "java.util.List",
        "javax.swing.JPanel",
    }
    assert resolved(found, "calls", f"{STORE}.get") == {
        f"{store}/AbstractStore.java#AbstractStore.normalize",  # inherited
        f"{store}/Backend.java#Backend.get",  # a field's declared type
        f"{STORE}.Entry.value",  # a local's declared type, a member type
    }
    assert resolved(found, "calls", f"{STORE}.mustGet") == {f"{STORE}.get"}  # this.get
    assert resolved(found, "calls", f"{STORE}.put~1") == {f"{STORE}.put"}  # by arity
    assert resolved(found, "calls", f"{STORE}.open") == {
        STORE,  # new Store()
        f"{store}/Key.java#Key",
        f"{STORE}.put",
        f"{store}/Mode.java#Mode.label",
    }
    assert resolved(found, "calls", f"{CART}.add") == {
        f"{store}/Key.java#Key.of",  # a static method through an on-demand import
        f"{STORE}.put",  # a field of a type from that import
    }
    assert resolved(found, "calls", f"{CART}.describe") == {f"{util}.join", f"{CART}.names"}
    assert resolved(found, "calls", f"{CART}.Summary.text") == {f"{CART}.describe"}  # outer
    # Anonymous classes, external supertypes and implicit members could declare the
    # name, so it stays unresolved; so do calls on external types.
    assert unresolved(found, "calls", f"{CART}.listen") == {"new Runnable", "later.run", "describe"}
    assert unresolved(found, "calls", f"{CART}.View.refresh") == {"describe", "repaint"}
    assert unresolved(found, "calls", f"{store}/Mode.java#Mode") == {"name"}
    assert "key.value" in unresolved(found, "calls", f"{STORE}.get")  # a record accessor
    # An enum constant is a static field of its enum's type.
    assert resolved(found, "calls", f"{TEST}.labelsWrites") == {f"{store}/Mode.java#Mode.label"}
    ambiguous = [(e.source, e.targets) for e in found.edges if e.resolution == "ambiguous"]
    assert ambiguous == [
        # a static import of an overloaded method names every overload
        (f"file:{MAIN}/cart/Cart.java", (f"{util}.join", f"{util}.join~1")),
        (f"{util}.join~1", (f"{util}.join", f"{util}.join~1")),  # both accept one argument
    ]
    assert resolved(found, "references", STORE) == {
        f"{store}/Cached.java#Cached",  # an annotation
        f"{store}/AbstractStore.java#AbstractStore",
        f"{store}/Backend.java#Backend",
        f"{STORE}.Entry",  # a type argument
    }
    assert resolved(found, "tested_by", f"{STORE}.mustGet") == {f"{TEST}.getsWhatWasPut"}
    assert resolved(found, "tested_by", f"{TEST}.fixture") == {f"{TEST}.getsWhatWasPut"}
    defines = resolved(found, "defines", "module:java:com.example.shop.store")
    assert STORE in defines and TEST in defines  # test sources share the package
    assert f"{STORE}.get" not in defines  # inside its class
    members = ("Store", "Store~1", "get", "mustGet", "put", "put~1", "open", "Entry")
    assert resolved(found, "contains", STORE) == {f"{STORE}.{m}" for m in members}
    assert resolved(found, "calls", "symbol:Main.java#Main.main") == {
        CART,
        STORE,
        f"{CART}.add",
        f"{CART}.describe",
    }


def test_search_and_context(tmp_path: Path, fts: Path) -> None:
    with oxide.open_repository(FIXTURE, data_dir=tmp_path / "data") as repo:
        repo.index()
        result = repo.search("mustGet store key", symbols=["mustGet"])
        assert result.entries[0].entity.key == f"{STORE}.mustGet"
        bundle = repo.build_context("open the store", symbols=["open"], budget=1024)
        assert 0 < bundle.used <= 1024
        assert "static Store open(Mode mode)" in bundle.payload


def test_scoping_is_conservative(tmp_path: Path) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "p/T.java": "package p;\nclass T {\n"
                "  static void f() {}\n  void g() {}\n  void g(int a, int b) {}\n"
                "  void shadow(Object T) { T.f(); }\n"  # a variable obscures the type
                "  <U extends T> void generic(U x) { x.g(); }\n"  # a type parameter
                "  void lambda() { java.util.function.Consumer<T> c = t -> t.g(); g(); }\n"
                "  void local() { class L { void q() { g(); } } new L(); }\n"
                "  void arity() { g(1); }\n"  # no g(int): unknown, not g()
                "  void loose() { var t = new T(); t.g(); T[] ts = {}; ts[0].g(); }\n"
                "  void hash() { hashCode(); toString(); }\n"
                "  class In { void g(String s) {} void h() { g(); } }\n"
                "  static class Up extends T { void h() { g(); super.g(); f(); } }\n"
                "  void anon(T o) { new Base() { void r() { o.g(); T.f(); } };\n"
                "    new Runnable() { public void run() { o.g(); } }; }\n"
                "}\n",
                "p/Base.java": "package p;\nclass Base {}\n",
            },
        )
    )
    t = "symbol:p/T.java#T"
    assert not resolved(found, "calls", f"{t}.shadow")
    assert not resolved(found, "calls", f"{t}.generic")
    assert resolved(found, "calls", f"{t}.lambda") == {f"{t}.g"}  # t is untyped: t.g opaque
    assert unresolved(found, "calls", f"{t}.local") == {"new L", "g"}  # a local class
    assert not resolved(found, "calls", f"{t}.arity")
    assert resolved(found, "calls", f"{t}.loose") == {t}  # var and arrays carry no type
    assert unresolved(found, "calls", f"{t}.hash") == {"hashCode", "toString"}
    # The innermost class declaring the name decides, even when no overload fits.
    assert not resolved(found, "calls", f"{t}.In.h")
    assert resolved(found, "calls", f"{t}.Up.h") == {f"{t}.g", f"{t}.f"}  # inherited
    # Inside an anonymous class, a captured base declares no field o; an external one may.
    assert resolved(found, "calls", f"{t}.anon") == {"symbol:p/Base.java#Base", f"{t}.g", f"{t}.f"}
    assert unresolved(found, "calls", f"{t}.anon") == {"new Runnable", "o.g"}


def test_type_lookup_precedence(tmp_path: Path) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "a/Box.java": "package a;\npublic class Box { public static class In {} }\n",
                "b/Box.java": "package b;\npublic class Box {}\n",
                "c/Box.java": "package c;\npublic class Box {}\n",
                "c/Use.java": "package c;\nimport a.Box;\nimport b.*;\n"
                "class Use { Box single; a.Box.In nested; }\n",
                "c/Demand.java": "package c;\nimport a.*;\nimport b.*;\n"
                "class Demand { Box pkg; }\n",
                "d/Demand.java": "package d;\nimport a.*;\nimport b.*;\n"
                "class Demand { Box two; }\n",
                "d/Ext.java": "package d;\nimport x.Box;\nclass Ext { Box external; }\n",
                "d/Sub.java": "package d;\nclass Sub extends a.Box { In inherited; }\n",
                "a/Tag.java": "package a;\npublic @interface Tag {}\n",
                "e/A.java": "package e;\nimport a.Tag;\n"
                "@Tag class A {\n  @Tag void m() {}\n  @interface Tag {}\n}\n",
                "e/B.java": "package e;\nclass B<T> {}\n",
                "e/C.java": "package e;\n"
                "class C<T> extends B<T> { static class B<U> {} B<T> f; }\n",
                "e/D.java": "package e;\n"
                "sealed interface D<T extends B<T>> permits L { class B<U> {} class L {} }\n",
                "e/L.java": "package e;\nfinal class L implements D<B<?>> {}\n",
                "e/R.java": "package e;\nrecord R(B<?> b) { record B<V>() {} }\n",
            },
        )
    )
    refs = {
        e.evidence.path: e.targets
        for e in found.edges
        if e.kind == "references" and e.evidence is not None and e.name == "Box"
    }
    assert resolved(found, "references", "symbol:c/Use.java#Use") == {
        "symbol:a/Box.java#Box",  # a single-type import shadows the package
        "symbol:a/Box.java#Box.In",  # a qualified name
    }
    assert resolved(found, "references", "symbol:c/Demand.java#Demand") == {
        "symbol:c/Box.java#Box"  # the package beats on-demand imports
    }
    assert refs["d/Demand.java"] == ("symbol:a/Box.java#Box", "symbol:b/Box.java#Box")
    assert refs["d/Ext.java"] == ()  # an external single-type import shadows too
    assert resolved(found, "references", "symbol:d/Sub.java#Sub") == {
        "symbol:a/Box.java#Box",
        "symbol:a/Box.java#Box.In",  # an inherited member type
    }
    # A declaration's annotations resolve in the enclosing scope, but stay its own references.
    assert resolved(found, "references", "symbol:e/A.java#A") == {"symbol:a/Tag.java#Tag"}
    assert resolved(found, "references", "symbol:e/A.java#A.m") == {"symbol:e/A.java#A.Tag"}
    # So do its supertypes; its own type parameters stay opaque there.
    assert resolved(found, "references", "symbol:e/C.java#C") == {
        "symbol:e/B.java#B",
        "symbol:e/C.java#C.B",  # the field, in the body
    }
    assert unresolved(found, "references", "symbol:e/C.java#C") == {"T"}
    # So do type-parameter bounds and permits (javac 25); a record's components see its members.
    assert resolved(found, "references", "symbol:e/D.java#D") == {
        "symbol:e/B.java#B",
        "symbol:e/L.java#L",
    }
    assert unresolved(found, "references", "symbol:e/D.java#D") == {"T"}
    assert resolved(found, "references", "symbol:e/R.java#R") == {"symbol:e/R.java#R.B"}


def test_identity_is_local_and_deterministic(tmp_path: Path) -> None:
    files = {"A.java": "class A { void f() {} void f(int x) {} class B { void f() {} } }\n"}
    first = derived(write(tmp_path / "one", files))
    second = derived(write(tmp_path / "two", {**files, "C.java": "class C { void f() {} }\n"}))
    keys = {e.key for e in first.entities}
    assert {"symbol:A.java#A.f", "symbol:A.java#A.f~1", "symbol:A.java#A.B.f"} <= keys
    assert keys <= {e.key for e in second.entities}
    assert derived(tmp_path / "one") == first
    assert dialect("src/X.java") == "java" and dialect("X.jav") is None


def test_coverage_cache_validation_and_limits(tmp_path: Path) -> None:
    root = write(
        tmp_path,
        {
            "Ok.java": "class Ok { void ok() {} }\n",
            "Broken.java": "class Broken { void broken( { return; }\n void after() {} }\n",
            "Latin.java": b"class Latin { } // \xff\n",
        },
    )
    full = derived(root)
    files = {f.path: f for f in full.files}
    assert files["Broken.java"].coverage == "partial" and files["Broken.java"].diagnostics
    assert files["Latin.java"].coverage == "unsupported"
    assert "symbol:Broken.java#Broken" in {e.key for e in full.entities}
    assert set(full.facts) == {"java"}
    for f in capture(root).files:
        parsed = java.parse(f.data)
        if not isinstance(parsed, str):
            assert (
                java_facts.decode(json.loads(json.dumps(java_facts.encode(parsed))), len(f.data))
                == parsed
            )
    assert derive(capture(root), "test-repo", full.facts).reused == 2
    cached = copy.deepcopy(full.facts)
    for key in cached["java"]:
        cached["java"][key] = ["damaged"]
    assert derive(capture(root), "test-repo", cached) == full
    deep = ("class P { int x = " + "(" * 70000 + "1" + ")" * 70000 + "; }\n").encode()
    assert java.parse(deep) == python.TOO_DEEP


def test_unsound_cached_facts_are_parsed_never_trusted_or_fatal(tmp_path: Path) -> None:
    source = (
        "package p;\nimport static q.R.s;\nimport q.*;\n"
        "class A<T> extends B implements C {\n  int n = 1;\n"
        "  void m(D d, int... xs) { d.go(1); this.m(d); new E() { void r() {} }; }\n"
        "  @Test void t() {}\n  enum F { G { }, H }\n}\n"
    )
    snapshot = capture(write(tmp_path / "repo", {"p/A.java": source}))
    full = derive(snapshot, "r")
    ((key, good),) = full.facts["java"].items()
    size = len(snapshot.files[0].data)
    rejected = 0
    for at in _paths(good):
        for bad in BAD:
            entry = _replace(good, at, bad)
            try:
                java_facts.decode(entry, size)
            except ValueError:
                rejected += 1
                result = derive(snapshot, "r", {"java": {key: entry}})
                assert result == full and result.reused == 0, (at, bad)
                continue
            # Well-formed facts parse could have produced: they may differ, never fail.
            validate(derive(snapshot, "r", {"java": {key: entry}}))
    assert rejected > len(_paths(good)) * len(BAD) // 2


def test_persisted_incremental_equals_full_rebuild(tmp_path: Path) -> None:
    root = write(
        tmp_path / "repo",
        {
            "a/A.java": "package a;\npublic class A { public static void go() {} }\n",
            "b/B.java": "package b;\nimport a.A;\nclass B { void b() { A.go(); } }\n",
        },
    )
    steps: list[dict[str, str | bytes | None]] = [
        {"a/More.java": "package a;\nclass More extends A { void more() { go(); } }\n"},
        {"a/A.java": "package a;\npublic class A { public static void go(int x) {} }\n"},
        {"a/More.java": None, "b/BTest.java": "package b;\nclass BTest { @Test void t() {} }\n"},
        {"a/A.java": "package renamed;\npublic class A { public static void go() {} }\n"},
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
