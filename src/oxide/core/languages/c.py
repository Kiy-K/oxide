"""C language slice: tree-sitter syntax facts (docs/c.md); resolution is in ``c_resolve``.

Symbols are file-level declarations, all with the file as physical parent: function
definitions and prototypes, tagged struct/union/enum definitions, typedefs, variables
and macros, including those in every branch of a preprocessor conditional. The
preprocessor never runs and macro bodies stay opaque text. One compiled query collects
every fact in C; Python only assigns each captured node to the file-level declaration
whose span holds it. A name bound anywhere in a declaration (parameters, locals, local
typedefs and tags) is opaque in all of it: no block scoping, no type inference.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from typing import Any

import tree_sitter_c
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from oxide.core.languages.python import TOO_DEEP, Use, as_list, syntax_diagnostics, too_deep

VERSION = "oxide-c-v1"
# The JSON shape of ``Parsed`` that generations retain for reuse; bump on any change.
FACTS_VERSION = "oxide-c-facts-v1"
KINDS = ("function", "struct", "union", "enum", "type", "variable", "macro")
TAGS = {"struct_specifier": "struct", "union_specifier": "union", "enum_specifier": "enum"}

LANGUAGE = Language(tree_sitter_c.language())
_TOP = (
    "[(function_definition) (declaration) (type_definition) (struct_specifier)"
    " (union_specifier) (enum_specifier) (preproc_def) (preproc_function_def)] @decl"
)
_QUERY = Query(
    LANGUAGE,
    f"""
    (preproc_include path: [(string_literal) (system_lib_string)] @include)
    (translation_unit {_TOP})
    (ERROR {_TOP})
    (preproc_if {_TOP})
    (preproc_ifdef {_TOP})
    (preproc_else {_TOP})
    (preproc_elif {_TOP})
    (preproc_elifdef {_TOP})
    (call_expression function: (identifier) @call)
    (type_identifier) @type
    [(struct_specifier name: (type_identifier)) (union_specifier name: (type_identifier))
      (enum_specifier name: (type_identifier))] @tag
    (parameter_declaration declarator: (_) @bind)
    (declaration declarator: (_) @bind)
    (type_definition declarator: (_) @bind)
    """,
)
_BROKEN = Query(LANGUAGE, "(ERROR) @error (MISSING) @missing")
# The grammar ends a macro body at a block comment, so ``#define F(x) \\ a /* c */ \\ b``
# misparses; the parser sees block comments as spaces (offsets and newlines kept).
_LEXEMES = re.compile(rb'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|//[^\n]*|/\*.*?\*/', re.DOTALL)


def _blank(match: re.Match[bytes]) -> bytes:
    text = match.group()
    return re.sub(rb"[^\n]", b" ", text) if text.startswith(b"/*") else text


_WRAPPERS = (
    "pointer_declarator",
    "array_declarator",
    "function_declarator",
    "init_declarator",
    "parenthesized_declarator",
    "attributed_declarator",
)
# Recovery around unexpanded macros (``API int (f)(void)``) can name a keyword.
_KEYWORDS = frozenset(
    b"auto bool break case char const continue default do double else enum extern float for"
    b" goto if inline int long register restrict return short signed sizeof static struct"
    b" switch typedef union unsigned void volatile while _Bool _Complex".split()
)


@dataclass(slots=True)
class Decl:
    kind: str
    name: str
    start: int
    end: int
    signature: str
    static: bool  # internal linkage: visible only where its file is
    defines: bool  # a function with a body (a prototype otherwise)


@dataclass(frozen=True, slots=True)
class Include:
    path: str
    system: bool  # ``<path>`` rather than ``"path"``
    start: int
    end: int


@dataclass(slots=True)
class Scope:
    decl: Decl | None
    bound: set[str] = field(default_factory=set)
    calls: list[Use] = field(default_factory=list)
    refs: list[Use] = field(default_factory=list)  # typedef names, and ``struct S`` tags


@dataclass(slots=True)
class Parsed:
    """Scope 0 is the file: code outside any symbol."""

    includes: list[Include]
    scopes: list[Scope]
    diagnostics: list[str]


def _text(data: bytes, node: Node | None) -> str:
    return "" if node is None else data[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _line(text: str) -> str:
    return text.split("\n", 1)[0].strip()


def _span(node: Node) -> tuple[int, int]:
    return node.start_byte, node.end_byte


def _declared(node: Node | None) -> tuple[Node | None, bool]:
    """The name a declarator declares, and whether it is a function (not a pointer to
    one): the innermost wrapper other than parentheses decides."""
    function = False
    while node is not None and node.type not in ("identifier", "type_identifier"):
        if node.type not in _WRAPPERS:
            return None, False
        if node.type == "parenthesized_declarator":
            node = node.named_children[-1] if node.named_children else None
            continue
        function = node.type == "function_declarator"
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
    if node is not None and node.text in _KEYWORDS:
        return None, False
    return node, function


def _static(data: bytes, node: Node) -> bool:
    return any(
        c.type == "storage_class_specifier" and _text(data, c) == "static" for c in node.children
    )


class _Facts:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.scopes = [Scope(None)]
        self.names: set[int] = set()  # start bytes of declared symbol names

    def add(self, kind: str, name: Node, span: Node, signature: str, *flags: bool) -> None:
        decl = Decl(kind, _text(self.data, name), *_span(span), signature, *flags)
        self.scopes.append(Scope(decl))
        self.names.add(name.start_byte)

    def signature(self, node: Node, cut: Node | None) -> str:
        """``node``'s text without ``cut`` (a body), whitespace collapsed."""
        data = self.data
        if cut is None:
            text = data[node.start_byte : node.end_byte]
        else:
            text = data[node.start_byte : cut.start_byte] + data[cut.end_byte : node.end_byte]
        return " ".join(text.decode("utf-8", "replace").split())

    def top(self, node: Node) -> None:
        data = self.data
        if node.type in ("preproc_def", "preproc_function_def"):
            name = node.child_by_field_name("name")
            if name is not None:
                shown = _line(_text(data, node)).removesuffix("\\").rstrip()
                self.add("macro", name, node, shown, False, False)
        elif node.type in TAGS:
            self.tag(node, False)
        elif node.type == "function_definition":
            name, _ = _declared(node.child_by_field_name("declarator"))
            if name is not None and name.type == "identifier":
                signature = self.signature(node, node.child_by_field_name("body"))
                self.add("function", name, node, signature, _static(data, node), True)
        else:  # a declaration or typedef: its tag definition, then each declarator
            static = _static(data, node)
            body = self.tag(node.child_by_field_name("type"), static)
            typedef = node.type == "type_definition"
            signature = self.signature(node, body)
            for declarator in node.children_by_field_name("declarator"):
                name, function = _declared(declarator)
                if name is None or (name.type == "type_identifier") != typedef:
                    continue
                kind = "type" if typedef else "function" if function else "variable"
                shown = _line(signature) if kind == "variable" else signature
                self.add(kind, name, node, shown, static, False)

    def tag(self, node: Node | None, static: bool) -> Node | None:
        """A named struct, union or enum definition as a symbol; its body, if any."""
        if node is None or node.type not in TAGS:
            return None
        name, body = node.child_by_field_name("name"), node.child_by_field_name("body")
        if name is not None and body is not None:
            self.add(TAGS[node.type], name, node, self.signature(node, body), static, False)
        return body


