"""Shared ECMAScript facts/resolution for TypeScript/TSX and JavaScript/JSX.

Each native grammar has a compiled query; declaration scopes, identities, cached
facts and graph construction are shared. Conservative source-only module semantics
are documented in docs/typescript.md and docs/javascript.md. No type inference,
package loading or repository execution occurs here.

Parsing and the cached facts codec are here; syntax extraction is in ``typescript_syntax``
and graph construction in ``typescript_resolve``.
"""

from __future__ import annotations

from bisect import bisect_right
from typing import Any

from tree_sitter import Node, Parser, QueryCursor

from oxide.core.languages.python import (
    TOO_DEEP,
    Nesting,
    Use,
    as_list,
    syntax_diagnostics,
    too_deep,
)
from oxide.core.languages.typescript_syntax import (
    _BROKEN,
    _CALLEE,
    _FUNCTIONS,
    _KIND_NAMES,
    _LANGUAGES,
    _NAME,
    _QUERIES,
    Decl,
    Dialect,
    Import,
    Parsed,
    Scope,
    _Facts,
    _span,
)

VERSION = "oxide-typescript-v3"
# The JSON shape of ``Parsed`` that generations retain for reuse; bump on any change.
FACTS_VERSION = "oxide-typescript-facts-v2"

JAVASCRIPT_VERSION = "oxide-javascript-v1"
JAVASCRIPT_FACTS_VERSION = "oxide-javascript-facts-v1"


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
