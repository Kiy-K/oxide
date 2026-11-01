"""C++ slice conformance (docs/cpp.md): identity, kinds, provenance, C++ name lookup and
its conservative limits, C interop, macro recovery, the ``.h`` test, cached facts and
persisted incremental/full-rebuild equality."""

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
from oxide.core.indexing import derive, dialect, source_dialect, validate
from oxide.core.languages import cpp, cpp_facts, cpp_macros, python

FIXTURE = REPO / "fixtures/cpp_repo"
HEADER = "symbol:include/shop/store.hpp#shop"
SOURCE = "symbol:src/store.cpp#shop"


def test_fixture_entities_kinds_and_provenance() -> None:
    found = derived(FIXTURE)
    files = {f.path: f for f in found.files}
    assert {p: f.language for p, f in files.items() if f.language != "unknown"} == {
        "include/shop/limits.h": "c",  # a C header stays C
        "include/shop/store.hpp": "cpp",
        "src/cart.h": "cpp",  # a ``.h`` with C++ syntax
        "src/limits.c": "c",
        "src/main.cpp": "cpp",
        "src/store.cpp": "cpp",
        "tests/test_store.cpp": "cpp",
    }
    assert all(f.coverage == "complete" for f in found.files if f.language != "unknown")
    entities = {e.key: e for e in found.entities}
    kinds = {k: e.kind for k, e in entities.items() if e.category == "symbol"}
    assert kinds[HEADER] == "namespace"
    assert kinds[f"{HEADER}.Key"] == "type"
    assert kinds[f"{HEADER}.Mode"] == "enum"
    assert kinds[f"{HEADER}.Store"] == "class"
    assert kinds["symbol:src/cart.h#shop.Cart"] == "struct"
    # In-class declarations and out-of-line definitions are methods; overloads take ~n.
    for key in ("Store.Store", "Store.get", "Store.get~1", "Store.open", "Store.operator=="):
        assert kinds[f"{HEADER}.{key}"] == kinds[f"{SOURCE}.{key}"] == "method"
    assert kinds[f"{HEADER}.Store._Store"] == "method"  # ``~Store``; ``~`` is the ordinal mark
    assert entities[f"{HEADER}.Store._Store"].name == "~Store"
    assert kinds[f"{HEADER}.store_limit"] == kinds[f"{SOURCE}.store_limit"] == "function"
    assert kinds[f"{SOURCE}.hash_key"] == "function"  # in an anonymous namespace
    assert not any(e.test for e in found.entities)
    assert not any(e.category == "module" for e in found.entities)
    data = {f.path: f.data for f in capture(FIXTURE).files}
    for key, text in [
        (f"{HEADER}.Key", "using Key = std::string;"),
        (f"{HEADER}.Store.open", "static Store open(Mode mode);"),
        (f"{SOURCE}.Store.open", "Store Store::open(Mode mode) { return Store(mode); }"),
    ]:
        source = entities[key].source
        assert source is not None and source.digest == files[source.path].digest
        assert data[source.path][source.start : source.end].decode() == text
    assert entities[f"{HEADER}.Store"].signature == "class Store : public Backend"
    assert entities[f"{SOURCE}.Store.Store"].signature == "Store::Store(Mode mode)"
    assert entities["symbol:src/cart.h#shop.Cart"].signature == "template <typename T> struct Cart"


