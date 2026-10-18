"""Python language slice: tree-sitter syntax facts and conservative resolution.

Semantics are those of the Rust ``oxide-python-v1`` slice (docs/phase-2b.md § Python
slice): ``class``/``def`` symbols nested by declaration, ``python:`` modules from the
path, resolution only by Python scoping (no type inference), anything shadowed or
rebound stays unresolved, and ``TESTED_BY`` is a heuristic from pytest discovery.

One compiled tree-sitter query collects every fact in C; Python only assigns each
captured node to its innermost declaration scope (a bisect over body ranges) and
never walks the syntax tree recursively.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Any, Literal

import tree_sitter_python
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from oxide.models import Basis, Entity, RelationKind, Resolution, SourceRef

VERSION = "oxide-python-v3"
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


@dataclass(slots=True)
class Parsed:
    """Scope 0 is the module; declaration scopes follow in source order."""

    scopes: list[Scope]
    rebound: set[str]
    diagnostics: list[str]


class Nesting:
    """The innermost scope whose body holds a byte, in O(log depth).

    Scope 0 is the whole file. Bodies nest, so the innermost scope holding a byte is an
    ancestor-or-self of the last body starting at or before it, and ancestors' bodies
    end no earlier. ``jumps[k][j]`` is the 2**j-th ancestor of ``k``: skipping the
    ancestors that end at or before the byte takes O(log depth), where one parent at
    a time is quadratic after a deep nest.
    """

    def __init__(self, size: int) -> None:
        self.bodies: list[tuple[int, int]] = []  # (body start, scope), sorted
        self.ends = [size]
        self.parents = [0]
        self.jumps: list[list[int]] = [[]]

    def add(self, parent: int, body: tuple[int, int]) -> None:
        own = len(self.ends)
        up = [parent]
        while len(self.jumps[up[-1]]) >= len(up):
            up.append(self.jumps[up[-1]][len(up) - 1])
        self.ends.append(body[1])
        self.parents.append(parent)
        self.jumps.append(up)
        bisect.insort(self.bodies, (body[0], own))

    def scope_at(self, at: int) -> int:
        i = bisect.bisect_right(self.bodies, (at, len(self.ends))) - 1
        k = self.bodies[i][1] if i >= 0 else 0
        if not k or at < self.ends[k]:
            return k
        for j in range(len(self.jumps[k]) - 1, -1, -1):
            if j < len(self.jumps[k]) and (up := self.jumps[k][j]) and self.ends[up] <= at:
                k = up
        return self.parents[k]


class _Facts:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.scopes = [Scope(None, None, (0, len(data)))]
        self.nesting = Nesting(len(data))
        self.scope_at = self.nesting.scope_at

    def text(self, node: Node | None) -> str:
        if node is None:
            return ""
        return self.data[node.start_byte : node.end_byte].decode("utf-8", "replace")

    def bind(self, scope: int, node: Node | None) -> None:
        if node is not None:
            self.scopes[scope].bound.update(self.text(n) for n in _targets(node))

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
            return
        parameters = node.child_by_field_name("parameters")
        for parameter in parameters.named_children if parameters is not None else []:
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


def _targets(node: Node) -> list[Node]:
    """Identifiers a target binds; attribute and subscript targets bind nothing."""
    found, stack = [], [node]
    while stack:
        current = stack.pop()
        if current.type == "identifier":
            found.append(current)
        elif current.type not in ("attribute", "subscript"):
            stack.extend(current.named_children)
    return found


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
        if name is not None:
            use = Use(name, call.start_byte, call.end_byte)
            scopes[facts.scope_at(call.start_byte)].calls.append(use)
    for node in captures.get("import", []):
        scopes[facts.scope_at(node.start_byte)].imports.extend(facts.imports(node))
    for node in captures.get("bind", []):
        facts.bind(facts.scope_at(node.start_byte), node)
    rebound = {facts.text(n) for node in captures.get("rebind", []) for n in node.named_children}
    for scope in scopes:
        scope.calls.sort(key=lambda u: (u.start, u.end))
        scope.imports.sort(key=lambda i: (i.start, i.end, i.item))
    return Parsed(scopes, rebound, syntax_diagnostics(root, _BROKEN))


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
FACTS_VERSION = "oxide-python-facts-v2"


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
        ]
        for s in parsed.scopes
    ]
    return [scopes, sorted(parsed.rebound), parsed.diagnostics]


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
        scopes, rebound, diagnostics = as_list(value)
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
                )
                for parent, decl, body, bound, imports, calls, bases in map(
                    as_list, as_list(scopes)
                )
            ],
            set(as_list(rebound)),
            as_list(diagnostics),
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
            and texts(*s.bound, *(u.name for u in uses))
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
    return texts(*parsed.rebound, *parsed.diagnostics)


@dataclass(frozen=True, slots=True)
class Edge:
    """A derived relation before storage; ``targets`` holds every candidate endpoint."""

    kind: RelationKind
    source: str
    targets: tuple[str, ...]
    resolution: Resolution
    basis: Basis
    name: str | None
    evidence: SourceRef | None


# Resolution result: candidate entity keys, or None when the name is opaque.
type Res = list[str] | None


def module_name(path: str) -> str | None:
    if not path.endswith(".py"):
        return None
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) if parts and all(p.isidentifier() for p in parts) else None


def module_key(name: str) -> str:
    return f"module:python:{name}"


@dataclass(slots=True)
class _File:
    path: str
    digest: str
    parsed: Parsed
    keys: list[str]
    children: list[list[int]]
    module: str | None
    init: bool


def symbol_keys(path: str, decls: list[tuple[int, str, int]]) -> tuple[list[str], list[list[int]]]:
    """Keys of the file (index 0) and of each declaration ``(parent, name, start)``, in
    order (index k + 1, parents first), and each one's children (ADR-0010 § SymbolId)."""
    children: list[list[int]] = [[] for _ in range(len(decls) + 1)]
    siblings: dict[tuple[int, str], list[tuple[int, int]]] = {}
    for k, (parent, name, start) in enumerate(decls, start=1):
        children[parent].append(k)
        siblings.setdefault((parent, name), []).append((start, k))
    ordinals = [0] * (len(decls) + 1)
    for group in siblings.values():
        for ordinal, (_, k) in enumerate(sorted(group)):
            ordinals[k] = ordinal
    segments: list[str] = [""]
    for k, (parent, name, _) in enumerate(decls, start=1):
        own = name + (f"~{ordinals[k]}" if ordinals[k] else "")
        segments.append(f"{segments[parent]}.{own}" if segments[parent] else own)
    keys = [f"file:{path}"] + [f"symbol:{path}#{s}" for s in segments[1:]]
    return keys, children


