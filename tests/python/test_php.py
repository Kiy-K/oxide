"""PHP slice conformance (docs/php.md): grammar, declarations, imports, resolution, evidence."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import REPO
from test_typescript import derived, resolved

from oxide.capture import capture
from oxide.core.indexing import derive, dialect, validate
from oxide.core.languages import php, php_composer, php_facts

FIXTURE = REPO / "fixtures/php_repo"


def test_dialect() -> None:
    assert dialect("a/B.php") == "php"
    assert dialect("a/b.phtml") == "php"


def test_declarations_kinds_and_namespaces() -> None:
    p = php.parse(
        b"<?php namespace App; interface I {} trait T {} "
        b'enum E: string implements I { case A = "a"; public function e() {} } '
        b"final class C extends B implements I { use T; const K = 1; "
        b"private string $p; public function f(): self { return new self(); } } "
        b"function g() {}"
    )
    assert not isinstance(p, str)
    assert [(d.kind, d.name, d.ns) for d in p.decls if d.parent is None] == [
        ("interface", "I", "App"),
        ("trait", "T", "App"),
        ("enum", "E", "App"),
        ("class", "C", "App"),
        ("function", "g", "App"),
    ]
    assert ("case", "A") in {(d.kind, d.name) for d in p.decls}
    assert ("method", "f") in {(d.kind, d.name) for d in p.decls}
    assert ("constant", "K") in {(d.kind, d.name) for d in p.decls}
    assert ("property", "p") in {(d.kind, d.name) for d in p.decls}
    assert not p.diagnostics


def test_imports_aliases_and_group_use() -> None:
    p = php.parse(
        b"<?php namespace A; use B\\C; use D\\E as F; "
        b"use function U\\helper; use const V\\K; use X\\{Y, Z as W};"
    )
    assert not isinstance(p, str)
    assert {(i.path, i.alias, i.kind) for i in p.imports} == {
        ("B\\C", "", "class"),
        ("D\\E", "F", "class"),
        ("U\\helper", "", "function"),
        ("V\\K", "", "const"),
        ("X\\Y", "", "class"),
        ("X\\Z", "W", "class"),
    }


def test_bases_and_conservative_calls() -> None:
    p = php.parse(
        b"<?php namespace A; class C extends B implements I, \\J { use T; "
        b"function f() { helper(); Logger::write(1); $this->find(); self::go(); "
        b"new B(); $fn(); $o->$m(); call_user_func('x'); } }"
    )
    assert not isinstance(p, str)
    assert {b.name for b in p.bases} == {"B", "I", "\\J", "T"}
    assert {(c.name, c.kind) for c in p.calls} == {
        ("helper", "function"),
        ("Logger::write", "static"),
        ("$this->find", "method"),
        ("self::go", "static"),
        ("B", "new"),
    }


def test_mixed_php_html_spans_are_inside_php_tags() -> None:
    data = b"<html>\n<body>Hi</body>\n<?php function f() { return 1; } ?>\n<footer>x</footer>"
    p = php.parse(data)
    assert not isinstance(p, str)
    assert [d.name for d in p.decls] == ["f"]
    assert b"function f" in data[p.decls[0].start : p.decls[0].end]


_SOURCES = (
    b"<?php namespace A; use B\\C as D; class X extends Y implements I { use T; "
    b"const K = 1; public function f() { helper(); $this->g(); new Y(); } }",
    b"<h1>title</h1><?php function f() {} ?><p>tail</p>",
)


def test_facts_codec_roundtrip() -> None:
    for src in _SOURCES:
        parsed = php.parse(src)
        assert not isinstance(parsed, str)
        assert php_facts.decode(php_facts.encode(parsed), len(src)) == parsed
        with pytest.raises(ValueError):
            php_facts.decode(["garbage"], len(src))


def test_partial_parse_reports_diagnostics_not_empty_success(tmp_path: Path) -> None:
    (tmp_path / "Broken.php").write_bytes(b"<?php class Broken { public function oops( }")
    found = derive(capture(tmp_path), "test-repo")
    validate(found)
    info = next(f for f in found.files if f.path.endswith("Broken.php"))
    assert info.coverage == "partial" and info.diagnostics


def test_longest_psr4_prefix_wins_and_trailing_slash_is_significant() -> None:
    c = php_composer.load(
        {
            "composer.json": json.dumps(
                {
                    "autoload": {
                        "psr-4": {
                            "App\\": "src/",
                            "App\\Deep\\": "deep/",
                            "Bare": "bare/",
                        },
                        "psr-0": {"Legacy\\": "legacy/"},
                        "classmap": ["lib/"],
                        "files": ["src/Support/helpers.php"],
                    }
                }
            ).encode()
        }
    )
    assert php_composer.fqn_at(c, "deep/Thing.php") == ("psr4", "App\\Deep\\Thing")
    assert php_composer.fqn_at(c, "src/Thing.php") == ("psr4", "App\\Thing")
    assert php_composer.fqn_at(c, "legacy/Legacy/Thing.php") == ("psr0", "Legacy\\Thing")
    assert php_composer.path_for(c, "App\\Deep\\Thing") == ("psr4", "deep/Thing.php")
    assert php_composer.classmap_roots(c) == ("lib/",)
    assert c.files == ("src/Support/helpers.php",)


def test_malformed_composer_is_ignored() -> None:
    assert php_composer.load({"composer.json": b"{not json"}) == php_composer.Composer((), (), ())


def _write(root: Path, name: str, source: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)


def test_entities_bases_calls_and_four_evidence_labels(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "composer.json",
        json.dumps({"autoload": {"psr-4": {"App\\": "src/"}, "classmap": ["lib/"]}}),
    )
    _write(
        tmp_path,
        "src/Logger.php",
        "<?php namespace App\\Support; class Logger { public static function write($x) {} }",
    )
    _write(
        tmp_path,
        "src/Dup.php",
        "<?php namespace App; class Dup {}",
    )
    _write(
        tmp_path,
        "src/Other.php",
        "<?php namespace Other; class Dup {}",
    )
    _write(tmp_path, "src/Dup2.php", "<?php namespace App; class Dup {}")
    _write(
        tmp_path,
        "lib/global_helper.php",
        "<?php function helper() {}",
    )
    _write(
        tmp_path,
        "src/Store.php",
        "<?php namespace App; use App\\Support\\Logger as Log; "
        "class Store extends Base { public Dup $other; public function find() { "
        "Log::write(1); new Missing(); $this->find(); Dup::go(); helper(); } }",
    )
    found = derived(tmp_path)
    entities = {e.key: e for e in found.entities}
    assert entities["symbol:src/Store.php#App.Store"].kind == "class"
    assert entities["symbol:src/Store.php#App.Store.find"].kind == "method"
    assert entities["symbol:src/Logger.php#App.Support.Logger"].kind == "class"
    assert resolved(found, "calls", "symbol:src/Store.php#App.Store.find") == {
        "symbol:src/Logger.php#App.Support.Logger.write",
        "symbol:src/Store.php#App.Store.find",
        "symbol:lib/global_helper.php#helper",  # heuristic: PHP's global-function fallback
    }
    assert {
        (e.resolution, e.basis) for e in found.edges if e.kind in ("references", "calls", "imports")
    } >= {
        ("resolved", "resolved"),
        ("resolved", "heuristic"),
        ("ambiguous", "syntactic"),
        ("unresolved", "syntactic"),
    }


def test_fixture_structure() -> None:
    found = derived(FIXTURE)
    validate(found)
    assert len(found.entities) > 20
    assert any(f.path.endswith(".phtml") for f in found.files)
    assert {f.coverage for f in found.files if f.language == "php"} == {"complete"}


def test_php_l_oracle_acceptance() -> None:
    if shutil.which("php") is None:
        pytest.skip("php CLI not installed")
    for f in capture(FIXTURE).files:
        if not f.path.endswith(".php"):
            continue
        ok = (
            subprocess.run(["php", "-l", str(FIXTURE / f.path)], capture_output=True).returncode
            == 0
        )
        parsed = php.parse(f.data)
        assert ok == (not isinstance(parsed, str) and not parsed.diagnostics)


def test_dynamic_calls_emit_no_edges(tmp_path: Path) -> None:
    source = (
        "<?php class C { function run() { "
        "$fn = 'x'; $fn(); $o->$m(); call_user_func('x'); "
        "call_user_func_array('x', []); ${'dyn'}(); "
        "$this->find(); self::go(); } }"
    )
    _write(tmp_path, "d.php", source)
    parsed = php.parse(source.encode())
    assert not isinstance(parsed, str)
    assert {(c.name, c.kind) for c in parsed.calls} == {
        ("$this->find", "method"),
        ("self::go", "static"),
    }
    found = derived(tmp_path)
    assert resolved(found, "calls", "symbol:d.php#C.run") == set()


def test_unsupported_construct_does_not_invent_edges(tmp_path: Path) -> None:
    _write(tmp_path, "u.php", "<?php $x = SomeFactory::make()->run();")
    found = derived(tmp_path)
    calls = [e for e in found.edges if e.kind == "calls"]
    assert calls and all(e.resolution == "unresolved" for e in calls)


def test_classmap_evidence_is_heuristic(tmp_path: Path) -> None:
    _write(tmp_path, "composer.json", json.dumps({"autoload": {"classmap": ["lib/"]}}))
    _write(tmp_path, "lib/Vendor/Missing.php", "<?php class Different {}")
    _write(tmp_path, "app.php", "<?php new \\Vendor\\Missing();")
    found = derived(tmp_path)
    edges = [e for e in found.edges if e.kind == "calls" and e.source == "file:app.php"]
    assert {e.basis for e in edges} == {"heuristic"}
    assert edges[0].targets == ("file:lib/Vendor/Missing.php",)


def test_names_are_case_insensitive_but_keys_keep_source_case(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "a.php",
        "<?php namespace App; class Logger { public static function write() {} } "
        "class Store { function f() { LOGGER::WRITE(1); } }",
    )
    found = derived(tmp_path)
    assert "symbol:a.php#App.Logger.write" in {e.key for e in found.entities}
    assert resolved(found, "calls", "symbol:a.php#App.Store.f") == {"symbol:a.php#App.Logger.write"}


def test_class_names_do_not_fall_back_to_global(tmp_path: Path) -> None:
    _write(tmp_path, "g.php", "<?php class Helper {}")
    _write(tmp_path, "a.php", "<?php namespace App; class Store { function f() { new Helper(); } }")
    found = derived(tmp_path)
    assert not resolved(found, "calls", "symbol:a.php#App.Store.f")


def test_namespace_only_file_keeps_a_valid_module(tmp_path: Path) -> None:
    _write(tmp_path, "src/Logger.php", "<?php namespace App\\Support; class Logger {}")
    _write(tmp_path, "app.php", "<?php namespace App; use App\\Support\\Logger; Logger::write();")
    found = derived(tmp_path)  # validate() runs here
    assert "module:php:App" in {e.key for e in found.entities}


def test_same_name_different_kinds_get_distinct_keys(tmp_path: Path) -> None:
    _write(tmp_path, "a.php", "<?php namespace App; class Same {} function Same() {}")
    found = derived(tmp_path)  # validate() runs here
    keys = [e.key for e in found.entities if e.category == "symbol"]
    assert len(keys) == len(set(keys)) == 2


def test_dynamic_scoped_call_emits_no_edge(tmp_path: Path) -> None:
    source = "<?php class C { function f() { $cls::m(); $obj::$method(); } }"
    _write(tmp_path, "d.php", source)
    parsed = php.parse(source.encode())
    assert not isinstance(parsed, str)
    assert parsed.calls == []
    found = derived(tmp_path)
    assert not [e for e in found.edges if e.kind == "calls"]


def test_trait_methods_resolve_from_this(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "a.php",
        "<?php namespace App; trait T { public function cached() {} } "
        "class C { use T; function run() { $this->cached(); } }",
    )
    found = derived(tmp_path)
    assert resolved(found, "calls", "symbol:a.php#App.C.run") == {"symbol:a.php#App.T.cached"}


def test_import_paths_are_absolute_and_proven() -> None:
    found = derived(FIXTURE)
    imports = [e for e in found.edges if e.kind == "imports" and e.source == "file:src/Store.php"]
    assert imports and all(e.resolution == "resolved" and e.basis == "resolved" for e in imports)


def test_absolute_autoload_roots_are_rejected() -> None:
    composer = php_composer.load(
        {"composer.json": json.dumps({"autoload": {"psr-4": {"App\\": "/abs/src"}}}).encode()}
    )
    assert composer.rules == ()


def test_aliases_and_current_namespace_do_not_collapse(tmp_path: Path) -> None:
    _write(tmp_path, "a.php", "<?php namespace A; class X {}")
    _write(tmp_path, "b.php", "<?php namespace B; class X {}")
    _write(
        tmp_path,
        "c.php",
        "<?php namespace C; use A\\X as Y; use B\\X; "
        "class Z { function f() { new Y(); new X(); } }",
    )
    found = derived(tmp_path)
    assert resolved(found, "calls", "symbol:c.php#C.Z.f") == {
        "symbol:a.php#A.X",
        "symbol:b.php#B.X",
    }