def parse(data: bytes) -> Parsed | str:
    """Syntax facts, or the reason the file is unsupported."""
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "C source is not UTF-8"
    root = Parser(LANGUAGE).parse(_LEXEMES.sub(_blank, data) if b"/*" in data else data).root_node
    if too_deep(root):
        return TOO_DEEP
    captures = QueryCursor(_QUERY).captures(root)
    facts = _Facts(data)
    end = -1
    for node in sorted(captures.get("decl", []), key=lambda n: n.start_byte):
        if node.start_byte >= end:  # not inside a function or an earlier declaration
            facts.top(node)
            end = node.end_byte
    scopes = facts.scopes
    # File-level spans by start. A tag definition nests in its declaration, so each span
    # knows its enclosing one and a byte's scope is the innermost span holding it.
    tops = sorted((d.start, d.end, k) for k, s in enumerate(scopes) if (d := s.decl))
    starts = [t[0] for t in tops]
    outer: list[int] = []
    stack: list[int] = []
    for start, _, _ in tops:
        while stack and start >= tops[stack[-1]][1]:
            stack.pop()
        outer.append(stack[-1] if stack else -1)
        stack.append(len(outer) - 1)

    def scope_at(at: int) -> int:
        j = bisect.bisect_right(starts, at) - 1
        while j >= 0 and at >= tops[j][1]:
            j = outer[j]
        return tops[j][2] if j >= 0 else 0

    # Declared names are not uses: tag and typedef names in their declarations.
    skip = set(facts.names)
    for node in captures.get("tag", []):
        name = node.child_by_field_name("name")
        if name is None:
            continue
        skip.add(name.start_byte)
        if name.start_byte in facts.names:
            continue
        tag = f"{TAGS[node.type]} {_text(data, name)}"
        if node.child_by_field_name("body") is not None:  # a local definition
            scopes[scope_at(node.start_byte)].bound.add(tag)
        else:
            scopes[scope_at(node.start_byte)].refs.append(Use(tag, *_span(node)))
    for node in captures.get("bind", []):
        name, _ = _declared(node)
        if name is not None:
            skip.add(name.start_byte)
            if name.start_byte not in facts.names:
                scopes[scope_at(name.start_byte)].bound.add(_text(data, name))
    for node in captures.get("type", []):
        if node.start_byte not in skip:
            scopes[scope_at(node.start_byte)].refs.append(Use(_text(data, node), *_span(node)))
    for node in captures.get("call", []):
        scopes[scope_at(node.start_byte)].calls.append(Use(_text(data, node), *_span(node)))
    includes = []
    for node in captures.get("include", []):
        text = _text(data, node)
        if len(text) > 2:
            includes.append(Include(text[1:-1], node.type == "system_lib_string", *_span(node)))
    includes.sort(key=lambda item: (item.start, item.end))
    for scope in scopes:
        scope.calls.sort(key=lambda u: (u.start, u.end))
        scope.refs.sort(key=lambda u: (u.start, u.end))
    return Parsed(includes, scopes, syntax_diagnostics(root, _BROKEN))


