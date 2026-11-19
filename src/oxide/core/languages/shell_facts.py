"""Shell syntax facts' cache codec; extraction is in ``shell``."""

from __future__ import annotations

import re
from typing import Any

from oxide.core.languages.python import as_list
from oxide.core.languages.shell_model import DECL_KINDS, USE_KINDS, Decl, Parsed, ShellDialect, Use

_DIALECTS = frozenset({"bash", "sh"})
# A function name is any non-blank token (``do-thing``), a path may be quoted with
# spaces; both come from the file, so the codec only rejects control bytes.
_NAME = re.compile(r"[^\x00-\x20\x7f]+")
_PATH = re.compile(r"[^\x00-\x1f\x7f]*")


def encode(parsed: Parsed) -> list[Any]:
    return [
        parsed.dialect,
        [
            [d.kind, d.name, d.start, d.end, d.signature, d.owner, d.local, d.ordinal]
            for d in parsed.decls
        ],
        [[u.name, u.start, u.end, u.owner, u.kind, u.path] for u in parsed.uses],
        list(parsed.diagnostics),
        list(parsed.bash_features),
    ]


def decode(value: Any, size: int, dialect: ShellDialect) -> Parsed:
    """The inverse of ``encode``; cached bytes are untrusted, so anything ``parse``
    could not have produced raises ``ValueError`` and the caller parses instead."""
    try:
        stored, d, uses, diagnostics, features = as_list(value)
        if stored != dialect:
            raise ValueError("facts are for a different dialect")
        parsed = Parsed(
            dialect,
            [Decl(*as_list(x)) for x in as_list(d)],
            [Use(*as_list(u)) for u in as_list(uses)],
            as_list(diagnostics),
            as_list(features),
        )
        if dialect not in _DIALECTS or not _sound(parsed, size):
            raise ValueError("parse facts are ill-typed, out of bounds or malformed")
    except (TypeError, ValueError) as error:
        raise ValueError(f"malformed parse facts: {error}") from None
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
            and d.kind in DECL_KINDS
            and type(d.name) is str
            and _NAME.fullmatch(d.name) is not None
            and span(d.start, d.end)
            and type(d.signature) is str
            and owner(d.owner, k)
            and type(d.local) is bool
            and type(d.ordinal) is int
            and 0 <= d.ordinal < 1_000_000
        ):
            return False
    for u in parsed.uses:
        if not (
            type(u.name) is str
            and span(u.start, u.end)
            and owner(u.owner, count)
            and type(u.kind) is str
            and u.kind in USE_KINDS
            and type(u.path) is str
            and _PATH.fullmatch(u.path) is not None
        ):
            return False
    return all(type(v) is str for v in (*parsed.diagnostics, *parsed.bash_features))
