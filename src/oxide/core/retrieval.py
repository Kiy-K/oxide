"""Entry-point retrieval (SPEC § Retrieval): lexical hits and structural seeds from the
typed query context, merged by entity with every piece of evidence kept.

Entry points start TreeRouter; they are never final context. Order is a declared
precedence (seeds in hint order: selection, paths, symbols, changed; then lexical
rank), not a fused score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING

from oxide.models import ChannelStatus, Entity, HintIssue, HintKind, Seed

if TYPE_CHECKING:
    from oxide.storage.ladybug import View

TERMS_VERSION = "oxide-terms-v1"
RETRIEVAL_VERSION = "oxide-retrieval-v2"
LEXICAL_LIMIT = 20
SYMBOL_LIMIT = 8
# Most declarations one name lookup reads before filtering by dotted path.
NAMED_LIMIT = 256

_WORD = re.compile(r"[A-Za-z0-9_]+")
_PART = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")


def terms(text: str) -> list[str]:
    """Lowercase code-aware terms: ``RetryPolicy`` -> ``retry policy retrypolicy``.

    Splits at non-ASCII-alphanumerics, ``_``, camelCase, acronym ends and digit
    changes; keeps the joined identifier; drops one-character terms (the FTS never
    matches them). Same rules as the Rust ``oxide-terms-v1``.
    """
    out: list[str] = []
    for word in _WORD.findall(text):
        out.extend(_word_terms(word))
    return out


# Words repeat across names, signatures and paths: caching them halves `document`'s cost
# on large repositories (docs/phase-6/fts-footprint § 4).
@lru_cache(maxsize=1 << 16)
def _word_terms(word: str) -> tuple[str, ...]:
    parts = [p.lower() for p in _PART.findall(word)]
    joined = ("".join(parts),) if len(parts) > 1 else ()
    return (*(p for p in parts if len(p) > 1), *joined)


def document(entity: Entity) -> str:
    """The lexical document of an entity: terms of its name, signature and file path."""
    if entity.category == "repository":
        return ""
    texts = [entity.name, entity.signature or ""]
    if entity.source is not None:
        texts.append(entity.source.path)
    return " ".join(t for text in texts for t in terms(text))


@dataclass(frozen=True, slots=True)
class Hints:
    """Typed query context: explicit structural hints, never agent objects."""

    selection: tuple[str, int, int] | None = None
    paths: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()


@dataclass(slots=True)
class Entry:
    key: str
    rank: int | None = None
    score: float | None = None
    seeds: list[Seed] = field(default_factory=list)


@dataclass(slots=True)
class Retrieved:
    entries: list[Entry]
    lexical: ChannelStatus
    issues: list[HintIssue]


def retrieve(
    view: View,
    query: str,
    hints: Hints,
    *,
    lexical: bool = True,
    structural: bool = True,
    lexical_limit: int = LEXICAL_LIMIT,
    symbol_limit: int = SYMBOL_LIMIT,
) -> Retrieved:
    entries: dict[str, Entry] = {}
    issues: list[HintIssue] = []

    def entry(key: str) -> Entry:
        return entries.setdefault(key, Entry(key))

    if structural:
        for seed, key in _seeds(view, hints, symbol_limit, issues):
            if seed not in entry(key).seeds:
                entry(key).seeds.append(seed)
    if not lexical:
        status = ChannelStatus("disabled", "lexical channel turned off")
    elif view.lexical_unavailable is not None:
        status = ChannelStatus("unavailable", view.lexical_unavailable)
    else:
        hits, truncated = view.lexical(" ".join(terms(query)), lexical_limit)
        for rank, (key, score) in enumerate(hits, start=1):
            found = entry(key)
            found.rank, found.score = rank, score
        status = ChannelStatus("ran", view.lexical_scorer, len(hits), truncated)
    return Retrieved(list(entries.values()), status, issues)


def _seeds(
    view: View, hints: Hints, symbol_limit: int, issues: list[HintIssue]
) -> list[tuple[Seed, str]]:
    out: list[tuple[Seed, str]] = []

    def path_hint(kind: HintKind, hint: str, path: str) -> str | None:
        """Exact path first; else a unique case-folded match (ADR-0010 § RepoPath)."""
        folded = [] if path in view.files else [p for p in view.files if p.lower() == path.lower()]
        if path in view.files or len(folded) == 1:
            real = path if path in view.files else folded[0]
            out.append((Seed(kind, hint, real != path), f"file:{real}"))
            return real
        issue = HintIssue(kind, hint, "ambiguous", len(folded)) if folded else None
        issues.append(issue or HintIssue(kind, hint, "not_found"))
        return None

    if hints.selection is not None:
        path, start, end = hints.selection
        hint = f"{path}@{start}..{end}"
        if (real := path_hint("selection", hint, path)) is not None:
            inner = view.innermost(real, start, end)
            if inner is not None:
                seed, _ = out.pop()
                out.append((seed, inner))
    for path in hints.paths:
        path_hint("path", path, path)
    for symbol in hints.symbols:
        keys, cut = _symbols(view, symbol, symbol_limit)
        if cut:
            issues.append(HintIssue("symbol", symbol, "truncated", cut))
        elif not keys:
            issues.append(HintIssue("symbol", symbol, "not_found"))
        out.extend((Seed("symbol", symbol), key) for key in keys)
    for path in hints.changed:
        path_hint("changed", path, path)
    return out


def _symbols(view: View, hint: str, limit: int) -> tuple[list[str], int]:
    """An exact name, or a dotted suffix of the declaration path (``Auth.refresh``).
    Returns the matches and the limit that cut them (0 when none did)."""
    parts = hint.split(".")
    named, truncated = view.named(parts[-1], NAMED_LIMIT)
    cut = NAMED_LIMIT if truncated else 0
    found = set()
    for key in named:
        if key.startswith("symbol:"):
            path = [segment.split("~")[0] for segment in key.rsplit("#", 1)[1].split(".")]
            if path[-len(parts) :] == parts:
                found.add(key)
        elif len(parts) == 1:
            found.add(key)
    if len(parts) > 1:
        modules, truncated = view.named(hint, NAMED_LIMIT)
        cut = NAMED_LIMIT if truncated else cut
        found.update(modules)
    ordered = sorted(found)
    if len(ordered) > limit:
        cut = limit
    return ordered[:limit], cut
