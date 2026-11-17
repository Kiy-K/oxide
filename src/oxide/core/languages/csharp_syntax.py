"""Node-level helpers of the C# syntax facts (``csharp``)."""

from __future__ import annotations

from tree_sitter import Node

from oxide.core.languages.python import Use

COMMENTS = ("comment",)
NAMES = ("identifier", "qualified_name", "generic_name", "alias_qualified_name")
# Type syntax whose element and argument types are found by looking inside it.
WRAPPERS = ("nullable_type", "array_type", "pointer_type", "ref_type", "tuple_type")


def text(data: bytes, node: Node | None) -> str:
    return "" if node is None else data[node.start_byte : node.end_byte].decode("utf-8", "replace")


def id(data: bytes, node: Node | None) -> str:
    """An identifier without its verbatim ``@``."""
    return text(data, node).removeprefix("@")


def named(node: Node | None) -> list[Node]:
    return [] if node is None else [c for c in node.named_children if c.type not in COMMENTS]


def dotted(data: bytes, node: Node | None) -> str | None:
    """The dotted name of a name node, without type arguments and ``global::``."""
    if node is None:
        return None
    if node.type == "identifier":
        return id(data, node)
    if node.type == "generic_name":
        return dotted(data, node.children[0])
    if node.type == "alias_qualified_name":
        return dotted(data, node.child_by_field_name("name"))
    if node.type == "qualified_name":
        left = dotted(data, node.child_by_field_name("qualifier"))
        right = dotted(data, node.child_by_field_name("name"))
        return None if left is None or right is None else f"{left}.{right}"
    return None


def type_name(data: bytes, node: Node | None) -> str | None:
    """The dotted type a type node names, without type arguments; None for ``var``,
    primitives, arrays, tuples and anything else."""
    while node is not None and node.type in ("nullable_type", "ref_type"):
        node = next(iter(named(node)), None)
    return dotted(data, node) if node is not None and node.type in NAMES else None


def uses(data: bytes, node: Node, out: dict[tuple[int, int], Use]) -> None:
    """Every named type in type syntax ``node``, type arguments included."""
    if node.type in NAMES:
        if (name := dotted(data, node)) is not None:
            out[node.start_byte, node.end_byte] = Use(name, node.start_byte, node.end_byte)
        stack = [node]
        while stack:  # the type arguments of any segment of a qualified name
            for c in stack.pop().named_children:
                if c.type == "type_argument_list":
                    for arg in named(c):
                        uses(data, arg, out)
                elif c.type in NAMES:
                    stack.append(c)
    elif node.type in WRAPPERS or node.type == "tuple_element":
        for c in named(node):
            uses(data, c, out)


def attributes(node: Node) -> list[Node]:
    return [a for lst in node.children if lst.type == "attribute_list" for a in named(lst)]


def signature(data: bytes, node: Node, end: int) -> str:
    """The header up to ``end``, without attributes, whitespace collapsed."""
    pieces, at = [], node.start_byte
    for lst in node.children:
        if lst.type == "attribute_list":
            pieces.append(data[at : lst.start_byte])
            at = lst.end_byte
    pieces.append(data[at:end])
    text = " ".join(b"".join(pieces).decode("utf-8", "replace").split())
    return text.removesuffix(";").rstrip(" =").removesuffix("=>").rstrip()


def span(node: Node) -> tuple[int, int]:
    return node.start_byte, node.end_byte


def expression(data: bytes, node: Node | None) -> str | None:
    """``this``, ``base``, ``a.b.c``, ``this.x`` or a type's name as an expression; None
    for anything whose value's type would be needed."""
    if node is None:
        return None
    if node.type in ("this", "base"):
        return node.type
    if node.type == "member_access_expression":
        member = node.child_by_field_name("name")
        inner = node.child_by_field_name("expression")
        if inner is None:
            first = node.children[0].type if node.children else ""
            head = first if first in ("this", "base") else None
        else:
            head = expression(data, inner)
        tail = dotted(data, member)
        return None if head is None or tail is None else f"{head}.{tail}"
    return dotted(data, node) if node.type in NAMES else None


def namespace(data: bytes, node: Node, file_scoped: tuple[str, int] | None) -> str:
    """The namespace ``node`` stands in: enclosing blocks, and the file-scoped one."""
    parts: list[str] = []
    parent = node.parent
    while parent is not None:
        if parent.type == "namespace_declaration":
            parts.append(dotted(data, parent.child_by_field_name("name")) or "")
        parent = parent.parent
    if file_scoped is not None and node.start_byte >= file_scoped[1]:
        parts.append(file_scoped[0])
    return ".".join(p for p in reversed(parts) if p)


def has_default(parameter: Node) -> bool:
    """A parameter with ``= value``: an expression follows its name."""
    name = parameter.child_by_field_name("name")
    return name is not None and any(
        c.start_byte > name.end_byte and c.is_named for c in parameter.children
    )
