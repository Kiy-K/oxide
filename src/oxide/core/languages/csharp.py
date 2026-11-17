"""C# language slice: tree-sitter syntax facts (docs/csharp.md); resolution is in
``csharp_resolve``, the facts' cache codec in ``csharp_facts``.

Symbols are the types of a file (class, struct, interface, enum, record, delegate) and
their methods, constructors, destructors, operators, properties, indexers, events,
fields and enum members, nested as declared. Namespaces are not symbols: each type
records the namespace it is declared in, and each ``using`` the namespace block it
stands in. A variable bound anywhere in a scope keeps its declared type when every
binding of the name agrees (``var``, primitives, arrays and tuples have none); one
compiled query collects every fact in C, and Python assigns each captured node to its
innermost scope.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import tree_sitter_c_sharp
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from oxide.core.languages.csharp_syntax import (
    NAMES,
    attributes,
    dotted,
    expression,
    has_default,
    id,
    named,
    namespace,
    signature,
    span,
    text,
    type_name,
    uses,
)
from oxide.core.languages.nesting import Nesting
from oxide.core.languages.python import TOO_DEEP, Use, syntax_diagnostics, too_deep

VERSION = "oxide-csharp-v1"
# The JSON shape of ``Parsed`` that generations retain for reuse; bump on any change.
FACTS_VERSION = "oxide-csharp-facts-v1"
_TYPE_KINDS = {
    "class_declaration": "class",
    "struct_declaration": "struct",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "delegate_declaration": "delegate",
}
TYPE_KINDS = frozenset(_TYPE_KINDS.values())
MEMBER_KINDS = frozenset(
    {"method", "constructor", "destructor", "operator", "property", "indexer", "event"}
    | {"field", "enum_member"}
)
KINDS = frozenset(TYPE_KINDS | MEMBER_KINDS)
_HEADER = ("base_list", "type_parameter_list", "type_parameter_constraints_clause")
# xUnit, NUnit and MSTest test attributes, by simple name without ``Attribute``.
TEST_ATTRIBUTES = frozenset(
    {"Test", "TestCase", "TestCaseSource", "Fact", "Theory", "TestMethod", "DataTestMethod"}
    | {"SkippableFact", "SkippableTheory"}
)
_OPERATORS = {
    "+": "op_Addition", "-": "op_Subtraction", "*": "op_Multiply", "/": "op_Division",
    "%": "op_Modulus", "&": "op_BitwiseAnd", "|": "op_BitwiseOr", "^": "op_ExclusiveOr",
    "<<": "op_LeftShift", ">>": "op_RightShift", ">>>": "op_UnsignedRightShift",
    "==": "op_Equality", "!=": "op_Inequality", "<": "op_LessThan", ">": "op_GreaterThan",
    "<=": "op_LessThanOrEqual", ">=": "op_GreaterThanOrEqual", "!": "op_LogicalNot",
    "~": "op_OnesComplement", "++": "op_Increment", "--": "op_Decrement",
    "true": "op_True", "false": "op_False",
}  # fmt: skip

LANGUAGE = Language(tree_sitter_c_sharp.language())
_QUERY = Query(
    LANGUAGE,
    f"""
    (file_scoped_namespace_declaration) @fsns
    (using_directive) @using
    [{" ".join(f"({t})" for t in _TYPE_KINDS)}] @type
    [(method_declaration) (constructor_declaration) (destructor_declaration)
      (operator_declaration) (conversion_operator_declaration) (property_declaration)
      (indexer_declaration) (event_declaration) (event_field_declaration)
      (field_declaration) (enum_member_declaration)] @member
    (type_parameter name: _ @type_parameter)
    (local_function_statement name: _ @local_function)
    (invocation_expression) @call
    (object_creation_expression) @new
    (_ type: _ @tref)
    (method_declaration returns: _ @tref)
    (base_list (_) @tref)
    (type_argument_list (_) @tref)
    (as_expression right: _ @tref)
    (explicit_interface_specifier (_) @tref)
    (attribute name: _ @attribute)
    [(parameter) (declaration_pattern) (declaration_expression) (catch_declaration)
      (foreach_statement)] @var
    (implicit_parameter) @untyped
    (tuple_pattern name: _ @untyped)
    (variable_declaration) @vars
    (property_declaration) @property
    (event_declaration) @property
    (enum_member_declaration) @constant
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
    ns: str  # the namespace the declaration is in, "" for the global one
    tparams: int  # declared type parameters
    arity: int  # declared parameters of a method, constructor or delegate; 0 otherwise
    optional: int  # of those, parameters with a default value
    varargs: bool  # a trailing ``params`` parameter
    test: bool  # a method with a test attribute


