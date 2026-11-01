"""Java syntax facts' cache codec; extraction is in ``java``."""

from __future__ import annotations

import re
from typing import Any

from oxide.core.languages.java import (
    KINDS,
    TYPE_KINDS,
    Call,
    Decl,
    Import,
    Parsed,
    Scope,
)
from oxide.core.languages.python import Use, as_list


def encode(parsed: Parsed) -> list[Any]:
    scopes = [
        [
            s.parent,
            s.decl and [getattr(s.decl, f) for f in Decl.__slots__],
            [[u.name, u.start, u.end] for u in s.supers],
            [[name, kind] for name, kind in sorted(s.bound.items())],
            sorted(s.types),
            [[c.name, c.start, c.end, c.arity] for c in s.calls],
            [[u.name, u.start, u.end] for u in s.refs],
        ]
        for s in parsed.scopes
    ]
    imports = [[i.static, i.path, i.wildcard, i.start, i.end] for i in parsed.imports]
    return [parsed.package, imports, scopes, parsed.diagnostics]


def decode(value: Any, size: int) -> Parsed:
    """The inverse of ``encode`` for a file of ``size`` bytes. Cached facts come from
    disk: anything ``parse`` could not have produced raises ValueError, and the caller
    parses instead."""
    try:
        package, imports, scopes, diagnostics = as_list(value)
        parsed = Parsed(
            package,
            [Import(*as_list(i)) for i in as_list(imports)],
            [
                Scope(
                    parent,
                    None if decl is None else Decl(*as_list(decl)),
                    [Use(*as_list(u)) for u in as_list(supers)],
                    {name: kind for name, kind in map(as_list, as_list(bound))},
                    set(as_list(types)),
                    [Call(*as_list(c)) for c in as_list(calls)],
                    [Use(*as_list(u)) for u in as_list(refs)],
                )
                for parent, decl, supers, bound, types, calls, refs in map(as_list, as_list(scopes))
            ],
            as_list(diagnostics),
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"malformed parse facts: {error}") from None
    if not _sound(parsed, size):
        raise ValueError("parse facts are ill-typed, out of bounds or not a scope tree")
    return parsed


_NAME = r"(?!\d)[\w$]+"
_DOTTED = re.compile(rf"{_NAME}(?:\.{_NAME})*")


def _sound(parsed: Parsed, size: int) -> bool:
    """What ``parse`` guarantees: in-bounds spans, typed values, a scope tree whose
    parents precede their children, identifier and dotted names, so identities stay
    unique and lookups well-formed."""

    def span(a: Any, b: Any) -> bool:
        return type(a) is int and type(b) is int and 0 <= a <= b <= size

    def dotted(v: Any) -> bool:
        return type(v) is str and _DOTTED.fullmatch(v) is not None

    def texts(*values: Any) -> bool:
        return all(type(v) is str for v in values)

    scopes = parsed.scopes
    if not scopes or scopes[0].parent is not None or scopes[0].decl is not None:
        return False
    for k, s in enumerate(scopes):
        d = s.decl
        if k and not (type(s.parent) is int and 0 <= s.parent < k):
            return False
        owner = scopes[s.parent].decl if k and type(s.parent) is int else None
        if d is not None and not (
            k
            and type(d.kind) is str
            and d.kind in KINDS
            # members belong to a declared type; types to one or to the file
            and (owner.kind in TYPE_KINDS if owner else s.parent == 0 and d.kind in TYPE_KINDS)
            and dotted(d.name)
            and "." not in d.name
            and span(d.start, d.end)
            and texts(d.signature)
            and type(d.arity) is int
            and d.arity >= 0
            and {type(d.varargs), type(d.test)} == {bool}
        ):
            return False
        calls = all(
            span(c.start, c.end) and texts(c.name) and type(c.arity) is int for c in s.calls
        )
        if not (
            calls
            and all(span(u.start, u.end) and dotted(u.name) for u in (*s.supers, *s.refs))
            and all(dotted(n) and (t is None or dotted(t)) for n, t in s.bound.items())
            and texts(*s.types)
        ):
            return False
    return (
        (parsed.package == "" or dotted(parsed.package))
        and texts(*parsed.diagnostics)
        and all(
            type(i.static) is bool
            and dotted(i.path)
            and type(i.wildcard) is bool
            and span(i.start, i.end)
            for i in parsed.imports
        )
    )
