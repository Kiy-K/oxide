"""Java language slice: tree-sitter syntax facts (docs/java.md); resolution is in
``java_resolve``, the facts' cache codec in ``java_facts``.

Symbols are the member types of a file and their methods, constructors and annotation
elements, nested as declared. Local and anonymous classes are not symbols: each is a
type scope without a declaration (and an enum constant's body is one too), whose
members resolution treats as unknown. A variable bound anywhere in a scope keeps its
declared type when every binding of the name agrees (``var``, primitives and arrays have
none); one compiled query collects every fact in C, and Python assigns each captured
node to its innermost scope.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import tree_sitter_java
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from oxide.core.languages.nesting import Nesting
from oxide.core.languages.python import TOO_DEEP, Use, syntax_diagnostics, too_deep

VERSION = "oxide-java-v1"
# The JSON shape of ``Parsed`` that generations retain for reuse; bump on any change.
FACTS_VERSION = "oxide-java-facts-v1"
_TYPE_KINDS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "annotation_type_declaration": "annotation",
}
TYPE_KINDS = frozenset(_TYPE_KINDS.values())
_HEADER = ("type_parameters", "superclass", "super_interfaces", "extends_interfaces", "permits")
KINDS = frozenset({*TYPE_KINDS, "method", "constructor"})
# JUnit 4/5 and TestNG test annotations, by simple name.
TEST_ANNOTATIONS = frozenset(
    {"Test", "ParameterizedTest", "RepeatedTest", "TestFactory"} | {"TestTemplate"}
)
_COMMENTS = ("line_comment", "block_comment")

LANGUAGE = Language(tree_sitter_java.language())
_QUERY = Query(
    LANGUAGE,
    f"""
    (package_declaration [(identifier) (scoped_identifier)] @package)
    (import_declaration) @import
    [{" ".join(f"({t})" for t in _TYPE_KINDS)}] @type
    [(method_declaration) (constructor_declaration) (compact_constructor_declaration)
      (annotation_type_element_declaration)] @member
    (object_creation_expression (class_body)) @anonymous
    (enum_constant body: (class_body) @constant_body)
    (enum_constant name: (identifier) @constant)
    (method_invocation) @call
    (object_creation_expression) @new
    [(type_identifier) (scoped_type_identifier)] @ref
    (marker_annotation name: _ @ref)
    (annotation name: _ @ref)
    (type_parameter (type_identifier) @type_parameter)
    [(formal_parameter) (catch_formal_parameter) (enhanced_for_statement) (resource)
      (instanceof_expression name: _) (type_pattern) (record_pattern_component)] @var
    (spread_parameter (variable_declarator name: (identifier) @untyped))
    (inferred_parameters (identifier) @untyped)
    (lambda_expression parameters: (identifier) @untyped)
    [(local_variable_declaration) (field_declaration) (constant_declaration)] @vars
    """,
)
_BROKEN = Query(LANGUAGE, "(ERROR) @error (MISSING) @missing")


@dataclass(slots=True)
class Decl:
    kind: str
    name: str
    start: int
    end: int
    signature: str
    arity: int  # declared parameters of a method or constructor; 0 for types
    varargs: bool
    test: bool  # a method with a test annotation


@dataclass(frozen=True, slots=True)
class Import:
    static: bool
    path: str  # dotted, without ``.*``
    wildcard: bool
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Call:
    """``m``, ``this.m``, ``super.m``, ``x.m``, ``this.x.m`` or ``A.B.m``; ``new T`` for an
    instance creation (a space never occurs in a Java name)."""

    name: str
    start: int
    end: int
    arity: int


@dataclass(slots=True)
class Scope:
    """A declared type or member, or (``decl`` None) a local or anonymous class body."""

    parent: int | None  # None for scope 0, the file
    decl: Decl | None
    supers: list[Use] = field(default_factory=list)  # named supertypes of a type scope
    bound: dict[str, str | None] = field(default_factory=dict)  # variable -> declared type
    types: set[str] = field(default_factory=set)  # type parameters and local class names
    calls: list[Call] = field(default_factory=list)
    refs: list[Use] = field(default_factory=list)


@dataclass(slots=True)
class Parsed:
    package: str  # "" for the default package
    imports: list[Import]
    scopes: list[Scope]
    diagnostics: list[str]


def _text(data: bytes, node: Node | None) -> str:
    return "" if node is None else data[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _dotted(data: bytes, node: Node | None) -> str:
    return "".join(_text(data, node).split())


def _named(node: Node | None) -> list[Node]:
    return [] if node is None else [c for c in node.named_children if c.type not in _COMMENTS]


def type_name(data: bytes, node: Node | None) -> str | None:
    """The dotted class or interface a type node names, without type arguments; None
    for ``var``, primitives, arrays and anything else."""
    while node is not None and node.type in ("generic_type", "annotated_type"):
        named = [c for c in _named(node) if c.type != "type_arguments"]
        node = named[0 if node.type == "generic_type" else -1] if named else None
    if node is None or node.type not in ("type_identifier", "scoped_type_identifier"):
        return None
    name = _dotted(data, node)
    return None if name == "var" else name


def _annotations(node: Node) -> list[Node]:
    kinds = ("marker_annotation", "annotation")
    return [
        a for m in node.children if m.type == "modifiers" for a in m.children if a.type in kinds
    ]


def _signature(data: bytes, node: Node, end: int) -> str:
    """The header up to ``end``, without annotations, whitespace collapsed."""
    pieces, at = [], node.start_byte
    for a in _annotations(node):
        pieces.append(data[at : a.start_byte])
        at = a.end_byte
    pieces.append(data[at:end])
    return " ".join(b"".join(pieces).decode("utf-8", "replace").split()).removesuffix(";")


def _path(node: Node) -> bool:
    """``this.x`` or ``a.b.c``: a field access of identifiers, or of ``this`` and one."""
    first = True
    while node.type == "field_access":
        inner, member = node.child_by_field_name("object"), node.child_by_field_name("field")
        if inner is None or member is None or member.type != "identifier":
            return False
        if inner.type == "this":
            return first
        node, first = inner, False
    return node.type == "identifier"


def _span(node: Node) -> tuple[int, int]:
    return node.start_byte, node.end_byte


class _Facts:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.scopes = [Scope(None, None)]
        self.nesting = Nesting(len(data))
        self.headers: dict[int, list[tuple[int, int]]] = {}  # type scope -> header clause spans
        self.created: set[int] = set()  # start bytes of instance creation types

    def scope_at(self, at: int) -> int:
        return self.nesting.scope_at(at)

    def _open(self, parent: int, scope: Scope, span: Node) -> None:
        # Past the modifiers: javac resolves a declaration's annotations in the enclosing scope.
        start = next((c.start_byte for c in span.children if c.type != "modifiers"), span.end_byte)
        self.scopes.append(scope)
        self.nesting.add(parent, (start, span.end_byte))

    def _is_type(self, k: int) -> bool:
        decl = self.scopes[k].decl
        return decl is not None and decl.kind in TYPE_KINDS

    def type(self, node: Node) -> None:
        data, parent = self.data, self.scope_at(node.start_byte)
        name, body = node.child_by_field_name("name"), node.child_by_field_name("body")
        if name is None or body is None:
            return
        supers = []
        # javac resolves these in the enclosing scope (a record's components see its members).
        header = [_span(c) for c in node.children if c.type in _HEADER]
        self.headers[len(self.scopes)] = header
        for clause in node.children:
            if clause.type == "superclass":
                listed = _named(clause)
            elif clause.type in ("super_interfaces", "extends_interfaces"):
                listed = [t for c in _named(clause) for t in _named(c)]
            else:
                continue
            for t in listed:
                if (base := type_name(data, t)) is not None:
                    supers.append(Use(base, *_span(t)))
        if parent and not self._is_type(parent):  # a local class
            self.scopes[parent].types.add(_text(data, name))
            self._open(parent, Scope(parent, None, supers), node)
            return
        signature = _signature(data, node, body.start_byte)
        kind = _TYPE_KINDS[node.type]
        decl = Decl(kind, _text(data, name), *_span(node), signature, 0, False, False)
        self._open(parent, Scope(parent, decl, supers), node)

    def member(self, node: Node) -> None:
        data, parent = self.data, self.scope_at(node.start_byte)
        name = node.child_by_field_name("name")
        if name is None or not self._is_type(parent):
            return  # a member of a local or anonymous class: part of that scope
        body = node.child_by_field_name("body")
        params = [
            p
            for p in _named(node.child_by_field_name("parameters"))
            if p.type in ("formal_parameter", "spread_parameter")
        ]
        method = node.type in ("method_declaration", "annotation_type_element_declaration")
        decl = Decl(
            "method" if method else "constructor",
            _text(data, name),
            *_span(node),
            _signature(data, node, body.start_byte if body else node.end_byte),
            len(params),
            any(p.type == "spread_parameter" for p in params),
            method
            and any(
                _dotted(data, a.child_by_field_name("name")).rpartition(".")[2] in TEST_ANNOTATIONS
                for a in _annotations(node)
            ),
        )
        self._open(parent, Scope(parent, decl), node)

    def anonymous(self, body: Node, base: str | None) -> None:
        parent = self.scope_at(body.start_byte)
        supers = [Use(base, body.start_byte, body.start_byte)] if base else []
        self._open(parent, Scope(parent, None, supers), body)

    def bind(self, name: Node | None, kind: str | None) -> None:
        if name is None:
            return
        bound, text = self.scopes[self.scope_at(name.start_byte)].bound, _text(self.data, name)
        bound[text] = kind if bound.get(text, kind) == kind else None

    def variable(self, node: Node) -> None:
        data = self.data
        if node.type in ("type_pattern", "record_pattern_component"):
            parts = _named(node)
            if len(parts) == 2 and parts[1].type == "identifier":
                self.bind(parts[1], type_name(data, parts[0]))
            return
        name = node.child_by_field_name("name")
        if node.type == "catch_formal_parameter":
            caught = next((c for c in node.named_children if c.type == "catch_type"), None)
            types = _named(caught)
            self.bind(name, type_name(data, types[0]) if len(types) == 1 else None)
            return
        kind = node.child_by_field_name("right" if node.type == "instanceof_expression" else "type")
        array = node.child_by_field_name("dimensions") is not None
        self.bind(name, None if array else type_name(data, kind))

    def variables(self, node: Node) -> None:
        kind = type_name(self.data, node.child_by_field_name("type"))
        for declarator in node.children_by_field_name("declarator"):
            array = declarator.child_by_field_name("dimensions") is not None
            self.bind(declarator.child_by_field_name("name"), None if array else kind)

    def call(self, node: Node) -> None:
        data, name = self.data, node.child_by_field_name("name")
        receiver = node.child_by_field_name("object")
        if name is None:
            return
        if receiver is None:
            form = ""
        elif receiver.type in ("this", "super", "identifier") or _path(receiver):
            form = _dotted(data, receiver) + "."
        else:
            return  # a call on an expression's value: needs its type
        arity = len(_named(node.child_by_field_name("arguments")))
        self.use(Call(form + _text(data, name), node.start_byte, name.end_byte, arity))

    def new(self, node: Node) -> None:
        kind = node.child_by_field_name("type")
        if kind is None or not node.children or node.children[0].type != "new":
            return  # ``outer.new Inner()``: needs the outer value's type
        base = type_name(self.data, kind)
        if base is not None:
            self.created.add(kind.start_byte)
            arity = len(_named(node.child_by_field_name("arguments")))
            self.use(Call(f"new {base}", node.start_byte, kind.end_byte, arity))

    def use(self, call: Call) -> None:
        self.scopes[self.scope_at(call.start)].calls.append(call)


def _body(node: Node, what: str) -> Node:
    """The node a scope spans: an anonymous class's body, else the declaration."""
    if what != "anonymous":
        return node
    return next(c for c in reversed(node.named_children) if c.type == "class_body")


