"""Unexpanded-macro idioms the C++ grammar cannot parse (docs/cpp.md), and the C++
``.h`` test. ``masked`` rewrites them in place for the parser only: every offset is
kept and all texts and spans still come from the original bytes."""

from __future__ import annotations

import re

from oxide.core.languages.c import _LEXEMES, _blank

# ``class API Name : Base {``: an export macro between the key and the name breaks the
# grammar, so the parser sees an all-caps word there as spaces (offsets kept).
_EXPORT = re.compile(
    rb"\b(?:class|struct|union)\s+([A-Z][A-Z0-9_]*(?:\([^()]*\))?)\s+(?=[A-Za-z_]\w*\s*(?:final\s*)?"
    rb"(?:\{|:(?!:)\s*[A-Za-z_]))"
)
# A line holding only an all-caps macro or macro call (``FMT_BEGIN_NAMESPACE``,
# ``Q_OBJECT``, ``FMT_PRAGMA_GCC(...)``) is one the grammar cannot place; the parser sees
# it as spaces.
_LONE = re.compile(
    rb"(?m)^[ \t]*([A-Z][A-Z0-9]*_[A-Z0-9_]*(?:\([^()\n]*(?:\([^()\n]*\)[^()\n]*)*\))?)"
    rb"[ \t]*(?://[^\n]*)?$"
)
# ``FMT_CONSTEXPR inline auto f()``, ``FMT_NO_UNIQUE_ADDRESS Alloc a;``, ``FMT_API
# Name(...)``: an all-caps specifier macro before a keyword, a type and a declared name,
# a destructor or a constructor.
_SPECIFIER = re.compile(
    rb"\b([A-Z][A-Z0-9]*_[A-Z0-9_]*)[ \t]+(?=(?:inline|static|constexpr|consteval|virtual"
    rb"|explicit|extern|friend|auto|void|bool|char|int|long|short|unsigned|signed|float"
    rb"|double|const|template|typename|class|struct|enum|union|operator|noexcept)\b"
    rb"|[A-Za-z_]\w*(?:::\w+)*(?:<[^;{}()\n]*>)?[ \t*&]+[A-Za-z_~]|~|[A-Za-z_]\w*\()"
)
# ``~E() YAML_CPP_NOEXCEPT = default;``, ``f() const LOCKS_EXCLUDED(mu) {``: an all-caps
# macro or macro call after a declarator.
_TRAILING = re.compile(
    rb"(?:(?<=\))|(?<=\bconst)|(?<=\boverride)|(?<=\bnoexcept))\s+"
    rb"([A-Z][A-Z0-9]*_[A-Z0-9_]*(?:\([^()\n]*\))?)"
    rb"(?=\s*(?:[;{=]|->|const\b|override\b|final\b))"
)
# ``template <class T, FMT_ENABLE_IF(...)>``: a macro call as a template parameter, which
# the parser sees as an unnamed ``class`` parameter of the same length.
_PARAMETER = re.compile(
    rb"(?<=[<,])\s*([A-Z][A-Z0-9]*_[A-Z0-9_]*\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\))"
    rb"(?=\s*>)"
)
# ``#if __has_include(<x.h>)``: an angle path in a condition, which the parser sees as
# the identifier `` x_h `` (the grammar takes neither it nor a string there).
_ANGLE = re.compile(rb"(?m)^([ \t]*#[ \t]*(?:el)?if\b[^\n]*?\()<([\w./+-]+)>\)")


def _angle(match: re.Match[bytes]) -> bytes:
    return match.group(1) + b" " + re.sub(rb"[./+-]", b"_", match.group(2)) + b" )"


# A ``.h`` file is C++ when, outside comments and literals, it has C++-only syntax.
_CPLUSPLUS = re.compile(
    rb"\b(?:namespace\s+\w*\s*\{|template\s*<|class\s+\w+\s*(?:final\s*)?[:{]"
    rb"|(?:public|private|protected)\s*:(?!:))|\w::~?\w"
)


def is_cplusplus(data: bytes) -> bool:
    """Whether a ``.h`` file is C++ (docs/cpp.md); C otherwise."""
    return _CPLUSPLUS.search(_LEXEMES.sub(b" ", data)) is not None


def _parameter(match: re.Match[bytes]) -> bytes:
    start, end = match.span(1)
    text = bytearray(match.group())
    call = bytes(text[start - match.start() : end - match.start()])
    shown = b"class" + bytes(b if b == 10 else 32 for b in call[5:])
    if b"\n" in call[:5]:
        shown = bytes(b if b == 10 else 32 for b in call)
    text[start - match.start() : end - match.start()] = shown
    return bytes(text)


def _hidden(match: re.Match[bytes]) -> bytes:
    start, end = match.span(1)
    text = match.group()
    return text[: start - match.start()] + b" " * (end - start) + text[end - match.start() :]


def masked(data: bytes) -> bytes:
    """What the parser reads: ``data`` with the unexpanded-macro idioms above (and block
    comments, as in C) blanked or rewritten in place, so every offset is kept."""
    seen = _LEXEMES.sub(_blank, data) if b"/*" in data else data
    for pattern in (_EXPORT, _LONE, _SPECIFIER, _TRAILING):
        seen = pattern.sub(_hidden, seen)
    while (quoted := _ANGLE.sub(_angle, seen)) != seen:  # several on one line
        seen = quoted
    return _PARAMETER.sub(_parameter, seen)
