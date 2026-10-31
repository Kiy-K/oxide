"""C++ syntax facts' cache codec; extraction is in ``cpp``."""

from __future__ import annotations

import re
from typing import Any

from oxide.core.languages.c import Include
from oxide.core.languages.cpp import CLASSES, KINDS, Decl, Parsed, Scope
from oxide.core.languages.java import Call
from oxide.core.languages.python import Use, as_list


def encode(parsed: Parsed) -> list[Any]:
    scopes = [
        [
            s.parent,
            s.decl and [getattr(s.decl, f) for f in Decl.__slots__],
            sorted(s.bound),
            s.usings,
            s.bases,
            [[u.name, u.start, u.end, u.arity] for u in s.calls],
            [[u.name, u.start, u.end] for u in s.refs],
        ]
        for s in parsed.scopes
    ]
    includes = [[i.path, i.system, i.start, i.end] for i in parsed.includes]
    return [includes, scopes, parsed.diagnostics]


def decode(value: Any, size: int) -> Parsed:
    """The inverse of ``encode`` for a file of ``size`` bytes. Cached facts come from
    disk: anything ``parse`` could not have produced raises ValueError, and the caller
    parses instead."""
    try:
        includes, scopes, diagnostics = as_list(value)
        parsed = Parsed(
            [Include(*as_list(i)) for i in as_list(includes)],
            [
                Scope(
                    parent,
                    None if decl is None else Decl(*as_list(decl)),
                    set(as_list(bound)),
                    list(as_list(usings)),
                    list(as_list(bases)),
                    [Call(*as_list(u)) for u in as_list(calls)],
                    [Use(*as_list(u)) for u in as_list(refs)],
                )
                for parent, decl, bound, usings, bases, calls, refs in map(as_list, as_list(scopes))
            ],
            as_list(diagnostics),
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"malformed parse facts: {error}") from None
    if not _sound(parsed, size):
        raise ValueError("parse facts are ill-typed, out of bounds or not a scope tree")
    return parsed


# A symbol's name: an identifier, a destructor or an operator; never ``#`` (the key's).
_NAME = re.compile(r"~?[^\W\d]\w*|operator[^#]+")
_QUALIFIER = re.compile(r"(?:::)?(?:[^\W\d]\w*(?:::[^\W\d]\w*)*)?")


def _sound(parsed: Parsed, size: int) -> bool:
    """What ``parse`` guarantees: in-bounds spans, string texts, a file scope then
    symbol scopes whose parents come first and are containers, and valid names, so
    identities stay unique."""

    def span(a: Any, b: Any) -> bool:
        return type(a) is int and type(b) is int and 0 <= a <= b <= size

    def texts(*values: Any) -> bool:
        return all(type(v) is str for v in values)

    scopes = parsed.scopes
    if not scopes or scopes[0].decl is not None or scopes[0].parent != 0:
        return False
    for k, s in enumerate(scopes):
        d = s.decl
        uses: list[Use | Call] = [*s.calls, *s.refs]
        if k and not (
            d is not None
            and type(s.parent) is int
            and 0 <= s.parent < k
            and (
                not s.parent
                or ((p := scopes[s.parent].decl) is not None and p.kind in ("namespace", *CLASSES))
            )
            and d.kind in KINDS
            and texts(d.name, d.qualifier, d.signature)
            and _NAME.fullmatch(d.name) is not None
            and _QUALIFIER.fullmatch(d.qualifier) is not None
            and span(d.start, d.end)
            and type(d.static) is bool
            and type(d.defines) is bool
            and type(d.low) is int
            and type(d.high) is int
        ):
            return False
        if not (
            all(span(u.start, u.end) for u in uses)
            and all(type(u.arity) is int for u in s.calls)
            and texts(*s.bound, *s.usings, *s.bases, *(u.name for u in uses))
        ):
            return False
    return texts(*parsed.diagnostics) and all(
        texts(i.path) and type(i.system) is bool and span(i.start, i.end) for i in parsed.includes
    )
