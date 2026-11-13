"""Validation of caller-supplied arguments for ``oxide.api``."""

from __future__ import annotations

from collections.abc import Iterable

from oxide.core.retrieval import Hints
from oxide.models import InvalidArgument

MAX_OFFSET = (1 << 63) - 1  # LadybugDB INT64


def count(name: str, value: object, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= high:
        raise InvalidArgument(f"{name} must be an integer in [0, {high}], got {value!r}")
    return value


def texts(name: str, values: Iterable[str]) -> tuple[str, ...]:
    try:
        out = () if isinstance(values, str) else tuple(values)
    except TypeError:
        out = None
    if out is None or isinstance(values, str) or not all(isinstance(v, str) for v in out):
        raise InvalidArgument(f"{name} must be an iterable of strings, got {values!r}")
    return out


def hints(
    query: str,
    paths: Iterable[str],
    symbols: Iterable[str],
    selection: tuple[str, int, int] | None,
    changed: Iterable[str],
) -> Hints:
    if not isinstance(query, str):
        raise InvalidArgument(f"query must be a string, got {query!r}")
    if selection is not None:
        if not (isinstance(selection, tuple) and len(selection) == 3):
            raise InvalidArgument(f"selection must be (path, start, end), got {selection!r}")
        path, start, end = selection
        texts("selection path", [path])
        if count("selection start", start, MAX_OFFSET) > count("selection end", end, MAX_OFFSET):
            raise InvalidArgument(f"selection start {start} is after its end {end}")
    seed_paths = texts("paths", paths)
    seed_symbols = texts("symbols", symbols)
    seed_changed = texts("changed", changed)
    if not query.strip() and not (selection or seed_paths or seed_symbols or seed_changed):
        raise InvalidArgument("query is empty: give words or identifiers, or a path or symbol hint")
    return Hints(selection, seed_paths, seed_symbols, seed_changed)
