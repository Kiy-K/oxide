"""PHP slice conformance (docs/php.md): grammar, declarations, imports, resolution, evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import REPO
from test_typescript import derived, resolved

from oxide.capture import capture
from oxide.core.indexing import dialect
from oxide.core.languages import php

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
