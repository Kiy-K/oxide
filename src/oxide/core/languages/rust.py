"""Rust language slice: tree-sitter syntax facts (docs/rust.md); resolution is in
``rust_resolve``, the facts' types and cache codec in ``rust_facts``.

One compiled query collects every fact in C. Items are then assigned to their container
by a single pass over their start-sorted spans with an explicit stack (module, impl, trait
or "body", whose items are not symbols), so nothing recurses and no node's parent is read.
A name bound anywhere in a declaration (parameters, patterns, generics, nested items) is
opaque in all of it: no block scoping, no type inference. Macro bodies are token trees
and are not read; attributes are the sibling nodes before an item.
"""

from __future__ import annotations

import bisect
import re

from tree_sitter import Node, Parser, Query, QueryCursor

from oxide.core.languages.python import TOO_DEEP, Use, syntax_diagnostics, too_deep
from oxide.core.languages.rust_facts import (
    FACTS_VERSION,
    MAX_NAME,
    VERSION,
    Decl,
    Import,
    Mod,
    Parsed,
    Scope,
    decode,
    encode,
)
from oxide.core.languages.rust_macros import LANGUAGE, unwrap

__all__ = ["FACTS_VERSION", "VERSION", "Parsed", "decode", "encode", "parse"]

_QUERY = Query(
    LANGUAGE,
    """
    [(function_item) (function_signature_item) (struct_item) (enum_item) (union_item)
      (trait_item) (impl_item) (type_item) (const_item) (static_item) (macro_definition)
      (mod_item) (use_declaration) (extern_crate_declaration)] @item
    (attribute_item) @attr
    [(use_declaration) (visibility_modifier) (attribute_item) (inner_attribute_item)] @skip
    (call_expression function: [(identifier) (scoped_identifier)] @call)
    (call_expression function: (generic_function
      function: [(identifier) (scoped_identifier)] @call))
    (call_expression function: (field_expression value: (self) field: (field_identifier) @method))
    (macro_invocation macro: [(identifier) (scoped_identifier)] @macro)
    (type_identifier) @ref
    (scoped_type_identifier) @ref
    (scoped_identifier) @path
    (_ pattern: (identifier) @bind)
    (field_pattern name: (shorthand_field_identifier) @bind)
    [(tuple_pattern) (tuple_struct_pattern) (slice_pattern) (or_pattern) (reference_pattern)
      (ref_pattern) (mut_pattern) (captured_pattern) (match_pattern) (closure_parameters)] @pattern
    (type_parameter name: (type_identifier) @bind)
    (const_parameter name: (identifier) @bind)
    (associated_type name: (type_identifier) @bind)
    [(struct_item name: (type_identifier) @bind) (enum_item name: (type_identifier) @bind)
      (union_item name: (type_identifier) @bind) (trait_item name: (type_identifier) @bind)
      (type_item name: (type_identifier) @bind) (function_item name: (identifier) @bind)
      (const_item name: (identifier) @bind) (static_item name: (identifier) @bind)
      (macro_definition name: (identifier) @bind)]
    """,
)
_BROKEN = Query(LANGUAGE, "(ERROR) @error (MISSING) @missing")

_ATTRIBUTE = re.compile(rb"#\s*\[\s*([\w:]+)")
_PATH_ATTRIBUTE = re.compile(rb'#\s*\[\s*path\s*=\s*"((?:[^"\\]|\\.)*)"')
_GAP = re.compile(rb"(?:\s|//[^\n]*|/\*.*?\*/)*", re.DOTALL)
_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_TESTS = ("test", "bench", "rstest", "test_case")
_KINDS = {
    "function_item": "function",
    "function_signature_item": "function",
    "struct_item": "struct",
    "enum_item": "enum",
    "union_item": "union",
    "type_item": "type",
    "const_item": "constant",
    "static_item": "variable",
    "macro_definition": "macro",
}
_PATH_PARTS = ("identifier", "type_identifier", "crate", "self", "super")


