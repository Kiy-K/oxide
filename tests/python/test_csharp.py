"""C# slice conformance (docs/csharp.md): kinds, namespaces, provenance, type and member
resolution, tests and the facts codec."""

from __future__ import annotations

from pathlib import Path

from conftest import REPO
from test_typescript import derived, resolved

from oxide.capture import capture
from oxide.core.indexing import dialect
from oxide.core.languages import csharp, csharp_facts

FIXTURE = REPO / "fixtures/csharp_repo"
S = "symbol:src/Shop/Storage"
STORE = f"{S}/Store.cs#Store"
CART = "symbol:src/Shop/Cart/Cart.cs#Cart"
TEST = "symbol:tests/Shop.Tests/StoreTests.cs#StoreTests.GetsWhatWasPut"


def test_dialect() -> None:
    assert dialect("a/B.cs") == "csharp"


def test_entities_kinds_and_provenance() -> None:
    found = derived(FIXTURE)
    entities = {e.key: e for e in found.entities}
    kinds = {k: e.kind for k, e in entities.items() if e.category == "symbol"}
    assert kinds[f"{S}/IBackend.cs#IBackend"] == "interface"
    assert kinds[f"{S}/Key.cs#Key"] == "record"
    assert kinds[f"{S}/Mode.cs#Mode"] == "enum"
    assert kinds[f"{S}/Mode.cs#Mode.Write"] == "enum_member"
    assert kinds[f"{S}/Mode.cs#CachedAttribute.Seconds"] == "property"
    assert kinds[f"{STORE}.Entry.Value"] == "property"  # nested
    assert kinds[f"{STORE}.Store"] == kinds[f"{STORE}.Store~1"] == "constructor"
    assert kinds[f"{STORE}.keys"] == "field"
    assert entities[TEST].test and not entities[f"{TEST[:-14]}Fixture"].test
    assert {k for k, e in entities.items() if e.category == "module"} == {
        f"module:csharp:Shop.{n}" for n in ("Cart", "Storage", "Tests", "Util")
    }
    data = {f.path: f.data for f in capture(FIXTURE).files}
    src = entities[f"{STORE}.Get"].source
    assert src is not None
    text = data[src.path][src.start : src.end].decode()
    assert text.startswith("[Cached(Seconds = 5)]\n    public override string? Get")
    assert entities[f"{STORE}.Get"].signature == "public override string? Get(string key)"


def test_relations() -> None:
    found = derived(FIXTURE)
    util = "symbol:src/Shop/Util/Strings.cs#Strings"
    assert resolved(found, "imports", "file:src/Shop/Cart/Cart.cs") == {
        "module:csharp:Shop.Storage",
        util,  # using static
    }
    assert resolved(found, "references", STORE) == {f"{S}/AbstractStore.cs#AbstractStore"}
    assert resolved(found, "calls", f"{STORE}.Save") == {
        f"{util}.Trim",  # through an alias
        f"{S}/Key.cs#Key",  # new Key
        f"{STORE}.Touch",
    }
    # Resolution is by name and arity, not argument types: Put(string, int) and the
    # inherited Put(string, string) both fit, so the call is ambiguous, not resolved.
    assert resolved(found, "calls", f"{STORE}.Put") == set()
    assert resolved(found, "calls", f"{CART}.Add") == {
        f"{S}/IBackend.cs#IBackend.Put",  # a field's declared type
        f"{util}.Trim",
        STORE,
    }
    assert resolved(found, "calls", TEST) == {STORE, f"{STORE}.Get"}
    assert resolved(found, "references", f"{STORE}.Get") == {f"{S}/Mode.cs#CachedAttribute"}


def test_facts_codec_roundtrip() -> None:
    for f in capture(FIXTURE).files:
        if f.path.endswith(".cs"):
            parsed = csharp.parse(f.data)
            assert not isinstance(parsed, str)
            assert csharp_facts.decode(csharp_facts.encode(parsed), len(f.data)) == parsed


def test_override_does_not_hide_same_arity_overload(tmp_path: Path) -> None:
    (tmp_path / "sample.cs").write_text(
        "class Base { public virtual void M(object x) {} public void M(string x) {} } "
        'class Derived : Base { public override void M(object x) {} void Run() { M("x"); } }'
    )
    result = derived(tmp_path)
    assert not resolved(result, "calls", "symbol:sample.cs#Derived.Run")


