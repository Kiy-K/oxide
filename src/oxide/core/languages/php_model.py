"""The PHP syntax facts' data model (``php`` extracts it, ``php_facts`` caches it)."""

from __future__ import annotations

import bisect
from dataclasses import dataclass

KINDS = frozenset(
    {
        "function",
        "method",
        "class",
        "interface",
        "trait",
        "enum",
        "constant",
        "case",
        "property",
    }
)
TYPE_KINDS = frozenset({"class", "interface", "trait", "enum"})


@dataclass(slots=True)
class Decl:
    kind: str
    name: str
    start: int
    end: int
    signature: str
    ns: str  # dot-joined namespace, "" for the global one
    parent: int | None  # index into Parsed.decls; None at top level
    ordinal: int  # source-order index among same-scope declarations of the same name


@dataclass(frozen=True, slots=True)
class Import:
    kind: str  # class | function | const
    path: str  # backslash-joined, as written
    alias: str
    ns: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Use:
    name: str  # backslash-joined or simple, as written
    start: int
    end: int
    owner: int | None  # declaration index owning the reference
    kind: str  # "" (plain) | extends | implements | trait


@dataclass(frozen=True, slots=True)
class Call:
    name: str
    start: int
    end: int
    owner: int | None
    kind: str  # function | static | method | new


@dataclass(slots=True)
class Parsed:
    decls: list[Decl]
    imports: list[Import]
    bases: list[Use]
    refs: list[Use]
    calls: list[Call]
    diagnostics: list[str]
    namespaces: list[tuple[int, int, str]]  # (start, end, namespace)


def key_fqn(fqn: str) -> str:
    """PHP's backslash separator to the dot form used in entity keys."""
    return fqn.replace("\\", ".")


def ns_at(regions: list[tuple[int, int, str]], at: int) -> str:
    """The namespace governing offset ``at``, or "" for the global one."""
    i = region_at(regions, at)
    return regions[i][2] if i >= 0 else ""


def region_at(regions: list[tuple[int, int, str]], at: int) -> int:
    """The index of the namespace region holding ``at``, or -1 outside every region.

    Alias scope is per region, so the index rather than the name is what two statements
    in different bracketed namespaces must not collide on.
    """
    i = bisect.bisect_right(regions, at, key=lambda r: r[0]) - 1
    return i if i >= 0 and at < regions[i][1] else -1


def fqn(decl: Decl, decls: list[Decl]) -> str:
    """Fully qualified name in dot form, including the enclosing declarations.

    Every segment carries its own ordinal: two same-named classes in disjoint branches
    would otherwise give their members the same key.
    """
    parts = [_named(decl)]
    parent = decl.parent
    seen = 0
    while parent is not None and 0 <= parent < len(decls) and seen < 512:
        parts.append(_named(decls[parent]))
        parent = decls[parent].parent
        seen += 1
    parts.reverse()
    return ".".join([*([decl.ns] if decl.ns else []), *parts])


def lookup_name(decl: Decl, decls: list[Decl]) -> str:
    """The name this declaration is written as, ordinals left out.

    A `~n` ordinal is a key-uniquefier, not part of the language: resolving by ``fqn``
    would make an ordinal-bearing declaration unreachable by the name it is used with.
    """
    parts = [decl.name]
    parent = decl.parent
    seen = 0
    while parent is not None and 0 <= parent < len(decls) and seen < 512:
        parts.append(decls[parent].name)
        parent = decls[parent].parent
        seen += 1
    parts.reverse()
    return ".".join([*([decl.ns] if decl.ns else []), *parts])


def _named(decl: Decl) -> str:
    return decl.name + (f"~{decl.ordinal}" if decl.ordinal else "")


def symbol_key(path: str, name: str) -> str:
    return f"symbol:{path}#{name}"
