"""Pass-through conditional-compilation macros (docs/rust.md § Macros).

Crates such as tokio wrap whole item lists in ``cfg_rt! { pub mod runtime; ... }`` and many
use ``cfg_if! { if #[cfg(unix)] { ... } else { ... } }``. Those macros emit their items
unchanged, but a macro body is a token tree, so the items (and ``mod`` declarations, which
mount whole files) would be invisible. For an item-position invocation of ``cfg_*!`` whose
contents become error-free Rust items once the macro name, delimiters and ``cfg_if``'s
``if #[cfg(..)]`` / ``else`` / branch braces are blanked, the blanks replace those tokens
with spaces. The text keeps its length, so every byte offset and every span is still the
original file's; the result is parsed instead of the original. Every other macro is opaque.
"""

from __future__ import annotations

import tree_sitter_rust
from tree_sitter import Language, Node, Parser, Query, QueryCursor

LANGUAGE = Language(tree_sitter_rust.language())
MAX_ROUNDS = 4  # nested pass-through macros
_ITEM_MACROS = Query(
    LANGUAGE,
    "[(source_file (macro_invocation) @m) (declaration_list (macro_invocation) @m)]",
)


def _blanks(data: bytes, macro: Node) -> list[tuple[int, int]] | None:
    """Byte ranges to blank so the invocation's contents become items, None when it is
    not a pass-through macro."""
    name = macro.child_by_field_name("macro")
    tree = next((c for c in macro.children if c.type == "token_tree"), None)
    if name is None or tree is None or tree.child_count < 2:
        return None
    last = data[name.start_byte : name.end_byte].rsplit(b"::", 1)[-1]
    if not last.startswith(b"cfg_"):
        return None
    blanks = [(macro.start_byte, tree.start_byte + 1), (tree.end_byte - 1, macro.end_byte)]
    if last == b"cfg_if":
        for child in tree.children[1:-1]:
            start, end = child.start_byte, child.end_byte
            text = data[start:end]
            if child.type == "token_tree" and text.startswith(b"{"):
                blanks += [(start, start + 1), (end - 1, end)]
            elif text in (b"if", b"else", b"#") or (
                child.type == "token_tree" and text.startswith(b"[")
            ):
                blanks.append((start, end))
            else:
                return None
    base = tree.start_byte + 1
    inner = bytearray(data[base : tree.end_byte - 1])
    for start, end in blanks:
        low, high = max(start - base, 0), max(min(end - base, len(inner)), 0)
        inner[low:high] = b" " * (high - low)
    return None if Parser(LANGUAGE).parse(bytes(inner)).root_node.has_error else blanks


def unwrap(data: bytes, root: Node) -> tuple[bytes, Node]:
    """``data`` with pass-through macros opened up, and its syntax tree."""
    if b"cfg_" not in data:
        return data, root
    for _ in range(MAX_ROUNDS):
        edits = [
            blank
            for macro in QueryCursor(_ITEM_MACROS).captures(root).get("m", [])
            for blank in _blanks(data, macro) or []
        ]
        if not edits:
            break
        buffer = bytearray(data)
        for start, end in edits:
            buffer[start:end] = b" " * (end - start)
        data = bytes(buffer)
        root = Parser(LANGUAGE).parse(data).root_node
    return data, root
