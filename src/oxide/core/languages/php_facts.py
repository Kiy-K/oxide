"""PHP syntax facts' cache codec; extraction is in ``php``."""

from __future__ import annotations

import re
from typing import Any

from oxide.core.languages.php_model import KINDS, Call, Decl, Import, Parsed, Use
from oxide.core.languages.python import as_list

_CALL_KINDS = frozenset({"function", "static", "method", "new"})
_USE_KINDS = frozenset({"", "extends", "implements", "trait"})
_IMPORT_KINDS = frozenset({"class", "function", "const"})
_NAME = re.compile(r"[\w$\\]+")
_NS = re.compile(r"[\w.]*")


def encode(parsed: Parsed) -> list[Any]:
    return [
        [
            [d.kind, d.name, d.start, d.end, d.signature, d.ns, d.parent, d.ordinal]
            for d in parsed.decls
        ],
        [[i.kind, i.path, i.alias, i.ns, i.start, i.end] for i in parsed.imports],
        [[u.name, u.start, u.end, u.owner, u.kind] for u in parsed.bases],
        [[u.name, u.start, u.end, u.owner, u.kind] for u in parsed.refs],
        [[c.name, c.start, c.end, c.owner, c.kind] for c in parsed.calls],
        list(parsed.diagnostics),
        [list(b) for b in parsed.namespaces],
    ]


def decode(value: Any, size: int) -> Parsed:
    """The inverse of ``encode``; cached bytes are untrusted, so anything ``parse``
    could not have produced raises ``ValueError`` and the caller parses instead."""
    try:
        decls, imports, bases, refs, calls, diagnostics, spaces = as_list(value)
        parsed = Parsed(
            [Decl(*as_list(d)) for d in as_list(decls)],
            [Import(*as_list(i)) for i in as_list(imports)],
            [Use(*as_list(u)) for u in as_list(bases)],
            [Use(*as_list(u)) for u in as_list(refs)],
            [Call(*as_list(c)) for c in as_list(calls)],
            as_list(diagnostics),
            [(a, b, n) for a, b, n in map(as_list, as_list(spaces))],
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"malformed parse facts: {error}") from None
    if not _sound(parsed, size):
        raise ValueError("parse facts are ill-typed, out of bounds or not a declaration tree")
    return parsed


def _sound(parsed: Parsed, size: int) -> bool:
    def span(a: Any, b: Any) -> bool:
        return type(a) is int and type(b) is int and 0 <= a <= b <= size

    def owner(v: Any, count: int) -> bool:
        return v is None or (type(v) is int and 0 <= v < count)

    count = len(parsed.decls)
    for k, d in enumerate(parsed.decls):
        if not (
            type(d.kind) is str
            and d.kind in KINDS
            and type(d.name) is str
            and _NAME.fullmatch(d.name) is not None
            and span(d.start, d.end)
            and type(d.signature) is str
            and type(d.ns) is str
            and _NS.fullmatch(d.ns) is not None
            and owner(d.parent, k)
            and type(d.ordinal) is int
            and 0 <= d.ordinal < 1_000_000
        ):
            return False
    for i in parsed.imports:
        if not (
            i.kind in _IMPORT_KINDS
            and all(type(v) is str for v in (i.path, i.alias, i.ns))
            and _NAME.fullmatch(i.path) is not None
            and span(i.start, i.end)
        ):
            return False
    for u in (*parsed.bases, *parsed.refs):
        if not (
            type(u.name) is str
            and span(u.start, u.end)
            and u.kind in _USE_KINDS
            and owner(u.owner, count)
        ):
            return False
    for c in parsed.calls:
        if not (
            type(c.name) is str
            and span(c.start, c.end)
            and c.kind in _CALL_KINDS
            and owner(c.owner, count)
        ):
            return False
    return all(
        span(a, b) and type(n) is str and _NS.fullmatch(n) is not None
        for a, b, n in parsed.namespaces
    ) and all(type(v) is str for v in parsed.diagnostics)
