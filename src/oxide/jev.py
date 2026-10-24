"""The JEV adapter for admitted-candidate ordering (ADR-0009). Internal and optional: not
exported from ``oxide``, never constructed by the CLI, MCP server or the default path.

It sends the request Decision Study v3 validated, byte for byte: TypeSafe JEV, pinned
``jev-1.14.0``, renderer ``ds1-compact-v1`` with rank (task once; per capsule a header
line and an excerpt of at most 1,024 bytes), capsules in digest order, one ``noul``
question per capsule. The call runs in a child process killed at the 2.0 s deadline, so
no late reply is read and nothing outlives the request. There is no retry. Any failure
raises ``ordering.Unanswered`` with its outcome, and the request keeps the heuristic.

Source leaves the machine only for a repository the caller explicitly authorized
(``order``). A credential is not consent: without that authorization nothing is sent.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from oxide.core.capsule import DEADLINE, Capsule, CapsuleJudge
from oxide.core.ordering import AdmittedOrder, Order, Unanswered
from oxide.models import SourceRef

if TYPE_CHECKING:
    from oxide.api import Repository

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.14.0"
NAME = f"typesafe/{MODEL}"
RENDERER = "ds1-compact-v1"
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
COMPACT_BYTES = 1024
COMPACT_ORIGINS = 3
MANIFEST: dict[str, Any] = {
    "provider": "typesafe",
    "endpoint": ENDPOINT,
    "model": MODEL,
    "long_context": False,
    "deadline_s": DEADLINE,
    "retries": 0,
    "questions": QUESTIONS_DIGEST,
    "usd_per_million_input_tokens": 0.042,
}
MANIFEST_DIGEST = (
    "sha256:" + hashlib.sha256(json.dumps(MANIFEST, sort_keys=True).encode()).hexdigest()
)
# Decision Study v3's manifest: a change here is a new policy identity (ADR-0009 § 4).
assert MANIFEST_DIGEST == (
    "sha256:d9ae934c9a6a3650a6f2ca6ef3f3ab919103cc7e9788951a7b8699c354407b1d"
), MANIFEST_DIGEST

CHILD = """
import json, sys, urllib.error, urllib.request
job = json.load(sys.stdin)
req = urllib.request.Request(job["url"], data=json.dumps(job["body"]).encode(), method="POST",
    headers={"Content-Type": "application/json", "Authorization": "Bearer " + job["key"]})
try:
    with urllib.request.urlopen(req, timeout=job["deadline"]) as r:
        print(json.dumps({"status": r.status, "body": r.read().decode()}))
except urllib.error.HTTPError as e:
    print(json.dumps({"status": e.code, "body": e.read().decode(errors="replace")}))