def parse(data: bytes) -> Parsed | str:
    """Syntax facts, or the reason the file is unsupported."""
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "Java source is not UTF-8"
    root = Parser(LANGUAGE).parse(data).root_node
    if too_deep(root):
        return TOO_DEEP
    captures = QueryCursor(_QUERY).captures(root)
    facts = _Facts(data)
    openers = ("type", "member", "anonymous", "constant_body")
    opening = [(_body(n, what), n, what) for what in openers for n in captures.get(what, [])]
    for body, node, what in sorted(opening, key=lambda o: o[0].start_byte):  # parents first
        if what == "type":
            facts.type(node)
        elif what == "member":
            facts.member(node)
        elif what == "anonymous":
            facts.anonymous(body, type_name(data, node.child_by_field_name("type")))
        else:
            enum = facts.scopes[facts.scope_at(node.start_byte)].decl
            facts.anonymous(node, enum.name if enum is not None else None)
    scopes = facts.scopes
    for node in captures.get("type_parameter", []):
        scopes[facts.scope_at(node.start_byte)].types.add(_text(data, node))
    for node in captures.get("constant", []):
        enum = scopes[facts.scope_at(node.start_byte)].decl
        facts.bind(node, enum.name if enum is not None else None)
    for node in captures.get("var", []):
        facts.variable(node)
    for node in captures.get("vars", []):
        facts.variables(node)
    for node in captures.get("untyped", []):
        facts.bind(node, None)
    for node in captures.get("call", []):
        facts.call(node)
    for node in captures.get("new", []):
        facts.new(node)
    # Type parameter declarations, and instance creations (calls of the class).
    skip = {n.start_byte for n in captures.get("type_parameter", [])} | facts.created
    reach = -1
    for node in sorted(captures.get("ref", []), key=lambda n: (n.start_byte, -n.end_byte)):
        if node.start_byte < reach:
            continue  # a part of a qualified type already seen
        reach = node.end_byte
        if node.start_byte not in skip and (name := _dotted(data, node)) != "var":
            k = facts.scope_at(node.start_byte)
            header = any(s <= node.start_byte < e for s, e in facts.headers.get(k, ()))
            if header and name.split(".")[0] not in scopes[k].types:  # but sees its type parameters
                k = scopes[k].parent or 0
            scopes[k].refs.append(Use(name, *_span(node)))
    imports = []
    for node in captures.get("import", []):
        path = next((c for c in node.named_children if c.type.endswith("identifier")), None)
        if path is not None:
            kinds = {c.type for c in node.children}
            imports.append(
                Import("static" in kinds, _dotted(data, path), "asterisk" in kinds, *_span(node))
            )
    imports.sort(key=lambda item: (item.start, item.end))
    for scope in scopes:
        scope.calls.sort(key=lambda u: (u.start, u.end))
        scope.refs.sort(key=lambda u: (u.start, u.end))
    package = min(captures.get("package", []), key=lambda n: n.start_byte, default=None)
    return Parsed(_dotted(data, package), imports, scopes, syntax_diagnostics(root, _BROKEN))
