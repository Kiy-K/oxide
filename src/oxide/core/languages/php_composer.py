"""Conservative Composer autoload evidence (docs/php.md).

Only the declarative autoload mappings in ``composer.json`` are read: no Composer
code is executed and no filesystem scan happens here. PSR-4/PSR-0 give a reliable
FQN ↔ path mapping; classmap and files are weaker, file-level evidence.
"""

from __future__ import annotations

import json
import posixpath
from collections.abc import Mapping
from dataclasses import dataclass

# (style, prefix, root directory), prefixes keep their trailing backslash.
Rule = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class Composer:
    rules: tuple[Rule, ...]
    classmap: tuple[str, ...]
    files: tuple[str, ...]


def _norm(base: str, raw: str, directory: bool = False) -> str:
    path = posixpath.normpath(posixpath.join(base, raw)).lstrip("/")
    if path == ".":
        return ""
    return path + "/" if directory and not path.endswith("/") else path


def _paths(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(v for v in value if isinstance(v, str))
    return ()


def load(manifests: Mapping[str, bytes]) -> Composer:
    rules: list[Rule] = []
    classmap: list[str] = []
    files: list[str] = []
    for path, data in sorted(manifests.items()):
        try:
            manifest = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(manifest, dict):
            continue
        base = posixpath.dirname(path)
        for section in ("autoload", "autoload-dev"):
            block = manifest.get(section)
            if not isinstance(block, dict):
                continue
            for style, key in (("psr4", "psr-4"), ("psr0", "psr-0")):
                mapping = block.get(key)
                if not isinstance(mapping, dict):
                    continue
                for prefix, roots in sorted(mapping.items()):
                    if not isinstance(prefix, str):
                        continue
                    for root in _paths(roots):
                        if root.startswith("/"):
                            continue
                        joined = _norm(base, root, directory=True)
                        if not joined.startswith(".."):
                            rules.append((style, prefix, joined))
            classmap.extend(
                _norm(base, p, directory=True)
                for p in _paths(block.get("classmap"))
                if not p.startswith("/")
            )
            files.extend(
                _norm(base, p) for p in _paths(block.get("files")) if not p.startswith("/")
            )
    return Composer(
        tuple(sorted(set(rules), key=lambda r: (-len(r[1]), r[1], r[2]))),
        tuple(sorted(set(classmap))),
        tuple(sorted(set(files))),
    )


def fqn_at(composer: Composer, path: str) -> tuple[str, str] | None:
    """The FQN a captured PHP file is expected to declare, by its path."""
    for style, prefix, root in composer.rules:
        if root and not path.startswith(root):
            continue
        rest = path[len(root) :].removesuffix(".php")
        if style == "psr0":
            folder = prefix.replace("\\", "/")
            if not rest.startswith(folder):
                continue
            rest = rest[len(folder) :]
        if not rest or any(not part or part.startswith(".") for part in rest.split("/")):
            continue
        return style, prefix + rest.replace("/", "\\")
    return None


def path_for(composer: Composer, php_fqn: str) -> tuple[str, str] | None:
    """The path a fully qualified class name is expected to live at."""
    for style, prefix, root in composer.rules:
        if not php_fqn.startswith(prefix):
            continue
        rest = php_fqn[len(prefix) :].replace("\\", "/")
        if not rest:
            continue
        folder = prefix.replace("\\", "/") if style == "psr0" else ""
        return style, f"{root}{folder}{rest}.php"
    return None


def classmap_roots(composer: Composer) -> tuple[str, ...]:
    return composer.classmap
