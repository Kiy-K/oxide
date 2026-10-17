"""Derivation: captured source to entities and typed relations (SPEC § Indexing and publication).

Every file is file evidence with explicit coverage, whether or not a language slice
understands it. The derivation identity names every component that shapes the
derived knowledge, so a change in any of them is a different generation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.metadata import version
from typing import Any

from oxide.capture import Capture
from oxide.core import python
from oxide.core.python import Edge
from oxide.core.retrieval import TERMS_VERSION
from oxide.models import Coverage, Entity, FileInfo, OxideError, SourceRef

ID_NAMESPACE = "oxide-id-v2"
TREE_PROJECTION = "1"


@dataclass(frozen=True, slots=True)
class Derived:
    files: tuple[FileInfo, ...]
    entities: tuple[Entity, ...]
    edges: tuple[Edge, ...]
    # ``python.encode``d facts of every parsed Python file by digest: a cache for the
    # next derivation, not knowledge.
    facts: dict[str, Any]
    reused: int = field(default=0, compare=False)  # files whose cached facts were usable


def components(storage: dict[str, str]) -> dict[str, str]:
    """Named versions of everything that shapes a generation (ADR-0010 § DerivationId)."""
    parser = (
        f"tree-sitter={version('tree-sitter')} tree-sitter-python={version('tree-sitter-python')}"
    )
    return {
        "id-namespace": ID_NAMESPACE,
        "python": python.VERSION,
        "python-parser": parser,
        "terms": TERMS_VERSION,
        "tree-projection": TREE_PROJECTION,
        **storage,
    }


def derivation_id(parts: dict[str, str]) -> str:
    encoded = json.dumps(sorted(parts.items()), separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def validate(derived: Derived) -> None:
    """Publication invariants: unique keys, existing endpoints, and exactly one physical
    parent for every entity but the repository (so TreeIndex cannot contradict identity)."""
    keys: set[str] = set()
    for entity in derived.entities:
        if entity.key in keys:
            raise OxideError(f"invalid derivation: duplicate entity {entity.key}")
        keys.add(entity.key)
    parents: dict[str, int] = {}
    for edge in derived.edges:
        missing = [k for k in (edge.source, *edge.targets) if k not in keys]
        if missing:
            raise OxideError(f"invalid derivation: {edge.kind} edge to unknown {missing[0]}")
        if edge.kind == "contains":
            parents[edge.targets[0]] = parents.get(edge.targets[0], 0) + 1
    for key in keys - {"repo"}:
        if parents.get(key) != 1:
            raise OxideError(f"invalid derivation: {key} has {parents.get(key, 0)} parents")


def derive(capture: Capture, repo_id: str, cached: Mapping[str, Any] | None = None) -> Derived:
    """``cached`` holds facts of an earlier derivation under the same components, by
    file digest. Parsing is a pure function of the bytes, so reusing them derives
    exactly what parsing would; an entry ``python.decode`` rejects is parsed instead.
    Resolution always runs over the whole capture."""
    cached = cached or {}
    facts: dict[str, Any] = {}
    reused = 0
    files: list[FileInfo] = []
    entities = [Entity("repo", "repository", "repository", repo_id, None, None, False)]
    edges: list[Edge] = []
    sources: list[tuple[str, str, python.Parsed]] = []
    for f in capture.files:
        key = f"file:{f.path}"
        name = f.path.rsplit("/", 1)[-1]
        source = SourceRef(f.path, 0, len(f.data), f.digest)
        entities.append(Entity(key, "file", "file", name, None, source, False))
        edges.append(Edge("contains", "repo", (key,), "resolved", "syntactic", None, None))
        if not f.path.endswith(".py"):
            files.append(FileInfo(f.path, f.digest, len(f.data), "unknown", "unsupported", ()))
            continue
        raw = cached.get(f.digest)
        parsed: python.Parsed | str | None = None
        if raw is not None:
            try:
                parsed = python.decode(raw, len(f.data))
                reused += 1
            except ValueError:
                raw = None  # unusable: parsed below and replaced, so it never spreads
        if parsed is None:
            parsed = python.parse(f.data)
        if isinstance(parsed, str):
            info = FileInfo(f.path, f.digest, len(f.data), "python", "unsupported", (parsed,))
        else:
            sources.append((f.path, f.digest, parsed))
            facts[f.digest] = python.encode(parsed) if raw is None else raw
            coverage: Coverage = "partial" if parsed.diagnostics else "complete"
            info = FileInfo(
                f.path, f.digest, len(f.data), "python", coverage, tuple(parsed.diagnostics)
            )
        files.append(info)
    python.Resolver(sources).derive(entities, edges)
    return Derived(tuple(files), tuple(entities), tuple(edges), facts, reused)
