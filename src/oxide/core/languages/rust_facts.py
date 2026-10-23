"""Rust syntax facts and their cache codec (docs/rust.md); extraction is in ``rust``.

Scope 0 is the file. Every other scope is a declaration site: a symbol of a module
(any inline ``mod`` nesting is the declaration's ``module`` path), an ``impl`` block
(``<impl Trait for Type>``), or an associated function or constant, whose parent is its
``impl`` or ``trait``. Spans of scopes sorted by start nest only through that parent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from oxide.core.languages.python import Use, as_list

VERSION = "oxide-rust-v1"
# The JSON shape of ``Parsed`` that generations retain for reuse; bump on any change.
FACTS_VERSION = "oxide-rust-facts-v1"
KINDS = (
    "function",
    "method",
    "struct",
    "enum",
    "union",
    "trait",
    "type",
    "constant",
    "variable",
    "macro",
    "impl",
)
MAX_NAME = 256  # an impl's name is bounded; longer source text is cut


@dataclass(slots=True)
class Decl:
    kind: str
    name: str  # the identifier, or ``<impl ...>`` for an impl block
    module: tuple[str, ...]  # enclosing inline modules within the file
    start: int
    end: int
    signature: str
    public: bool  # any ``pub``; for a macro, ``#[macro_export]``
    test: bool  # carries a ``#[test]``-like attribute
    target: str | None = None  # impl: the self type's path, when it is a plain path
    trait: str | None = None  # impl: the trait's path
    variants: tuple[str, ...] = ()  # enum variant names


@dataclass(frozen=True, slots=True)
class Import:
    """One leaf of a ``use`` tree. ``path`` may start with ``::`` (absolute), ``crate``,
    ``self`` or ``super`` and may end in ``self``; a glob imports the path's names."""

    module: tuple[str, ...]  # inline module of the declaration
    scope: int  # 0 for a module-level ``use``, else the declaration holding it
    path: tuple[str, ...]
    alias: str | None  # ``_`` binds nothing
    glob: bool
    public: bool
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Mod:
    name: str
    module: tuple[str, ...]  # inline modules enclosing the declaration
    inline: bool  # ``mod m { ... }`` rather than ``mod m;``
    path: str | None  # ``#[path = "..."]``
    public: bool
    start: int
    end: int


@dataclass(slots=True)
class Scope:
    parent: int | None
    decl: Decl | None
    bound: set[str] = field(default_factory=set)  # opaque in this scope and its children
    calls: list[Use] = field(default_factory=list)  # ``a::b``, ``self.m``, ``name!``
    refs: list[Use] = field(default_factory=list)


@dataclass(slots=True)
class Parsed:
    imports: list[Import]
    mods: list[Mod]
    scopes: list[Scope]
    diagnostics: list[str]


def encode(parsed: Parsed) -> list[Any]:
    scopes = [
        [
            s.parent,
            s.decl
            and [
                *(getattr(s.decl, f) for f in Decl.__slots__ if f not in ("module", "variants")),
                list(s.decl.module),
                list(s.decl.variants),
            ],
            sorted(s.bound),
            [[u.name, u.start, u.end] for u in s.calls],
            [[u.name, u.start, u.end] for u in s.refs],
        ]
        for s in parsed.scopes
    ]
    imports = [
        [list(i.module), i.scope, list(i.path), i.alias, i.glob, i.public, i.start, i.end]
        for i in parsed.imports
    ]
    mods = [
        [m.name, list(m.module), m.inline, m.path, m.public, m.start, m.end] for m in parsed.mods
    ]
    return [imports, mods, scopes, parsed.diagnostics]


def _decl(value: Any) -> Decl:
    kind, name, start, end, signature, public, test, target, trait, module, variants = as_list(
        value
    )
    return Decl(
        kind, name, tuple(as_list(module)), start, end, signature, public, test, target, trait,
        tuple(as_list(variants)),
    )  # fmt: skip


