"""The PHP syntax facts' data model (``php`` extracts it, ``php_facts`` caches it)."""

from __future__ import annotations

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


def fqn(decl: Decl, decls: list[Decl]) -> str:
    """Fully qualified name in dot form, including the enclosing declarations."""
    parts = [decl.name]
    parent = decl.parent
    seen = 0
    while parent is not None and 0 <= parent < len(decls) and seen < 512:
        parts.append(decls[parent].name)
        parent = decls[parent].parent
        seen += 1
    parts.reverse()
    return ".".join([*( [decl.ns] if decl.ns else []), *parts])


def symbol_key(path: str, name: str) -> str:
    return f"symbol:{path}#{name}"