@dataclass(frozen=True, slots=True)
class Import:
    """``using N;`` (``kind`` "using"), ``using static T;`` ("static") or ``using A = X;``
    ("alias", named ``alias``); ``ns`` is the namespace block it stands in."""

    kind: str
    path: str  # dotted, without type arguments
    alias: str
    ns: str
    is_global: bool
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Call:
    """``m``, ``this.m``, ``base.m``, ``x.m``, ``this.x.m`` or ``A.B.m``; ``new T`` for an
    object creation (a space never occurs in a C# name)."""

    name: str
    start: int
    end: int
    arity: int


@dataclass(slots=True)
class Scope:
    """A declared type or member; scope 0 is the file (``decl`` None)."""

    parent: int | None  # None for scope 0, the file
    decl: Decl | None
    supers: list[Use] = field(default_factory=list)  # named base types of a type scope
    bound: dict[str, str | None] = field(default_factory=dict)  # variable -> declared type
    types: set[str] = field(default_factory=set)  # type parameters and local functions
    calls: list[Call] = field(default_factory=list)
    refs: list[Use] = field(default_factory=list)
    attrs: list[Use] = field(default_factory=list)  # attribute names: ``N`` or ``NAttribute``


@dataclass(slots=True)
class Parsed:
    imports: list[Import]
    scopes: list[Scope]
    diagnostics: list[str]


