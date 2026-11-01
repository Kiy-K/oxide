"""C++ language slice: tree-sitter syntax facts (docs/cpp.md); resolution is in ``cpp_resolve``.

Symbols are namespace blocks, classes, structs, unions and enums with a body, functions
and methods (definitions, prototypes and out-of-line definitions such as
``int Box::get()``), typedefs and aliases, namespace-scope variables and macros, nested
as written. The preprocessor never runs. One compiled query collects every use; Python
only walks the declaration containers and assigns each use to the innermost symbol
whose span holds it. A name bound anywhere in a symbol (parameters, locals, template
parameters, a class's fields) is opaque in all of it: no block scoping, no types.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import tree_sitter_cpp
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from oxide.core.languages.c import _KEYWORDS as C_KEYWORDS
from oxide.core.languages.c import _WRAPPERS, Include, _line, innermost
from oxide.core.languages.cpp_macros import masked
from oxide.core.languages.java import Call
from oxide.core.languages.python import TOO_DEEP, Use, syntax_diagnostics, too_deep

VERSION = "oxide-cpp-v1"
# The JSON shape of ``Parsed`` that generations retain for reuse; bump on any change.
FACTS_VERSION = "oxide-cpp-facts-v1"
CLASSES = ("class", "struct", "union")
KINDS = ("namespace", *CLASSES, "enum", "function", "type", "variable", "macro")
TAGS = {
    "class_specifier": "class",
    "struct_specifier": "struct",
    "union_specifier": "union",
    "enum_specifier": "enum",
}

LANGUAGE = Language(tree_sitter_cpp.language())
_QUERY = Query(
    LANGUAGE,
    """
    (preproc_include path: [(string_literal) (system_lib_string)] @include)
    (call_expression function: [(identifier) (qualified_identifier) (template_function)
      (field_expression argument: (this))]) @call
    (type_identifier) @type
    (qualified_identifier) @qualified
    (base_class_clause [(type_identifier) (qualified_identifier) (template_type)] @base)
    [(class_specifier name: (_) body: (_)) (struct_specifier name: (_) body: (_))
      (union_specifier name: (_) body: (_)) (enum_specifier name: (_) body: (_))] @local
    (parameter_declaration declarator: (_) @bind)
    (optional_parameter_declaration declarator: (_) @bind)
    (variadic_parameter_declaration declarator: (_) @bind)
    (declaration declarator: (_) @bind)
    (field_declaration declarator: (_) @bind)
    (type_definition declarator: (_) @bind)
    (for_range_loop declarator: (_) @bind)
    (structured_binding_declarator (identifier) @bind)
    (type_parameter_declaration (type_identifier) @bind)
    (optional_type_parameter_declaration name: (_) @bind)
    (variadic_type_parameter_declaration (type_identifier) @bind)
    (alias_declaration name: (_) @bind)
    (using_declaration) @using
    """,
)
_BROKEN = Query(LANGUAGE, "(ERROR) @error (MISSING) @missing")
_NAMES = (
    "identifier",
    "field_identifier",
    "type_identifier",
    "qualified_identifier",
    "destructor_name",
    "operator_name",
    "operator_cast",
    "template_function",
)
_INNER = ("parenthesized_declarator", "reference_declarator", "variadic_declarator")
_CONDITIONAL = ("preproc_if", "preproc_ifdef", "preproc_else", "preproc_elif", "preproc_elifdef")
_KEYWORDS = C_KEYWORDS | frozenset(
    b"alignas alignof catch class concept consteval constexpr constinit co_await co_return"
    b" co_yield decltype delete explicit export false final friend mutable namespace new"
    b" noexcept nullptr operator override private protected public requires static_assert"
    b" template this thread_local throw true try typeid typename using virtual static_cast"
    b" dynamic_cast const_cast reinterpret_cast".split()
)


@dataclass(slots=True)
class Decl:
    kind: str
    name: str
    qualifier: str  # ``A::B`` of ``int A::B::f()``; a leading ``::`` is absolute
    start: int
    end: int
    signature: str
    static: bool  # internal linkage: visible only where its file is
    defines: bool  # a function with a body (a prototype otherwise)
    low: int  # a function's fewest and most arguments (-1: variadic); 0 otherwise
    high: int


@dataclass(slots=True)
class Scope:
    parent: int
    decl: Decl | None
    bound: set[str] = field(default_factory=set)
    usings: list[str] = field(default_factory=list)  # ``using namespace`` targets
    bases: list[str] = field(default_factory=list)  # a class's base classes, as written
    calls: list[Call] = field(default_factory=list)
    refs: list[Use] = field(default_factory=list)  # type names, qualified or not


@dataclass(slots=True)
class Parsed:
    """Scope 0 is the file: code outside any symbol."""

    includes: list[Include]
    scopes: list[Scope]
    diagnostics: list[str]


def _text(data: bytes, node: Node | None) -> str:
    return "" if node is None else data[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _squash(text: str | bytes) -> str:
    return " ".join((text if isinstance(text, str) else text.decode("utf-8", "replace")).split())


def qualified(data: bytes, node: Node | None) -> list[str] | None:
    """The parts of a (qualified) name, ``""`` first for a leading ``::``; template
    arguments dropped. None for a keyword or anything else."""
    parts: list[str] = []
    while node is not None and node.type == "qualified_identifier":
        scope = node.child_by_field_name("scope")
        if scope is not None and scope.type == "template_type":
            scope = scope.child_by_field_name("name")
        if scope is not None and scope.type not in ("namespace_identifier", "type_identifier"):
            return None
        parts.append(_text(data, scope))
        node = node.child_by_field_name("name")
    if node is not None and node.type in ("template_function", "template_type", "template_method"):
        node = node.child_by_field_name("name")
    if node is None:
        return None
    if node.type == "destructor_name":
        last = "~" + _text(data, node.named_children[-1] if node.named_children else None)
    elif node.type == "operator_name":
        last = _squash(_text(data, node))
    elif node.type == "operator_cast":
        last = "operator " + _squash(_text(data, node.child_by_field_name("type")))
    elif node.type in _NAMES or node.type == "namespace_identifier":
        last = _text(data, node)
    else:
        return None
    if last.encode() in _KEYWORDS or not last.strip("~") or any(p in _KEYWORDS_STR for p in parts):
        return None
    return [*parts, last]


_KEYWORDS_STR = frozenset(k.decode() for k in _KEYWORDS)


def _declared(node: Node | None) -> tuple[Node | None, Node | None]:
    """The name a declarator declares and its function declarator, if it declares a
    function (not a pointer to one): the innermost wrapper decides."""
    function: Node | None = None
    while node is not None and node.type not in _NAMES:
        if node.type in _INNER:
            node = node.named_children[-1] if node.named_children else None
            continue
        if node.type not in _WRAPPERS:
            return None, None
        function = node if node.type == "function_declarator" else None
        inner = node.child_by_field_name("declarator")
        # ``API T name(...)``: recovery takes ``T`` as the name and keeps ``name`` in an
        # ERROR before the parameters.
        broken = [c for c in node.children if c.type == "ERROR"] if function else []
        if broken and broken[0].named_children:
            last = broken[0].named_children[-1]
            inner = last if last.type == "identifier" else inner
        if inner is None and node.type == "attributed_declarator" and node.named_children:
            inner = node.named_children[0]
        node = inner
    if node is not None and node.type == "operator_cast":
        function = node  # ``operator bool() const`` holds its own parameters
    if node is not None and node.type == "qualified_identifier":
        last = node
        while (
            name := last.child_by_field_name("name")
        ) is not None and name.type == "qualified_identifier":
            last = name
        if name is not None and name.type == "operator_cast":
            function = name
    return node, function


def _arity(function: Node) -> tuple[int, int]:
    """Fewest and most arguments a function declarator takes (-1: any number)."""
    params = function.child_by_field_name("parameters")
    if params is None:  # a conversion function: ``operator T() const``
        abstract = function.child_by_field_name("declarator")
        params = abstract.child_by_field_name("parameters") if abstract is not None else None
    low = high = 0
    for p in params.children if params is not None else []:
        if p.type in ("...", "variadic_parameter_declaration"):
            return low, -1
        if p.type == "optional_parameter_declaration":
            high += 1
        elif p.type == "parameter_declaration" and p.text != b"void":
            low, high = low + 1, high + 1
    return low, high


def _static(node: Node) -> bool:
    return any(c.type == "storage_class_specifier" and c.text == b"static" for c in node.children)


type Frame = tuple[Iterator[Node], int, bool]  # children, parent scope, internal linkage


class _Facts:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.scopes = [Scope(0, None)]
        self.names: set[int] = set()  # start bytes of declared symbol names

    def add(
        self, parent: int, kind: str, parts: list[str], name: Node, span: Node, *rest: Any
    ) -> int:
        qualifier = "::".join(parts[:-1]) if parts[:-1] != [""] else "::"
        if parts[:1] == [""] and len(parts) > 2:
            qualifier = "::" + qualifier[2:] if qualifier.startswith("::") else "::" + qualifier
        decl = Decl(kind, parts[-1], qualifier, span.start_byte, span.end_byte, *rest)
        self.scopes.append(Scope(parent, decl))
        self.names.add(name.start_byte)
        return len(self.scopes) - 1

    def signature(self, span: Node, cut: Node | None, end: int | None = None) -> str:
        """``span``'s text without ``cut`` (a body), whitespace collapsed."""
        data, stop = self.data, end if end is not None else span.end_byte
        if cut is None:
            return _squash(data[span.start_byte : stop])
        return _squash(data[span.start_byte : cut.start_byte] + data[cut.end_byte : stop])

    def walk(self, root: Node) -> None:
        stack: list[Frame] = [(iter(root.children), 0, False)]
        while stack:
            children, parent, static = stack[-1]
            node = next(children, None)
            if node is None:
                stack.pop()
            elif (frame := self.declaration(node, node, parent, static)) is not None:
                stack.append(frame)

    def member(self, parent: int) -> bool:
        decl = self.scopes[parent].decl
        return decl is not None and decl.kind in CLASSES

    def declaration(self, node: Node, span: Node, parent: int, static: bool) -> Frame | None:
        """Adds the symbols ``node`` declares; the frame of its members, if any."""
        kind, data = node.type, self.data
        if kind in _CONDITIONAL or kind == "ERROR":
            return iter(node.children), parent, static
        if kind == "linkage_specification":
            body = node.child_by_field_name("body")
            if body is not None and body.type == "declaration_list":
                return iter(body.children), parent, static
            return self.declaration(body, body, parent, static) if body is not None else None
        if kind == "template_declaration":
            skip = ("template_parameter_list", "requires_clause")
            inner = [c for c in node.named_children if c.type not in skip]
            return self.declaration(inner[-1], span, parent, static) if inner else None
        if kind == "namespace_definition":
            return self.namespace(node, parent, static)
        if kind in TAGS:
            return self.tag(node, span, parent, static)
        if kind in ("preproc_def", "preproc_function_def"):
            name = node.child_by_field_name("name")
            if name is not None:
                shown = _line(_text(data, node)).removesuffix("\\").rstrip()
                self.add(
                    parent, "macro", [_text(data, name)], name, node, shown, False, False, 0, 0
                )
        elif kind == "function_definition":
            name, function = _declared(node.child_by_field_name("declarator"))
            parts = qualified(data, name)
            if parts is None or name is None or function is None:
                return None
            body = node.child_by_field_name("body")
            ends = ("field_initializer_list", "compound_statement", "try_statement")
            cut = next((c.start_byte for c in node.children if c.type in ends), None)
            internal = static or (_static(node) and not self.member(parent))
            shown = self.signature(span, None, cut)
            self.add(
                parent,
                "function",
                parts,
                name,
                span,
                shown,
                internal,
                body is not None,
                *_arity(function),
            )
        elif kind == "alias_declaration":
            name = node.child_by_field_name("name")
            if name is not None:
                shown = self.signature(span, None)
                self.add(
                    parent, "type", [_text(data, name)], name, span, shown, static, False, 0, 0
                )
        elif kind in ("declaration", "field_declaration", "type_definition"):
            return self.declarators(node, span, parent, static)
        return None

    def declarators(self, node: Node, span: Node, parent: int, static: bool) -> Frame | None:
        """A declaration's tag definition, then each declarator; the tag's frame."""
        member = self.member(parent)
        internal = static or (_static(node) and not member)
        tag = node.child_by_field_name("type")
        frame = self.tag(tag, None, parent, internal)
        typedef = node.type == "type_definition"
        body = tag.child_by_field_name("body") if frame is not None and tag is not None else None
        signature = self.signature(span, body)
        for declarator in node.children_by_field_name("declarator"):
            name, function = _declared(declarator)
            parts = qualified(self.data, name)
            if parts is None or name is None or (name.type == "type_identifier") != typedef:
                continue
            if typedef:
                self.add(parent, "type", parts, name, span, signature, internal, False, 0, 0)
            elif function is not None:
                arity = _arity(function)
                self.add(parent, "function", parts, name, span, signature, internal, False, *arity)
            elif not member:  # fields are not symbols; they are bound in their class
                shown = _line(signature)
                self.add(parent, "variable", parts, name, span, shown, internal, False, 0, 0)
        return frame

    def namespace(self, node: Node, parent: int, static: bool) -> Frame | None:
        name, body = node.child_by_field_name("name"), node.child_by_field_name("body")
        if body is None:
            return None
        if name is None:  # anonymous: its members have internal linkage
            return iter(body.children), parent, True
        named = name.named_children if name.type == "nested_namespace_specifier" else [name]
        parts = [_text(self.data, c) for c in named]
        if not parts or any(not p or p in _KEYWORDS_STR for p in parts):
            return iter(body.children), parent, static
        shown = f"namespace {'::'.join(parts)}"
        k = self.add(parent, "namespace", parts, name, node, shown, static, False, 0, 0)
        return iter(body.children), k, static

    def tag(self, node: Node | None, span: Node | None, parent: int, static: bool) -> Frame | None:
        """A class, struct, union or enum with a body as a symbol; its members' frame."""
        if node is None or node.type not in TAGS:
            return None
        name, body = node.child_by_field_name("name"), node.child_by_field_name("body")
        parts = qualified(self.data, name)
        if name is None or body is None or parts is None:
            return None
        span = span or node
        shown = self.signature(span, body, node.end_byte)
        k = self.add(parent, TAGS[node.type], parts, name, span, shown, static, False, 0, 0)
        return (iter(body.children), k, static) if node.type != "enum_specifier" else None