"""


def questions(n: int) -> dict[str, Any]:
    return {
        f"c{i}": {"type": "noul", "instructions": QUESTION.format(id=f"c{i}"), "criteria": CRITERIA}
        for i in range(1, n + 1)
    }


def _bounded(excerpt: str) -> tuple[str, bool]:
    data = excerpt.encode()
    if len(data) <= COMPACT_BYTES:
        return excerpt, False
    cut = data.rfind(b"\n", 0, COMPACT_BYTES) + 1 or COMPACT_BYTES
    return data[:cut].decode(errors="ignore"), True


def _header(c: Capsule) -> str:
    name = c.key.rsplit("#", 1)[1] if c.key.startswith("symbol:") else c.name
    where = c.path or "-"
    if c.lines is not None:
        where += f":{c.lines[0]}-{c.lines[1]}"
    parts = [f"{c.category} {c.kind} {name} {where}"]
    if c.test:
        parts.append("test")
    if c.lexical_rank is not None:
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


def render(capsules: Sequence[Capsule]) -> dict[str, Any]:
    """The JEV ``state`` of one request (``ds1-compact-v1`` with rank)."""
    first = capsules[0]
    assert all(c.query_digest == first.query_digest for c in capsules), "one task a request"
    rows = {}
    for i, c in enumerate(capsules, 1):
        text, cut = _bounded(c.excerpt or (c.signature or ""))
        tail = f"\n… ({c.source_bytes} bytes in full)" if cut or "excerpt" in c.truncated else ""
        rows[f"c{i}"] = f"{_header(c)}\n{text.rstrip(chr(10))}{tail}"
    task = {"task": first.query, "task_clipped_to_1024_bytes": "query" in first.truncated}
    return {**task, "candidates": rows}


def body(capsules: Sequence[Capsule]) -> dict[str, Any]:
    return {"model": MODEL, "state": render(capsules), "questions": questions(len(capsules))}


def call(url: str, payload: dict[str, Any], key: str, deadline: float) -> dict[str, Any]:
    """One request in a child process killed at ``deadline``: status, latency, body."""
    job = json.dumps({"url": url, "body": payload, "key": key, "deadline": deadline})
    env = {k: v for k in ("PATH", "SYSTEMROOT") if (v := os.environ.get(k)) is not None}
    start = time.monotonic()
    try:
        done = subprocess.run(
            [sys.executable, "-I", "-c", CHILD],
            input=job,
            capture_output=True,
            text=True,
            timeout=deadline,  # kills the child on expiry
            env=env,  # SYSTEMROOT: Windows sockets need it
        )
    except subprocess.TimeoutExpired:
        return {"status": "killed", "latency_s": time.monotonic() - start}
    latency = time.monotonic() - start
    if done.returncode != 0 or not done.stdout.strip():
        return {"status": "error", "latency_s": latency}
    reply: dict[str, Any] = json.loads(done.stdout)
    return {"status": reply["status"], "latency_s": latency, "body": reply["body"]}


def decide(
    reply: Mapping[str, Any], capsules: Sequence[Capsule], deadline: float
) -> dict[str, Any]:
    """Judgments by capsule digest; raises ``Unanswered`` unless complete and on time."""
    status = reply["status"]
    if status == "killed" or (status == 200 and reply["latency_s"] > deadline):
        raise Unanswered("deadline")
    if status == "error":
        raise Unanswered("transport")
    if status != 200:
        raise Unanswered(f"http {status}")
    try:
        data = json.loads(reply["body"])
        model = data["model"]
        found = {}
        for i, c in enumerate(capsules, 1):
            answer = data["answers"].get(f"c{i}")
            if answer is not None:
                found[c.digest] = answer["noul"]
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise Unanswered("unparsable") from error
    if model != MODEL:
        raise Unanswered("wrong_model")
    if len(found) != len(capsules):
        raise Unanswered("incomplete")
    return found


@dataclass(frozen=True, slots=True)
class Jev:
    """The ``capsule.Adapter`` that calls JEV: one attempt, no retry."""

    key: str
    url: str = ENDPOINT
    deadline: float = DEADLINE

    def __call__(self, capsules: Sequence[Capsule]) -> Mapping[str, float | None]:
        reply = call(self.url, body(capsules), self.key, self.deadline)
        return decide(reply, capsules, self.deadline)


def _refused(capsules: Sequence[Capsule]) -> Mapping[str, float | None]:
    raise Unanswered("not_authorized")


def order(
    repository: Repository, authorized: str | os.PathLike[str], key: str, url: str = ENDPOINT
) -> Order:
    """JEV ordering for ``repository``, sending its source only if ``authorized`` names
    its root; otherwise every request falls back as ``not_authorized``, unsent."""
    allowed = Path(authorized).resolve() == repository.root
    adapter = Jev(key, url) if allowed else _refused

    def build(query: str, derivation: str, source: Callable[[SourceRef], bytes]) -> AdmittedOrder:
        return AdmittedOrder(
            CapsuleJudge(NAME, query, derivation, source, adapter), MANIFEST_DIGEST
        )

    return build
