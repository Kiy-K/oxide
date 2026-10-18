"""Derivation: captured source to entities and typed relations (SPEC § Indexing and publication).

Every file is file evidence with explicit coverage, whether or not a language slice
understands it. The derivation identity names every component that shapes the
derived knowledge, so a change in any of them is a different generation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from importlib.metadata import version
from typing import Any, Literal

from oxide.capture import Capture
from oxide.core import python, typescript
from oxide.core.python import Edge
from oxide.core.retrieval import TERMS_VERSION
from oxide.models import Coverage, Entity, FileInfo, OxideError, SourceRef

ID_NAMESPACE = "oxide-id-v2"
TREE_PROJECTION = "1"
# ``facts.json``: one section of encoded facts by digest per dialect (the same bytes
# parse differently across grammars); include each slice's facts version.
FACTS_FORMAT = (
    f"oxide-facts-v2 {python.FACTS_VERSION} {typescript.FACTS_VERSION} "
    f"{typescript.JAVASCRIPT_FACTS_VERSION}"
)

Dialect = Literal["python", "typescript", "tsx", "javascript"]


def dialect(path: str) -> Dialect | None:
    """The language slice and grammar that parse ``path``, None when none does."""
    return "python" if path.endswith(".py") else typescript.dialect(path)


@dataclass(frozen=True, slots=True)
class Derived:
    files: tuple[FileInfo, ...]
    entities: tuple[Entity, ...]
    edges: tuple[Edge, ...]
    # Encoded facts of every parsed file, by dialect and digest (``FACTS_FORMAT``): a
    # cache for the next derivation, not knowledge.
    facts: dict[str, dict[str, Any]]
    reused: int = field(default=0, compare=False)  # files whose cached facts were usable


def components(storage: dict[str, str]) -> dict[str, str]:
    """Named versions of everything that shapes a generation (ADR-0010 § DerivationId)."""
    parser = f"tree-sitter={version('tree-sitter')}"
    return {
        "id-namespace": ID_NAMESPACE,
        "python": python.VERSION,
        "python-parser": f"{parser} tree-sitter-python={version('tree-sitter-python')}",
        "typescript": typescript.VERSION,
        "typescript-parser": f"{parser} tree-sitter-typescript={version('tree-sitter-typescript')}",
        "javascript": typescript.JAVASCRIPT_VERSION,
        "javascript-parser": f"{parser} tree-sitter-javascript={version('tree-sitter-javascript')}",
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


def _reuse[P](
    raw: Any, data: bytes, decode: Callable[[Any, int], P], parse: Callable[[bytes], P | str]
) -> tuple[P | str, bool]:
    """Cached facts when ``decode`` accepts them, else a parse; and whether reused."""
    if raw is not None:
        try:
            return decode(raw, len(data)), True
        except ValueError:
            pass  # unusable: parsed and replaced, so it never spreads
    return parse(data), False


def derive(capture: Capture, repo_id: str, cached: Mapping[str, Any] | None = None) -> Derived:
    """``cached`` holds facts of an earlier derivation under the same components, by
    dialect and file digest. Parsing is a pure function of the bytes, so reusing them
    derives exactly what parsing would; an entry ``decode`` rejects is parsed instead.
    Resolution always runs over the whole capture."""
    cached = cached or {}
    facts: dict[str, dict[str, Any]] = {}
    reused = 0
    files: list[FileInfo] = []
    entities = [Entity("repo", "repository", "repository", repo_id, None, None, False)]
    edges: list[Edge] = []
    py_sources: list[tuple[str, str, python.Parsed]] = []
    ts_sources: list[tuple[str, str, typescript.Parsed]] = []
    for f in capture.files:
        key = f"file:{f.path}"
        name = f.path.rsplit("/", 1)[-1]
        source = SourceRef(f.path, 0, len(f.data), f.digest)
        entities.append(Entity(key, "file", "file", name, None, source, False))
        edges.append(Edge("contains", "repo", (key,), "resolved", "syntactic", None, None))
        lang = dialect(f.path)
        if lang is None:
            files.append(FileInfo(f.path, f.digest, len(f.data), "unknown", "unsupported", ()))
            continue
        section = cached.get(lang)
        raw = section.get(f.digest) if isinstance(section, dict) else None
        language = lang if lang in ("python", "javascript") else "typescript"
        diagnostics: list[str] | str
        if lang == "python":
            py, hit = _reuse(raw, f.data, python.decode, python.parse)
            if not isinstance(py, str):
                py_sources.append((f.path, f.digest, py))
                encoded = raw if hit else python.encode(py)
            diagnostics = py if isinstance(py, str) else py.diagnostics
        else:
            parse = partial(typescript.parse, language=lang)
            ts, hit = _reuse(raw, f.data, typescript.decode, parse)
            if not isinstance(ts, str):
                ts_sources.append((f.path, f.digest, ts))
                encoded = raw if hit else typescript.encode(ts)
            diagnostics = ts if isinstance(ts, str) else ts.diagnostics
        reused += hit
        if isinstance(diagnostics, str):
            info = FileInfo(f.path, f.digest, len(f.data), language, "unsupported", (diagnostics,))
        else:
            facts.setdefault(lang, {})[f.digest] = encoded
            coverage: Coverage = "partial" if diagnostics else "complete"
            info = FileInfo(f.path, f.digest, len(f.data), language, coverage, tuple(diagnostics))
        files.append(info)
    python.Resolver(py_sources).derive(entities, edges)
    parsed = {path for path, _, _ in ts_sources}
    others = {f.path: f.data for f in capture.files if f.path not in parsed}
    typescript.Resolver(ts_sources, others).derive(entities, edges)
    return Derived(tuple(files), tuple(entities), tuple(edges), facts, reused)
