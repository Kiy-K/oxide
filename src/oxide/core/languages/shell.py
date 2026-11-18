"""Bash / POSIX sh language slice: tree-sitter syntax facts (docs/shell.md).

One grammar (Bash, a superset of POSIX sh) parses both dialects; the shebang or
filename decides the dialect and Bash-only syntax in a POSIX file is reported as
a diagnostic. Resolution lives in ``shell_resolve``; the codec in ``shell_facts``.
"""

from __future__ import annotations

import tree_sitter_bash
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from oxide.core.languages.python import TOO_DEEP, syntax_diagnostics, too_deep
from oxide.core.languages.shell_model import (
    DECLARATIONS,
    INTERPRETERS,
    Decl,
    Parsed,
    ShellDialect,
    Use,
)
from oxide.core.languages.shell_syntax import (
    bash_features,
    detect,
    has_expansion,
    is_dynamic,
    literal,
    named,
    span,
    static_path,
    text,
    variable,
)

__all__ = ["BASH_FACTS_VERSION", "SH_FACTS_VERSION", "VERSION", "Parsed", "detect", "parse"]

VERSION = "oxide-shell-v1"
BASH_FACTS_VERSION = "oxide-shell-facts-v1"
SH_FACTS_VERSION = "oxide-shell-facts-v1-sh"
LANGUAGE = Language(tree_sitter_bash.language())

_QUERY = Query(
    LANGUAGE,
    """
    (function_definition) @function
    (variable_assignment) @assign
    (declaration_command) @declare
    (command) @command
    (simple_expansion) @expand
    (expansion) @expand
    """,
)
_BROKEN = Query(LANGUAGE, "(ERROR) @error (MISSING) @missing")


def _name(data: bytes, node: Node | None) -> str | None:
    if node is None:
        return None
    if node.type == "subscript":
        node = next((c for c in named(node) if c.type == "variable_name"), None)
    return text(data, node) if node is not None and node.type == "variable_name" else None


def _function_name(data: bytes, node: Node) -> str | None:
    name = node.child_by_field_name("name")
    return text(data, name) if name is not None and name.type == "word" else None


def _signature(data: bytes, node: Node, name: str) -> str:
    if node.type == "function_definition":
        body = node.child_by_field_name("body")
        head = data[node.start_byte : (body.start_byte if body else node.end_byte)]
        return " ".join(head.decode("utf-8", "replace").split()).strip()
    return f"{name}=" + text(data, node).partition("=")[2][:80]


def _command(data: bytes, node: Node, owner: int | None) -> list[Use]:
    name_node = node.child_by_field_name("name")
    command = literal(data, next(iter(named(name_node)), None))
    if command and (command.startswith("@") or command == "}"):
        return []  # Bats ``@test`` and a closing ``}`` are syntax, not commands
    # ``Node`` wrappers are recreated on each access, so compare byte ranges.
    args = [
        c
        for c in named(node)
        if name_node is None
        or (c.start_byte, c.end_byte) != (name_node.start_byte, name_node.end_byte)
    ]
    start, end = span(node)
    if command in ("source", "."):
        if not args:
            return []
        written = text(data, args[0])
        path = static_path(data, args[0])
        kind = "source" if path else "dynamic_source"
        return [Use(written, start, end, owner, kind, path or "")]
    if command in INTERPRETERS:
        for arg in args:
            value = literal(data, arg)
            if value is None:
                if is_dynamic(arg) or has_expansion(arg):
                    return [Use(text(data, arg), start, end, owner, "dynamic_script", "")]
                continue
            if value in ("-c", "-s"):  # inline command string, not a script path
                return []
            if value.startswith("-"):
                continue
            path = static_path(data, arg)
            return [
                Use(
                    text(data, arg),
                    start,
                    end,
                    owner,
                    "script" if path else "dynamic_script",
                    path or "",
                )
            ]
        return []
    if command is None:
        written = text(data, name_node)
        # ``}`` and ``@test`` (Bats) are syntax, not command names.
        if not written or written[0] in "@}":
            return []
        return [Use(written, start, end, owner, "dynamic_command", "")]
    path = static_path(data, next(iter(named(name_node)), None))
    kind = "script" if path else "command"
    return [Use(command, start, end, owner, kind, path or "")]


def parse(data: bytes, dialect: ShellDialect = "bash") -> Parsed | str:
    """Syntax facts, or the reason the file is unsupported."""
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "Shell source is not UTF-8"
    root = Parser(LANGUAGE).parse(data).root_node
    if too_deep(root):
        return TOO_DEEP
    captures = QueryCursor(_QUERY).captures(root)
    decls: list[Decl] = []
    spans: list[tuple[int, int, int]] = []
    ordinals: dict[tuple[str, int | None], int] = {}

    def declare(kind: str, node: Node, name: str, owner: int | None, local: bool = False) -> None:
        key = (name, owner)
        ordinal = ordinals.get(key, 0)
        ordinals[key] = ordinal + 1
        decls.append(
            Decl(kind, name, *span(node), _signature(data, node, name), owner, local, ordinal)
        )
        if kind == "function":
            spans.append((node.start_byte, node.end_byte, len(decls) - 1))

    def owner_at(at: int) -> int | None:
        for start, end, k in reversed(spans):
            if start <= at < end:
                return k
        return None

    for node in sorted(captures.get("function", []), key=lambda n: n.start_byte):
        name = _function_name(data, node)
        if name:
            declare("function", node, name, None)

    for node in sorted(captures.get("assign", []), key=lambda n: n.start_byte):
        if node.parent is not None and node.parent.type == "declaration_command":
            continue
        name = _name(data, node.child_by_field_name("name"))
        if name:
            # A plain assignment is global in shell even inside a function.
            declare("variable", node, name, None)

    for node in sorted(captures.get("declare", []), key=lambda n: n.start_byte):
        children = list(node.children)
        keyword = text(data, children[0]) if children else ""
        if keyword not in DECLARATIONS:
            continue
        for child in children:
            local = keyword == "local"
            owner = owner_at(child.start_byte) if local else None
            if child.type == "variable_assignment":
                name = _name(data, child.child_by_field_name("name"))
                if name:
                    declare("variable", child, name, owner, local)
            elif child.type == "variable_name":
                name = text(data, child)
                declare("variable", child, name, owner, local)

    uses: list[Use] = []
    for node in captures.get("command", []):
        uses.extend(_command(data, node, owner_at(node.start_byte)))
    for node in captures.get("expand", []):
        name = variable(data, node)
        if name is not None:
            uses.append(Use(name, *span(node), owner_at(node.start_byte), "variable", ""))

    diagnostics = syntax_diagnostics(root, _BROKEN)
    if dialect == "sh" and (features := bash_features(data, root)):
        diagnostics.append("Bash-only syntax in POSIX sh: " + ", ".join(features))
    return Parsed(dialect, decls, sorted(uses, key=lambda u: (u.start, u.end)), diagnostics, [])
