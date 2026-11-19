"""PHP language slice: tree-sitter syntax facts (docs/php.md).

The full grammar (``language_php``) parses inline HTML as ``text`` nodes, so PHP-only
and mixed PHP/HTML files share one extraction path. Resolution lives in ``php_resolve``;
the cache codec in ``php_facts``.
"""

from __future__ import annotations

import tree_sitter_php
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from oxide.core.languages.php_model import Call, Decl, Import, Parsed, Use, ns_at
from oxide.core.languages.php_syntax import field, ident, named, php_name, span, text
from oxide.core.languages.python import TOO_DEEP, syntax_diagnostics, too_deep

VERSION = "oxide-php-v1"
FACTS_VERSION = "oxide-php-facts-v3"
LANGUAGE = Language(tree_sitter_php.language_php())

_KINDS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "trait_declaration": "trait",
    "enum_declaration": "enum",
}
# A callee whose name is not written as a static identifier cannot be resolved from
# syntax alone; these are named built-ins that take a callable, which are also skipped.
_DYNAMIC_FUNCTIONS = frozenset(
    {
        "call_user_func",
        "call_user_func_array",
        "forward_static_call",
        "forward_static_call_array",
        "is_callable",
    }
)
_QUERY = Query(
    LANGUAGE,
    """
    (namespace_definition) @ns
    [ (class_declaration) (interface_declaration) (trait_declaration) (enum_declaration) ] @type
    (function_definition) @function
    (method_declaration) @method
    (const_declaration (const_element) @const)
    (enum_case) @case
    (property_declaration (property_element) @property)
    (namespace_use_declaration) @import
    (base_clause (_) @base)
    (class_interface_clause (_) @base)
    (use_declaration) @trait
    (function_call_expression) @call
    (scoped_call_expression) @call
    (member_call_expression) @call
    (nullsafe_member_call_expression) @call
    (object_creation_expression) @call
    (named_type [(name) (qualified_name)] @ref)
    """,
)
_BROKEN = Query(LANGUAGE, "(ERROR) @error (MISSING) @missing")


def _namespaces(data: bytes, nodes: list[Node]) -> list[tuple[int, int, str]]:
    """Namespace regions. An unbracketed ``namespace N;`` governs the rest of the file,
    not just its own node; a bracketed one covers exactly its body."""
    ordered = sorted(nodes, key=lambda n: n.start_byte)
    regions: list[tuple[int, int, str]] = []
    for i, node in enumerate(ordered):
        name = php_name(data, field(node, "name")).replace("\\", ".")
        if field(node, "body") is not None:
            regions.append((node.start_byte, node.end_byte, name))
        else:
            end = ordered[i + 1].start_byte if i + 1 < len(ordered) else len(data)
            regions.append((node.end_byte, end, name))
    return sorted(regions)


def _signature(data: bytes, node: Node) -> str:
    head = text(data, node)
    body = next(
        (
            c
            for c in node.children
            if c.type in ("compound_statement", "declaration_list", "enum_declaration_list")
        ),
        None,
    )
    if body is not None:
        head = head[: body.start_byte - node.start_byte]
    return " ".join(head.replace(";", " ").split()).strip()


def _import(data: bytes, node: Node, ns: str) -> list[Import]:
    raw = text(data, node)
    kind = (
        "function"
        if raw.startswith("use function")
        else "const"
        if raw.startswith("use const")
        else "class"
    )
    group = next((c for c in node.children if c.type == "namespace_use_group"), None)
    prefix = ""
    for c in node.named_children:
        if c.type == "namespace_name" and c.start_byte < (
            group.start_byte if group else node.end_byte
        ):
            prefix = php_name(data, c)
    clauses = [c for c in node.named_children if c.type == "namespace_use_clause"]
    if group is not None:
        prefix = php_name(data, field(node, "name")) or prefix
        clauses = [c for c in group.named_children if c.type == "namespace_use_clause"]
    out: list[Import] = []
    for clause in clauses:
        path = php_name(data, next(iter(named(clause)), None))
        if prefix and not path.startswith("\\"):
            path = f"{prefix}\\{path}"
        alias = ident(data, field(clause, "alias"))
        s, e = span(node) if group is None else span(clause)
        out.append(Import(kind, path, alias, ns, s, e))
    return out