def test_fixture_relations() -> None:
    found = derived(FIXTURE)
    assert resolved(found, "imports", "file:include/shop/store.hpp") == {
        "file:include/shop/limits.h"
    }
    assert unresolved(found, "imports", "file:src/main.cpp") == {"cstdio"}
    # A prototype references its definitions, an overload by its parameter count.
    for member in ("Store.get", "Store.get~1", "Backend.capacity", "Store._Store"):
        assert f"{SOURCE}.{member}" in resolved(found, "references", f"{HEADER}.{member}")
    # Members, overloads by arity, ``this->``, inherited members, constructors and C.
    calls = {
        f"{SOURCE}.Store.get": {f"{SOURCE}.Store.get~1"},
        f"{SOURCE}.Store.must_get": {f"{SOURCE}.Store.get"},
        f"{SOURCE}.Store.get~1": {f"{SOURCE}.hash_key"},
        f"{SOURCE}.Store.Store": {f"{SOURCE}.Backend.capacity", "symbol:src/limits.c#clamp_size"},
        f"{SOURCE}.Store.open": {f"{SOURCE}.Store.Store"},
        # ``using namespace shop``, qualified or not; the C library stays unresolved.
        "symbol:src/main.cpp#main": {f"{SOURCE}.Store.open", f"{SOURCE}.store_limit"},
    }
    for source, targets in calls.items():
        assert resolved(found, "calls", source) == targets, source
    assert unresolved(found, "calls", "symbol:src/main.cpp#main") == {"std::printf"}
    assert unresolved(found, "calls", f"{SOURCE}.Store.must_get") == {"std::out_of_range"}
    # Types: the class itself inside its members, aliases, bases, through includes.
    assert f"{HEADER}.Store" in resolved(found, "references", f"{SOURCE}.Store.open")
    assert resolved(found, "references", f"{HEADER}.Store") == {
        f"{HEADER}.Backend",
        f"{HEADER}.Mode",
    }
    assert resolved(found, "references", "symbol:tests/test_store.cpp#test_must_get") == {
        f"{HEADER}.Store"
    }


def test_lookup_is_conservative(tmp_path: Path) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "a.hpp": "#define TWICE(x) ((x) * 2)\nint f(int a);\nint g(int a, int b = 0);\n"
                "struct Base { int inherited(); };\nnamespace n { int f(int a); struct T {}; }\n"
                "int shared();\n",
                "a.cpp": '#include "a.hpp"\n#include <vector>\n'
                "int f(int a) { return a; }\nint g(int a, int b) { return a + b; }\n"
                "int n::f(int a) { return TWICE(a); }\n"
                "struct Local : Base {\n  int cb;\n"
                "  int m(int f) { return f(1) + cb() + inherited(); }\n};\n"
                "struct Far : std::vector<int> { int k() { return f(1) + size(); } };\n"
                "int h() { return g(1) + g(1, 2) + f(1, 2) + n::f(2) + ::f(3); }\n"
                "template <typename f> int t() { return f(1); }\n"
                "namespace n { int u() { return f(4); } }\n"
                "int w() { using n::f; return f(5); }\n",
                "b.cpp": "int unseen() { return shared(); }\nint shared() { return 1; }\n"
                "int c() { return f(1); }\n",
            },
        )
    )
    a = "symbol:a.cpp#"
    # A parameter, a field, a template parameter or ``using`` hides a function.
    assert resolved(found, "calls", f"{a}Local.m") == {"symbol:a.hpp#Base.inherited"}
    assert not resolved(found, "calls", f"{a}t")
    assert not resolved(found, "calls", f"{a}w")
    # A base outside the capture may declare the name, so nothing is guessed.
    assert not resolved(found, "calls", f"{a}Far.k")
    # Default arguments and arity; qualified and global names; the innermost scope.
    assert resolved(found, "calls", f"{a}h") == {f"{a}g", f"{a}n.f", f"{a}f"}
    assert unresolved(found, "calls", f"{a}h") == {"f"}  # two arguments: no ``f`` takes them
    assert resolved(found, "calls", f"{a}n.f") == {"symbol:a.hpp#TWICE"}
    assert resolved(found, "calls", f"{a}n.u") == {f"{a}n.f"}
    # C++ needs a declaration: without an include, nothing is linked (C would be).
    assert not resolved(found, "calls", "symbol:b.cpp#c")
    assert resolved(found, "calls", "symbol:b.cpp#unseen") == {"symbol:b.cpp#shared"}


def test_c_headers_are_visible_from_cpp(tmp_path: Path) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "lib.h": "typedef struct S { int a; } S;\nint lib_get(S *s);\n#define LIB_MAX 4\n",
                "lib.c": '#include "lib.h"\nint lib_get(S *s) { return s->a; }\n',
                "use.cc": '#include "lib.h"\nextern "C" int extra(void);\n'
                "int use(S *s) { return lib_get(s) + LIB_MAX + extra(); }\n",
            },
        )
    )
    assert dialect("lib.h") == "c" and dialect("use.cc") == "cpp"
    assert resolved(found, "calls", "symbol:use.cc#use") == {
        "symbol:lib.c#lib_get",
        "symbol:use.cc#extra",  # a prototype: no captured definition
    }
    assert resolved(found, "references", "symbol:use.cc#use") == {"symbol:lib.h#S~1"}