def parse(data: bytes) -> Parsed | str:
    """Syntax facts, or the reason the file is unsupported."""
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "C++ source is not UTF-8"
    root = Parser(LANGUAGE).parse(masked(data)).root_node
    if too_deep(root):
        return TOO_DEEP
    captures = QueryCursor(_QUERY).captures(root)
    facts = _Facts(data)
    facts.walk(root)
    scopes = facts.scopes
    scope_at = innermost([(d.start, d.end, k) for k, s in enumerate(scopes) if (d := s.decl)])
    _uses(data, captures, facts.names, scopes, scope_at)
    includes = []
    for node in captures.get("include", []):
        text = _text(data, node)
        if len(text) > 2:
            includes.append(
                Include(
                    text[1:-1], node.type == "system_lib_string", node.start_byte, node.end_byte
                )
            )
    includes.sort(key=lambda item: (item.start, item.end))
    for scope in scopes:
        scope.calls.sort(key=lambda u: (u.start, u.end))
        scope.refs.sort(key=lambda u: (u.start, u.end))
    return Parsed(includes, scopes, syntax_diagnostics(root, _BROKEN))


def _uses(
    data: bytes,
    captures: dict[str, list[Node]],
    names: set[int],
    scopes: list[Scope],
    scope_at: Any,
) -> None:
    """Assigns bindings, ``using`` directives, bases, calls and type uses to scopes."""
    skip = set(names)  # declared names are not uses
    for node in captures.get("local", []):
        name = node.child_by_field_name("name")
        if name is not None and name.start_byte not in names:
            skip.add(name.start_byte)
            if (parts := qualified(data, name)) is not None:
                scopes[scope_at(node.start_byte)].bound.add(parts[-1])
    for node in captures.get("bind", []):
        name, _ = _declared(node)
        if name is not None and (parts := qualified(data, name)) is not None:
            skip.add(name.start_byte)
            if name.start_byte not in names and len(parts) == 1:
                scopes[scope_at(name.start_byte)].bound.add(parts[0])
    for node in captures.get("using", []):
        target = node.named_children[-1] if node.named_children else None
        parts = qualified(data, target)
        if parts is not None:
            scope = scopes[scope_at(node.start_byte)]
            if any(c.type == "namespace" for c in node.children):
                scope.usings.append("::".join(parts))
            else:  # ``using X::n``: ``n`` is opaque where it is brought in
                scope.bound.add(parts[-1])
    taken: set[tuple[int, int]] = set()  # qualified names already read as part of another
    for expression in captures.get("call", []):
        callee = expression.child_by_field_name("function")
        arguments = expression.child_by_field_name("arguments")
        if callee is None or arguments is None or arguments.type != "argument_list":
            continue
        taken.add((callee.start_byte, callee.end_byte))
        if callee.type == "field_expression":
            parts = qualified(data, callee.child_by_field_name("field"))
            shown = f"this->{parts[-1]}" if parts and len(parts) == 1 else None
        else:  # not a destructor or an operator
            parts = qualified(data, callee)
            shown = "::".join(parts) if parts and parts[-1].isidentifier() else None
        if shown is not None:
            arity = sum(1 for c in arguments.named_children if c.type != "comment")
            call = Call(shown, callee.start_byte, callee.end_byte, arity)
            scopes[scope_at(callee.start_byte)].calls.append(call)
    for node in captures.get("qualified", []):
        inner = node.child_by_field_name("name")
        if inner is not None and inner.type == "qualified_identifier":
            taken.add((inner.start_byte, inner.end_byte))
    for node in captures.get("qualified", []):
        if (node.start_byte, node.end_byte) in taken:
            continue
        last = node
        while (
            inner := last.child_by_field_name("name")
        ) is not None and inner.type == "qualified_identifier":
            last = inner
        if inner is None or inner.type not in ("type_identifier", "template_type"):
            continue
        skip.add(inner.start_byte)
        if (parts := qualified(data, node)) is not None and node.start_byte not in names:
            scopes[scope_at(node.start_byte)].refs.append(
                Use("::".join(parts), node.start_byte, node.end_byte)
            )
    for node in captures.get("base", []):
        parts = qualified(data, node)
        if parts is not None:
            scopes[scope_at(node.start_byte)].bases.append("::".join(parts))
    for node in captures.get("type", []):
        if node.start_byte not in skip:
            scopes[scope_at(node.start_byte)].refs.append(
                Use(_text(data, node), node.start_byte, node.end_byte)
            )