def decode(value: Any, size: int) -> Parsed:
    """The inverse of ``encode`` for a file of ``size`` bytes. Cached facts come from
    disk: anything ``parse`` could not have produced raises ValueError, and the caller
    parses instead."""
    try:
        imports, mods, scopes, diagnostics = as_list(value)
        parsed = Parsed(
            [
                Import(tuple(as_list(m)), s, tuple(as_list(p)), a, g, v, b, e)
                for m, s, p, a, g, v, b, e in map(as_list, as_list(imports))
            ],
            [
                Mod(n, tuple(as_list(m)), i, p, v, b, e)
                for n, m, i, p, v, b, e in map(as_list, as_list(mods))
            ],
            [
                Scope(
                    parent,
                    None if decl is None else _decl(decl),
                    set(as_list(bound)),
                    [Use(*as_list(u)) for u in as_list(calls)],
                    [Use(*as_list(u)) for u in as_list(refs)],
                )
                for parent, decl, bound, calls, refs in map(as_list, as_list(scopes))
            ],
            as_list(diagnostics),
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"malformed parse facts: {error}") from None
    if not _sound(parsed, size):
        raise ValueError("parse facts are ill-typed, out of bounds or not a scope tree")
    return parsed


_IDENTIFIER = re.compile(r"[^\W\d]\w*")


def _sound(parsed: Parsed, size: int) -> bool:
    """What ``parse`` guarantees: in-bounds spans, string texts, identifier names (an
    impl's ``<impl ...>`` without the key separators), a scope tree whose only nesting is
    an impl or trait holding functions and constants, and scopes in start order."""

    def span(a: Any, b: Any) -> bool:
        return type(a) is int and type(b) is int and 0 <= a <= b <= size

    def ident(v: Any) -> bool:
        return type(v) is str and _IDENTIFIER.fullmatch(v) is not None

    def idents(v: Any) -> bool:
        return type(v) is tuple and all(ident(x) for x in v)

    def texts(*values: Any) -> bool:
        return all(type(v) is str for v in values)

    def flags(*values: Any) -> bool:
        return all(type(v) is bool for v in values)

    def optional(*values: Any) -> bool:
        return all(v is None or type(v) is str for v in values)

    scopes = parsed.scopes
    if not scopes or (scopes[0].parent, scopes[0].decl) != (None, None):
        return False
    at = 0
    for k, s in enumerate(scopes):
        d, uses = s.decl, (*s.calls, *s.refs)
        if k:
            if d is None or type(s.parent) is not int or not 0 <= s.parent < k:
                return False
            holder = scopes[s.parent].decl
            nested = s.parent > 0
            if not (
                texts(d.name, d.kind, d.signature)
                and span(d.start, d.end)
                and flags(d.public, d.test)
                and idents(d.module)
                and type(d.variants) is tuple
                and texts(*d.variants)
                and optional(d.target, d.trait)
            ):
                return False
            named = d.name.startswith("<impl ") and d.name.endswith(">") and "." not in d.name
            if not (
                d.kind in KINDS
                and (named if d.kind == "impl" else ident(d.name))
                and len(d.name) <= MAX_NAME + 8
                and (d.kind in ("method", "constant") if nested else d.kind != "method")
                and (not nested or (holder is not None and holder.kind in ("impl", "trait")))
                and (not nested or (holder is not None and holder.start <= d.start))
                and (not nested or (holder is not None and d.end <= holder.end))
                and d.start >= at
                and (d.kind == "impl" or (d.target is None and d.trait is None))
            ):
                return False
            at = d.start
        if not (
            all(span(u.start, u.end) for u in uses) and texts(*s.bound, *(u.name for u in uses))
        ):
            return False
    return (
        texts(*parsed.diagnostics)
        and all(
            idents(i.module)
            and type(i.scope) is int
            and 0 <= i.scope < len(scopes)
            and type(i.path) is tuple
            and i.path
            and texts(*i.path)
            and optional(i.alias)
            and flags(i.glob, i.public)
            and span(i.start, i.end)
            for i in parsed.imports
        )
        and all(
            ident(m.name)
            and idents(m.module)
            and flags(m.inline, m.public)
            and optional(m.path)
            and span(m.start, m.end)
            for m in parsed.mods
        )
    )