def test_cplusplus_headers_are_detected_by_syntax() -> None:
    assert cpp_macros.is_cplusplus(b"namespace a {\nint f();\n}\n")
    assert cpp_macros.is_cplusplus(b"class A : public B {};\n")
    assert cpp_macros.is_cplusplus(b"template <typename T> T id(T);\n")
    assert cpp_macros.is_cplusplus(b"int x = std::max(1, 2);\n")
    assert not cpp_macros.is_cplusplus(b"// namespace a { template <class T> std::x\n")
    assert not cpp_macros.is_cplusplus(b'#ifdef __cplusplus\nextern "C" {\n#endif\nint f(void);\n')
    assert not cpp_macros.is_cplusplus(b'const char *s = "a::b";\nstruct class_ { int x; };\n')
    assert source_dialect("a.h", b"namespace a {}\n") == "cpp"
    assert source_dialect("a.c", b"namespace a {}\n") == "c"
    assert source_dialect("a.hpp", b"int f(void);\n") == "cpp"


def test_unexpanded_macro_idioms_parse() -> None:
    source = (
        "#if __has_include(<version>) && FOO_HAS(<a/b.h>)\n#endif\n"
        "#define NS_BEGIN namespace lib {\n#define NS_END }\n"
        "NS_BEGIN\n"
        "class LIB_API Widget : public Base {\n public:\n"
        "  LIB_CONSTEXPR Widget() {}\n  LIB_CONSTEXPR ~Widget() {}\n"
        "  LIB_INLINE int size() const LOCKS_EXCLUDED(mu_) { return 0; }\n"
        "  LIB_NO_UNIQUE_ADDRESS Alloc alloc_;\n  Q_OBJECT\n};\n"
        "template <typename T, LIB_ENABLE_IF(sizeof(T) > 1)>\nLIB_API auto conv(T v) -> int;\n"
        "Widget::~Widget() LIB_NOEXCEPT = default;\n"
        "NS_END\n"
    )
    data = source.encode()
    assert len(cpp_macros.masked(data)) == len(data)
    parsed = cpp.parse(data)
    assert not isinstance(parsed, str) and not parsed.diagnostics
    decls = [(s.decl.kind, s.decl.qualifier, s.decl.name) for s in parsed.scopes[1:] if s.decl]
    assert [d for d in decls if d[0] != "macro"] == [
        ("class", "", "Widget"),
        ("function", "", "Widget"),
        ("function", "", "~Widget"),
        ("function", "", "size"),
        ("function", "", "conv"),
        ("function", "Widget", "~Widget"),
    ]
    widget = next(s for s in parsed.scopes if s.decl and s.decl.name == "Widget")
    assert widget.bases == ["Base"] and widget.bound == {"alloc_"}
    assert widget.decl is not None and widget.decl.signature == "class LIB_API Widget : public Base"


def test_identity_is_local_and_deterministic(tmp_path: Path) -> None:
    files = {
        "a.cc": "namespace n { int f(); int f() { return 0; } }\nnamespace n { int f(int); }\n"
        "struct S { void m(); void m(int); };\nvoid S::m() {}\n"
    }
    first = derived(write(tmp_path / "one", files))
    second = derived(write(tmp_path / "two", {**files, "b.cc": "int f() { return 1; }\n"}))
    keys = {e.key for e in first.entities}
    assert {"symbol:a.cc#n.f", "symbol:a.cc#n.f~1", "symbol:a.cc#n~1.f"} <= keys
    assert {"symbol:a.cc#S.m", "symbol:a.cc#S.m~1", "symbol:a.cc#S.m~2"} <= keys
    assert keys <= {e.key for e in second.entities}
    assert derived(tmp_path / "one") == first


