"""Cargo layout and module trees for Rust resolution (docs/rust.md), without running Cargo.

Packages come from captured ``Cargo.toml`` files (stdlib ``tomllib``; a manifest that does
not parse is ignored). A package's crates are its conventional and declared targets; the
module tree of each crate follows ``mod`` declarations from its root file. A file belongs
to the first crate that reaches it (library, binaries, tests, examples, benches, build
script, in package-path order); later crates that declare it share that module. Files no
crate reaches become one-file crates with their package's edition and dependencies.
Features, ``cfg``, ``[patch]``, registry and git dependencies are not modeled.
"""

from __future__ import annotations

import posixpath
import tomllib
from dataclasses import dataclass, field
from typing import Any

from oxide.core.languages.rust_facts import Parsed

DEFAULT_EDITION = 2021  # of a file no manifest owns (Cargo's own default is 2015)
type Toml = dict[str, Any]
type Dep = tuple[str | None, bool]  # (directory of a local package, renamed by ``package =``)
_ORDER = ("lib", "bin", "test", "example", "bench", "build")
_CONVENTIONS = (
    ("src/bin", "bin"),
    ("tests", "test"),
    ("examples", "example"),
    ("benches", "bench"),
)


@dataclass(frozen=True, slots=True)
class Crate:
    root: str  # path of the root file
    name: str
    edition: int
    kind: str  # one of ``_ORDER`` or "file"
    deps: tuple[tuple[str, str], ...]  # (extern crate name, root path of that library)


@dataclass(slots=True)
class Module:
    key: str
    crate: Crate
    path: tuple[str, ...]  # from the crate root
    file: str  # the file holding the module (an inline module shares its parent's)
    parent: Module | None
    public: bool

    @property
    def label(self) -> str:
        head = self.crate.name
        if self.crate.kind not in ("lib", "file"):
            head += f" ({self.crate.kind})"
        return "::".join((head, *self.path))


@dataclass(slots=True)
class Layout:
    modules: list[Module] = field(default_factory=list)
    # (file, inline module path within the file) -> the module declaring that region
    at: dict[tuple[str, tuple[str, ...]], Module] = field(default_factory=dict)
    children: dict[str, dict[str, list[Module]]] = field(
        default_factory=dict
    )  # key -> name -> modules (several for ``cfg`` variants)
    roots: dict[str, Module] = field(default_factory=dict)  # crate root path -> its module


def _table(value: Any) -> Toml:
    return value if isinstance(value, dict) else {}


def _join(*parts: str) -> str:
    return "/".join(p for p in parts if p)


def _norm(path: str) -> str:
    """A normalized repository-relative path; ``..`` is kept so it matches no file."""
    joined = posixpath.normpath(path)
    return "" if joined == "." else joined


def _manifest(data: bytes) -> Toml | None:
    try:
        return tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, RecursionError):
        return None  # untrusted input: deeply nested tables are as unreadable as malformed ones


@dataclass(slots=True)
class _Package:
    directory: str
    name: str
    edition: int
    auto: frozenset[str]  # target kinds inferred from the layout (``autotests = false``, ...)
    lib: tuple[str, str]  # (library name, root path)
    declared: list[tuple[str, str, str]]  # (kind, name, root path)
    deps: dict[str, Dep]
    build: dict[str, Dep]


def _dependencies(table: Toml, directory: str, workspace: str, shared: Toml) -> dict[str, Dep]:
    found: dict[str, Dep] = {}
    for key, entry in table.items():
        spec, base = _table(entry), directory
        if spec.get("workspace") is True:
            spec, base = _table(shared.get(key)), workspace
        where = _norm(_join(base, str(spec["path"]))) if "path" in spec else None
        found[key.replace("-", "_")] = (where, "package" in spec)
    return found


def _collect(
    doc: Toml, keys: tuple[str, ...], directory: str, workspace: str, shared: Toml
) -> dict[str, Dep]:
    """The named dependency tables of a manifest, and of its ``[target.*]`` sections."""
    found: dict[str, Dep] = {}
    for table in [doc, *map(_table, _table(doc.get("target")).values())]:
        for key in keys:
            found.update(_dependencies(_table(table.get(key)), directory, workspace, shared))
    return found