class _Facts:
    def __init__(self, data: bytes, file_scoped: tuple[str, int] | None) -> None:
        self.data = data
        self.file_scoped = file_scoped
        self.scopes = [Scope(None, None)]
        self.nesting = Nesting(len(data))
        self.headers: dict[int, list[tuple[int, int]]] = {}  # type scope -> header clause spans
        self.created: set[int] = set()  # start bytes of object creation types

    def scope_at(self, at: int) -> int:
        return self.nesting.scope_at(at)

    def _open(self, parent: int, scope: Scope, node: Node, span: Node | None = None) -> None:
        # Past attributes and modifiers: a declaration's attributes resolve in the enclosing scope.
        span = span or node
        skip = ("attribute_list", "modifier")
        start = next((c.start_byte for c in span.children if c.type not in skip), span.end_byte)
        self.scopes.append(scope)
        self.nesting.add(parent, (start, span.end_byte))

    def _is_type(self, k: int) -> bool:
        decl = self.scopes[k].decl
        return decl is not None and decl.kind in TYPE_KINDS

    def _decl(self, kind: str, name: str, node: Node, end: int, **more: int | bool) -> Decl:
        ns = namespace(self.data, node, self.file_scoped)
        sig = signature(self.data, node, end)
        d = {"tparams": 0, "arity": 0, "optional": 0, "varargs": False, "test": False} | more
        return Decl(kind, name, *span(node), sig, ns, **d)  # type: ignore[arg-type]

    def type(self, node: Node) -> None:
        data, parent = self.data, self.scope_at(node.start_byte)
        name = node.child_by_field_name("name")
        if name is None or (parent and not self._is_type(parent)):
            return
        body = node.child_by_field_name("body")
        parameters = node.child_by_field_name("parameters") or next(
            (c for c in node.children if c.type == "parameter_list"), None
        )
        kind = _TYPE_KINDS[node.type]
        header = [span(c) for c in node.children if c.type in _HEADER]
        self.headers[len(self.scopes)] = header
        supers = []
        for clause in node.children:
            if clause.type == "base_list":
                for t in named(clause):
                    if t.type == "primary_constructor_base_type":
                        t = t.child_by_field_name("type") or t
                    if (base := type_name(data, t)) is not None:
                        supers.append(Use(base, *span(t)))
        params = named(parameters) if parameters is not None else []
        params = [p for p in params if p.type == "parameter"]
        tparams = len(
            named(next((c for c in node.children if c.type == "type_parameter_list"), None))
        )
        end = body.start_byte if body is not None else (parameters or node).end_byte
        if body is None:
            end = max([end, *(c.end_byte for c in node.children if c.type in _HEADER)])
        decl = self._decl(
            kind,
            id(data, name),
            node,
            end,
            tparams=tparams,
            arity=len(params) if kind == "delegate" else 0,
        )
        self._open(parent, Scope(parent, decl, supers), node)

    def member(self, node: Node) -> None:
        data, parent = self.data, self.scope_at(node.start_byte)
        if not self._is_type(parent):
            return
        kind = node.type.removesuffix("_declaration")
        spans: list[tuple[Node | None, str, Node, Node | None]] = []  # name, text, span, decl
        if kind in ("field", "event_field"):
            declaration = next((c for c in node.children if c.type == "variable_declaration"), None)
            declarators = [
                d
                for d in (declaration.children if declaration else ())
                if d.type == "variable_declarator"
            ]
            for d in declarators:
                n = d.child_by_field_name("name")
                spans.append((n, id(data, n), node if len(declarators) == 1 else d, declaration))
            kind = "field" if kind == "field" else "event"
        else:
            name = node.child_by_field_name("name")
            spans.append((name, self._member_name(node, kind), node, None))
            kind = {
                "operator": "operator",
                "conversion_operator": "operator",
                "enum_member": "enum_member",
            }.get(kind, kind)
        for name, label, where, declaration in spans:
            if name is None and kind not in ("indexer", "operator"):
                continue
            self._declare(kind, label, node, where, declaration, parent)

    def _member_name(self, node: Node, kind: str) -> str:
        if kind == "indexer":
            return "this"
        if kind == "conversion_operator":
            explicit = any(c.type == "explicit" for c in node.children)
            return "op_Explicit" if explicit else "op_Implicit"
        if kind == "operator":
            kids = [c.type for c in node.children]
            token = kids[kids.index("operator") + 1] if "operator" in kids else ""
            return _OPERATORS.get(token, "op_Unknown")
        if kind == "destructor":
            return id(self.data, node.child_by_field_name("name"))
        return id(self.data, node.child_by_field_name("name"))

    def _declare(
        self, kind: str, label: str, node: Node, where: Node, declaration: Node | None, parent: int
    ) -> None:
        data = self.data
        if declaration is not None:  # a field: ``mods Type name``
            type_node = declaration.child_by_field_name("type")
            end = where.end_byte
            head = signature(data, node, type_node.end_byte if type_node else node.start_byte)
            sig = f"{head} {label}"
            decl = self._decl(kind, label, where, end)
            decl.signature = sig
        else:
            stops = [
                c.start_byte
                for c in node.children
                if node.child_by_field_name("body") == c
                or node.child_by_field_name("accessors") == c
                or node.child_by_field_name("value") == c
                or c.type in ("constructor_initializer", "arrow_expression_clause")
            ]
            if kind == "enum_member":
                stops.append(
                    next((c.start_byte for c in node.children if c.type == "="), node.end_byte)
                )
            params = [
                p for p in named(node.child_by_field_name("parameters")) if p.type == "parameter"
            ]
            optional = sum(has_default(p) for p in params)
            varargs = bool(params) and any(
                c.type == "modifier" and text(data, c) == "params" for c in params[-1].children
            )
            attrs = {
                (dotted(data, a.child_by_field_name("name")) or "").rpartition(".")[2]
                for a in attributes(node)
            }
            attrs |= {a.removesuffix("Attribute") for a in attrs}
            decl = self._decl(
                kind,
                label,
                node,
                min(stops, default=node.end_byte),
                tparams=len(
                    named(next((c for c in node.children if c.type == "type_parameter_list"), None))
                ),
                arity=len(params),
                optional=optional,
                varargs=varargs,
                test=kind == "method" and bool(attrs & TEST_ATTRIBUTES),
            )
        self._open(parent, Scope(parent, decl), node, where)

    def bind(self, name: Node | None, kind: str | None, owner: bool = False) -> None:
        """Bind ``name`` in its innermost scope, or (``owner``) in the enclosing type's:
        a field's or property's variable is the type's, not its own member's."""
        if name is None:
            return
        k = self.scope_at(name.start_byte)
        while owner and k and not self._is_type(k):
            k = self.scopes[k].parent or 0
        bound, key = self.scopes[k].bound, id(self.data, name)
        bound[key] = kind if bound.get(key, kind) == kind else None

    def variable(self, node: Node) -> None:
        data = self.data
        if node.type == "foreach_statement":
            left = node.child_by_field_name("left")
            if left is not None and left.type == "identifier":
                self.bind(left, type_name(data, node.child_by_field_name("type")))
            return
        self.bind(
            node.child_by_field_name("name"), type_name(data, node.child_by_field_name("type"))
        )

    def variables(self, node: Node) -> None:
        kind = type_name(self.data, node.child_by_field_name("type"))
        field_like = node.parent is not None and node.parent.type in (
            "field_declaration",
            "event_field_declaration",
        )
        for d in node.children:
            if d.type == "variable_declarator":
                self.bind(d.child_by_field_name("name"), kind, field_like)

    def call(self, node: Node) -> None:
        data = self.data
        function = node.child_by_field_name("function")
        arguments = node.child_by_field_name("arguments")
        if function is None:
            return
        if function.type in ("identifier", "generic_name"):
            form, name = "", dotted(data, function)
        elif function.type == "member_access_expression":
            receiver = expression(data, function.child_by_field_name("expression"))
            if function.child_by_field_name("expression") is None:
                receiver = expression(data, function)
                receiver = receiver.rpartition(".")[0] if receiver else None
            name = dotted(data, function.child_by_field_name("name"))
            form = "" if receiver is None else receiver + "."
            if receiver is None:
                return  # a call on an expression's value: needs its type
        elif function.type == "conditional_access_expression":
            binding = next(
                (c for c in function.named_children if c.type == "member_binding_expression"), None
            )
            receiver = expression(data, function.child_by_field_name("condition"))
            name = dotted(data, binding.child_by_field_name("name")) if binding else None
            if receiver is None:
                return
            form = receiver + "."
        else:
            return
        if name is None:
            return
        arity = len([a for a in named(arguments) if a.type == "argument"])
        end = function.end_byte
        self.use(Call(form + name, node.start_byte, end, arity))

    def new(self, node: Node) -> None:
        kind = node.child_by_field_name("type")
        base = type_name(self.data, kind)
        if kind is not None and base is not None:
            self.created.add(kind.start_byte)
            arity = len(
                [a for a in named(node.child_by_field_name("arguments")) if a.type == "argument"]
            )
            self.use(Call(f"new {base}", node.start_byte, kind.end_byte, arity))

    def use(self, call: Call) -> None:
        self.scopes[self.scope_at(call.start)].calls.append(call)


