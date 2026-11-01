"""C slice conformance (docs/c.md): identity, kinds, provenance, include and linkage
resolution, macro recovery, cached facts and persisted incremental/full-rebuild equality."""

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
from oxide.core.languages import c, python

FIXTURE = REPO / "fixtures/c_repo"
HEADER = "symbol:include/shop/store.h"


def test_fixture_entities_kinds_and_provenance() -> None:
    found = derived(FIXTURE)
    files = {f.path: f for f in found.files}
    assert all(
        f.language == "c" and f.coverage == "complete"
        for f in found.files
        if f.path.endswith((".c", ".h"))
    )
    assert files["Makefile"].coverage == "unsupported"
    entities = {e.key: e for e in found.entities}
    kinds = {k: e.kind for k, e in entities.items() if e.category == "symbol"}
    assert kinds[f"{HEADER}#STORE_EMPTY"] == "macro"
    assert kinds[f"{HEADER}#Key"] == "type"
    assert kinds[f"{HEADER}#Entry"] == "struct"
    # A tag and its same-named typedef: the tag, then the declaration holding it.
    assert (kinds[f"{HEADER}#Store"], kinds[f"{HEADER}#Store~1"]) == ("struct", "type")
    assert kinds[f"{HEADER}#Mode"] == "enum"
    assert kinds[f"{HEADER}#store_default_limit"] == "variable"
    assert kinds[f"{HEADER}#store_get"] == "function"  # a prototype
    assert kinds["symbol:src/store.c#store_get"] == "function"  # its definition
    assert kinds["symbol:src/cart.c#hash_key"] == "function"
    assert not any(e.test for e in found.entities)
    assert not any(e.category == "module" for e in found.entities)
    data = {f.path: f.data for f in capture(FIXTURE).files}
    for key, text in [
        (f"{HEADER}#STORE_MAX", "#define STORE_MAX 64\n"),
        (f"{HEADER}#Key", "typedef const char *Key;"),
        (f"{HEADER}#store_get", "const char *store_get(Store *s, Key key);"),
        (f"{HEADER}#Mode", "enum Mode { MODE_READ = 1, MODE_WRITE = 2 }"),
    ]:
        source = entities[key].source
        assert source is not None and source.digest == files[source.path].digest
        assert data[source.path][source.start : source.end].decode() == text
    assert (
        entities["symbol:src/store.c#store_open"].signature == "Store *store_open(enum Mode mode)"
    )
    assert entities[f"{HEADER}#Store~1"].signature == "typedef struct Store Store;"
    assert entities[f"{HEADER}#Store"].signature == "struct Store"


def test_fixture_relations() -> None:
    found = derived(FIXTURE)
    # Beside the including file, relative, or by a unique path suffix (``-Iinclude``).
    assert resolved(found, "imports", "file:src/cart.c") == {"file:src/cart.h"}
    assert resolved(found, "imports", "file:src/cart.h") == {"file:include/shop/store.h"}
    assert resolved(found, "imports", "file:src/store.c") == {"file:include/shop/store.h"}
    assert unresolved(found, "imports", "file:src/store.c") == {"stdlib.h", "string.h"}
    # Linkage: a file's own static wins; an external definition anywhere otherwise.
    assert resolved(found, "calls", "symbol:src/cart.c#cart_add") == {"symbol:src/cart.c#hash_key"}
    assert resolved(found, "calls", "symbol:src/store.c#store_get") == {
        "symbol:src/store.c#hash_key",
        f"{HEADER}#STORE_EMPTY",  # a macro call
        f"{HEADER}#store_size",  # a static inline in an included header
    }
    assert resolved(found, "calls", "symbol:src/main.c#main") == {
        "symbol:src/store.c#store_open",
        "symbol:src/cart.c#cart_add",
        "symbol:src/cart.c#cart_first",
    }
    # A local function pointer is opaque; the C library stays unresolved.
    assert resolved(found, "calls", "symbol:src/cart.c#cart_first") == {
        "symbol:src/store.c#store_must_get"
    }
    assert unresolved(found, "calls", "symbol:src/cart.c#cart_first") == {"get"}
    assert unresolved(found, "calls", "symbol:src/main.c#main") == {"printf"}
    # Prototypes reference their definitions; types and tags resolve through includes.
    assert resolved(found, "references", f"{HEADER}#store_get") == {
        "symbol:src/store.c#store_get",
        f"{HEADER}#Store~1",
        f"{HEADER}#Key",
    }
    assert resolved(found, "references", "symbol:src/cart.c#cart_add") == {
        "symbol:src/cart.h#Cart",
        f"{HEADER}#Key",
    }
    assert resolved(found, "references", f"{HEADER}#Store") == {f"{HEADER}#Entry"}
    # Conditional compilation is not evaluated: platform files are not chosen between.
    ambiguous = {(e.source, e.targets) for e in found.edges if e.resolution == "ambiguous"}
    platforms = ("symbol:src/path_posix.c#platform_path", "symbol:src/path_windows.c#platform_path")
    assert ambiguous == {
        ("symbol:src/main.c#main", platforms),
        (f"{HEADER}#platform_path", platforms),
    }