def _call(data: bytes, node: Node, owner: int | None) -> Call | None:
    s, e = span(node)
    if node.type == "function_call_expression":
        fun = field(node, "function")
        if fun is None or fun.type not in ("name", "relative_name", "qualified_name"):
            return None
        name = php_name(data, fun)
        if name.lstrip("\\").lower() in _DYNAMIC_FUNCTIONS:
            return None  # dynamic dispatch, even written ``\call_user_func``
        return Call(name, s, e, owner, "function")
    if node.type == "scoped_call_expression":
        scope, member = field(node, "scope"), field(node, "name")
        if (
            member is None
            or member.type != "name"
            or scope is None
            or scope.type not in ("name", "qualified_name", "relative_name", "relative_scope")
        ):
            return None
        prefix = php_name(data, scope)
        if not prefix:
            return None
        return Call(f"{prefix}::{php_name(data, member)}", s, e, owner, "static")
    if node.type in ("member_call_expression", "nullsafe_member_call_expression"):
        obj, member = field(node, "object"), field(node, "name")
        if member is None or member.type != "name" or obj is None or obj.type != "variable_name":
            return None
        if php_name(data, obj) != "$this":
            return None
        return Call(f"$this->{php_name(data, member)}", s, e, owner, "method")
    created = next((c for c in named(node) if c.type in ("name", "qualified_name")), None)
    if created is None:
        return None
    return Call(php_name(data, created), s, e, owner, "new")


def parse(data: bytes) -> Parsed | str:
    """Syntax facts, or the reason the file is unsupported."""
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "PHP source is not UTF-8"
    root = Parser(LANGUAGE).parse(data).root_node
    if too_deep(root):
        return TOO_DEEP
    captures = QueryCursor(_QUERY).captures(root)
    namespaces = _namespaces(data, captures.get("ns", []))
    decls: list[Decl] = []
    spans: list[tuple[int, int, int]] = []
    ordinals: dict[tuple[str, int | None], int] = {}

    def owner(at: int) -> int | None:
        for start, end, k in reversed(spans):
            if start <= at < end:
                return k
        return None

    def declare(kind: str, node: Node, name: str) -> None:
        parent = owner(node.start_byte)
        ident_key = (name, parent)
        ordinal = ordinals.get(ident_key, 0)
        ordinals[ident_key] = ordinal + 1
        decls.append(
            Decl(
                kind,
                name,
                *span(node),
                _signature(data, node),
                ns_at(namespaces, node.start_byte),
                parent,
                ordinal,
            )
        )
        spans.append((node.start_byte, node.end_byte, len(decls) - 1))

    opening = [
        (n, what)
        for what in ("type", "function", "method", "const", "case", "property")
        for n in captures.get(what, [])
    ]
    for node, what in sorted(opening, key=lambda o: (o[0].start_byte, -o[0].end_byte)):
        if what == "type":
            name_node = field(node, "name")
            declare(_KINDS[node.type], node, ident(data, name_node))
        elif what in ("function", "method"):
            declare(what, node, ident(data, field(node, "name")))
        elif what == "case":
            declare("case", node, ident(data, field(node, "name")))
        else:
            declare(
                "constant" if what == "const" else what,
                node,
                ident(data, field(node, "name") or next(iter(named(node)), None)).removeprefix("$"),
            )
    imports = sorted(
        (
            item
            for n in captures.get("import", [])
            for item in _import(data, n, ns_at(namespaces, n.start_byte))
        ),
        key=lambda i: (i.start, i.end),
    )
    bases: list[Use] = []
    for n in captures.get("base", []):
        bases.append(
            Use(
                php_name(data, n),
                *span(n),
                owner(n.start_byte),
                "extends" if n.parent and n.parent.type == "base_clause" else "implements",
            )
        )
    for n in captures.get("trait", []):
        # ``use T1, T2;`` and ``use A\{T1, T2};`` list several names in one statement.
        for name in named(n):
            if name.type in ("name", "qualified_name"):
                bases.append(Use(php_name(data, name), *span(name), owner(n.start_byte), "trait"))
    calls = sorted(
        (
            c
            for n in captures.get("call", [])
            if (c := _call(data, n, owner(n.start_byte))) is not None
        ),
        key=lambda c: (c.start, c.end),
    )
    refs = sorted(
        (
            Use(php_name(data, n), *span(n), owner(n.start_byte), "")
            for n in captures.get("ref", [])
        ),
        key=lambda u: (u.start, u.end),
    )
    return Parsed(decls, imports, bases, refs, calls, syntax_diagnostics(root, _BROKEN), namespaces)
