"""Python language slice: tree-sitter syntax facts (resolution is in ``python_resolve``).

Semantics are those of the Rust ``oxide-python-v1`` slice (docs/phase-2b.md § Python
slice): ``class``/``def`` symbols nested by declaration, ``python:`` modules from the
path, resolution only by Python scoping (no type inference), anything shadowed or
rebound stays unresolved, and ``TESTED_BY`` is a heuristic from pytest discovery.

One compiled tree-sitter query collects every fact in C; Python only assigns each
captured node to its innermost declaration scope (a bisect over body ranges) and
never walks the syntax tree recursively.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import tree_sitter_python
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from oxide.core.languages.nesting import Nesting

VERSION = "oxide-python-v7"
MAX_DIAGNOSTICS = 16
# tree-sitter's query cursor keeps node depth in 16 bits: it drops matches below this
# depth and its time turns super-linear (docs/parser-limits.md), so a file whose syntax
# nests deeper is unsupported rather than silently incomplete.
MAX_DEPTH = 65_535
TOO_DEEP = f"syntax nests deeper than {MAX_DEPTH:,} levels"

LANGUAGE = Language(tree_sitter_python.language())
_QUERY = Query(
    LANGUAGE,
    """
    (function_definition) @def
    (class_definition) @def
    (call) @call
    (import_statement) @import
    (import_from_statement) @import
    (global_statement) @rebind
    (nonlocal_statement) @rebind
    (assignment left: (_) @bind)
    (assignment left: (identifier) right: (call)) @made
    (augmented_assignment left: (_) @bind)
    (for_statement left: (_) @bind)
    (for_in_clause left: (_) @bind)
    (type_alias_statement left: (_) @bind)
    (named_expression name: (_) @bind)
    (as_pattern alias: (_) @bind)
    (delete_statement) @bind
    (case_pattern) @bind
    (lambda parameters: (_) @bind)
    """,
)
# Run only on trees with errors: ``(MISSING)`` inspects every node, which costs seconds
# on deeply nested source.
_BROKEN = Query(LANGUAGE, "(ERROR) @error (MISSING) @missing")


@dataclass(slots=True)
class Decl:
    cls: bool
    name: str
    start: int
    end: int
    signature: str


@dataclass(frozen=True, slots=True)
class Import:
    level: int
    module: tuple[str, ...]
    item: Literal["module", "name", "star"]
    name: str | None
    alias: str | None
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Use:
    name: str
    start: int
    end: int


@dataclass(slots=True)
class Scope:
    parent: int | None
    decl: Decl | None
    body: tuple[int, int]
    bound: set[str] = field(default_factory=set)
    imports: list[Import] = field(default_factory=list)
    calls: list[Use] = field(default_factory=list)
    bases: list[Use] = field(default_factory=list)
    # A method's first parameter when it is bound nowhere else (not a ``staticmethod``).
    receiver: str | None = None
    # Names bound exactly once in this scope, by ``name = callee(...)``: name -> callee.
    made: dict[str, str] = field(default_factory=dict)
    # A class with bases that ``bases`` does not record (keywords, splats, expressions).
    open: bool = False
    # ``(receiver, name)`` of each ``receiver.name = ...`` (or ``del``) on a plain name.
    assigned: set[tuple[str, str]] = field(default_factory=set)


@dataclass(slots=True)
class Parsed:
    """Scope 0 is the module; declaration scopes follow in source order."""

    scopes: list[Scope]
    rebound: set[str]
    diagnostics: list[str]
    # Attribute names the file assigns or deletes (``x.name = ...``) on any receiver.
    attrs: set[str] = field(default_factory=set)


class _Facts:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.scopes = [Scope(None, None, (0, len(data)))]
        self.nesting = Nesting(len(data))
        self.scope_at = self.nesting.scope_at
        self.binds: dict[tuple[int, str], int] = {}
        self.attrs: set[str] = set()

    def text(self, node: Node | None) -> str:
        if node is None:
            return ""
        return self.data[node.start_byte : node.end_byte].decode("utf-8", "replace")

    def bind(self, scope: int, node: Node | None) -> None:
        if node is None:
            return
        found, attributes = _targets(node)
        for name in map(self.text, found):
            self.scopes[scope].bound.add(name)
            self.binds[scope, name] = self.binds.get((scope, name), 0) + 1
        for a in attributes:
            name = self.text(a.child_by_field_name("attribute"))
            receiver = a.child_by_field_name("object")
            self.attrs.add(name)
            if receiver is not None and receiver.type == "identifier":
                self.scopes[scope].assigned.add((self.text(receiver), name))

    def dotted(self, node: Node) -> str | None:
        parts: list[str] = []
        current: Node | None = node
        while current is not None and current.type == "attribute":
            parts.append(self.text(current.child_by_field_name("attribute")))
            current = current.child_by_field_name("object")
        if current is None or current.type != "identifier":
            return None
        parts.append(self.text(current))
        return ".".join(reversed(parts))

    def super_member(self, callee: Node) -> str | None:
        """``super().name`` for a zero-argument ``super()`` receiver, else None."""
        receiver = callee.child_by_field_name("object")
        if receiver is None or receiver.type != "call":
            return None
        function = receiver.child_by_field_name("function")
        arguments = receiver.child_by_field_name("arguments")
        if function is None or self.dotted(function) != "super":
            return None
        if arguments is None or arguments.named_children:
            return None
        return f"super().{self.text(callee.child_by_field_name('attribute'))}"

    def declaration(self, node: Node) -> None:
        name = self.text(node.child_by_field_name("name"))
        if not name:
            return
        body = node.child_by_field_name("body")
        header_end = body.start_byte if body is not None else node.end_byte
        parent = node.parent
        decorated = parent is not None and parent.type == "decorated_definition"
        start = parent.start_byte if decorated and parent is not None else node.start_byte
        header = self.data[node.start_byte : header_end].decode("utf-8", "replace")
        decl = Decl(
            cls=node.type == "class_definition",
            name=name,
            start=start,
            end=node.end_byte,
            signature=header.strip().rstrip(":").rstrip(),
        )
        own = len(self.scopes)
        span = (body.start_byte, body.end_byte) if body is not None else (node.end_byte,) * 2
        enclosing = self.scope_at(node.start_byte)
        self.scopes.append(Scope(enclosing, decl, span))
        self.nesting.add(enclosing, span)
        if decl.cls:
            arguments = node.child_by_field_name("superclasses")
            for base in arguments.named_children if arguments is not None else []:
                dotted = self.dotted(base)
                if dotted is not None:
                    self.scopes[own].bases.append(Use(dotted, base.start_byte, base.end_byte))
                else:
                    self.scopes[own].open = True
            return
        parameters = node.child_by_field_name("parameters")
        params = parameters.named_children if parameters is not None else []
        outer = self.scopes[enclosing].decl
        static = (
            decorated
            and parent is not None
            and any(
                self.dotted(d.named_children[0]) == "staticmethod"
                for d in parent.named_children
                if d.type == "decorator" and d.named_children
            )
        )
        if outer is not None and outer.cls and params and not static:
            first = params[0]
            if first.type == "typed_parameter":
                first = first.named_children[0]
            if first.type == "identifier":
                self.scopes[own].receiver = self.text(first)
        for parameter in params:
            if parameter.type in ("default_parameter", "typed_default_parameter"):
                self.bind(own, parameter.child_by_field_name("name"))
            elif parameter.type == "typed_parameter":
                annotation = parameter.child_by_field_name("type")
                for child in parameter.named_children:
                    if child != annotation:
                        self.bind(own, child)
            else:
                self.bind(own, parameter)

    def aliased(self, node: Node) -> tuple[tuple[str, ...], str | None]:
        if node.type == "aliased_import":
            name = node.child_by_field_name("name")
            alias = node.child_by_field_name("alias")
            segments = tuple(self.text(n) for n in name.named_children) if name else ()
            return segments, self.text(alias) if alias is not None else None
        return tuple(self.text(n) for n in node.named_children), None

    def imports(self, node: Node) -> list[Import]:
        names = node.children_by_field_name("name")
        if node.type == "import_statement":
            out = []
            for name in names:
                module, alias = self.aliased(name)
                out.append(Import(0, module, "module", None, alias, name.start_byte, name.end_byte))
            return out
        source = node.child_by_field_name("module_name")
        if source is None:
            return []
        level, module = 0, tuple(self.text(n) for n in source.named_children)
        if source.type == "relative_import":
            module = ()
            for child in source.named_children:
                if child.type == "import_prefix":
                    level = self.text(child).count(".")
                else:
                    module = tuple(self.text(n) for n in child.named_children)
        out = []
        if any(child.type == "wildcard_import" for child in node.named_children):
            out.append(Import(level, module, "star", None, None, node.start_byte, node.end_byte))
        for name in names:
            dotted, alias = self.aliased(name)
            item = Import(
                level, module, "name", ".".join(dotted), alias, name.start_byte, name.end_byte
            )
            out.append(item)
        return out


def _targets(node: Node) -> tuple[list[Node], list[Node]]:
    """Identifiers a target binds, and its attribute targets (which bind no name)."""
    found, attributes, stack = [], [], [node]
    while stack:
        current = stack.pop()
        if current.type == "identifier":
            found.append(current)
        elif current.type == "attribute":
            attributes.append(current)
        elif current.type != "subscript":
            stack.extend(current.named_children)
    return found, attributes


def parse(data: bytes) -> Parsed | str:
    """Syntax facts, or the reason the file is unsupported."""
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "Python source is not UTF-8"
    root = Parser(LANGUAGE).parse(data).root_node
    if too_deep(root):
        return TOO_DEEP
    captures = QueryCursor(_QUERY).captures(root)
    facts = _Facts(data)
    for node in sorted(captures.get("def", []), key=lambda n: n.start_byte):
        facts.declaration(node)
    scopes = facts.scopes
    for call in captures.get("call", []):
        # Down from the call, never ``callee.parent``: tree-sitter finds a parent by
        # descending from the root, which is quadratic over a long method chain.
        callee = call.child_by_field_name("function")
        name = facts.dotted(callee) if callee is not None else None
        if name is None and callee is not None and callee.type == "attribute":
            name = facts.super_member(callee)
        if name is not None:
            use = Use(name, call.start_byte, call.end_byte)
            scopes[facts.scope_at(call.start_byte)].calls.append(use)
    for node in captures.get("import", []):
        scopes[facts.scope_at(node.start_byte)].imports.extend(facts.imports(node))
    for node in captures.get("bind", []):
        facts.bind(facts.scope_at(node.start_byte), node)
    for node in captures.get("made", []):
        k = facts.scope_at(node.start_byte)
        bound = facts.text(node.child_by_field_name("left"))
        right = node.child_by_field_name("right")
        function = right.child_by_field_name("function") if right is not None else None
        maker = facts.dotted(function) if function is not None else None
        if maker is not None and facts.binds.get((k, bound)) == 1:
            scopes[k].made[bound] = maker
    rebound = {facts.text(n) for node in captures.get("rebind", []) for n in node.named_children}
    for k, scope in enumerate(scopes):
        if scope.receiver is not None and facts.binds.get((k, scope.receiver)) != 1:
            scope.receiver = None
    for scope in scopes:
        scope.calls.sort(key=lambda u: (u.start, u.end))
        scope.imports.sort(key=lambda i: (i.start, i.end, i.item))
    return Parsed(scopes, rebound, syntax_diagnostics(root, _BROKEN), facts.attrs)


def too_deep(root: Node) -> bool:
    """Whether any node lies deeper than ``MAX_DEPTH``. A subtree is no taller than its
    node count, so only subtrees large enough to reach that depth are entered."""
    stack = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > MAX_DEPTH:
            return True
        stack.extend(
            (child, depth + 1)
            for child in node.children
            if depth + child.descendant_count > MAX_DEPTH
        )
    return False


def syntax_diagnostics(root: Node, broken: Query) -> list[str]:
    """Bounded diagnostics from ``broken``'s ``@error`` and ``@missing`` captures."""
    diagnostics: list[str] = []
    if root.has_error:
        captures = QueryCursor(broken).captures(root)
        nodes = [*captures.get("error", []), *captures.get("missing", [])]
        for node in sorted(nodes, key=lambda n: (n.start_byte, -n.end_byte, n.is_missing)):
            if node.is_missing:
                diagnostics.append(f"missing {node.type} at byte {node.start_byte}")
            else:
                diagnostics.append(f"syntax error at bytes {node.start_byte}..{node.end_byte}")
        if len(diagnostics) > MAX_DIAGNOSTICS:
            omitted = len(diagnostics) - MAX_DIAGNOSTICS
            diagnostics = [*diagnostics[:MAX_DIAGNOSTICS], f"{omitted} more syntax errors omitted"]
    return diagnostics