def encode(parsed: Parsed) -> list[Any]:
    scopes = [
        [
            s.decl and [getattr(s.decl, f) for f in Decl.__slots__],
            sorted(s.bound),
            [[u.name, u.start, u.end] for u in s.calls],
            [[u.name, u.start, u.end] for u in s.refs],
        ]
        for s in parsed.scopes
    ]
    includes = [[i.path, i.system, i.start, i.end] for i in parsed.includes]
    return [includes, scopes, parsed.diagnostics]


def decode(value: Any, size: int) -> Parsed:
    """The inverse of ``encode`` for a file of ``size`` bytes. Cached facts come from
    disk: anything ``parse`` could not have produced raises ValueError, and the caller
    parses instead."""
    try:
        includes, scopes, diagnostics = as_list(value)
        parsed = Parsed(
            [Include(*as_list(i)) for i in as_list(includes)],
            [
                Scope(
                    None if decl is None else Decl(*as_list(decl)),
                    set(as_list(bound)),
                    [Use(*as_list(u)) for u in as_list(calls)],
                    [Use(*as_list(u)) for u in as_list(refs)],
                )
                for decl, bound, calls, refs in map(as_list, as_list(scopes))
            ],
            as_list(diagnostics),
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"malformed parse facts: {error}") from None
    if not _sound(parsed, size):
        raise ValueError("parse facts are ill-typed, out of bounds or not a scope list")
    return parsed


_IDENTIFIER = re.compile(r"[^\W\d]\w*")


def _sound(parsed: Parsed, size: int) -> bool:
    """What ``parse`` guarantees: in-bounds spans, string texts, a file scope then
    symbol scopes, and identifier names, so identities stay unique."""

    def span(a: Any, b: Any) -> bool:
        return type(a) is int and type(b) is int and 0 <= a <= b <= size

    def texts(*values: Any) -> bool:
        return all(type(v) is str for v in values)

    scopes = parsed.scopes
    if not scopes or scopes[0].decl is not None:
        return False
    for k, s in enumerate(scopes):
        d, uses = s.decl, (*s.calls, *s.refs)
        if k and not (
            d is not None
            and d.kind in KINDS
            and type(d.name) is str
            and _IDENTIFIER.fullmatch(d.name) is not None
            and span(d.start, d.end)
            and texts(d.signature)
            and type(d.static) is bool
            and type(d.defines) is bool
        ):
            return False
        if not (
            all(span(u.start, u.end) for u in uses) and texts(*s.bound, *(u.name for u in uses))
        ):
            return False
    return texts(*parsed.diagnostics) and all(
        texts(i.path) and type(i.system) is bool and span(i.start, i.end) for i in parsed.includes
    )
