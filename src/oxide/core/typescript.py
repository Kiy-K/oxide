"""TypeScript language slice: tree-sitter syntax facts and conservative ES-module
resolution (docs/typescript.md).

Same shape as the Python slice: one compiled query per dialect (``.ts``/``.mts``/
``.cts`` and ``.tsx``) collects every fact in C, Python only assigns captured nodes
to their innermost declaration scope, and resolution follows lexical scoping and
the import/export graph of relative specifiers and of ``tsconfig`` ``paths`` and
``baseUrl`` (``tsconfig.py``): no type inference, no packages. Anything shadowed,
rebound or reached through an unresolvable module stays unresolved.
"""

from __future__ import annotations

import bisect
import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

import tree_sitter_typescript
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from oxide.core import tsconfig
from oxide.core.python import (
    Edge,
    Res,
    Use,
    as_list,
    edge_target,
    structural,
    symbol_keys,
    syntax_diagnostics,
)
from oxide.models import Entity, RelationKind, SourceRef

VERSION = "oxide-typescript-v2"
# The JSON shape of ``Parsed`` that generations retain for reuse; bump on any change.
FACTS_VERSION = "oxide-typescript-facts-v1"

Dialect = Literal["typescript", "tsx"]
_COMMON = """
(function_declaration) @def
(generator_function_declaration) @def
(function_signature) @def
(class_declaration) @def
(abstract_class_declaration) @def
(interface_declaration) @def
(enum_declaration) @def
(type_alias_declaration) @def
(internal_module) @def
(module) @def
(method_definition) @def
(method_signature) @def
(abstract_method_signature) @def
(variable_declarator value: [(arrow_function) (function_expression) (generator_function)]) @def
(public_field_definition value: [(arrow_function) (function_expression)]) @def
(export_statement value: [(arrow_function) (function_expression) (class)] @def)
(export_statement declaration: [
  (lexical_declaration (variable_declarator) @def)
  (variable_declaration (variable_declarator) @def)
  (ambient_declaration [
    (lexical_declaration (variable_declarator) @def)
    (variable_declaration (variable_declarator) @def)])])
(call_expression function: (_) @callee)
(new_expression constructor: (_) @callee)
(import_statement) @import
(export_statement) @export
(variable_declarator name: (_) @bind)
(required_parameter pattern: (_) @bind)
(optional_parameter pattern: (_) @bind)
(arrow_function parameter: (_) @bind)
(catch_clause parameter: (_) @bind)
(for_in_statement left: (_) @bind)
(import_alias . (identifier) @bind)
(assignment_expression left: (identifier) @rebind)
(augmented_assignment_expression left: (identifier) @rebind)
(update_expression argument: (identifier) @rebind)
(ERROR) @error
(MISSING) @missing
"""
_JSX = """
(jsx_opening_element name: (_) @callee)
(jsx_self_closing_element name: (_) @callee)
"""
_LANGUAGES: dict[Dialect, Language] = {
    "typescript": Language(tree_sitter_typescript.language_typescript()),
    "tsx": Language(tree_sitter_typescript.language_tsx()),
}
_QUERIES: dict[Dialect, Query] = {
    "typescript": Query(_LANGUAGES["typescript"], _COMMON),
    "tsx": Query(_LANGUAGES["tsx"], _COMMON + _JSX),
}

KINDS = {
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "function_signature": "function",
    "variable_declarator": "function",
    "arrow_function": "function",
    "function_expression": "function",
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "class": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "type_alias_declaration": "type",
    "internal_module": "namespace",
    "module": "namespace",
    "method_definition": "method",
    "method_signature": "method",
    "abstract_method_signature": "method",
    "public_field_definition": "method",
}
_FUNCTIONS = ("arrow_function", "function_expression", "generator_function")
_KIND_NAMES = frozenset([*KINDS.values(), "variable"])
# Their members are reached through ``this`` or instances, never as bare names.
_MEMBER_SCOPES = ("class", "interface", "enum")
# Synthetic names of anonymous declarations; never identifiers (ADR-0010 § SymbolId).
DEFAULT, AMBIENT = "<default>", "<module>"
_NAME = re.compile(r"[^\s.#~<>]+|<default>|<module>")
_PREFIX = "module:typescript:"