# The JSON shape of ``Parsed`` that generations retain for reuse; bump on any change.
FACTS_VERSION = "oxide-python-facts-v4"


def encode(parsed: Parsed) -> list[Any]:
    scopes = [
        [
            s.parent,
            s.decl and [s.decl.cls, s.decl.name, s.decl.start, s.decl.end, s.decl.signature],
            list(s.body),
            sorted(s.bound),
            [[i.level, list(i.module), i.item, i.name, i.alias, i.start, i.end] for i in s.imports],
            [[u.name, u.start, u.end] for u in s.calls],
            [[u.name, u.start, u.end] for u in s.bases],
            s.receiver,
            sorted(s.made.items()),
            s.open,
            sorted(map(list, s.assigned)),
        ]
        for s in parsed.scopes
    ]
    return [scopes, sorted(parsed.rebound), parsed.diagnostics, sorted(parsed.attrs)]


def as_list(value: Any) -> list[Any]:
    if type(value) is not list:
        raise TypeError(f"expected a list, got {type(value).__name__}")
    return value


def decode(value: Any, size: int) -> Parsed:
    """The inverse of ``encode`` for a file of ``size`` bytes: ``decode(encode(p), n) == p``.
    Cached facts are read from disk, so they are checked here. Anything ``parse`` could
    not have produced raises ValueError, and the caller parses instead. That way the
    resolver only sees well-typed, in-bounds facts: a tree whose parents precede their
    scopes, and identifier names, so keys stay unique."""
    try:
        scopes, rebound, diagnostics, attrs = as_list(value)
        parsed = Parsed(
            [
                Scope(
                    parent,
                    None if decl is None else Decl(*as_list(decl)),
                    tuple(as_list(body)),  # length checked by _sound
                    set(as_list(bound)),
                    [
                        Import(level, tuple(as_list(module)), *rest)
                        for level, module, *rest in map(as_list, as_list(imports))
                    ],
                    [Use(*as_list(u)) for u in as_list(calls)],
                    [Use(*as_list(u)) for u in as_list(bases)],
                    receiver,
                    dict(map(as_list, as_list(made))),
                    opened,
                    {(r, n) for r, n in map(as_list, as_list(assigned))},
                )
                for (
                    parent,
                    decl,
                    body,
                    bound,
                    imports,
                    calls,
                    bases,
                    receiver,
                    made,
                    opened,
                    assigned,
                ) in map(as_list, as_list(scopes))
            ],
            set(as_list(rebound)),
            as_list(diagnostics),
            set(as_list(attrs)),
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"malformed parse facts: {error}") from None
    if not _sound(parsed, size):
        raise ValueError("parse facts are ill-typed, out of bounds or not a scope tree")
    return parsed


