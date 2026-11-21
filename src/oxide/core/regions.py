"""Pure byte-range helpers for packing views (header and module-level regions)."""

from __future__ import annotations

import re

MAX_HEADER_LINES = 8


def header_end(data: bytes, start: int, end: int, name: str) -> int | None:
    """End of the header view: through the first line naming the entity, at most
    ``MAX_HEADER_LINES`` (the first line when none does). None when not smaller."""
    named = re.compile(rb"(?<![\w\x80-\xff])" + re.escape(name.encode()) + rb"(?![\w\x80-\xff])")
    at, first = start, None
    for line in data[start:end].splitlines(keepends=True)[:MAX_HEADER_LINES]:
        at += len(line)
        first = first or at
        if named.search(line):
            return at if at < end else None
    return first if first is not None and first < end else None


def module_regions(data: bytes, declarations: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Byte ranges of a file's module-level source: maximal runs of whole lines that
    no top-level declaration touches, from the first to the last non-blank line."""
    spans = sorted(declarations)
    regions: list[tuple[int, int]] = []
    run: tuple[int, int] | None = None
    at = d = 0
    for line in data.split(b"\n"):
        start, end = at, at + len(line)
        at = end + 1
        while d < len(spans) and spans[d][1] <= start:
            d += 1
        if d < len(spans) and spans[d][0] <= end:
            if run is not None:
                regions.append(run)
            run = None
        elif line.strip():
            run = (run[0] if run else start, end)
    return [*regions, run] if run is not None else regions
