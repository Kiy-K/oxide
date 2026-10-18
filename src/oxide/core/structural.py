"""Explicit structural search: an ast-grep rule over the published snapshot's source.

Read-only and explicitly invoked; never part of retrieval, routing or packing.
Callers and implementors are precomputed relations, not searches (docs/precomputed-
relations-migration/); this answers shape queries those can't (docs/phase-6/
astgrep-feasibility/). ``ast-grep-py`` is the optional ``structural`` extra and parses
with its own bundled grammars (the same versions OXIDE pins, measured for 0.51.0).
It has no timeout and holds the GIL, so the bounds are the file scope and ``limit``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from oxide.core.indexing import dialect
from oxide.models import (
    InvalidArgument,
    NativeUnavailable,
    SourceRef,
    StructuralMatch,
    StructuralResult,
)

if TYPE_CHECKING:
    from oxide.storage.ladybug import View

GRAMMARS = {"python": ("python",), "typescript": ("typescript", "tsx")}


def _in_scope(path: str, paths: tuple[str, ...]) -> bool:
    return not paths or any(path == p or path.startswith(p.rstrip("/") + "/") for p in paths)


def search(
    view: View,
    rule: str | Mapping[str, Any],
    language: str,
    paths: tuple[str, ...],
    limit: int,
) -> StructuralResult:
    try:
        from ast_grep_py import SgRoot
    except ImportError as error:
        raise NativeUnavailable(
            "structural search needs ast-grep-py: install the 'structural' extra "
            "(pip install 'oxide[structural]')"
        ) from error
    config: Any = {"rule": {"pattern": rule}} if isinstance(rule, str) else dict(rule)
    grammars = GRAMMARS[language]
    for grammar in grammars:  # reject a malformed rule before reading any source
        try:
            SgRoot("", grammar).root().find_all(config)
        except Exception as error:  # ast-grep raises untyped errors for invalid rules
            raise InvalidArgument(f"invalid structural rule for {grammar}: {error}") from error

    files = sorted(
        (
            i
            for i in view.files.values()
            if dialect(i.path) in grammars and _in_scope(i.path, paths)
        ),
        key=lambda i: i.path,
    )
    matches: list[StructuralMatch] = []
    partial: list[str] = []
    skipped: list[str] = []
    scanned = 0
    for info in files:
        if len(matches) > limit:
            break
        data = view.source(SourceRef(info.path, 0, 0, info.digest))
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:  # ast-grep takes str; replacement would shift offsets
            skipped.append(info.path)
            continue
        scanned += 1
        if info.coverage == "partial":  # the indexer's parse had errors
            partial.append(info.path)
        root = SgRoot(text, dialect(info.path) or language).root()
        # ast-grep positions are characters; SourceRef is bytes.
        table = None if text.isascii() else _byte_offsets(text)
        lines = text.split("\n")
        for node in sorted(root.find_all(config), key=lambda n: n.range().start.index):
            span = node.range()
            start, end = span.start.index, span.end.index
            if table is not None:
                start, end = table[start], table[end]
            matches.append(
                StructuralMatch(
                    SourceRef(info.path, start, end, info.digest),
                    span.start.line + 1,
                    span.end.line + 1,
                    view.innermost(info.path, start, end),
                    lines[span.start.line].rstrip("\r"),
                )
            )
            if len(matches) > limit:
                break
    return StructuralResult(
        view.snapshot,
        language,
        tuple(matches[:limit]),
        len(matches) > limit,
        scanned,
        tuple(partial),
        tuple(skipped),
    )


def _byte_offsets(text: str) -> list[int]:
    """UTF-8 byte offset of every character index, and of the end."""
    table, at = [], 0
    for ch in text:
        table.append(at)
        at += len(ch.encode("utf-8"))
    table.append(at)
    return table