def _packages(docs: dict[str, Toml]) -> dict[str, _Package]:
    found: dict[str, _Package] = {}
    for directory, doc in docs.items():
        package = _table(doc.get("package"))
        if not package:
            continue
        root = directory
        while "workspace" not in docs.get(root, {}) and root:
            root = posixpath.dirname(root)
        space = _table(docs.get(root, {}).get("workspace"))
        shared = _table(space.get("dependencies"))
        edition = package.get("edition")
        if isinstance(edition, dict):
            edition = _table(space.get("package")).get("edition")
        name = str(package.get("name", "")).replace("-", "_")
        lib = _table(doc.get("lib"))

        declared = []
        for kind in ("bin", "test", "example", "bench"):
            entries = doc.get(kind)
            for spec in map(_table, entries if isinstance(entries, list) else []):
                if "path" in spec:
                    declared.append(
                        (
                            kind,
                            str(spec.get("name", "")),
                            _norm(_join(directory, str(spec["path"]))),
                        )
                    )
        if isinstance(package.get("build"), str):
            declared.append(("build", "build", _norm(_join(directory, package["build"]))))
        found[directory] = _Package(
            directory,
            name,
            int(str(edition)) if str(edition).isdigit() else 2015,
            frozenset(
                kind
                for kind in ("bin", "test", "example", "bench")
                if package.get(f"auto{kind}s") is not False
            ),
            (
                str(lib.get("name", name)).replace("-", "_"),
                _norm(_join(directory, str(lib.get("path", "src/lib.rs")))),
            ),
            declared,
            _collect(doc, ("dependencies", "dev-dependencies"), directory, root, shared),
            _collect(doc, ("build-dependencies",), directory, root, shared),
        )
    return found


def _owner(path: str, packages: dict[str, _Package]) -> _Package | None:
    """The package of the nearest ancestor directory that has a manifest with ``[package]``."""
    directory = posixpath.dirname(path)
    while directory not in packages and directory:
        directory = posixpath.dirname(directory)
    return packages.get(directory)


def _discover(package: _Package, paths: list[str]) -> list[tuple[str, str, str]]:
    """Conventional targets among the package's files."""
    prefix = package.directory + "/" if package.directory else ""
    found: list[tuple[str, str, str]] = []
    for path in paths:
        parts = path[len(prefix) :].split("/")
        if parts == ["src", "main.rs"] and "bin" in package.auto:
            found.append(("bin", package.name, path))
        elif parts == ["build.rs"]:
            found.append(("build", "build", path))
        for folder, kind in _CONVENTIONS:
            if kind not in package.auto:
                continue
            base = folder.split("/")
            rest = parts[len(base) :]
            if parts[: len(base)] == base and len(rest) == 1:
                found.append((kind, rest[0][:-3], path))
            elif parts[: len(base)] == base and len(rest) == 2 and rest[1] == "main.rs":
                found.append((kind, rest[0], path))
    return found


