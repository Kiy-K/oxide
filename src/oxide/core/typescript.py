"""Shared ECMAScript facts/resolution for TypeScript/TSX and JavaScript/JSX.

Each native grammar has a compiled query; declaration scopes, identities, cached
facts and graph construction are shared. Conservative source-only module semantics
are documented in docs/typescript.md and docs/javascript.md. No type inference,
package loading or repository execution occurs here.
"""

from __future__ import annotations

import json
import posixpath
import re
from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from oxide.core import tsconfig
from oxide.core.python import (
    TOO_DEEP,
    Edge,
    Nesting,
    Res,
    Use,
    as_list,
    edge_target,
    structural,
    symbol_keys,
    syntax_diagnostics,
    too_deep,
)
from oxide.models import Entity, RelationKind, SourceRef

VERSION = "oxide-typescript-v3"
# The JSON shape of ``Parsed`` that generations retain for reuse; bump on any change.
FACTS_VERSION = "oxide-typescript-facts-v2"

JAVASCRIPT_VERSION = "oxide-javascript-v1"
JAVASCRIPT_FACTS_VERSION = "oxide-javascript-facts-v1"

Dialect = Literal["typescript", "tsx", "javascript"]
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
(call_expression) @call
(new_expression) @call
(import_statement) @import
(export_statement) @export
(lexical_declaration) @wrapper
(variable_declaration) @wrapper
(ambient_declaration) @wrapper
(class_body) @members
(variable_declarator) @declarator
(required_parameter pattern: (_) @bind)
(optional_parameter pattern: (_) @bind)
(arrow_function parameter: (_) @bind)
(catch_clause parameter: (_) @bind)
(for_in_statement left: (_) @bind)
(import_alias . (identifier) @bind)
(assignment_expression left: (identifier) @rebind)
(augmented_assignment_expression left: (identifier) @rebind)
(update_expression argument: (identifier) @rebind)
"""
_JSX = """
(jsx_opening_element) @call
(jsx_self_closing_element) @call
"""
# The native JS grammar shares the ECMAScript node schema, not TS-only nodes.
_JAVASCRIPT = (
    """
(function_declaration) @def
(generator_function_declaration) @def
(class_declaration) @def
(method_definition) @def
(variable_declarator value: [(arrow_function) (function_expression) (generator_function)]) @def
(field_definition value: [(arrow_function) (function_expression)]) @def
(export_statement value: [(arrow_function) (function_expression) (class)] @def)
(export_statement declaration: [
  (lexical_declaration (variable_declarator) @def)
  (variable_declaration (variable_declarator) @def)])
(call_expression) @call
(new_expression) @call
(import_statement) @import
(export_statement) @export
(lexical_declaration) @wrapper
(variable_declaration) @wrapper
(expression_statement) @wrapper
(class_body) @members
(variable_declarator) @declarator
(formal_parameters (_) @bind)
(arrow_function parameter: (_) @bind)
(catch_clause parameter: (_) @bind)
(for_in_statement left: (_) @bind)
(assignment_expression right: [
  (arrow_function) (function_expression) (generator_function) (class)]) @def