def test_global_qualification_starts_at_global_namespace(tmp_path: Path) -> None:
    (tmp_path / "n.cs").write_text("namespace N { class Target {} }")
    (tmp_path / "a.cs").write_text(
        "namespace A { namespace N { class Target {} } "
        "class Rel { N.Target f; } class Abs { global::N.Target f; } }"
    )
    result = derived(tmp_path)
    assert resolved(result, "references", "symbol:a.cs#Rel.f") == {"symbol:a.cs#Target"}
    assert resolved(result, "references", "symbol:a.cs#Abs.f") == {"symbol:n.cs#Target"}


def test_file_local_types_stay_in_their_file(tmp_path: Path) -> None:
    (tmp_path / "a.cs").write_text("file class Helper {} class A { Helper h; }")
    (tmp_path / "b.cs").write_text("file class Helper {} class B { Helper h; }")
    result = derived(tmp_path)
    assert resolved(result, "references", "symbol:a.cs#A.h") == {"symbol:a.cs#Helper"}
    assert resolved(result, "references", "symbol:b.cs#B.h") == {"symbol:b.cs#Helper"}


def test_usings_and_aliases_are_scoped_to_namespace_blocks(tmp_path: Path) -> None:
    (tmp_path / "s.cs").write_text(
        "namespace X { class A {} } namespace Y { class B {} } "
        "namespace N { using T = X.A; class One { T f; } } "
        "namespace N { using T = Y.B; class Two { T f; } }"
    )
    result = derived(tmp_path)
    assert resolved(result, "references", "symbol:s.cs#One.f") == {"symbol:s.cs#A"}
    assert resolved(result, "references", "symbol:s.cs#Two.f") == {"symbol:s.cs#B"}


def test_static_import_is_scoped_to_its_namespace(tmp_path: Path) -> None:
    (tmp_path / "s.cs").write_text(
        "namespace Lib { class Helpers { public static int Abs(int x) => x; } } "
        "namespace A { using static Lib.Helpers; class C { int F() => Abs(1); } } "
        "namespace B { class D { int F() => Abs(1); } }"
    )
    result = derived(tmp_path)
    assert resolved(result, "calls", "symbol:s.cs#C.F") == {"symbol:s.cs#Helpers.Abs"}
    assert not resolved(result, "calls", "symbol:s.cs#D.F")


def test_alias_to_external_namespace_does_not_bind_project_type(tmp_path: Path) -> None:
    (tmp_path / "s.cs").write_text(
        "namespace A { class String {} } namespace N { using A = System; class C { A.String f; } }"
    )
    result = derived(tmp_path)
    assert not resolved(result, "references", "symbol:s.cs#C.f")


def test_optional_and_params_can_both_be_omitted(tmp_path: Path) -> None:
    (tmp_path / "s.cs").write_text(
        'class C { void M(int a, string b = "x", params int[] rest) {} void Run() { M(1); } }'
    )
    result = derived(tmp_path)
    assert resolved(result, "calls", "symbol:s.cs#C.Run") == {"symbol:s.cs#C.M"}


def test_unary_and_binary_operators_have_distinct_names(tmp_path: Path) -> None:
    (tmp_path / "s.cs").write_text(
        "class C { public static C operator +(C x) => x; "
        "public static C operator +(C x, C y) => x; }"
    )
    names = {e.name for e in derived(tmp_path).entities if e.category == "symbol"}
    assert {"op_UnaryPlus", "op_Addition"} <= names


def test_chained_conditional_call_is_extracted() -> None:
    parsed = csharp.parse(b"class C { void Run(){ a?.B?.M(); a?.M(); } }")
    assert not isinstance(parsed, str)
    assert {c.name for s in parsed.scopes for c in s.calls} == {"a.B.M", "a.M"}


def test_oracle_utf16_column_counts_astral_chars() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "oracle_compare", REPO / "scripts/csharp_oracle_compare.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # A😀B: UTF-16 units are A=0, 😀=1..2, B=3; Python indices 0,1,2 respectively.
    assert [module.utf16_column("A\U0001f600B", c) for c in (0, 1, 3, 4)] == [0, 1, 2, 3]
