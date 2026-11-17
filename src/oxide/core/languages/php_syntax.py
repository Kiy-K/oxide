"""Node-level helpers for the PHP syntax facts (``php``)."""

from __future__ import annotations

from tree_sitter import Node

NAMES = frozenset({"name", "qualified_name", "namespace_name", "relative_name", "variable_name"})
COMMENTS = frozenset({"comment"})


def text(data: bytes, node: Node | None) -> str:
    return "" if node is None else data[node.start_byte : node.end_byte].decode("utf-8", "replace")


def span(node: Node) -> tuple[int, int]:
    return node.start_byte, node.end_byte


def named(node: Node | None) -> list[Node]:
    return [] if node is None else [c for c in node.named_children if c.type not in COMMENTS]


def field(node: Node | None, name: str) -> Node | None:
    return None if node is None else node.child_by_field_name(name)


def ident(data: bytes, node: Node | None) -> str:
    """A simple identifier without decorators, or the text of a name node."""
    if node is None:
        return ""
    if node.type == "variable_name":
        inner = field(node, "name") or next((c for c in named(node) if c.type == "name"), None)
        return "$" + (text(data, inner) or text(data, node).removeprefix("$"))
    return "".join(text(data, c) for c in named(node) if c.type == "name") or text(data, node)


def php_name(data: bytes, node: Node | None) -> str:
    """A name as written, backslash-separated, whitespace and comments removed."""
    if node is None:
        return ""
    if node.type in ("name", "variable_name"):
        return ident(data, node)
    return "".join(text(data, node).split())


def namespace(data: bytes, node: Node) -> str:
    """The dot-joined namespace of the nearest enclosing ``namespace_definition``."""
    current: Node | None = node.parent
    while current is not None:
        if current.type == "namespace_definition":
            return php_name(data, field(current, "name")).replace("\\", ".")
        current = current.parent
    return ""