@dataclass(slots=True)
class Decl:
    kind: str
    name: str
    start: int
    end: int
    signature: str
    # The name a top-level declaration is exported as (``default`` included).
    export: str | None


@dataclass(frozen=True, slots=True)
class Import:
    """One import or export clause of a module. ``source`` is the specifier, None for
    ``export { local as name }``; ``local`` is the name it binds, ``export`` the name
    it exports, ``name`` the export it imports (``default`` for a default import)."""

    source: str | None
    item: Literal["name", "namespace", "star", "side"]
    name: str | None
    local: str | None
    export: str | None
    start: int
    end: int


@dataclass(slots=True)
class Scope:
    parent: int | None
    decl: Decl | None
    body: tuple[int, int]
    bound: set[str] = field(default_factory=set)
    calls: list[Use] = field(default_factory=list)
    bases: list[Use] = field(default_factory=list)


@dataclass(slots=True)
class Parsed:
    """Scope 0 is the module; declaration scopes follow in source order."""

    scopes: list[Scope]
    imports: list[Import]
    rebound: set[str]
    diagnostics: list[str]


def dialect(path: str) -> Dialect | None:
    """The grammar of a TypeScript path (``.d.ts`` included), None for other files."""
    if path.endswith(".tsx"):
        return "tsx"
    return "typescript" if path.endswith((".ts", ".mts", ".cts")) else None


def _targets(node: Node) -> list[Node]:
    """Identifiers a binding pattern binds; defaults, keys and types bind nothing."""
    found, stack = [], [node]
    while stack:
        current = stack.pop()
        kind = current.type
        if kind in ("identifier", "shorthand_property_identifier_pattern"):
            found.append(current)
        elif kind in ("pair_pattern", "assignment_pattern", "object_assignment_pattern"):
            part = current.child_by_field_name("value" if kind == "pair_pattern" else "left")
            stack.extend([part] if part is not None else [])
        elif kind in ("object_pattern", "array_pattern", "rest_pattern"):
            stack.extend(current.named_children)
    return found


def _span(node: Node) -> tuple[int, int]:
    return node.start_byte, node.end_byte


