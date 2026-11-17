"""C# namespace-block scope and symbol identity (``csharp_resolve``, ``csharp_emit``).

Usings, aliases and ``using static`` belong to the lexical namespace block containing
them, not just to the namespace's dotted name: reopening a namespace starts a new scope.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from oxide.core.languages.csharp_model import Parsed

type Site = tuple[int, int]  # (file, scope)
type Found = list[Site] | None  # None: unknown, so unresolved; []: certainly none
type Variable = tuple[Site, str | None]  # the declaring scope and the declared type
type Alias = list[Site] | str  # a type ([] when not captured) or a namespace
type Using = str | Site  # a namespace, or a statically imported type

NO_BLOCK = -1  # a namespace level with no lexical block; byte offsets are non-negative


def module_key(namespace: str) -> str:
    return f"module:csharp:{namespace}"


def levels(namespace: str) -> list[str]:
    """``A.B`` -> ``A.B``, ``A``, the global namespace."""
    parts = namespace.split(".") if namespace else []
    return [".".join(parts[:n]) for n in range(len(parts), -1, -1)]


def blocks_at(blocks: list[tuple[int, int, str]], at: int) -> list[tuple[int, int, str]]:
    """Namespace blocks enclosing ``at``, innermost first."""
    return sorted((b for b in blocks if b[0] <= at < b[1]), key=lambda b: b[0], reverse=True)


def block_at(blocks: list[tuple[int, int, str]], at: int) -> int | None:
    found = blocks_at(blocks, at)
    return found[0][0] if found else None


def scopes_at(
    blocks: list[tuple[int, int, str]], ns: str, at: int | None
) -> list[tuple[str, int | None]]:
    """Namespace levels visible at ``at``, each paired with the block owning its usings
    (``NO_BLOCK``: none; ``None``: the global namespace)."""
    found = blocks_at(blocks, at) if at is not None else []
    result: list[tuple[str, int | None]] = []
    for level in levels(ns):
        block = next((b[0] for b in found if b[2] == level), NO_BLOCK) if level else None
        result.append((level, block))
    return result


def visible_blocks(blocks: list[tuple[int, int, str]], at: int) -> list[int | None]:
    """Block keys whose static imports are visible at ``at``, innermost first."""
    return [*(b[0] for b in blocks_at(blocks, at)), None]


def alias_at(
    aliases: dict[tuple[int | None, str], Alias],
    blocks: list[tuple[int, int, str]],
    name: str,
    ns: str,
    at: int | None,
) -> Alias | None:
    for _level, block in scopes_at(blocks, ns, at):
        if block != NO_BLOCK and (alias := aliases.get((block, name))) is not None:
            return alias
    return None


@dataclass(slots=True)
class File:
    path: str
    digest: str
    parsed: Parsed
    keys: list[str | None]
    owners: list[str]  # per scope, the key of the nearest declared scope (or the file)
    spans: list[tuple[int, int, str]]  # top-level declarations: (start, end, namespace)
    aliases: dict[tuple[int | None, str], Alias] = field(default_factory=dict)
    usings: dict[int | None, list[Using]] = field(default_factory=dict)
    static_demand: dict[int | None, list[Site]] = field(default_factory=dict)


def symbol_keys(path: str, parsed: Parsed) -> list[str | None]:
    """Per scope, ``symbol:<path>#Outer.Inner.m``: the file key for scope 0. Equal
    segments (overloads, constructors' names aside) take ``~n`` in source order, and
    members nest under their owner's segment, ordinal included."""
    keys: list[str | None] = [f"file:{path}"]
    segments = [""]
    seen: dict[str, int] = {}
    for s in parsed.scopes[1:]:
        if s.decl is None or s.parent is None:
            keys.append(None)
            segments.append("")
            continue
        owner = segments[s.parent]
        segment = f"{owner}.{s.decl.name}" if owner else s.decl.name
        n = seen.get(segment, 0)
        seen[segment] = n + 1
        segment += f"~{n}" if n else ""
        segments.append(segment)
        keys.append(f"symbol:{path}#{segment}")
    return keys
