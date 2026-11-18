"""Node-level helpers for the shell syntax facts (``shell``)."""

from __future__ import annotations

from tree_sitter import Node

from oxide.core.languages.shell_model import ShellDialect

_BASH_TOKENS = {
    "[[": "[[ ]] test",
    "((": "(( )) arithmetic",
    "function": "function keyword",
    "select": "select loop",
    "declare": "declare",
    "local": "local",
    "typeset": "typeset",
    "coproc": "coproc",
    "<(": "process substitution",
    ">(": "process substitution",
}
_BASH_NODES = {
    "array": "array",
    "subscript": "array subscript",
    "process_substitution": "process substitution",
    "regex": "[[ =~ ]] regex",
    "postfix_expression": "C-style arithmetic",
    "c_style_for_statement": "C-style for loop",
}
_DYNAMIC_NODES = frozenset(
    {
        "concatenation",
        "simple_expansion",
        "expansion",
        "command_substitution",
        "process_substitution",
        "arithmetic_expansion",
        "brace_expression",
        "translated_string",
        "ansi_c_string",
        "array",
        "subscript",
    }
)


def text(data: bytes, node: Node | None) -> str:
    return "" if node is None else data[node.start_byte : node.end_byte].decode("utf-8", "replace")


def span(node: Node) -> tuple[int, int]:
    return node.start_byte, node.end_byte


def named(node: Node | None) -> list[Node]:
    return [] if node is None else list(node.named_children)


def literal(data: bytes, node: Node | None) -> str | None:
    """The statically known text of a word, or None when expansion could change it.

    There is no ``literal`` node in tree-sitter-bash: ``word`` and ``raw_string``
    are leaf tokens and a double-quoted ``string`` is only literal when it holds
    a single ``string_content`` and no expansions.
    """
    if node is None:
        return None
    if node.type == "word":
        return text(data, node)
    if node.type == "raw_string" and node.child_count == 0:
        value = text(data, node)
        return value[1:-1] if len(value) >= 2 else None
    if node.type == "number":
        return text(data, node)
    if node.type == "string":
        parts = named(node)
        if len(parts) == 1 and parts[0].type == "string_content":
            return text(data, parts[0])
    return None


def static_path(data: bytes, node: Node | None) -> str | None:
    """A literal path that is relative to the script and contains a slash.

    A slashless name is looked up in PATH at runtime, and an absolute path cannot
    be shown to name a captured file without knowing the repository's location,
    so neither is statically determinable.
    """
    value = literal(data, node)
    if value is None or "/" not in value or value.startswith("/"):
        return None
    return value


def variable(data: bytes, node: Node | None) -> str | None:
    """The plain name a ``$x`` or ``${x}`` expansion reads, else None."""
    if node is None or node.type not in ("simple_expansion", "expansion"):
        return None
    if node.type == "expansion" and node.child_by_field_name("operator") is not None:
        return None
    parts = [c for c in named(node) if c.type == "variable_name"]
    if len(parts) != 1 or len(named(node)) != 1:
        return None
    value = text(data, parts[0])
    # Positional parameters ($1, $2, …) are variable_name nodes but never a
    # statically named variable, so they are excluded.
    return value if value and not value.isdigit() else None


def is_dynamic(node: Node | None) -> bool:
    return node is not None and node.type in _DYNAMIC_NODES


def has_expansion(node: Node | None) -> bool:
    """Whether any descendant can change at runtime (a quoted string included)."""
    if node is None:
        return False
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in _DYNAMIC_NODES:
            return True
        stack.extend(current.children)
    return False


def bash_features(data: bytes, root: Node) -> list[str]:
    """Bash-only constructs used, for classifying a POSIX-classified file."""
    found: set[str] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in _BASH_NODES:
            found.add(_BASH_NODES[node.type])
        elif not node.is_named and node.type in _BASH_TOKENS:
            found.add(_BASH_TOKENS[node.type])
        elif node.type in ("expansion",):
            if any(not c.is_named and c.type in ("/", "//", "^^", ",,") for c in node.children):
                found.add("bash parameter expansion")
        elif (
            node.type == "command"
            and (c := node.child_by_field_name("name")) is not None
            and literal(data, next(iter(named(c)), None)) == "source"
        ):
            found.add("source (Bash)")
        stack.extend(node.children)
    return sorted(found)


def detect(path: str, data: bytes) -> ShellDialect | None:
    """Dialect from a shebang or a shell filename; None when the file is not shell."""
    head = data.split(b"\n", 1)[0]
    if head.startswith(b"#!"):
        interp = " ".join(head[2:].decode("utf-8", "replace").split()).lower()
        if "bash" in interp:
            return "bash"
        if any(
            f"/{s}" in interp or interp.endswith(" " + s) or interp.endswith("/" + s)
            for s in ("sh", "dash", "ash", "ksh")
        ):
            return "sh"
    name = path.rsplit("/", 1)[-1]
    if name.endswith((".bash", ".bashrc", ".bash_profile", ".bash_login", ".bash_logout", ".bats")):
        return "bash"
    if name.endswith(".sh"):
        return "sh"
    return None