(assignment_expression) @assignment
(identifier) @cjs_identifier
[(arrow_function) (function_expression) (generator_function)] @anonymous_function
[(statement_block) (switch_body) (for_statement) (for_in_statement)] @lexical_block
(assignment_expression left: (identifier) @rebind)
(augmented_assignment_expression left: (identifier) @rebind)
(update_expression argument: (identifier) @rebind)
"""
    + _JSX
)
_CALLEE = {
    "call_expression": "function",
    "new_expression": "constructor",
    "jsx_opening_element": "name",
    "jsx_self_closing_element": "name",
}
_LANGUAGES: dict[Dialect, Language] = {
    "typescript": Language(tree_sitter_typescript.language_typescript()),
    "tsx": Language(tree_sitter_typescript.language_tsx()),
    "javascript": Language(tree_sitter_javascript.language()),
}
_BROKEN = {
    d: Query(language, "(ERROR) @error (MISSING) @missing") for d, language in _LANGUAGES.items()
}
_QUERIES: dict[Dialect, Query] = {
    "typescript": Query(_LANGUAGES["typescript"], _COMMON),
    "tsx": Query(_LANGUAGES["tsx"], _COMMON + _JSX),
    "javascript": Query(_LANGUAGES["javascript"], _JAVASCRIPT),
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
    "field_definition": "method",
    "assignment_expression": "function",
}
_FUNCTIONS = ("arrow_function", "function_expression", "generator_function")
_KIND_NAMES = frozenset([*KINDS.values(), "variable"])
# Their members are reached through ``this`` or instances, never as bare names.
_MEMBER_SCOPES = ("class", "interface", "enum")
# Synthetic names of anonymous declarations; never identifiers (ADR-0010 § SymbolId).
DEFAULT, AMBIENT = "<default>", "<module>"
_NAME = re.compile(r"[^\s.#~<>]+|<default>|<module>|<export:[^\s.#~<>]+>")
_PREFIX = "module:typescript:"
# Internal tag cannot collide with a byte-exact filesystem path; never persisted.
_JS_MODULE = "\0javascript:"


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
    item: Literal[
        "name", "namespace", "star", "side", "require", "require_name", "commonjs", "opaque", "esm"
    ]
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
    """The native grammar for a supported ECMAScript source path."""
    if path.endswith((".js", ".jsx", ".mjs", ".cjs")):
        return "javascript"
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
        self.nesting = Nesting(len(data))
        self.scope_at = self.nesting.scope_at
        self.declarators: set[tuple[int, int]] = set()  # those that are declarations
        # Parents that matter, by node id: a declaration's wrapping statement, and the
        # program for top-level statements (``Node.parent`` is O(depth), see ``parse``).
        self.parents: dict[int, Node] = {}
        self.decorated: dict[int, int] = {}  # member id -> start of its decorators

    def top(self, node: Node) -> bool:
        parent = self.parents.get(node.id)
        return parent is not None and parent.type == "program"

    def text(self, node: Node | None) -> str:
        if node is None:
            return ""
        return self.data[node.start_byte : node.end_byte].decode("utf-8", "replace")

    def bind(self, scope: int, node: Node) -> None:
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
        if node.type == "assignment_expression":
            left = node.child_by_field_name("left")
            target = self.dotted(left) if left is not None else None
            if target == "module.exports":
                return "<export:default>"
            if target is not None and target.startswith(("exports.", "module.exports.")):
                member = target.removeprefix("module.").removeprefix("exports.")
                if "." not in member:
                    return f"<export:{member}>"
            return None
        if node.type in ("arrow_function", "function_expression", "class"):
            return DEFAULT  # captured only as ``export default <value>``
        name = node.child_by_field_name("name") or node.child_by_field_name("property")
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
        if node.type == "assignment_expression":
            value = node.child_by_field_name("right")
            if value is not None:
                fn = value
                kind = "class" if value.type == "class" else "function"
        if node.type in ("variable_declarator", "public_field_definition", "field_definition"):
            self.declarators.add(_span(node))
            if value is not None and value.type in _FUNCTIONS:
                fn = value
            else:
                kind = "variable"  # an exported variable: its initializer is its body
        # The statement holding the declaration (``declare``, ``export``); its range is
        # the declaration's unless it declares several. Exports count only at module level.
        outer, export = node, None
        parent = self.parents.get(node.id)
        lone = True
        if parent is not None and parent.type in ("lexical_declaration", "variable_declaration"):
            lone = parent.named_child_count == 1
            outer, parent = parent, self.parents.get(parent.id)
        if parent is not None and parent.type == "ambient_declaration":
            outer, parent = parent, self.parents.get(parent.id)
        if parent is not None and parent.type == "export_statement":
            outer = parent
            if self.top(parent):
                default = any(c.type == "default" for c in parent.children)
                export = "default" if default else name
        if not lone:
            outer = node
        start = self.decorated.get(outer.id, outer.start_byte)
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
        enclosing = self.scope_at(node.start_byte)
        self.scopes.append(Scope(enclosing, decl, span))
        self.nesting.add(enclosing, span)
        if kind == "class":
            for heritage in (c for c in node.named_children if c.type == "class_heritage"):
                for clause in heritage.named_children:
                    self.bases(
                        own,
                        [clause]
                        if clause.type in ("identifier", "member_expression")
                        else clause.named_children,
                    )
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
        return f"{'JavaScript' if language == 'javascript' else 'TypeScript'} source is not UTF-8"
    root = Parser(_LANGUAGES[language]).parse(data).root_node
    if too_deep(root):
        return TOO_DEEP
    captures = QueryCursor(_QUERIES[language]).captures(root)
    facts = _Facts(data)
    for parent in (root, *captures.get("wrapper", []), *captures.get("export", [])):
        facts.parents.update((child.id, parent) for child in parent.named_children)
    for members in captures.get("members", []):  # siblings are O(depth) too
        run = None
        for child in members.named_children:
            if child.type == "decorator":
                run = child.start_byte if run is None else run
            elif run is not None:
                facts.decorated[child.id], run = run, None
    # A function-valued exported declarator matches two patterns: declared once.
    defs = {(n.start_byte, -n.end_byte, n.type): n for n in captures.get("def", [])}
    for _, node in sorted(defs.items()):
        facts.declaration(node)
    scopes = facts.scopes
    # Calls and declarators are captured whole and read downwards, never through
    # ``Node.parent``: tree-sitter finds a parent by descending from the root, which is
    # quadratic over a long method chain.
    for at in captures.get("call", []):
        callee = at.child_by_field_name(_CALLEE[at.type])
        if callee is None:
            continue
        # ``await f<T>(x)`` parses as a call of the await expression; ``f!(x)`` similarly.
        while callee.type in ("await_expression", "non_null_expression") and callee.named_children:
            callee = callee.named_children[0]
        name = facts.dotted(callee)
        if name is None:
            continue
        if at.type.startswith("jsx") and "." not in name and not name[0].isupper():
            continue  # an intrinsic element such as <div>, not a component
        scopes[facts.scope_at(at.start_byte)].calls.append(Use(name, *_span(at)))
    imports: list[Import] = []
    for node in captures.get("import", []):
        if facts.top(node):
            imports.extend(facts.imports(node))
    for node in captures.get("export", []):
        if facts.top(node):
            imports.extend(facts.exports(node))
    for node in captures.get("declarator", []):
        target = node.child_by_field_name("name")
        if target is not None and _span(node) not in facts.declarators:  # not ``f = () =>``
            facts.bind(facts.scope_at(target.start_byte), target)
    for node in captures.get("bind", []):
        facts.bind(facts.scope_at(node.start_byte), node)
    rebound = {facts.text(n) for n in captures.get("rebind", [])}
    if language == "javascript":
        _opaque_bindings(facts, captures, list(defs.values()))
        if captures.get("import") or captures.get("export"):
            imports.append(Import(None, "esm", None, None, None, 0, 0))
        _commonjs(facts, captures, imports, rebound)
    for scope in scopes:
        scope.calls.sort(key=lambda u: (u.start, u.end, u.name))
    imports.sort(key=lambda i: (i.start, i.end))
    return Parsed(scopes, imports, rebound, syntax_diagnostics(root, _BROKEN[language]))


def _opaque_bindings(facts: _Facts, captures: dict[str, list[Node]], defs: list[Node]) -> None:
    """Unmodeled callbacks and blocks must not leak declarations into an outer lookup."""
    recognized = {
        (node.child_by_field_name("value") or node.child_by_field_name("right") or node).id
        for node in defs
    }
    functions = [
        node.child_by_field_name("value") or node.child_by_field_name("right") or node
        for node in defs
    ] + captures.get("anonymous_function", [])
    function_bodies = {
        body.id for node in functions if (body := node.child_by_field_name("body")) is not None
    }
    bodies = [(s.body, False) for s in facts.scopes[1:]]
    bodies.extend(
        (_span(node), True)
        for node in captures.get("lexical_block", [])
        if node.id not in function_bodies
    )
    for node in captures.get("anonymous_function", []):
        body = node.child_by_field_name("body")
        if node.id not in recognized and body is not None:
            bodies.append((_span(body), True))
    nesting = Nesting(len(facts.data))
    anonymous = [False]
    for span, opaque in sorted(bodies, key=lambda b: (b[0][0], -b[0][1], b[1])):
        if span[0] != span[1]:
            nesting.add(nesting.scope_at(span[0]), span)
            anonymous.append(opaque)
    for scope in facts.scopes[1:]:
        if scope.decl is not None and anonymous[nesting.scope_at(scope.decl.start)]:
            assert scope.parent is not None
            facts.scopes[scope.parent].bound.add(scope.decl.name)


def _commonjs(
    facts: _Facts, captures: dict[str, list[Node]], imports: list[Import], rebound: set[str]
) -> None:
    """Only unconditional module-level literal requires and static identifier exports.

    Any use of the export object outside a recognized left-hand side makes its
    shape unknowable. This also rejects aliasing, mutation, computed keys and cycles
    through an exported object, rather than guessing at runtime effects.
    """
    scopes = facts.scopes
    shadowed = set(rebound)
    for scope in scopes:
        shadowed.update(scope.bound)
        if scope.decl is not None:
            shadowed.add(scope.decl.name)
    if "require" not in shadowed:
        for node in captures.get("declarator", []):
            parent = facts.parents.get(node.id)
            if parent is None or not facts.top(parent):
                continue
            value, target = node.child_by_field_name("value"), node.child_by_field_name("name")
            if value is None or target is None or value.type != "call_expression":
                continue
            callee, args = (
                value.child_by_field_name("function"),
                value.child_by_field_name("arguments"),
            )
            if callee is None or facts.text(callee) != "require" or args is None:
                continue
            if len(args.named_children) != 1 or args.named_children[0].type != "string":
                continue
            string = args.named_children[0]
            # Escaped module specifiers need JS string evaluation, which we do not do.
            source = facts.text(string)[1:-1]
            if "\\" in source:
                continue
            bindings: list[tuple[str, str | None]] = []
            if target.type == "identifier":
                bindings.append((facts.text(target), None))
            elif target.type == "object_pattern":
                for part in target.named_children:
                    if part.type == "shorthand_property_identifier_pattern":
                        bindings.append((facts.text(part), facts.text(part)))
                    elif part.type == "pair_pattern":
                        key, binding = (
                            part.child_by_field_name("key"),
                            part.child_by_field_name("value"),
                        )
                        if key is not None and binding is not None and binding.type == "identifier":
                            bindings.append((facts.text(binding), facts.text(key)))
            for binding_name, name in bindings:
                scopes[0].bound.discard(binding_name)
                imports.append(
                    Import(
                        source,
                        "require" if name is None else "require_name",
                        name,
                        binding_name,
                        None,
                        *_span(node),
                    )
                )
    exports: list[Import] = []
    allowed: list[tuple[int, int]] = []
    opaque = bool({"exports", "module"} & shadowed)
    names: set[str] = set()
    replaced = False
    for node in captures.get("assignment", []):
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        if left is None or right is None:
            continue
        name = facts.dotted(left)
        if name is None or not (
            name == "module.exports" or name.startswith(("exports.", "module.exports."))
        ):
            continue
        parent = facts.parents.get(node.id)
        if parent is None or parent.type != "expression_statement" or not facts.top(parent):
            opaque = True
            continue
        allowed.append(_span(left))
        pairs: list[tuple[str, str]] = []
        if name == "module.exports":
            if replaced or names:
                opaque = True
            replaced = True
            if right.type == "identifier":
                pairs = [("default", facts.text(right))]
            elif right.type in (*_FUNCTIONS, "class"):
                pairs = [("default", "<export:default>")]
            elif right.type == "object":
                for part in right.named_children:
                    if part.type == "shorthand_property_identifier":
                        pairs.append((facts.text(part), facts.text(part)))
                    elif part.type == "pair":
                        key, value = (
                            part.child_by_field_name("key"),
                            part.child_by_field_name("value"),
                        )
                        if (
                            key is not None
                            and key.type == "property_identifier"
                            and value is not None
                            and value.type == "identifier"
                        ):
                            pairs.append((facts.text(key), facts.text(value)))
                        else:
                            opaque = True
                    elif part.type != "comment":
                        opaque = True
            else:
                opaque = True
        elif right.type in ("identifier", *_FUNCTIONS, "class") and not replaced:
            member = name.removeprefix("module.").removeprefix("exports.")
            local_name = facts.text(right) if right.type == "identifier" else f"<export:{member}>"
            pairs = [(member, local_name)]
        else:
            opaque = True
        for export, binding_name in pairs:
            # A property called default is not the CJS module's default value.
            if export == "default" and (name != "module.exports" or right.type == "object"):
                opaque = True
            if export in names or "." in export:
                opaque = True
            names.add(export)
            exports.append(Import(None, "commonjs", binding_name, None, export, *_span(node)))
    allowed.sort()
    starts = [span[0] for span in allowed]
    for node in captures.get("cjs_identifier", []):
        if facts.text(node) in ("module", "exports") and not (
            (at := bisect_right(starts, node.start_byte) - 1) >= 0
            and node.start_byte < allowed[at][1]
        ):
            opaque = True
    if opaque:
        # An explicit opaque export poisons every lookup, including unknown members.
        imports.append(Import(None, "opaque", None, None, None, 0, 0))
    else:
        imports.extend(exports)


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
            i.item
            in (
                "name",
                "namespace",
                "star",
                "side",
                "require",
                "require_name",
                "commonjs",
                "opaque",
                "esm",
            )
            and optional(i.source, i.name, i.local, i.export)
            and spans((i.start, i.end))
        ):
            return False
    return texts(*parsed.rebound, *parsed.diagnostics)


_JS_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs")
_EXTENSIONS = (".d.ts", ".d.mts", ".d.cts", ".ts", ".tsx", ".mts", ".cts")
# Specifier extensions that name a TypeScript source (``./a.js`` is ``a.ts`` under ESM).
_SPECIFIER_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")
# What TypeScript's lookup can load (sources it could not parse, JavaScript).
_LOADED = (*_EXTENSIONS, ".js", ".jsx", ".mjs", ".cjs")


def module_name(path: str) -> str | None:
    """``src/a.ts`` -> ``src/a``; ``src/index.ts`` -> ``src``; None at the root index."""
    if path.endswith(_JS_EXTENSIONS):
        return _JS_MODULE + path  # distinct runtime files are distinct modules
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
    return (
        "module:javascript:" + name.removeprefix(_JS_MODULE)
        if name.startswith(_JS_MODULE)
        else _PREFIX + name
    )


def is_test_file(path: str) -> bool:
    """Jest/Vitest discovery: ``*.test.ts``, ``*.spec.tsx`` or under ``__tests__/``."""
    *directories, name = path.split("/")
    return (
        "__tests__" in directories or re.search(r"\.(test|spec)\.[cm]?[jt]sx?$", name) is not None
    )


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
    """Conservative cross-file resolution over ECMAScript sources of one capture."""

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
        self.commonjs: set[int] = set()
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
            if self._commonjs_file(path, parsed):
                self.commonjs.add(len(self.files) - 1)

    def _commonjs_file(self, path: str, parsed: Parsed) -> bool:
        if any(i.item == "esm" for i in parsed.imports):
            return False
        if path.endswith(".cjs"):
            return True
        if not path.endswith((".js", ".jsx")):
            return False
        # ESM syntax or the nearest captured package's type disables CJS globals.
        if any(s.decl is not None and s.decl.export is not None for s in parsed.scopes):
            return False
        if any(i.item in ("name", "namespace", "star", "side") for i in parsed.imports):
            return False
        directory = posixpath.dirname(path)
        while True:
            package = posixpath.join(directory, "package.json")
            if package in self.others:
                try:
                    config = json.loads(self.others[package])
                except (ValueError, UnicodeDecodeError):
                    return False
                return isinstance(config, dict) and config.get("type", "commonjs") == "commonjs"
            if not directory:
                return True
            directory = posixpath.dirname(directory)

    def derive(self, entities: list[Entity], edges: list[Edge]) -> None:
        for name in sorted(self.modules):
            key = module_key(name)
            entities.append(
                Entity(key, "module", "module", name.removeprefix(_JS_MODULE), None, None, False)
            )
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
            if dialect(path) == "javascript":
                if source in (".", "..") or source.endswith("/"):
                    return None  # directory-only loader semantics are outside this source lookup
                return self._javascript_specifier(joined)
            name = _directory(_stem(joined))
            if name in self.modules:
                return name
            module = self._javascript_specifier(joined)
            return module if module is not None and module.startswith(_JS_MODULE) else None
        if dialect(path) == "javascript":
            return None  # JS does not inherit TypeScript compiler path aliases
        options = self.projects.options(path)
        for candidate in tsconfig.candidates(options, source) if options is not None else ():
            stem = _stem(candidate)
            # TypeScript's lookup: the file (TypeScript before JavaScript), then the
            # directory (its package.json, then index). What it would find first and
            # OXIDE does not model stops the lookup unresolved, never at a later one.
            for found in (stem + e for e in _EXTENSIONS):
                if found in self.paths:
                    return module_name(found)
            js = [stem + e for e in _JS_EXTENSIONS if stem + e in self.paths]
            if js:
                return module_name(js[0]) if len(js) == 1 else None
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

    def _javascript_specifier(self, joined: str) -> str | None:
        """Captured source lookup, not a simulation of a loader or package manager."""
        if joined in self.paths:
            module = module_name(joined)
            return module if module is not None and len(self.modules[module]) == 1 else None
        if joined in self.others:
            return None  # an unsupported/partial target must not fall through
        suffix = posixpath.splitext(joined)[1]
        if suffix:
            # A missing emitted JS path may name one TS source; never merge it with JS.
            substitutions = {
                ".js": (".ts", ".tsx"),
                ".jsx": (".tsx",),
                ".mjs": (".mts",),
                ".cjs": (".cts",),
            }
            candidates = [joined[: -len(suffix)] + e for e in substitutions.get(suffix, ())]
        else:
            candidates = [joined + e for e in (*_JS_EXTENSIONS, ".ts", ".tsx", ".mts", ".cts")]
            if any(p in self.others for p in candidates) or joined + ".json" in self.others:
                return None
            if not any(p in self.paths for p in candidates):
                if joined + "/package.json" in self.others:
                    return None
                candidates = [joined + "/index" + e for e in (*_JS_EXTENSIONS, ".ts", ".tsx")]
        found = [p for p in candidates if p in self.paths]
        if any(p in self.others for p in candidates):
            return None
        module = module_name(found[0]) if len(found) == 1 else None
        return module if module is not None and len(self.modules[module]) == 1 else None

    def _import(self, i: int, item: Import) -> Res:
        """What an import clause names: its module, or what a named export resolves to."""
        module = self._specifier(i, item.source)
        if module is None:
            return None
        if item.item in ("require", "require_name") and (
            i not in self.commonjs or any(j not in self.commonjs for j in self.modules[module])
        ):
            return None
        if item.item in ("name", "require_name"):
            exported = self._export(module, item.name, frozenset())
            if (
                exported == []
                and item.name == "default"
                and all(j in self.commonjs for j in self.modules[module])
            ):
                return [module_key(module)]  # default CJS object, with static named members
            return exported or None
        if item.item == "require":
            return self._export(module, "default", frozenset()) or [module_key(module)]
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
        if j in self.commonjs and (
            file.parsed.diagnostics or any(item.item == "opaque" for item in file.parsed.imports)
        ):
            return None
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
            if item.item == "commonjs" and j not in self.commonjs:
                continue
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
            if (decl := file.parsed.scopes[c].decl) is not None
            and decl.name == name
            and (dialect(file.path) != "javascript" or decl.kind != "method")
        ]
        for item in file.parsed.imports:
            if item.local != name:
                continue
            if item.item in ("require", "require_name") and j not in self.commonjs:
                return None
            module = self._specifier(j, item.source)
            if module is None:
                return None
            if item.item in ("require", "require_name") and any(
                k not in self.commonjs for k in self.modules[module]
            ):
                return None
            if item.item == "require":
                found.extend(self._export(module, "default", seen) or [module_key(module)])
                continue
            if item.item == "namespace":
                found.append(module_key(module))
                continue
            res = self._export(module, item.name, seen)
            if (
                res == []
                and item.name == "default"
                and all(k in self.commonjs for k in self.modules[module])
            ):
                res = [module_key(module)]
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
            if dialect(file.path) == "javascript" and head in scopes[current].bound:
                return None
            if decl is not None and decl.kind not in _MEMBER_SCOPES:
                if head in scopes[current].bound:
                    return None
                found = [
                    file.keys[c]
                    for c in file.children[current]
                    if (d := scopes[c].decl) is not None
                    and d.name == head
                    and (dialect(file.path) != "javascript" or d.kind != "method")
                ]
                if found:
                    break
            current = scopes[current].parent
        if not member or not found:
            if found and any(key.startswith("module:javascript:") for key in found):
                return None  # a known module object is not a known callable
            return found or None
        # Only a namespace import has statically known members.
        if len(found) == 1 and found[0].startswith((_PREFIX, "module:javascript:")):
            module = (
                found[0].removeprefix(_PREFIX)
                if found[0].startswith(_PREFIX)
                else _JS_MODULE + found[0].removeprefix("module:javascript:")
            )
            return self._export(module, member, frozenset()) or None
        return None