def _decls(parsed: Parsed) -> list[tuple[int, str, int]]:
    out = []
    for scope in parsed.scopes[1:]:
        assert scope.parent is not None and scope.decl is not None
        out.append((scope.parent, scope.decl.name, scope.decl.start))
    return out


def _is_test(file: _File, k: int) -> bool:
    name = file.path.rsplit("/", 1)[-1]
    if not (name.startswith("test_") or name.endswith("_test.py")):
        return False
    scopes = file.parsed.scopes
    decl, parent = scopes[k].decl, scopes[k].parent
    assert decl is not None
    if parent == 0:
        return decl.name.startswith("Test" if decl.cls else "test")
    if parent is None:
        return False
    outer = scopes[parent]
    return (
        not decl.cls
        and decl.name.startswith("test")
        and outer.parent == 0
        and outer.decl is not None
        and outer.decl.cls
        and outer.decl.name.startswith("Test")
    )


def _bound_name(item: Import) -> str | None:
    if item.item == "module":
        return item.alias or (item.module[0] if item.module else None)
    if item.item == "name":
        return item.alias or item.name
    return None


def _import_text(item: Import) -> str:
    text = "." * item.level + ".".join(item.module)
    if item.item == "name" and item.name is not None:
        text += ("." if item.module else "") + item.name
    return text


def edge_target(res: Res, symbols_only: bool) -> tuple[tuple[str, ...], Resolution, Basis]:
    if res and (not symbols_only or all(k.startswith("symbol:") for k in res)):
        if len(res) == 1:
            return (res[0],), "resolved", "resolved"
        return tuple(res), "ambiguous", "syntactic"
    return (), "unresolved", "syntactic"


def structural(kind: RelationKind, source: str, target: str, at: SourceRef | None) -> Edge:
    return Edge(kind, source, (target,), "resolved", "syntactic", None, at)