def test_search_and_context(tmp_path: Path, fts: Path) -> None:
    with oxide.open_repository(FIXTURE, data_dir=tmp_path / "data") as repo:
        repo.index()
        result = repo.search("must get store entries", symbols=["store_must_get"])
        assert result.entries[0].entity.key in {
            "symbol:src/store.c#store_must_get",
            f"{HEADER}#store_must_get",
        }
        bundle = repo.build_context("open the store", symbols=["store_open"], budget=1024)
        assert 0 < bundle.used <= 1024
        assert "Store *store_open(enum Mode mode)" in bundle.payload


def test_scoping_and_linkage_are_conservative(tmp_path: Path) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "a.h": "#define twice(x) ((x) * 2)\nstatic int shared(void) { return 1; }\n"
                "typedef int T;\n",
                "a.c": '#include "a.h"\nint f(void) { return 1; }\nint twice(int x);\n'
                "int g(int f) { return f(); }\n"
                "int h(void) { int (*shared)(void) = 0; return shared(); }\n"
                "int k(void) { typedef long T; T x = 0; struct S { int a; } s; struct S *p;"
                " return twice(f()) + shared(); }\n",
                "b.c": "static int hidden(void) { return 0; }\nint f(void) { return 2; }\n",
                "c.c": "int c(void) { return hidden() + shared() + f(); }\n",
            },
        )
    )
    assert not resolved(found, "calls", "symbol:a.c#g")  # a parameter named f
    assert not resolved(found, "calls", "symbol:a.c#h")  # a local named shared
    # Macros win over a same-named function; a visible definition over one elsewhere.
    assert resolved(found, "calls", "symbol:a.c#k") == {
        "symbol:a.h#twice",
        "symbol:a.c#f",
        "symbol:a.h#shared",
    }
    assert not resolved(found, "references", "symbol:a.c#k")  # local typedef and tag
    # Another file's static is invisible; externals of other files are ambiguous.
    assert unresolved(found, "calls", "symbol:c.c#c") == {"hidden", "shared"}
    assert {e.targets for e in found.edges if e.source == "symbol:c.c#c" and e.name == "f"} == {
        ("symbol:a.c#f", "symbol:b.c#f")
    }


def test_include_resolution(tmp_path: Path) -> None:
    found = derived(
        write(
            tmp_path,
            {
                "lib/stdio.h": "int mine;\n",
                "lib/x/util.h": "int util;\n",
                "one/dup.h": "",
                "two/dup.h": "",
                "src/a.c": '#include <stdio.h>\n#include <x/util.h>\n#include "dup.h"\n'
                '#include "../lib/stdio.h"\n#include "missing.h"\n#include NAME\n',
            },
        )
    )
    imports = {
        e.evidence.start if e.evidence else -1: e for e in found.edges if e.kind == "imports"
    }
    edges = [imports[k] for k in sorted(imports)]
    assert [(e.resolution, e.targets) for e in edges] == [
        ("unresolved", ()),  # an angle include without a directory is the system's
        ("resolved", ("file:lib/x/util.h",)),
        ("ambiguous", ("file:one/dup.h", "file:two/dup.h")),
        ("resolved", ("file:lib/stdio.h",)),
        ("unresolved", ()),
    ]
    assert dialect("a/b.h") == dialect("a/b.c") == "c"