def test_coverage_cache_validation_and_limits(tmp_path: Path) -> None:
    root = write(
        tmp_path,
        {
            "ok.cpp": "int ok() { return 0; }\n",
            "broken.cpp": "class { int broken(;\n};\nint after() { return 1; }\n",
            "latin.hpp": b"// \xff\n",
        },
    )
    full = derived(root)
    files = {f.path: f for f in full.files}
    assert files["broken.cpp"].coverage == "partial" and files["broken.cpp"].diagnostics
    assert files["latin.hpp"].coverage == "unsupported"
    assert "symbol:broken.cpp#after" in {e.key for e in full.entities}
    assert set(full.facts) == {"cpp"}
    for f in capture(root).files:
        parsed = cpp.parse(f.data)
        if not isinstance(parsed, str):
            encoded = json.loads(json.dumps(cpp_facts.encode(parsed)))
            assert cpp_facts.decode(encoded, len(f.data)) == parsed
    assert derive(capture(root), "test-repo", full.facts).reused == 2
    cached = copy.deepcopy(full.facts)
    for key in cached["cpp"]:
        cached["cpp"][key] = ["damaged"]
    assert derive(capture(root), "test-repo", cached) == full
    deep = ("int x = " + "(" * 70000 + "1" + ")" * 70000 + ";\n").encode()
    assert cpp.parse(deep) == python.TOO_DEEP


def test_unsound_cached_facts_are_parsed_never_trusted_or_fatal(tmp_path: Path) -> None:
    source = (
        '#include "a.h"\n#define M(x) (x)\nusing namespace std;\nnamespace n {\n'
        "template <typename T> struct S : B { int a; int f(T t) const; };\n"
        "int S<int>::f(int t) const { return M(this->g(t, ::h())); }\n}\n"
    )
    snapshot = capture(write(tmp_path / "repo", {"p/a.cpp": source}))
    full = derive(snapshot, "r")
    ((key, good),) = full.facts["cpp"].items()
    size = len(snapshot.files[0].data)
    rejected = 0
    for at in _paths(good):
        for bad in BAD:
            entry = _replace(good, at, bad)
            try:
                cpp_facts.decode(entry, size)
            except ValueError:
                rejected += 1
                result = derive(snapshot, "r", {"cpp": {key: entry}})
                assert result == full and result.reused == 0, (at, bad)
                continue
            # Well-formed facts parse could have produced: they may differ, never fail.
            validate(derive(snapshot, "r", {"cpp": {key: entry}}))
    assert rejected > len(_paths(good)) * len(BAD) // 2


def test_search_and_context(tmp_path: Path, fts: Path) -> None:
    with oxide.open_repository(FIXTURE, data_dir=tmp_path / "data") as repo:
        repo.index()
        result = repo.search("must get from the store", symbols=["must_get"])
        assert result.entries[0].entity.key in {
            f"{SOURCE}.Store.must_get",
            f"{HEADER}.Store.must_get",
        }
        bundle = repo.build_context("open the store", symbols=["Store.open"], budget=1024)
        assert 0 < bundle.used <= 1024
        assert "Store Store::open(Mode mode)" in bundle.payload


def test_persisted_incremental_equals_full_rebuild(tmp_path: Path) -> None:
    root = write(
        tmp_path / "repo",
        {
            "inc/a.h": "int a(void);\n",
            "a.c": '#include "inc/a.h"\nint a(void) { return 0; }\n',
            "b.cpp": '#include "a.h"\nint b() { return a(); }\n',
        },
    )
    steps: list[dict[str, str | bytes | None]] = [
        {"more.cpp": '#include "inc/a.h"\nint more() { return a(); }\n'},
        {"inc/a.h": "namespace n { int a(); }\nusing namespace n;\n"},  # the header is C++ now
        {"a.c": None, "a.cc": '#include "inc/a.h"\nint n::a() { return 1; }\n'},
        {"inc/a.h": "int a(void);\n", "more.cpp": None},
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


def test_cyclic_bases_terminate_and_defaults_stay_on_prototypes(tmp_path: Path) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "a.cc": "int g();\nstruct A : B { int f() { return g(); } };\n"
                "struct B : A { int h() { return g(); } };\n"
                "struct P { int k(int a = 0); };\nint P::k(int a) { return a + k(); }\n"
            },
        )
    )
    assert resolved(found, "calls", "symbol:a.cc#A.f") == {"symbol:a.cc#g"}
    assert resolved(found, "references", "symbol:a.cc#P.k") == {"symbol:a.cc#P.k~1"}
    assert resolved(found, "calls", "symbol:a.cc#P.k~1") == {"symbol:a.cc#P.k~1"}
