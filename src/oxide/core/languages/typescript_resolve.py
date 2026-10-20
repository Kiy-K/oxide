"""ECMAScript module resolution and graph construction (docs/typescript.md, docs/javascript.md)."""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass

from oxide.core.languages import tsconfig
from oxide.core.languages.python import (
    Use,
)
from oxide.core.languages.python_resolve import Edge, Res, edge_target, structural, symbol_keys
from oxide.core.languages.typescript_syntax import (
    _JS_MODULE,
    _MEMBER_SCOPES,
    _PREFIX,
    Import,
    Parsed,
    dialect,
)
from oxide.models import Entity, RelationKind, SourceRef

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
