"""Model-facing renderers for Decision Study v1 (preregistration.md § Contract boundary).

A renderer is a pure function of one request's sendable capsules, in presentation order,
to one JEV ``state``; batch ids ``c1…cN`` follow that order and each id gets one ``noul``
question over the shared state. A renderer only drops or re-encodes canonical fields;
the full-query diagnostic is the one exception (never promotable).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from oxide.core.capsule import Capsule

QUESTION = (
    "The state describes one software task and candidate code regions found for it. "
    "Must a developer read the code of candidate `candidates.{id}` to resolve the task?"
)
CRITERIA = {
    "true": "The candidate's code is needed to understand, locate or make the change the "
    "task asks for: it is where the change goes, or code the change depends on directly.",
    "false": "The candidate is unrelated, or only shares words or a topic with the task.",
}
QUESTIONS_DIGEST = "sha256:" + hashlib.sha256(json.dumps([QUESTION, CRITERIA]).encode()).hexdigest()
RANK_FIELDS = ("lexical_rank", "lexical_score")
TASK_FIELDS = ("query", "query_digest", "schema", "derivation", "source_digest")
COMPACT_BYTES = 1024
COMPACT_ORIGINS = 3


def questions(n: int) -> dict[str, Any]:
    return {
        f"c{i}": {"type": "noul", "instructions": QUESTION.format(id=f"c{i}"), "criteria": CRITERIA}
        for i in range(1, n + 1)
    }


def _fields(c: Capsule, rank: bool, drop: Sequence[str] = ()) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(c.canonical)
    for name in (*drop, *(() if rank else RANK_FIELDS)):
        data.pop(name, None)
    if drop:
        data["truncated"] = [t for t in data["truncated"] if t != "query"]
    return data


def _task(capsules: Sequence[Capsule], query: str | None) -> dict[str, Any]:
    first = capsules[0]
    assert all(c.query_digest == first.query_digest for c in capsules), "one task a request"
    if query is not None:
        return {"task": query}
    return {"task": first.query, "task_clipped_to_1024_bytes": "query" in first.truncated}


def canonical(capsules: Sequence[Capsule], rank: bool) -> dict[str, Any]:
    return {"candidates": {f"c{i}": _fields(c, rank) for i, c in enumerate(capsules, 1)}}


def shared(capsules: Sequence[Capsule], rank: bool, query: str | None = None) -> dict[str, Any]:
    rows = {f"c{i}": _fields(c, rank, TASK_FIELDS) for i, c in enumerate(capsules, 1)}
    return {**_task(capsules, query), "candidates": rows}


def _bounded(excerpt: str) -> tuple[str, bool]:
    data = excerpt.encode()
    if len(data) <= COMPACT_BYTES:
        return excerpt, False
    cut = data.rfind(b"\n", 0, COMPACT_BYTES) + 1 or COMPACT_BYTES
    return data[:cut].decode(errors="ignore"), True


def _header(c: Capsule, rank: bool) -> str:
    name = c.key.rsplit("#", 1)[1] if c.key.startswith("symbol:") else c.name
    where = c.path or "-"
    if c.lines is not None:
        where += f":{c.lines[0]}-{c.lines[1]}"
    parts = [f"{c.category} {c.kind} {name} {where}"]
    if c.test:
        parts.append("test")
    if rank and c.lexical_rank is not None:
        parts.append(f"rank {c.lexical_rank} score {c.lexical_score}")
    if c.seeds:
        parts.append("seeds " + ", ".join(c.seeds))
    if c.hinted:
        parts.append("hinted path")
    origins = list(c.origins[:COMPACT_ORIGINS])
    more = len(c.origins) - len(origins) + c.origins_omitted
    if more:
        origins.append(f"+{more} more")
    parts.append(f"depth {c.depth}" + (" via " + ", ".join(origins) if origins else ""))
    return " | ".join(parts)


def compact(capsules: Sequence[Capsule], rank: bool) -> dict[str, Any]:
    rows = {}
    for i, c in enumerate(capsules, 1):
        text, cut = _bounded(c.excerpt or (c.signature or ""))
        more = cut or "excerpt" in c.truncated
        tail = f"\n… ({c.source_bytes} bytes in full)" if more else ""
        rows[f"c{i}"] = f"{_header(c, rank)}\n{text.rstrip(chr(10))}{tail}"
    return {**_task(capsules, None), "candidates": rows}


RENDERERS = {"canonical": canonical, "shared": shared, "compact": compact}


def version(name: str, rank: bool, fullquery: bool = False) -> str:
    return f"ds1-{name}{'-fullquery' if fullquery else ''}-v1" + ("" if rank else "-norank")


def render(
    name: str, capsules: Sequence[Capsule], rank: bool, fullquery: str | None = None
) -> dict[str, Any]:
    """The JEV ``state`` for one request."""
    if fullquery is not None:
        assert name == "shared", "the full-query diagnostic is defined for shared only"
        return shared(capsules, rank, fullquery)
    return RENDERERS[name](capsules, rank)


def text(state: dict[str, Any], n: int) -> str:
    """What a tokenizer estimate counts: the state and every question, compact JSON."""
    return json.dumps({"state": state, "questions": questions(n)}, ensure_ascii=False)