class _Facts:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.scopes = [Scope(None, None, (0, len(data)))]
        self.bodies: list[tuple[int, int]] = []  # as in the Python slice
        self.declarators: set[tuple[int, int]] = set()  # those that are declarations

    def text(self, node: Node | None) -> str:
        if node is None:
            return ""
        return self.data[node.start_byte : node.end_byte].decode("utf-8", "replace")

    def scope_at(self, at: int) -> int:
        i = bisect.bisect_right(self.bodies, (at, len(self.scopes))) - 1
        k: int | None = self.bodies[i][1] if i >= 0 else 0
        while k:
            start, end = self.scopes[k].body
            if start <= at < end:
                return k
            k = self.scopes[k].parent
        return 0

    def bind(self, scope: int, node: Node) -> None:
        parent = node.parent
        if parent is not None and _span(parent) in self.declarators:
            return  # ``const f = () => ...`` declares f
        self.scopes[scope].bound.update(self.text(n) for n in _targets(node))

    def dotted(self, node: Node) -> str | None:
        """``a.b.c`` for a member chain over an identifier or ``this``, else None."""
        parts: list[str] = []
        current: Node | None = node
        while current is not None and current.type == "member_expression":
            prop = current.child_by_field_name("property")
            if prop is None or prop.type != "property_identifier":
                return None
            parts.append(self.text(prop))
            current = current.child_by_field_name("object")
        if current is None or current.type not in ("identifier", "this"):
            return None
        parts.append(self.text(current))
        return ".".join(reversed(parts))

    def base(self, node: Node) -> str | None:
        if node.type == "generic_type":
            node = node.child_by_field_name("name") or node
        if node.type in ("type_identifier", "identifier"):
            return self.text(node)
        if node.type == "nested_type_identifier":
            return re.sub(r"\s+", "", self.text(node))
        return self.dotted(node)

    def name(self, node: Node) -> str | None:
        if node.type in ("arrow_function", "function_expression", "class"):
            return DEFAULT  # captured only as ``export default <value>``
        name = node.child_by_field_name("name")
        if name is None:
            return None
        if node.type == "module" and name.type == "string":
            return AMBIENT  # declare module "pkg" { ... }
        while name.type == "nested_identifier":  # namespace A.B declares A
            name = name.child_by_field_name("object") or name.named_children[0]
        if name.type in ("identifier", "type_identifier", "property_identifier"):
            return self.text(name)
        return None  # computed, string, numeric and #private names

    def declaration(self, node: Node) -> None:
        name = self.name(node)
        if name is None:
            return
        kind = KINDS[node.type]
        fn = node
        value = node.child_by_field_name("value")
        if node.type in ("variable_declarator", "public_field_definition"):
            self.declarators.add(_span(node))
            if value is not None and value.type in _FUNCTIONS:
                fn = value
            else:
                kind = "variable"  # an exported variable: its initializer is its body
        # The statement holding the declaration (``declare``, ``export``); its range is
        # the declaration's unless it declares several. Exports count only at module level.
        outer, export = node, None
        parent = node.parent
        lone = True
        if parent is not None and parent.type in ("lexical_declaration", "variable_declaration"):
            lone = parent.named_child_count == 1
            outer, parent = parent, parent.parent
        if parent is not None and parent.type == "ambient_declaration":
            outer, parent = parent, parent.parent
        if parent is not None and parent.type == "export_statement":
            outer = parent
            if parent.parent is not None and parent.parent.type == "program":
                default = any(c.type == "default" for c in parent.children)
                export = "default" if default else name
        if not lone:
            outer = node
        start = outer.start_byte
        sibling = outer.prev_named_sibling
        while sibling is not None and sibling.type == "decorator":  # member decorators
            start, sibling = sibling.start_byte, sibling.prev_named_sibling
        # The header: from the first keyword (past decorators) to the body or value.
        body = fn.child_by_field_name("body")
        tail = body or (value if kind in ("type", "variable") else None)
        head = next((c.start_byte for c in outer.children if c.type != "decorator"), start)
        end = tail.start_byte if tail is not None else outer.end_byte
        header = self.data[head:end].decode("utf-8", "replace")
        signature = header.strip().rstrip(";=").rstrip()
        decl = Decl(kind, name, start, outer.end_byte, signature, export)
        if kind == "variable":
            span = _span(value) if value is not None else (node.end_byte,) * 2
        elif kind in _MEMBER_SCOPES or kind == "namespace":
            span = _span(body) if body is not None else (node.end_byte,) * 2
        else:  # parameters belong to the function's own scope
            params = fn.child_by_field_name("parameters") or fn.child_by_field_name("parameter")
            first = params or body
            span = (first.start_byte, fn.end_byte) if first is not None else (node.end_byte,) * 2
        own = len(self.scopes)
        self.scopes.append(Scope(self.scope_at(node.start_byte), decl, span))
        bisect.insort(self.bodies, (span[0], own))
        if kind == "class":
            for heritage in (c for c in node.named_children if c.type == "class_heritage"):
                for clause in heritage.named_children:
                    self.bases(own, clause.named_children)
        elif kind == "interface":
            for clause in (c for c in node.named_children if c.type == "extends_type_clause"):
                self.bases(own, clause.named_children)

    def bases(self, own: int, nodes: list[Node]) -> None:
        for node in nodes:
            name = self.base(node) if node.type != "type_arguments" else None
            if name is not None:
                self.scopes[own].bases.append(Use(name, *_span(node)))

    def source(self, node: Node) -> str | None:
        string = node.child_by_field_name("source")
        if string is None:
            return None
        return "".join(self.text(c) for c in string.named_children)

    def specifier(self, node: Node) -> tuple[str | None, str | None]:
        """(name, alias) of an import/export specifier; string names are skipped."""
        name, alias = node.child_by_field_name("name"), node.child_by_field_name("alias")
        if name is None or name.type != "identifier":
            return None, None
        if alias is not None and alias.type != "identifier":
            return None, None
        return self.text(name), self.text(alias) if alias is not None else None

    def imports(self, node: Node) -> list[Import]:
        source = self.source(node)
        out: list[Import] = []
        for child in node.named_children:
            if child.type == "import_require_clause":  # import x = require("m")
                local = self.text(child.named_children[0])
                out.append(
                    Import(self.source(child), "namespace", None, local, None, *_span(child))
                )
            if child.type != "import_clause":
                continue
            for part in child.named_children:
                if part.type == "identifier":  # import x from "m"
                    local = self.text(part)
                    out.append(Import(source, "name", "default", local, None, *_span(part)))
                elif part.type == "namespace_import" and part.named_children:
                    local = self.text(part.named_children[0])
                    out.append(Import(source, "namespace", None, local, None, *_span(part)))
                elif part.type == "named_imports":
                    for spec in part.named_children:
                        name, alias = self.specifier(spec)
                        if name is not None:
                            item = Import(source, "name", name, alias or name, None, *_span(spec))
                            out.append(item)
        if not out:  # import "m"
            out.append(Import(source, "side", None, None, None, *_span(node)))
        return out

    def exports(self, node: Node) -> list[Import]:
        """Clauses of a module-level export statement; declarations are captured as such."""
        source = self.source(node)
        out: list[Import] = []
        clause = False
        for child in node.named_children:
            if child.type == "export_clause":
                clause = True
                for spec in child.named_children:
                    name, alias = self.specifier(spec)
                    if name is not None:
                        out.append(Import(source, "name", name, None, alias or name, *_span(spec)))
            elif child.type == "namespace_export" and child.named_children:
                clause = True
                alias = self.text(child.named_children[0])
                out.append(Import(source, "namespace", None, None, alias, *_span(child)))
        value = node.child_by_field_name("value")
        if value is not None and value.type == "identifier":  # export default name;
            out.append(Import(None, "name", self.text(value), None, "default", *_span(node)))
        if source is not None and not clause:  # export * from "m"
            out.append(Import(source, "star", None, None, None, *_span(node)))
        return out