def parse(data: bytes) -> Parsed | str:
    """Syntax facts, or the reason the file is unsupported."""
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "C# source is not UTF-8"
    root = Parser(LANGUAGE).parse(data).root_node
    if too_deep(root):
        return TOO_DEEP
    captures = QueryCursor(_QUERY).captures(root)
    scoped = next(iter(captures.get("fsns", [])), None)
    file_scoped = (
        None
        if scoped is None
        else (dotted(data, scoped.child_by_field_name("name")) or "", scoped.end_byte)
    )
    facts = _Facts(data, file_scoped)
    opening = [(n, what) for what in ("type", "member") for n in captures.get(what, [])]
    for node, what in sorted(opening, key=lambda o: (o[0].start_byte, -o[0].end_byte)):
        facts.type(node) if what == "type" else facts.member(node)
    scopes = facts.scopes
    for node in captures.get("type_parameter", []):
        scopes[facts.scope_at(node.start_byte)].types.add(id(data, node))
    for node in captures.get("local_function", []):
        scopes[facts.scope_at(node.start_byte)].types.add(id(data, node))
    for node in captures.get("property", []):
        facts.bind(
            node.child_by_field_name("name"),
            type_name(data, node.child_by_field_name("type")),
            True,
        )
    for node in captures.get("constant", []):
        k = facts.scope_at(node.start_byte)
        while k and not facts._is_type(k):
            k = scopes[k].parent or 0
        enum = scopes[k].decl
        facts.bind(node.child_by_field_name("name"), enum.name if enum is not None else None, True)
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
    refs: dict[tuple[int, int], Use] = {}
    for node in captures.get("tref", []):
        uses(data, node, refs)
    for _key, use in sorted(refs.items()):
        if use.start in facts.created:
            continue  # an object creation: a call of the class
        k = facts.scope_at(use.start)
        header = any(s <= use.start < e for s, e in facts.headers.get(k, ()))
        if header and use.name.split(".")[0] not in scopes[k].types:  # but sees its type parameters
            k = scopes[k].parent or 0
        scopes[k].refs.append(use)
    for node in captures.get("attribute", []):
        if (name := dotted(data, node)) is not None:
            scopes[facts.scope_at(node.start_byte)].attrs.append(Use(name, *span(node)))
    imports = []
    for node in captures.get("using", []):
        item = _import(data, node, file_scoped)
        if item is not None:
            imports.append(item)
    imports.sort(key=lambda item: (item.start, item.end))
    for scope in scopes:
        scope.calls.sort(key=lambda u: (u.start, u.end))
        scope.refs.sort(key=lambda u: (u.start, u.end))
        scope.attrs.sort(key=lambda u: (u.start, u.end))
    return Parsed(imports, scopes, syntax_diagnostics(root, _BROKEN))


def _import(data: bytes, node: Node, file_scoped: tuple[str, int] | None) -> Import | None:
    kids = [c.type for c in node.children]
    alias = node.child_by_field_name("name")
    target = next((c for c in reversed(node.named_children) if c.type in NAMES), None)
    path = type_name(data, target)
    if path is None:
        return None
    kind = "alias" if alias is not None else "static" if "static" in kids else "using"
    ns = namespace(data, node, file_scoped)
    return Import(kind, path, id(data, alias), ns, "global" in kids, *span(node))