def test_macro_annotated_declarations_recover_their_names() -> None:
    parsed = c.parse(
        b"API lua_Integer luaL_len (lua_State *L, int idx) { return 1; }\n"
        b"API int lua_gettop (lua_State *L) { return 0; }\n"
        b"API int (lua_settop) (lua_State *L, int idx);\n"
        b"#define fastget(t,slot) \\\n  (!t  /* no table */  \\\n   ? (slot = 0)  \\\n"
        b"   : (slot = get(t)))  /* done */\n"
        b'const char *s = "/* text */"; char q = \'"\'; /* a\n comment */ int after;\n'
    )
    assert not isinstance(parsed, str)
    decls = [(s.decl.kind, s.decl.name) for s in parsed.scopes[1:] if s.decl]
    # A keyword is never a name: the parenthesized prototype is not a symbol.
    assert decls == [
        ("function", "luaL_len"),
        ("function", "lua_gettop"),
        ("macro", "fastget"),
        ("variable", "s"),
        ("variable", "q"),
        ("variable", "after"),
    ]
    macro = next(s.decl for s in parsed.scopes[1:] if s.decl and s.decl.kind == "macro")
    assert macro.signature == "#define fastget(t,slot)"


def test_identity_is_local_and_deterministic(tmp_path: Path) -> None:
    files = {
        "a.c": "int f(void);\nint f(void) { return 0; }\n#ifdef X\nint g;\n#else\nint g;\n#endif\n"
    }
    first = derived(write(tmp_path / "one", files))
    second = derived(write(tmp_path / "two", {**files, "b.c": "int f(void) { return 1; }\n"}))
    keys = {e.key for e in first.entities}
    assert {"symbol:a.c#f", "symbol:a.c#f~1", "symbol:a.c#g", "symbol:a.c#g~1"} <= keys
    assert keys <= {e.key for e in second.entities}
    assert derived(tmp_path / "one") == first


def test_coverage_cache_validation_and_limits(tmp_path: Path) -> None:
    root = write(
        tmp_path,
        {
            "ok.c": "int ok(void) { return 0; }\n",
            "broken.c": "int broken( {\n\treturn\n}\n\nint after(void) { return 1; }\n",
            "latin.h": b"// \xff\n",
        },
    )
    full = derived(root)
    files = {f.path: f for f in full.files}
    assert files["broken.c"].coverage == "partial" and files["broken.c"].diagnostics
    assert files["latin.h"].coverage == "unsupported"
    assert "symbol:broken.c#after" in {e.key for e in full.entities}
    assert set(full.facts) == {"c"}
    for f in capture(root).files:
        parsed = c.parse(f.data)
        if not isinstance(parsed, str):
            assert c.decode(json.loads(json.dumps(c.encode(parsed))), len(f.data)) == parsed
    assert derive(capture(root), "test-repo", full.facts).reused == 2
    cached = copy.deepcopy(full.facts)
    for key in cached["c"]:
        cached["c"][key] = ["damaged"]
    assert derive(capture(root), "test-repo", cached) == full
    deep = ("int x = " + "(" * 70000 + "1" + ")" * 70000 + ";\n").encode()
    assert c.parse(deep) == python.TOO_DEEP


def test_unsound_cached_facts_are_parsed_never_trusted_or_fatal(tmp_path: Path) -> None:
    source = (
        '#include "a.h"\n#define M(x) (x)\ntypedef struct S { int a; } T;\n'
        "static int f(T *t, struct S *s) { return M(g(t->a)); }\nint v, w;\n"
    )
    snapshot = capture(write(tmp_path / "repo", {"p/a.c": source}))
    full = derive(snapshot, "r")
    ((key, good),) = full.facts["c"].items()
    size = len(snapshot.files[0].data)
    rejected = 0
    for at in _paths(good):
        for bad in BAD:
            entry = _replace(good, at, bad)
            try:
                c.decode(entry, size)
            except ValueError:
                rejected += 1
                result = derive(snapshot, "r", {"c": {key: entry}})
                assert result == full and result.reused == 0, (at, bad)
                continue
            # Well-formed facts parse could have produced: they may differ, never fail.
            validate(derive(snapshot, "r", {"c": {key: entry}}))
    assert rejected > len(_paths(good)) * len(BAD) // 2


def test_persisted_incremental_equals_full_rebuild(tmp_path: Path) -> None:
    root = write(
        tmp_path / "repo",
        {
            "inc/a.h": "int a(void);\n",
            "a.c": '#include "inc/a.h"\nint a(void) { return 0; }\n',
            "b.c": '#include "a.h"\nint b(void) { return a(); }\n',
        },
    )
    steps: list[dict[str, str | bytes | None]] = [
        {"more.c": "int more(void) { return b(); }\n"},
        {"inc/a.h": None, "a.h": "#define a() 0\n"},  # includes re-resolve, facts reused
        {"a.c": "static int a(void) { return 1; }\n", "more.c": None},
        {"b.c": "typedef int a;\nint b(void) { a x = 0; return x; }\n"},
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