class Resolver:
    """Conservative cross-file resolution over every Python file of one capture."""

    def __init__(self, files: list[tuple[str, str, Parsed]]) -> None:
        self.files: list[_File] = []
        self.modules: dict[str, list[int]] = {}
        self.tests: set[str] = set()
        for path, file_digest, parsed in files:
            keys, children = symbol_keys(path, _decls(parsed))
            module = module_name(path)
            if module is not None:
                self.modules.setdefault(module, []).append(len(self.files))
            init = path.rsplit("/", 1)[-1] == "__init__.py"
            file = _File(path, file_digest, parsed, keys, children, module, init)
            self.tests.update(keys[k] for k in range(1, len(parsed.scopes)) if _is_test(file, k))
            self.files.append(file)

    def derive(self, entities: list[Entity], edges: list[Edge]) -> None:
        for name in sorted(self.modules):
            key = module_key(name)
            entities.append(Entity(key, "module", "module", name, None, None, False))
            edges.append(structural("contains", "repo", key, None))
            for i in self.modules[name]:
                file_key = f"file:{self.files[i].path}"
                edges.append(structural("contains_logical", key, file_key, None))
        for i in range(len(self.files)):
            self._emit(i, entities, edges)

    def _emit(self, i: int, entities: list[Entity], edges: list[Edge]) -> None:
        file = self.files[i]
        scopes = file.parsed.scopes

        def evidence(start: int, end: int) -> SourceRef:
            return SourceRef(file.path, start, end, file.digest)

        def edge(kind: RelationKind, source: str, res: Res, name: str, at: Use | Import) -> Edge:
            targets, resolution, basis = edge_target(res, kind == "calls")
            shown = None if resolution == "resolved" else name
            return Edge(kind, source, targets, resolution, basis, shown, evidence(at.start, at.end))

        bindings: list[list[tuple[str, Res]]] = []
        for k, scope in enumerate(scopes):
            bound: list[tuple[str, Res]] = []
            for item in scope.imports:
                target, binding = self._import(i, item)
                edges.append(edge("imports", file.keys[k], target, _import_text(item), item))
                name = _bound_name(item)
                if name is not None and binding is not False:
                    bound.append((name, binding))
            bindings.append(bound)

        for k, scope in enumerate(scopes):
            key = file.keys[k]
            if scope.decl is not None and scope.parent is not None:
                decl, parent = scope.decl, scope.parent
                outer = scopes[parent].decl
                kind = "class" if decl.cls else "method" if outer and outer.cls else "function"
                source = evidence(decl.start, decl.end)
                test = key in self.tests
                entities.append(
                    Entity(key, "symbol", kind, decl.name, decl.signature, source, test)
                )
                edges.append(structural("contains", file.keys[parent], key, None))
                if parent == 0 and file.module is not None:
                    edges.append(structural("defines", module_key(file.module), key, source))
                for base in scope.bases:
                    res = self._lookup(i, bindings, parent, base.name)
                    edges.append(edge("references", key, res, base.name, base))
            for call in scope.calls:
                relation = edge(
                    "calls", key, self._lookup(i, bindings, k, call.name), call.name, call
                )
                callee = relation.targets[0] if relation.resolution == "resolved" else None
                test_key = self._enclosing_test(i, k)
                if (
                    callee
                    and callee.startswith("symbol:")
                    and callee not in self.tests
                    and test_key
                ):
                    at = evidence(call.start, call.end)
                    edges.append(
                        Edge("tested_by", callee, (test_key,), "resolved", "heuristic", None, at)
                    )
                edges.append(relation)

    def _enclosing_test(self, i: int, k: int | None) -> str | None:
        file = self.files[i]
        while k:
            if file.keys[k] in self.tests:
                return file.keys[k]
            k = file.parsed.scopes[k].parent
        return None

    def _lookup(self, i: int, bindings: list[list[tuple[str, Res]]], k: int, name: str) -> Res:
        file = self.files[i]
        if "." in name or name in file.parsed.rebound:
            return None
        scopes = file.parsed.scopes
        current: int | None = k
        while current is not None:
            decl = scopes[current].decl
            # Enclosing class bodies are not scopes for nested functions.
            if current == k or decl is None or not decl.cls:
                found = self._in_scope(i, current, name, bindings[current])
                if found is not False:
                    return found
            current = scopes[current].parent
        return None

    def _in_scope(
        self, i: int, s: int, name: str, bindings: list[tuple[str, Res]] | None
    ) -> Res | Literal[False]:
        """Candidates bound to ``name`` in scope ``s``; False when ``s`` does not bind it."""
        file = self.files[i]
        scope = file.parsed.scopes[s]
        if any(m.item == "star" for m in scope.imports) or name in scope.bound:
            return None
        found = [
            file.keys[c]
            for c in file.children[s]
            if (decl := file.parsed.scopes[c].decl) is not None and decl.name == name
        ]
        bound = bool(found)
        if bindings is not None:
            for bound_name, res in bindings:
                if bound_name == name:
                    if res is None:
                        return None
                    bound = True
                    found.extend(res)
        elif any(_bound_name(m) == name for m in scope.imports):
            return None
        return sorted(set(found)) if bound else False

    def _import(self, i: int, item: Import) -> tuple[Res, Res | Literal[False]]:
        """(what the import names, what it binds); False binds nothing."""
        base = self._absolute(i, item)
        module: Res = self._module(base) if base is not None else None
        if item.item == "module":
            if item.alias is not None:
                return module, module
            return module, self._module(item.module[0]) if item.module else None
        if item.item == "name" and item.name is not None:
            found = self._from(base, item.name) if base is not None else None
            return found, found
        return module, False

    def _module(self, name: str) -> Res:
        return [module_key(name)] if name in self.modules else None

    def _from(self, module: str, name: str) -> Res:
        found: list[str] = []
        if f"{module}.{name}" in self.modules:
            found.append(module_key(f"{module}.{name}"))
        for j in self.modules.get(module, []):
            if name in self.files[j].parsed.rebound:
                return None
            res = self._in_scope(j, 0, name, None)
            if res is None:
                return None
            if res is not False:
                found.extend(res)
        return sorted(set(found)) or None

    def _absolute(self, i: int, item: Import) -> str | None:
        if item.level == 0:
            return ".".join(item.module) or None
        file = self.files[i]
        if file.module is None:
            return None
        package = file.module.split(".")
        if not file.init:
            package.pop()
        for _ in range(1, item.level):
            if not package:
                return None
            package.pop()
        return ".".join([*package, *item.module]) if package else None