def parse(data: bytes, language: Dialect) -> Parsed | str:
    """Syntax facts, or the reason the file is unsupported."""
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "TypeScript source is not UTF-8"
    root = Parser(_LANGUAGES[language]).parse(data).root_node
    captures = QueryCursor(_QUERIES[language]).captures(root)
    facts = _Facts(data)
    # A function-valued exported declarator matches two patterns: declared once.
    defs = {(n.start_byte, -n.end_byte, n.type): n for n in captures.get("def", [])}
    for _, node in sorted(defs.items()):
        facts.declaration(node)
    scopes = facts.scopes
    for callee in captures.get("callee", []):
        at = callee.parent
        # ``await f<T>(x)`` parses as a call of the await expression; ``f!(x)`` similarly.
        while callee.type in ("await_expression", "non_null_expression") and callee.named_children:
            callee = callee.named_children[0]
        name = facts.dotted(callee)
        if name is None or at is None:
            continue
        if at.type.startswith("jsx") and "." not in name and not name[0].isupper():
            continue  # an intrinsic element such as <div>, not a component
        scopes[facts.scope_at(at.start_byte)].calls.append(Use(name, *_span(at)))
    imports: list[Import] = []
    for node in captures.get("import", []):
        if node.parent is not None and node.parent.type == "program":
            imports.extend(facts.imports(node))
    for node in captures.get("export", []):
        if node.parent is not None and node.parent.type == "program":
            imports.extend(facts.exports(node))
    for node in captures.get("bind", []):
        facts.bind(facts.scope_at(node.start_byte), node)
    rebound = {facts.text(n) for n in captures.get("rebind", [])}
    for scope in scopes:
        scope.calls.sort(key=lambda u: (u.start, u.end, u.name))
    imports.sort(key=lambda i: (i.start, i.end))
    return Parsed(scopes, imports, rebound, syntax_diagnostics(root, captures))