class _Tree:
    def __init__(self, files: dict[str, Parsed]) -> None:
        self.files = files
        self.layout = Layout()
        self.claimed: dict[str, Module] = {}  # file -> the module it is

    def module(
        self, crate: Crate, name: str | None, file: str, within: tuple[str, ...],
        parent: Module | None, public: bool, ordinal: int = 0,
    ) -> Module:  # fmt: skip
        """A module; ``ordinal`` separates same-named variants of one parent (``cfg``)."""
        path = (*parent.path, name) if parent is not None and name else ()
        key = f"module:rust:{crate.root}"
        if parent is not None and name:
            key = f"{parent.key}::{name}" + (f"~{ordinal}" if ordinal else "")
        found = Module(key, crate, path, file, parent, public)
        self.layout.modules.append(found)
        self.layout.at[(file, within)] = found
        self.layout.children[key] = {}
        if parent is not None and name:
            self.layout.children[parent.key].setdefault(name, []).append(found)
        if not within:
            self.claimed[file] = found
        return found

    def locate(
        self, file: str, name: str, inline: tuple[str, ...], path: str | None, flat: bool
    ) -> str | None:
        """The file of ``mod name;`` declared in ``file`` under inline modules ``inline``."""
        here = posixpath.dirname(file)
        base = _join(here, *inline) if flat else _join(here, posixpath.basename(file)[:-3], *inline)
        if path is not None:
            found = _norm(_join(base if inline else here, path))
            return found if found in self.files else None
        for candidate in (_join(base, f"{name}.rs"), _join(base, name, "mod.rs")):
            if candidate in self.files:
                return candidate
        return None

    def mount(self, crate: Crate) -> None:
        if crate.root in self.claimed or crate.root not in self.files:
            return
        root = self.module(crate, None, crate.root, (), None, True)
        self.layout.roots[crate.root] = root
        stack = [root]
        while stack:
            module = stack.pop()
            file = module.file
            flat = file == crate.root or posixpath.basename(file) == "mod.rs"
            inline: dict[tuple[str, ...], Module] = {(): module}
            for mod in self.files[file].mods:
                holder = inline.get(mod.module)
                if holder is None:
                    continue  # inside a module that was not mounted
                siblings = self.layout.children[holder.key].get(mod.name, [])
                if mod.inline:
                    within = (*mod.module, mod.name)
                    if within not in inline:  # repeated inline variants share the first
                        inline[within] = self.module(
                            crate, mod.name, file, within, holder, mod.public, len(siblings)
                        )
                    continue
                target = self.locate(file, mod.name, mod.module, mod.path, flat)
                if target is None:
                    continue
                if target in self.claimed:
                    if self.claimed[target] not in siblings:
                        self.layout.children[holder.key].setdefault(mod.name, []).append(
                            self.claimed[target]
                        )
                else:
                    stack.append(
                        self.module(crate, mod.name, target, (), holder, mod.public, len(siblings))
                    )


def layout(files: dict[str, Parsed], manifests: dict[str, bytes]) -> Layout:
    """Modules and crates of one capture: ``files`` by path, ``manifests`` (``Cargo.toml``)
    by path."""
    docs = {p.rpartition("/")[0]: d for p, b in sorted(manifests.items()) if (d := _manifest(b))}
    packages = _packages(docs)
    owned: dict[str, list[str]] = {}
    for path in sorted(files):
        if (owner := _owner(path, packages)) is not None:
            owned.setdefault(owner.directory, []).append(path)
    libs = {d: p.lib[1] for d, p in packages.items() if p.lib[1] in files}
    tree, seen = _Tree(files), set[str]()

    def crate(package: _Package, kind: str, name: str, root: str) -> Crate:
        # Cargo's rule: a dependency is named by its library target, or by the key when
        # the entry renames it with ``package =``.
        deps = {
            key if renamed else packages[where].lib[0]: libs[where]
            for key, (where, renamed) in (
                package.build if kind == "build" else package.deps
            ).items()
            if where in libs
        }
        if kind != "lib" and package.directory in libs:
            deps.setdefault(package.lib[0], libs[package.directory])
        return Crate(root, name, package.edition, kind, tuple(sorted(deps.items())))

    for directory, package in sorted(packages.items()):
        targets = [("lib", *package.lib)] if directory in libs else []
        found = {  # a declared target overrides the one inferred from the same file
            t[2]: t for t in [*_discover(package, owned.get(directory, [])), *package.declared]
        }
        ordered = sorted(found.values(), key=lambda t: (_ORDER.index(t[0]), t[2]))
        for kind, name, root in [*targets, *ordered]:
            if root not in seen and root in files:
                seen.add(root)
                tree.mount(crate(package, kind, name, root))
    for path in sorted(files):
        if path not in tree.claimed:  # reached by no crate: a crate of its own
            owner = _owner(path, packages)
            if owner is not None:
                tree.mount(crate(owner, "file", posixpath.basename(path)[:-3], path))
            else:
                tree.mount(Crate(path, posixpath.basename(path)[:-3], DEFAULT_EDITION, "file", ()))
    return tree.layout