def _sound(parsed: Parsed, size: int) -> bool:
    """What ``parse`` guarantees of the facts of a file of ``size`` bytes."""

    def spans(*pairs: tuple[Any, ...]) -> bool:
        return all(
            len(p) == 2 and type(p[0]) is int and type(p[1]) is int and 0 <= p[0] <= p[1] <= size
            for p in pairs
        )

    def texts(*values: Any) -> bool:
        return all(type(v) is str for v in values)

    scopes = parsed.scopes
    if not scopes or (scopes[0].parent, scopes[0].decl, scopes[0].body) != (None, None, (0, size)):
        return False
    for k, s in enumerate(scopes):
        d = s.decl
        if k and not (
            type(s.parent) is int
            and 0 <= s.parent < k
            and d is not None
            and type(d.cls) is bool
            and type(d.name) is str
            and d.name.isidentifier()
            and texts(d.signature)
            and spans((d.start, d.end))
        ):
            return False
        uses = (*s.calls, *s.bases)
        if not (
            spans(s.body, *((u.start, u.end) for u in uses), *((i.start, i.end) for i in s.imports))
            and texts(*s.bound, *(u.name for u in uses), *s.made, *s.made.values())
            and (s.receiver is None or (type(s.receiver) is str and s.receiver.isidentifier()))
            and type(s.open) is bool
            and texts(*(x for pair in s.assigned for x in pair))
            and all(
                type(i.level) is int
                and i.level >= 0
                and texts(*i.module)
                and i.item in ("module", "name", "star")
                and all(v is None or type(v) is str for v in (i.name, i.alias))
                for i in s.imports
            )
        ):
            return False
    return texts(*parsed.rebound, *parsed.diagnostics, *parsed.attrs)
