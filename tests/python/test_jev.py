"""The internal JEV adapter (ADR-0009 §§ 2-4), against a localhost server only: one
attempt killed at the deadline, every failure named, nothing sent unless authorized."""

from __future__ import annotations

import http.server
import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import FIXTURE

import oxide
from oxide import jev
from oxide.core.capsule import DEADLINE, Capsule, build
from oxide.core.ordering import Unanswered
from oxide.core.packing import Candidate
from oxide.models import Entity, SourceRef

SOURCE = b"def a():\n    return 1\ndef b():\n    return 2\n"


def _capsules() -> list[Capsule]:
    out = []
    for n, name in enumerate("ab"):
        start = SOURCE.index(f"def {name}".encode())
        ref = SourceRef("m.py", start, start + 21, "sha256:x")
        entity = Entity(f"symbol:m.py#{name}", "symbol", "function", name, None, ref, False)
        built = build("task", "d", Candidate(entity, 0, n + 1, False), lambda r: SOURCE)
        assert built is not None
        out.append(built)
    return sorted(out, key=lambda c: c.digest)


class Server:
    def __init__(self) -> None:
        self.received: list[tuple[str, dict[str, Any], str]] = []
        self.release = threading.Event()
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                sent = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                path = self.path.strip("/")
                owner.received.append((path, sent, self.headers["Authorization"]))
                if path == "hang":
                    owner.release.wait(10)
                    return
                if path == "late":
                    time.sleep(1.5)
                status = 529 if path == "busy" else 200
                names = list(sent["questions"])[: -1 if path == "partial" else None]
                data = {
                    "model": "jev-latest" if path == "wrong" else jev.MODEL,
                    "answers": {q: {"type": "noul", "noul": 0.5} for q in names},
                }
                reply = b"not json" if path == "garbage" else json.dumps(data).encode()
                try:
                    self.send_response(status)
                    self.end_headers()
                    self.wfile.write(reply)
                except OSError:
                    pass  # the client was killed

            def log_message(self, *args: object) -> None:
                pass

        self.http = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.http.server_port}/"


@pytest.fixture
def server() -> Iterator[Server]:
    s = Server()
    yield s
    s.release.set()
    s.http.shutdown()


def test_pinned_identity_is_decision_study_v3s() -> None:
    assert jev.MANIFEST_DIGEST == (
        "sha256:d9ae934c9a6a3650a6f2ca6ef3f3ab919103cc7e9788951a7b8699c354407b1d"
    )
    assert jev.MANIFEST["retries"] == 0 and jev.MANIFEST["deadline_s"] == 2.0


def test_an_answer_is_judged_and_the_body_is_the_rendered_request(server: Server) -> None:
    caps = _capsules()
    found = jev.Jev("secret", server.base + "ok")(caps)
    assert found == {c.digest: 0.5 for c in caps}
    [(path, sent, auth)] = server.received
    assert path == "ok"
    assert sent == jev.body(caps) and auth == "Bearer secret"
    assert list(sent["state"]["candidates"]) == list(sent["questions"]) == ["c1", "c2"]
    assert sent["state"]["candidates"]["c1"].startswith("symbol function ")


@pytest.mark.parametrize(
    ("path", "outcome"),
    [
        ("busy", "http 529"),
        ("wrong", "wrong_model"),
        ("partial", "incomplete"),
        ("garbage", "unparsable"),
        ("hang", "deadline"),
        ("late", "deadline"),
    ],
)
def test_every_failure_is_one_attempt_with_a_named_outcome(
    server: Server, path: str, outcome: str
) -> None:
    start = time.monotonic()
    with pytest.raises(Unanswered) as raised:
        jev.Jev("k", server.base + path, deadline=1.0)(_capsules())
    assert raised.value.outcome == outcome
    assert len(server.received) == 1  # no retry
    assert time.monotonic() - start < 1.5  # killed at the deadline, the late reply unread


def test_unreachable_is_a_transport_failure() -> None:
    with pytest.raises(Unanswered) as raised:
        jev.Jev("k", "http://127.0.0.1:9/", deadline=1.0)(_capsules())
    assert raised.value.outcome == "transport"


@pytest.fixture
def repo(fts: Path) -> Iterator[oxide.Repository]:
    with oxide.open_repository(FIXTURE) as repository:
        repository.index()
        yield repository


QUERY = "where failed HTTP requests are retried"


def _context(repo: oxide.Repository, by: Any) -> oxide.ContextBundle:
    return repo._context(QUERY, 1024, (), (), None, (), by)


def test_an_authorized_repository_is_judged_once(repo: oxide.Repository, server: Server) -> None:
    bundle = _context(repo, jev.order(repo, FIXTURE, "k", server.base + "ok"))
    assert len(server.received) == 1
    assert bundle.versions["judge"] == "typesafe/jev-1.14.0@oxide-capsule-v1"
    assert bundle.versions["judge_manifest"] == jev.MANIFEST_DIGEST


def test_nothing_is_sent_without_authorization(
    repo: oxide.Repository, server: Server, tmp_path: Path
) -> None:
    bundle = _context(repo, jev.order(repo, tmp_path, "k", server.base + "ok"))
    assert server.received == []
    assert bundle.degradations[-1].endswith("fell back: not_authorized")
    plain = repo.build_context(QUERY, 1024)
    assert [i.source for i in bundle.items] == [i.source for i in plain.items]


def test_a_hung_judge_falls_back_and_leaves_nothing_running(
    repo: oxide.Repository, server: Server
) -> None:
    start = time.monotonic()
    bundle = _context(repo, jev.order(repo, FIXTURE, "k", server.base + "hang"))
    assert time.monotonic() - start < DEADLINE + 1.0
    assert bundle.degradations[-1].endswith("fell back: deadline")
    assert [i.source for i in bundle.items] == [
        i.source for i in repo.build_context(QUERY, 1024).items
    ]
    # The child is killed at the deadline, so the abandoned judge thread ends too.
    limit = time.monotonic() + 1.0
    while any(t.name == "oxide-judge" for t in threading.enumerate()):
        assert time.monotonic() < limit, "a judge thread outlived the request"
        time.sleep(0.05)