def encode(parsed: Parsed) -> list[Any]:
    scopes = [
        [
            s.parent,
            (d := s.decl) and [d.kind, d.name, d.start, d.end, d.signature, d.export],
            list(s.body),
            sorted(s.bound),
            [[u.name, u.start, u.end] for u in s.calls],
            [[u.name, u.start, u.end] for u in s.bases],
        ]
        for s in parsed.scopes
    ]
    imports = [
        [i.source, i.item, i.name, i.local, i.export, i.start, i.end] for i in parsed.imports
    ]
    return [scopes, imports, sorted(parsed.rebound), parsed.diagnostics]


def decode(value: Any, size: int) -> Parsed:
    """The inverse of ``encode`` for a file of ``size`` bytes, checked as the Python
    slice's are: anything ``parse`` could not have produced raises ValueError."""
    try:
        scopes, imports, rebound, diagnostics = as_list(value)
        parsed = Parsed(
            [
                Scope(
                    parent,
                    None if decl is None else Decl(*as_list(decl)),
                    tuple(as_list(body)),  # length checked by _sound
                    set(as_list(bound)),
                    [Use(*as_list(u)) for u in as_list(calls)],
                    [Use(*as_list(u)) for u in as_list(bases)],
                )
                for parent, decl, body, bound, calls, bases in map(as_list, as_list(scopes))
            ],
            [Import(*as_list(i)) for i in as_list(imports)],
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

    def optional(*values: Any) -> bool:
        return all(v is None or type(v) is str for v in values)

    scopes = parsed.scopes
    if not scopes or (scopes[0].parent, scopes[0].decl, scopes[0].body) != (None, None, (0, size)):
        return False
    for k, s in enumerate(scopes):
        d = s.decl
        if k and not (
            type(s.parent) is int
            and 0 <= s.parent < k
            and d is not None
            and type(d.kind) is str
            and d.kind in _KIND_NAMES
            and type(d.name) is str
            and _NAME.fullmatch(d.name) is not None
            and texts(d.signature)
            and optional(d.export)
            and spans((d.start, d.end))
        ):
            return False
        uses = (*s.calls, *s.bases)
        if not (
            spans(s.body, *((u.start, u.end) for u in uses))
            and texts(*s.bound, *(u.name for u in uses))
        ):
            return False
    for i in parsed.imports:
        if not (
            i.item in ("name", "namespace", "star", "side")
            and optional(i.source, i.name, i.local, i.export)
            and spans((i.start, i.end))
        ):
            return False
    return texts(*parsed.rebound, *parsed.diagnostics)


_EXTENSIONS = (".d.ts", ".d.mts", ".d.cts", ".ts", ".tsx", ".mts", ".cts")
# Specifier extensions that name a TypeScript source (``./a.js`` is ``a.ts`` under ESM).
_SPECIFIER_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")
# What TypeScript's lookup can load (sources it could not parse, JavaScript).
_LOADED = (*_EXTENSIONS, ".js", ".jsx", ".mjs", ".cjs")


def module_name(path: str) -> str | None:
    """``src/a.ts`` -> ``src/a``; ``src/index.ts`` -> ``src``; None at the root index."""
    for extension in _EXTENSIONS:
        if path.endswith(extension):
            return _directory(path[: -len(extension)])
    return None


def _stem(path: str) -> str:
    """A specifier path without the extension that names a TypeScript source."""
    for extension in _SPECIFIER_EXTENSIONS:
        if path.endswith(extension):
            return path.removesuffix(extension)
    return path


def _directory(name: str) -> str | None:
    return None if name in ("index", ".", "") else name.removesuffix("/index")


def module_key(name: str) -> str:
    return _PREFIX + name


def is_test_file(path: str) -> bool:
    """Jest/Vitest discovery: ``*.test.ts``, ``*.spec.tsx`` or under ``__tests__/``."""
    *directories, name = path.split("/")
    return "__tests__" in directories or re.search(r"\.(test|spec)\.[cm]?tsx?$", name) is not None


@dataclass(slots=True)
class _File:
    path: str
    digest: str
    parsed: Parsed
    keys: list[str]
    children: list[list[int]]
    module: str | None
    test: bool


class Resolver:
    """Conservative cross-file resolution over every TypeScript file of one capture."""

    def __init__(
        self, files: list[tuple[str, str, Parsed]], others: Mapping[str, bytes] | None = None
    ) -> None:
        """``others``: the capture's files that are not ``files``, by path: the
        ``tsconfig.json`` files, and what a bare specifier's lookup could reach instead
        of a parsed TypeScript source."""
        self.others = others or {}
        self.projects = tsconfig.Projects(self.others)
        self.paths = {path for path, _, _ in files}
        self.files: list[_File] = []
        self.modules: dict[str, list[int]] = {}
        self.memo: dict[tuple[str, str], list[str] | None] = {}
        for path, file_digest, parsed in files:
            decls = []
            for scope in parsed.scopes[1:]:
                assert scope.parent is not None and scope.decl is not None
                decls.append((scope.parent, scope.decl.name, scope.decl.start))
            keys, children = symbol_keys(path, decls)
            module = module_name(path)
            if module is not None:
                self.modules.setdefault(module, []).append(len(self.files))
            test = is_test_file(path)
            self.files.append(_File(path, file_digest, parsed, keys, children, module, test))

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

        for item in file.parsed.imports:
            if item.source is not None:
                shown = item.source + (f":{item.name}" if item.item == "name" else "")
                edges.append(edge("imports", file.keys[0], self._import(i, item), shown, item))

        for k, scope in enumerate(scopes):
            key = file.keys[k]
            if scope.decl is not None and scope.parent is not None:
                decl, parent = scope.decl, scope.parent
                source = evidence(decl.start, decl.end)
                entities.append(
                    Entity(key, "symbol", decl.kind, decl.name, decl.signature, source, file.test)
                )
                edges.append(structural("contains", file.keys[parent], key, None))
                if parent == 0 and file.module is not None:
                    edges.append(structural("defines", module_key(file.module), key, source))
                for base in scope.bases:
                    res = self._lookup(i, parent, base.name)
                    edges.append(edge("references", key, res, base.name, base))
            for call in scope.calls:
                relation = edge("calls", key, self._lookup(i, k, call.name), call.name, call)
                callee = relation.targets[0] if relation.resolution == "resolved" else None
                if file.test and callee is not None and not self._in_test_file(callee):
                    at = evidence(call.start, call.end)
                    edges.append(
                        Edge("tested_by", callee, (key,), "resolved", "heuristic", None, at)
                    )
                edges.append(relation)

    def _in_test_file(self, key: str) -> bool:
        return is_test_file(key.removeprefix("symbol:").rsplit("#", 1)[0])

    def _specifier(self, i: int, source: str | None) -> str | None:
        """The known module a specifier names: a relative one against the importing
        file, a bare one through its project's ``paths`` and ``baseUrl``. Else None
        (packages, unknowable projects, sources that are not TypeScript)."""
        if source is None:
            return None
        path = self.files[i].path
        if source in (".", "..") or source.startswith(("./", "../")):
            joined = posixpath.normpath(posixpath.join(posixpath.dirname(path), source))
            if joined == ".." or joined.startswith("../"):
                return None
            name = _directory(_stem(joined))
            return name if name in self.modules else None
        options = self.projects.options(path)
        for candidate in tsconfig.candidates(options, source) if options is not None else ():
            stem = _stem(candidate)
            # TypeScript's lookup: the file (TypeScript before JavaScript), then the
            # directory (its package.json, then index). What it would find first and
            # OXIDE does not model stops the lookup unresolved, never at a later one.
            for found in (stem + e for e in _EXTENSIONS):
                if found in self.paths:
                    return module_name(found)
            if candidate in self.others or any(stem + e in self.others for e in _LOADED):
                return None
            if f"{stem}/package.json" in self.others:
                return None
            for found in (f"{stem}/index{e}" for e in _EXTENSIONS):
                if found in self.paths:
                    return module_name(found)
            if any(f"{stem}/index{e}" in self.others for e in _LOADED):
                return None
        return None

    def _import(self, i: int, item: Import) -> Res:
        """What an import clause names: its module, or what a named export resolves to."""
        module = self._specifier(i, item.source)
        if module is None:
            return None
        if item.item == "name":
            return self._export(module, item.name, frozenset()) or None
        return [module_key(module)]

    def _export(
        self, module: str, name: str | None, seen: frozenset[tuple[str, str]]
    ) -> list[str] | None:
        """Keys ``module`` exports as ``name``: [] when it exports no such name, None
        when that cannot be known (rebound, opaque, through unknown modules, cycles)."""
        if name is None or (module, name) in seen:
            return None
        key = (module, name)
        if not seen and key in self.memo:
            return self.memo[key]
        found: list[str] | None = []
        for j in self.modules[module]:
            res = self._exports(j, name, seen | {key})
            if res is None or found is None:
                found = None
                break
            found.extend(res)
        found = sorted(set(found)) if found is not None else None
        if not seen:  # only whole results: one inside a cycle depends on its entry point
            self.memo[key] = found
        return found

    def _exports(self, j: int, name: str, seen: frozenset[tuple[str, str]]) -> list[str] | None:
        """What file ``j`` itself exports as ``name``, as ``_export``."""
        file = self.files[j]
        found: list[str] = []
        named = False
        for c in file.children[0]:
            decl = file.parsed.scopes[c].decl
            if decl is not None and decl.export == name:
                if decl.name in file.parsed.rebound:
                    return None
                named = True
                found.append(file.keys[c])
        stars: list[Import] = []
        for item in file.parsed.imports:
            if item.item == "star":
                stars.append(item)
                continue
            if item.export != name:
                continue
            named = True
            res: list[str] | None
            if item.source is None:  # export { local as name }
                res = self._local(j, item.name, seen) or None
            elif item.item == "namespace":
                res = self._import(j, item)
            else:
                module = self._specifier(j, item.source)
                res = None if module is None else self._export(module, item.name, seen) or None
            if res is None:
                return None
            found.extend(res)
        if named or name == "default":  # ``export *`` never re-exports a default
            return found
        for item in stars:
            module = self._specifier(j, item.source)
            res = None if module is None else self._export(module, name, seen)
            if res is None:
                return None
            found.extend(res)
        return found

    def _local(
        self, j: int, name: str | None, seen: frozenset[tuple[str, str]]
    ) -> list[str] | None:
        """What ``name`` is bound to in the module scope of file ``j``: [] when nothing."""
        file = self.files[j]
        if name is None or name in file.parsed.rebound or name in file.parsed.scopes[0].bound:
            return None
        found = [
            file.keys[c]
            for c in file.children[0]
            if (decl := file.parsed.scopes[c].decl) is not None and decl.name == name
        ]
        for item in file.parsed.imports:
            if item.local != name:
                continue
            module = self._specifier(j, item.source)
            if module is None:
                return None
            if item.item == "namespace":
                found.append(module_key(module))
                continue
            res = self._export(module, item.name, seen)
            if not res:
                return None
            found.extend(res)
        return sorted(set(found))

    def _lookup(self, i: int, k: int, name: str) -> Res:
        """Candidates for a bare name or ``namespace.member`` used in scope ``k``."""
        file = self.files[i]
        head, _, member = name.partition(".")
        if "." in member or head in file.parsed.rebound:
            return None
        scopes = file.parsed.scopes
        current: int | None = k
        found: list[str] | None = []
        while current is not None:
            if current == 0:
                found = self._local(i, head, frozenset())
                break
            decl = scopes[current].decl
            if decl is not None and decl.kind not in _MEMBER_SCOPES:
                if head in scopes[current].bound:
                    return None
                found = [
                    file.keys[c]
                    for c in file.children[current]
                    if (d := scopes[c].decl) is not None and d.name == head
                ]
                if found:
                    break
            current = scopes[current].parent
        if not member or not found:
            return found or None
        # Only a namespace import has statically known members.
        if len(found) == 1 and found[0].startswith(_PREFIX):
            return self._export(found[0].removeprefix(_PREFIX), member, frozenset()) or None
        return None
