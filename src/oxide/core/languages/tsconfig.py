"""``tsconfig.json`` as TypeScript reads it for module resolution (docs/typescript.md
§ Specifiers): the project that owns a file, and the candidate paths its ``paths`` and
``baseUrl`` give a bare specifier, in TypeScript's order. Read from captured bytes
only: whatever TypeScript would read from outside the repository (an ``extends`` of a
package or a missing file) makes the options unknown, and unknown options resolve
nothing.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_COMMENT = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.S)
_TRAILING_COMMA = re.compile(r'"(?:\\.|[^"\\])*"|,(?=\s*[}\]])')
# Inherited per key: the last config of the ``extends`` chain that sets a key wins.
_KEYS = ("baseUrl", "paths", "files", "include", "exclude")
_DEFAULT_EXCLUDE = ["node_modules", "bower_components", "jspm_packages"]
MAX_EXTENDS = 32  # a deeper ``extends`` chain is unknowable, not followed recursively

Merged = dict[str, tuple[Any, str]]  # key -> (value, directory of the config that set it)


def jsonc(data: bytes) -> Any:
    """JSON with comments and trailing commas, as tsconfig allows; ValueError if not."""
    text = data.decode("utf-8-sig")
    text = _COMMENT.sub(lambda m: m[0] if m[0][0] == '"' else " ", text)
    try:
        return json.loads(_TRAILING_COMMA.sub(lambda m: m[0] if m[0][0] == '"' else "", text))
    except RecursionError as error:
        raise ValueError("tsconfig nests too deeply") from error


@dataclass(frozen=True, slots=True)
class Options:
    base_url: str | None  # repository-relative directory
    paths: tuple[tuple[str, tuple[str, ...]], ...]
    paths_base: str  # what substitutions are relative to: baseUrl, else paths' config


def _join(directory: str, path: str) -> str | None:
    """``path`` against a repository-relative directory; None outside the repository."""
    if path.startswith("/"):
        return None
    joined = posixpath.normpath(posixpath.join(directory, path))
    return None if joined == ".." or joined.startswith("../") else joined


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _spec(directory: str, spec: str, include: bool) -> re.Pattern[str] | None:
    """An ``include``/``exclude`` pattern: ``**`` any directories, ``*``/``?`` within a
    name, a last name with neither a wildcard nor an extension a directory. Include
    wildcards skip names starting with ``.``, as TypeScript's do."""
    joined = _join(directory, spec)
    if joined is None:
        return None
    names = [] if joined == "." else joined.split("/")
    if not names or not re.search(r"[*?.]", names[-1]):
        names += ["**", "*"]
    if names[-1] == "**":
        if include:  # TypeScript rejects an include ending in **
            return None
        names.append("*")
    regex = ""
    for n, name in enumerate(names):
        if name == "**":
            regex += r"(?:[^/.][^/]*/)*" if include else r"(?:[^/]+/)*"
            continue
        part = "".join("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c) for c in name)
        if include and name[0] in "*?":
            part = r"(?!\.)" + part
        regex += part + ("" if n == len(names) - 1 else "/")
    return re.compile(regex)


class Projects:
    """The ``tsconfig.json`` files of one capture, by path."""

    def __init__(self, files: Mapping[str, bytes]) -> None:
        self.files = files
        self.merged: dict[str, Merged | None] = {}
        self.owners: dict[str, Options | None] = {}

    def options(self, path: str) -> Options | None:
        """The options of the project that owns ``path``: the nearest ``tsconfig.json``
        above it whose files, include and exclude contain it (TypeScript 5.6's
        ancestor search). None when none does, or when that project is unknowable."""
        if path not in self.owners:
            self.owners[path] = self._owner(path)
        return self.owners[path]

    def _owner(self, path: str) -> Options | None:
        directory = posixpath.dirname(path)
        while True:
            config = posixpath.join(directory, "tsconfig.json")
            if config in self.files:
                merged = self._merge(config, frozenset())
                if merged is None:
                    return None
                if self._owns(merged, directory, path):
                    return self._resolved(merged)
            if not directory:
                return None
            directory = posixpath.dirname(directory)

    def _merge(self, config: str, seen: frozenset[str]) -> Merged | None:
        """The keys a config sets, after its ``extends`` chain; None if unknowable."""
        if not seen and config in self.merged:
            return self.merged[config]
        merged = self._read(config, seen)
        if not seen:
            self.merged[config] = merged
        return merged

    def _read(self, config: str, seen: frozenset[str]) -> Merged | None:
        if config in seen or len(seen) >= MAX_EXTENDS or config not in self.files:
            return None
        try:
            value = jsonc(self.files[config])
        except ValueError:
            return None
        if not isinstance(value, dict):
            return None
        directory = posixpath.dirname(config)
        extends = value.get("extends", [])
        bases = [extends] if isinstance(extends, str) else extends
        options = value.get("compilerOptions", {})
        if not _strings(bases) or not isinstance(options, dict):
            return None
        merged: Merged = {}
        for base in bases:
            if not base.startswith(("./", "../")):
                return None  # a package: its options are outside the repository
            target = _join(directory, base)
            if target is not None and target not in self.files and not target.endswith(".json"):
                target += ".json"
            inherited = None if target is None else self._merge(target, seen | {config})
            if inherited is None:
                return None
            merged.update(inherited)
        for key in _KEYS:
            source = options if key in ("baseUrl", "paths") else value
            if key in source:
                merged[key] = (source[key], directory)
        base_url, paths = merged.get("baseUrl"), merged.get("paths")
        if base_url is not None and not isinstance(base_url[0], str):
            return None
        if paths is not None and not (
            isinstance(paths[0], dict) and all(_strings(v) for v in paths[0].values())
        ):
            return None
        if not all(_strings(merged[k][0]) for k in ("files", "include", "exclude") if k in merged):
            return None
        return merged

    def _owns(self, merged: Merged, directory: str, path: str) -> bool:
        files, include = merged.get("files"), merged.get("include")
        if files is not None and path in {_join(files[1], f) for f in files[0]}:
            return True
        if include is None:
            include = ([], directory) if files is not None else (["**/*"], directory)
        exclude = merged.get("exclude", (_DEFAULT_EXCLUDE, directory))

        def matches(specs: tuple[list[str], str], wildcard: bool) -> bool:
            patterns = (_spec(specs[1], s, wildcard) for s in specs[0])
            return any(p is not None and p.fullmatch(path) for p in patterns)

        return matches(include, True) and not matches(exclude, False)

    def _resolved(self, merged: Merged) -> Options:
        base_url = None
        if "baseUrl" in merged:
            value, directory = merged["baseUrl"]
            base_url = posixpath.normpath(posixpath.join(directory, value))
        patterns: list[tuple[str, tuple[str, ...]]] = []
        paths_base = base_url or "."
        if "paths" in merged:
            value, directory = merged["paths"]
            # TypeScript rejects a pattern or substitution with more than one ``*``.
            for pattern, substitutions in value.items():
                if pattern.count("*") <= 1:
                    patterns.append((pattern, tuple(s for s in substitutions if s.count("*") <= 1)))
            paths_base = base_url if base_url is not None else directory
        return Options(base_url, tuple(patterns), paths_base)


def candidates(options: Options, specifier: str) -> list[str]:
    """Where TypeScript looks for a bare ``specifier``, in order, before packages: each
    substitution of the best ``paths`` pattern (an exact one, else the longest prefix),
    then ``baseUrl``. Repository-relative; candidates outside the repository dropped."""
    found: list[str | None] = []
    best: tuple[int, str, tuple[str, ...]] | None = None
    for pattern, substitutions in options.paths:
        if pattern == specifier:
            best = (len(pattern) + 1, "", substitutions)
            break
        prefix, star, suffix = pattern.partition("*")
        if (
            star
            and specifier.startswith(prefix)
            and specifier.endswith(suffix)
            and len(specifier) >= len(prefix) + len(suffix)
            and (best is None or len(prefix) > best[0])
        ):
            best = (
                len(prefix),
                specifier[len(prefix) : len(specifier) - len(suffix)],
                substitutions,
            )
    if best is not None:
        found += [_join(options.paths_base, s.replace("*", best[1])) for s in best[2]]
    if options.base_url is not None:
        found.append(_join(options.base_url, specifier))
    return [c for c in found if c is not None]
