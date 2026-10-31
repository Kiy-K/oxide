"""Run one query (queries.Q) over one file's bytes in each arm.

Every arm returns `Match(line, end_line, start, end)`: 0-based lines and a half-open
byte span (OXIDE's SourceRef unit). ast-grep reports character offsets, converted
here; `regex` reports one hit per line in line mode (as rg does) and per match start
with `multiline` (rg -U), with end == start.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from functools import cache
from typing import Any

import tree_sitter_python
import tree_sitter_typescript
from ast_grep_py import SgRoot
from tree_sitter import Language, Parser, Query, QueryCursor, Tree

from queries import Q

GRAMMARS = {
    "python": Language(tree_sitter_python.language()),
    "typescript": Language(tree_sitter_typescript.language_typescript()),
    "tsx": Language(tree_sitter_typescript.language_tsx()),
}


@dataclass(frozen=True, order=True)
class Match:
    line: int
    end_line: int
    start: int
    end: int


def grammar(path: str) -> str | None:
    if path.endswith(".py"):
        return "python"
    if path.endswith(".tsx"):
        return "tsx"
    return "typescript" if path.endswith((".ts", ".mts", ".cts")) else None


def decode(data: bytes) -> tuple[str, bool]:
    """Text for ast-grep (it takes str) and whether the bytes were valid UTF-8."""
    try:
        return data.decode("utf-8"), True
    except UnicodeDecodeError:
        return data.decode("utf-8", "replace"), False


def byte_offsets(text: str) -> list[int] | None:
    """char index -> byte offset table, or None when the text is ASCII (identity)."""
    if text.isascii():
        return None
    table, at = [], 0
    for ch in text:
        table.append(at)
        at += len(ch.encode("utf-8", "surrogatepass"))
    table.append(at)
    return table


def astgrep_root(text: str, lang: str) -> Any:
    return SgRoot(text, lang).root()


def astgrep(
    q: Q, data: bytes, lang: str, root: Any = None, table: list[int] | None = None
) -> list[Match]:
    if root is None:
        text = decode(data)[0]
        root, table = astgrep_root(text, lang), byte_offsets(text)
    out = set()
    for node in root.find_all(q.rule):
        r = node.range()
        start, end = r.start.index, r.end.index
        if table is not None:
            start, end = table[start], table[end]
        out.add(Match(r.start.line, r.end.line, start, end))
    return sorted(out)


@cache
def _query(lang: str, source: str) -> Query:
    return Query(GRAMMARS[lang], source)


@cache
def _parser(lang: str) -> Parser:
    return Parser(GRAMMARS[lang])


def parse(data: bytes, lang: str) -> Tree:
    return _parser(lang).parse(data)


def treesitter(q: Q, data: bytes, lang: str, tree: Tree | None = None) -> list[Match]:
    tree = parse(data, lang) if tree is None else tree
    nodes = QueryCursor(_query(lang, q.ts)).captures(tree.root_node).get("m", [])
    out = set()
    for n in nodes:
        if q.check is None or q.check(n):
            out.add(Match(n.start_point[0], n.end_point[0], n.start_byte, n.end_byte))
    return sorted(out)


@cache
def _regex(pattern: str, multiline: bool) -> re.Pattern[str]:
    return re.compile(pattern, re.MULTILINE if multiline else 0)


def regex(q: Q, data: bytes, text: str | None = None) -> list[Match]:
    assert q.regex is not None
    text = decode(data)[0] if text is None else text
    rx = _regex(q.regex, q.multiline)
    if not q.multiline:
        return [Match(i, i, -1, -1) for i, line in enumerate(text.split("\n")) if rx.search(line)]
    starts = [0, *(i + 1 for i, c in enumerate(text) if c == "\n")]
    out = set()
    for m in rx.finditer(text):
        line = bisect.bisect_right(starts, m.start()) - 1
        out.add(Match(line, line, m.start(), m.start()))
    return sorted(out)
