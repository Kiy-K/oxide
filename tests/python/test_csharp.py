"""C# slice conformance (docs/csharp.md): kinds, namespaces, provenance, type and member
resolution, tests and the facts codec."""

from __future__ import annotations

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
