"""Go language slice: tree-sitter syntax facts (docs/go.md); resolution is in
``go_resolve``.

Symbols are package-level declarations, all with the file as physical parent except
interface method elements. One compiled query collects every fact in C; Python only
assigns each captured node to the package-level declaration whose span holds it.
A name bound anywhere in a declaration (parameters, ``:=``, ``var``, ``range``, ...)
is opaque in all of it: no block scoping, no type inference.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from typing import Any

import tree_sitter_go
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from oxide.core.python import TOO_DEEP, Use, as_list, syntax_diagnostics, too_deep

VERSION = "oxide-go-v1"
# The JSON shape of ``Parsed`` that generations retain for reuse; bump on any change.
FACTS_VERSION = "oxide-go-facts-v1"
KINDS = ("function", "method", "struct", "interface", "type", "constant", "variable")

LANGUAGE = Language(tree_sitter_go.language())
_NAMED = "[(type_identifier) (qualified_type)]"
_CALLEE = "[(identifier) (selector_expression operand: (identifier))]"
_QUERY = Query(
    LANGUAGE,
    f"""
    (package_clause (package_identifier) @package)
    (import_spec) @import
    (source_file [(function_declaration) (method_declaration) (type_declaration)
      (var_declaration) (const_declaration)] @decl)
    (call_expression function: {_CALLEE} @call)
    (type_conversion_expression type: {_NAMED} @call)
    (type_conversion_expression type: (generic_type type: {_NAMED} @call))
    (field_declaration !name type: {_NAMED} @ref)
    (field_declaration !name type: (generic_type type: {_NAMED} @ref))
    (type_elem . {_NAMED} @ref .)
    (type_elem . (generic_type type: {_NAMED} @ref) .)
    (composite_literal type: {_NAMED} @ref)
    (composite_literal type: (generic_type type: {_NAMED} @ref))
    (parameter_declaration name: (identifier) @bind)
    (variadic_parameter_declaration name: (identifier) @bind)
    (type_parameter_declaration name: (identifier) @bind)
    (short_var_declaration left: (expression_list (identifier) @bind))
    (range_clause left: (expression_list (identifier) @bind))
    (receive_statement left: (expression_list (identifier) @bind))
    (type_switch_statement alias: (expression_list (identifier) @bind))
    (var_spec name: (identifier) @bind)
    (const_spec name: (identifier) @bind)
    (type_spec name: (type_identifier) @bind)
    (type_alias name: (type_identifier) @bind)
    """,
)
_BROKEN = Query(LANGUAGE, "(ERROR) @error (MISSING) @missing")


@dataclass(slots=True)
class Decl:
    kind: str
    name: str
    owner: str | None  # a method's receiver base type, or an interface method's interface
    start: int
    end: int
    signature: str


@dataclass(frozen=True, slots=True)
class Import:
    alias: str | None  # a package name, "." or "_"
    path: str
    start: int
    end: int


@dataclass(slots=True)
class Scope:
    parent: int | None  # 0 for package-level declarations, an interface for its methods
    decl: Decl | None
    receiver: str | None = None  # a method's receiver parameter, unless rebound
    bound: set[str] = field(default_factory=set)
    calls: list[Use] = field(default_factory=list)
    refs: list[Use] = field(default_factory=list)


@dataclass(slots=True)
class Parsed:
    """Scope 0 is the file: package-level code outside any symbol."""

    package: str
    imports: list[Import]
    scopes: list[Scope]
    diagnostics: list[str]


def _text(data: bytes, node: Node | None) -> str:
    return "" if node is None else data[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _line(text: str) -> str:
    return text.split("\n", 1)[0].strip()


def _name(data: bytes, node: Node) -> str:
    """``x``, or ``q.x`` for a selector or qualified type; never via ``node.parent``."""
    if node.type == "selector_expression":
        left, right = node.child_by_field_name("operand"), node.child_by_field_name("field")
    elif node.type == "qualified_type":
        left, right = node.child_by_field_name("package"), node.child_by_field_name("name")
    else:
        return _text(data, node)
    return f"{_text(data, left)}.{_text(data, right)}"


def _base(data: bytes, node: Node | None) -> str | None:
    """The receiver base type: ``T`` of ``T``, ``*T``, ``T[P]`` or ``(*T)``."""
    while node is not None and node.type != "type_identifier":
        if node.type not in ("pointer_type", "generic_type", "parenthesized_type"):
            return None
        inner = node.child_by_field_name("type")
        if inner is None and node.named_children:
            inner = node.named_children[-1]
        node = inner
    return _text(data, node) if node is not None else None


class _Facts:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.scopes = [Scope(None, None)]
        self.names: set[int] = set()  # start bytes of declared symbol names

    def add(self, parent: int, decl: Decl, name: Node) -> int:
        self.scopes.append(Scope(parent, decl))
        self.names.add(name.start_byte)
        return len(self.scopes) - 1

    def function(self, node: Node) -> None:
        data = self.data
        name, body = node.child_by_field_name("name"), node.child_by_field_name("body")
        if name is None or _text(data, name) == "_":
            return
        header = data[node.start_byte : body.start_byte if body else node.end_byte]
        owner = receiver = None
        receiver_type: Node | None = None
        method = node.type == "method_declaration"
        if method:
            params = node.child_by_field_name("receiver")
            first = (
                next((p for p in params.named_children if p.type == "parameter_declaration"), None)
                if params
                else None
            )
            if first is not None:
                receiver_type = first.child_by_field_name("type")
                owner = _base(data, receiver_type)
                receiver = _text(data, first.child_by_field_name("name")) or None
        signature = header.decode("utf-8", "replace").strip()
        kind = "method" if method else "function"
        decl = Decl(kind, _text(data, name), owner, node.start_byte, node.end_byte, signature)
        scope = self.scopes[self.add(0, decl, name)]
        scope.receiver = receiver
        if receiver_type is not None:
            arguments = receiver_type.child_by_field_name("type_arguments")
            for elem in arguments.named_children if arguments is not None else []:
                scope.bound.update(
                    _text(data, child)
                    for child in elem.named_children
                    if child.type == "type_identifier"
                )

    def declaration(self, node: Node) -> None:
        """A type, var or const declaration: one symbol per declared name. A grouped
        declaration's entries get their spec's span, a single one the whole declaration."""
        keyword = node.type.split("_")[0]
        specs = [c for c in node.named_children if c.type != "comment"]
        listed = bool(specs) and specs[0].type == "var_spec_list"
        if listed:
            specs = [c for c in specs[0].named_children if c.type == "var_spec"]
        grouped = listed or any(c.type == "(" for c in node.children)
        for spec in specs:
            span = spec if grouped else node
            if keyword == "type":
                self.type_spec(spec, span)
                continue
            kind = "constant" if keyword == "const" else "variable"
            signature = f"{keyword} {_line(_text(self.data, spec))}"
            for name in spec.children_by_field_name("name"):
                if (text := _text(self.data, name)) != "_":
                    decl = Decl(kind, text, None, span.start_byte, span.end_byte, signature)
                    self.add(0, decl, name)

    def type_spec(self, spec: Node, span: Node) -> None:
        name, kind = spec.child_by_field_name("name"), spec.child_by_field_name("type")
        if name is None or kind is None or spec.type not in ("type_spec", "type_alias"):
            return
        own = _text(self.data, name)
        shape = {"struct_type": "struct", "interface_type": "interface"}.get(kind.type, "type")
        head = self.data[spec.start_byte : kind.start_byte].decode("utf-8", "replace")
        tail = shape if shape != "type" else _line(_text(self.data, kind))
        decl = Decl(shape, own, None, span.start_byte, span.end_byte, f"type {head}{tail}")
        k = self.add(0, decl, name)
        for elem in kind.named_children if shape == "interface" else []:
            method = elem.child_by_field_name("name")
            if elem.type == "method_elem" and method is not None:
                signature = _line(_text(self.data, elem))
                start, end = elem.start_byte, elem.end_byte
                self.add(
                    k, Decl("method", _text(self.data, method), own, start, end, signature), method
                )


def parse(data: bytes) -> Parsed | str:
    """Syntax facts, or the reason the file is unsupported."""
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "Go source is not UTF-8"
    root = Parser(LANGUAGE).parse(data).root_node
    if too_deep(root):
        return TOO_DEEP
    captures = QueryCursor(_QUERY).captures(root)
    facts = _Facts(data)
    for node in sorted(captures.get("decl", []), key=lambda n: n.start_byte):
        if node.type in ("function_declaration", "method_declaration"):
            facts.function(node)
        else:
            facts.declaration(node)
    scopes = facts.scopes
    # Package-level spans by start: a byte's scope is the last one starting at or before it.
    tops = sorted(
        (s.decl.start, s.decl.end, k)
        for k, s in enumerate(scopes)
        if s.parent == 0 and s.decl is not None
    )
    starts = [t[0] for t in tops]

    def scope_at(at: int) -> int:
        i = bisect.bisect_right(starts, at) - 1
        return tops[i][2] if i >= 0 and at < tops[i][1] else 0

    for node in captures.get("call", []):
        scopes[scope_at(node.start_byte)].calls.append(
            Use(_name(data, node), node.start_byte, node.end_byte)
        )
    for node in captures.get("ref", []):
        scopes[scope_at(node.start_byte)].refs.append(
            Use(_name(data, node), node.start_byte, node.end_byte)
        )
    receivers: dict[int, int] = {}
    for node in captures.get("bind", []):
        k, name = scope_at(node.start_byte), _text(data, node)
        if node.start_byte in facts.names:
            continue
        if name == scopes[k].receiver:  # the receiver itself once; again, a local
            receivers[k] = receivers.get(k, 0) + 1
            if receivers[k] == 1:
                continue
            scopes[k].receiver = None
        scopes[k].bound.add(name)
    imports = []
    for node in captures.get("import", []):
        alias, path = node.child_by_field_name("name"), node.child_by_field_name("path")
        if path is not None:
            imports.append(
                Import(_text(data, alias) or None, _text(data, path)[1:-1], *_span(node))
            )
    imports.sort(key=lambda item: (item.start, item.end))
    for scope in scopes:
        scope.calls.sort(key=lambda u: (u.start, u.end))
        scope.refs.sort(key=lambda u: (u.start, u.end))
    package = min(captures.get("package", []), key=lambda n: n.start_byte, default=None)
    return Parsed(_text(data, package), imports, scopes, syntax_diagnostics(root, _BROKEN))


def _span(node: Node) -> tuple[int, int]:
    return node.start_byte, node.end_byte


def encode(parsed: Parsed) -> list[Any]:
    scopes = [
        [
            s.parent,
            s.decl and [getattr(s.decl, f) for f in Decl.__slots__],
            s.receiver,
            sorted(s.bound),
            [[u.name, u.start, u.end] for u in s.calls],
            [[u.name, u.start, u.end] for u in s.refs],
        ]
        for s in parsed.scopes
    ]
    imports = [[i.alias, i.path, i.start, i.end] for i in parsed.imports]
    return [parsed.package, imports, scopes, parsed.diagnostics]


def decode(value: Any, size: int) -> Parsed:
    """The inverse of ``encode`` for a file of ``size`` bytes. Cached facts come from
    disk: anything ``parse`` could not have produced raises ValueError, and the caller
    parses instead."""
    try:
        package, imports, scopes, diagnostics = as_list(value)
        parsed = Parsed(
            package,
            [Import(*as_list(i)) for i in as_list(imports)],
            [
                Scope(
                    parent,
                    None if decl is None else Decl(*as_list(decl)),
                    receiver,
                    set(as_list(bound)),
                    [Use(*as_list(u)) for u in as_list(calls)],
                    [Use(*as_list(u)) for u in as_list(refs)],
                )
                for parent, decl, receiver, bound, calls, refs in map(as_list, as_list(scopes))
            ],
            as_list(diagnostics),
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"malformed parse facts: {error}") from None
    if not _sound(parsed, size):
        raise ValueError("parse facts are ill-typed, out of bounds or not a scope tree")
    return parsed


_IDENTIFIER = re.compile(r"[^\W\d]\w*")


def _sound(parsed: Parsed, size: int) -> bool:
    """What ``parse`` guarantees: in-bounds spans, string texts, a two-level scope tree
    and identifier names, so identities stay unique."""

    def span(a: Any, b: Any) -> bool:
        return type(a) is int and type(b) is int and 0 <= a <= b <= size

    def ident(v: Any) -> bool:
        return type(v) is str and _IDENTIFIER.fullmatch(v) is not None

    def texts(*values: Any) -> bool:
        return all(type(v) is str for v in values)

    scopes = parsed.scopes
    if not scopes or (scopes[0].parent, scopes[0].decl, scopes[0].receiver) != (None, None, None):
        return False
    for k, s in enumerate(scopes):
        d, uses = s.decl, (*s.calls, *s.refs)
        if k and not (
            d is not None
            and type(s.parent) is int
            and (s.parent == 0 or 0 < s.parent < k)
            and d.kind in KINDS
            and ident(d.name)
            and (d.owner is None or ident(d.owner))
            and (s.receiver is None or ident(s.receiver))
            and span(d.start, d.end)
            and texts(d.signature)
        ):
            return False
        if not (
            all(span(u.start, u.end) for u in uses) and texts(*s.bound, *(u.name for u in uses))
        ):
            return False
    package = parsed.package == "" or ident(parsed.package)  # in module keys
    return (
        package
        and texts(*parsed.diagnostics)
        and all(
            (i.alias is None or type(i.alias) is str) and texts(i.path) and span(i.start, i.end)
            for i in parsed.imports
        )
    )
