"""ECMAScript syntax facts shared by TypeScript/TSX and JavaScript/JSX: the compiled
grammar queries, fact types and their extraction from one syntax tree (``parse`` drives it).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Node, Query

from oxide.core.languages.nesting import Nesting
from oxide.core.languages.python import Use

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