def _text(data: bytes, node: Node | None) -> str:
    return "" if node is None else data[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _ident(data: bytes, node: Node | None) -> str:
    text = _text(data, node)
    return text[2:] if text.startswith("r#") else text


def _path(data: bytes, node: Node) -> str | None:
    """``a::b::c`` of a path or a type path, ``::a::b`` when absolute. Generic arguments
    are dropped; qualified (``<T as Tr>::f``) and other shapes are None."""
    parts: list[str] = []
    absolute = False
    while True:
        kind = node.type
        if kind in ("scoped_identifier", "scoped_type_identifier"):
            parts.append(_ident(data, node.child_by_field_name("name")))
            inner = node.child_by_field_name("path")
            if inner is None:
                absolute = True
                break
            node = inner
        elif kind in ("generic_type", "generic_type_with_turbofish"):
            inner = node.child_by_field_name("type")
            if inner is None:
                return None
            node = inner
        elif kind in _PATH_PARTS:
            parts.append(_ident(data, node))
            break
        else:
            return None
    return ("::" if absolute else "") + "::".join(reversed(parts))


def _squash(text: str) -> str:
    return " ".join(_COMMENT.sub(" ", text).split())


def _header(data: bytes, node: Node, body: Node | None) -> str:
    """The declaration up to its body; the first line of one without a body."""
    end = body.start_byte if body is not None else node.end_byte
    text = data[node.start_byte : end].decode("utf-8", "replace")
    if body is None and node.type != "function_signature_item":
        text = text.split("\n", 1)[0]  # a const, static or alias: its first line
    return _squash(text).rstrip(";").rstrip()


def _public(node: Node) -> bool:
    first = node.child(0)
    return first is not None and first.type == "visibility_modifier"


class _Facts:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.scopes = [Scope(None, None)]
        self.imports: list[Import] = []
        self.mods: list[Mod] = []
        self.names: set[int] = set()  # start bytes of declared symbol names

    def attributes(self, pending: list[Node], node: Node) -> tuple[bool, str | None, bool]:
        """(test, ``#[path]``, ``#[macro_export]``) of the attributes touching ``node``."""
        test = macro = False
        path = None
        boundary = node.start_byte
        for attribute in reversed(pending):
            if _GAP.fullmatch(self.data, attribute.end_byte, boundary) is None:
                break
            boundary = attribute.start_byte
            text = self.data[attribute.start_byte : attribute.end_byte]
            found, located = _ATTRIBUTE.match(text), _PATH_ATTRIBUTE.match(text)
            name = found.group(1).decode().rsplit("::", 1)[-1] if found else ""
            test = test or name in _TESTS
            macro = macro or name == "macro_export"
            if located is not None:
                path = located.group(1).decode("utf-8", "replace")
        return test, path, macro

    def impl(self, node: Node) -> tuple[str, str | None, str | None]:
        """``<impl Trait for Type>`` (no impl generics, comments or where clause), and the
        trait and self type as plain paths when they are."""
        data = self.data
        kind, trait = node.child_by_field_name("type"), node.child_by_field_name("trait")
        head = _squash(_text(data, kind))
        if trait is not None:
            head = f"{_squash(_text(data, trait))} for {head}"
        head = head.replace(".", "_").replace("~", "_")[:MAX_NAME]
        return (
            f"<impl {head}>",
            _path(data, kind) if kind is not None else None,
            _path(data, trait) if trait is not None else None,
        )

    def symbol(
        self, node: Node, parent: int, module: tuple[str, ...], kind: str, test: bool, macro: bool
    ) -> int | None:
        """The scope of a declaration, None when it has no usable name."""
        data = self.data
        name_node = node.child_by_field_name("name")
        body = node.child_by_field_name("body")
        target = trait = None
        variants: tuple[str, ...] = ()
        if node.type == "impl_item":
            name, target, trait = self.impl(node)
        elif name_node is None or (name := _ident(data, name_node)) in ("", "_"):
            return None
        else:
            self.names.add(name_node.start_byte)
        signature = f"macro_rules! {name}" if kind == "macro" else _header(data, node, body)
        if kind == "enum" and body is not None:
            variants = tuple(
                _ident(data, v.child_by_field_name("name"))
                for v in body.named_children
                if v.type == "enum_variant"
            )
        public = macro or _public(node)
        end = node.end_byte
        decl = Decl(
            kind,
            name,
            module,
            node.start_byte,
            end,
            signature,
            public,
            test,
            target,
            trait,
            variants,
        )
        self.scopes.append(Scope(parent, decl))
        return len(self.scopes) - 1

    def uses(self, node: Node, module: tuple[str, ...], scope: int) -> None:
        """Flatten a ``use`` tree into one import per leaf."""
        data, public = self.data, _public(node)
        stack: list[tuple[Node | None, tuple[str, ...]]] = [
            (node.child_by_field_name("argument"), ())
        ]
        while stack:
            leaf, prefix = stack.pop()
            if leaf is None:
                continue
            kind = leaf.type
            if kind == "use_list":
                stack.extend((c, prefix) for c in leaf.named_children)
            elif kind == "scoped_use_list":
                inner = leaf.child_by_field_name("path")
                head = prefix + (self.segments(inner) if inner is not None else ())
                stack.append((leaf.child_by_field_name("list"), head))
            elif kind in ("use_as_clause", "use_wildcard"):
                inner = leaf.child_by_field_name("path")
                if inner is None and leaf.named_children:
                    inner = leaf.named_children[0]
                path = prefix + (self.segments(inner) if inner is not None else ())
                alias = _ident(data, leaf.child_by_field_name("alias")) or None
                glob = kind == "use_wildcard"
                if path:
                    item = Import(
                        module, scope, path, alias, glob, public, leaf.start_byte, leaf.end_byte
                    )
                    self.imports.append(item)
            elif kind in ("identifier", "scoped_identifier", "crate", "self", "super"):
                path = prefix + self.segments(leaf)
                item = Import(
                    module, scope, path, None, False, public, leaf.start_byte, leaf.end_byte
                )
                self.imports.append(item)

    def segments(self, node: Node) -> tuple[str, ...]:
        text = _path(self.data, node)
        if text is None:
            return ()
        return ("::", *text[2:].split("::")) if text.startswith("::") else tuple(text.split("::"))

    def walk(self, items: list[Node]) -> None:
        """Assign items to containers: ``(end, role, scope, inline module path)``. Only a
        module holds symbols; an impl or trait holds functions and constants; the body of
        any other symbol holds nothing but local ``use`` declarations."""
        stack: list[tuple[int, str, int, tuple[str, ...]]] = []
        pending: list[Node] = []
        for node in items:
            while stack and node.start_byte >= stack[-1][0]:
                stack.pop()
            _, role, scope, module = stack[-1] if stack else (0, "mod", 0, ())
            kind = node.type
            if kind == "attribute_item":
                if role != "body":
                    pending.append(node)
                continue
            attributes, pending = pending, []
            if kind == "extern_crate_declaration":
                name = _ident(self.data, node.child_by_field_name("name"))
                alias = _ident(self.data, node.child_by_field_name("alias")) or None
                if role == "mod" and name not in ("", "self"):
                    item = Import(
                        module,
                        0,
                        ("::", name),
                        alias,
                        False,
                        _public(node),
                        node.start_byte,
                        node.end_byte,
                    )
                    self.imports.append(item)
                continue
            if kind == "use_declaration":
                if role != "impl" and role != "trait":
                    self.uses(node, module, scope if role == "body" else 0)
                continue
            if role == "body":
                continue
            test, path, macro = self.attributes(attributes, node)
            if kind == "mod_item":
                name = _ident(self.data, node.child_by_field_name("name"))
                if role == "mod" and name:
                    body = node.child_by_field_name("body")
                    start, end = node.start_byte, node.end_byte
                    self.mods.append(
                        Mod(name, module, body is not None, path, _public(node), start, end)
                    )
                    if body is not None:
                        stack.append((end, "mod", 0, (*module, name)))
                continue
            decl_kind = (
                "impl"
                if kind == "impl_item"
                else "trait"
                if kind == "trait_item"
                else _KINDS.get(kind)
            )
            if decl_kind is None or (role != "mod" and decl_kind not in ("function", "constant")):
                continue
            if role != "mod" and decl_kind == "function":
                decl_kind = "method"
            if kind in ("impl_item", "trait_item") and role != "mod":
                continue
            index = self.symbol(
                node,
                0 if role == "mod" else scope,
                module,
                decl_kind,
                test and decl_kind in ("function", "method"),
                macro,
            )
            if decl_kind in ("impl", "trait"):
                stack.append((node.end_byte, decl_kind, index or 0, module))
            elif index is not None:
                stack.append((node.end_byte, "body", index, module))


def parse(data: bytes) -> Parsed | str:
    """Syntax facts, or the reason the file is unsupported."""
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "Rust source is not UTF-8"
    root = Parser(LANGUAGE).parse(data).root_node
    if too_deep(root):
        return TOO_DEEP
    data, root = unwrap(data, root)  # `cfg_*!` wrappers around items are read as the items
    captures = QueryCursor(_QUERY).captures(root)
    facts = _Facts(data)
    stream = [*captures.get("item", []), *captures.get("attr", [])]
    facts.walk(sorted(stream, key=lambda n: (n.start_byte, -n.end_byte)))
    scopes = facts.scopes
    starts = [s.decl.start for s in scopes[1:] if s.decl is not None]

    def scope_at(at: int) -> int:
        """The innermost declaration holding a byte: the last scope starting at or before
        it, or the nearest enclosing impl or trait of that one; 0 for none."""
        k = bisect.bisect_right(starts, at)
        while k:
            decl = scopes[k].decl
            if decl is not None and at < decl.end:
                break
            k = scopes[k].parent or 0
        return k

    skipped: list[tuple[int, int]] = []
    for node in sorted(captures.get("skip", []), key=lambda n: n.start_byte):
        if skipped and node.start_byte < skipped[-1][1]:
            skipped[-1] = (skipped[-1][0], max(skipped[-1][1], node.end_byte))
        else:
            skipped.append((node.start_byte, node.end_byte))
    skip_starts = [s for s, _ in skipped]

    def inside_skipped(at: int) -> bool:
        i = bisect.bisect_right(skip_starts, at) - 1
        return i >= 0 and at < skipped[i][1]

    taken: set[tuple[int, int]] = set()  # spans already emitted as a call or macro
    for kind, suffix in (("call", ""), ("macro", "!")):
        for node in captures.get(kind, []):
            taken.add((node.start_byte, node.end_byte))
            name = _path(data, node)
            if name is not None:
                use = Use(name + suffix, node.start_byte, node.end_byte)
                scopes[scope_at(node.start_byte)].calls.append(use)
    for node in captures.get("method", []):
        use = Use("self." + _ident(data, node), node.start_byte, node.end_byte)
        scopes[scope_at(node.start_byte)].calls.append(use)
    # A scoped path's own prefix and last name are part of it, never separate uses.
    covered: set[tuple[int, int]] = set()
    for node in [*captures.get("ref", []), *captures.get("path", [])]:
        if node.type.startswith("scoped_"):
            for part in (node.child_by_field_name("path"), node.child_by_field_name("name")):
                if part is not None:
                    covered.add((part.start_byte, part.end_byte))
    bound_at = {n.start_byte for n in captures.get("bind", [])}
    for node in captures.get("ref", []):
        span = (node.start_byte, node.end_byte)
        if span in covered or node.start_byte in facts.names or node.start_byte in bound_at:
            continue
        if not inside_skipped(node.start_byte) and (name := _path(data, node)) is not None:
            scopes[scope_at(node.start_byte)].refs.append(Use(name, *span))
    for node in captures.get("path", []):
        span = (node.start_byte, node.end_byte)
        if span in covered or span in taken or inside_skipped(node.start_byte):
            continue
        if (name := _path(data, node)) is not None:
            scopes[scope_at(node.start_byte)].refs.append(Use(name, *span))
    for node in captures.get("bind", []):
        if node.start_byte not in facts.names:
            scopes[scope_at(node.start_byte)].bound.add(_ident(data, node))
    for node in captures.get("pattern", []):
        others = (node.child_by_field_name("type"), node.child_by_field_name("condition"))
        skip = {n.start_byte for n in others if n is not None}
        scopes[scope_at(node.start_byte)].bound.update(
            _ident(data, c)
            for c in node.named_children
            if c.type == "identifier" and c.start_byte not in skip
        )
    for scope in scopes:
        scope.calls.sort(key=lambda u: (u.start, u.end))
        scope.refs.sort(key=lambda u: (u.start, u.end))
    facts.imports.sort(key=lambda i: (i.start, i.end))
    return Parsed(facts.imports, facts.mods, scopes, syntax_diagnostics(root, _BROKEN))
