"""JEV calls for Decision Study v3 (preregistration.md § Judge configuration, § Spending).

    uv run --with tiktoken python docs/phase-6/decision-study-v3/scripts/call.py selfcheck
    uv run --with tiktoken python docs/phase-6/decision-study-v3/scripts/call.py run OUT ARM \
        --authorize CORPUS[,CORPUS…] --ledger LEDGER.json [--sent PRIMARY_JUDGMENTS.json]
    uv run --with tiktoken python docs/phase-6/decision-study-v3/scripts/call.py estimate OUT

The request bodies are v2's primary, byte for byte: v1's ``adapter.py`` (pinned
``jev-1.14.0``, ``ds1-compact-v1`` with rank, capsules in digest order, one ``noul``
question per capsule, model check). What changes is the deadline: production's
``capsule.DEADLINE`` (2.0 s) is enforced on the whole call, child start included. The
child process is killed at the deadline, so a late reply is never read; a reply whose
measured latency still exceeds the deadline is discarded as ``late``. A request without
a complete, valid answer by the deadline records no judgments, so the hybrid falls back
to the heuristic for the whole request. ``selfcheck`` (localhost only) proves both.

Spending is capped per request: a request is sent only if the ledger plus its tiktoken
estimate stays within ``CAP_USD``; a request that reports no billed tokens is charged its
estimate. ``run`` needs ``TYPESAFE_API_KEY`` and refuses unauthorized corpora.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "decision-study-v1" / "scripts"))
sys.path.insert(0, str(HERE.parents[1] / "decision-study-v2" / "scripts"))

import adapter  # noqa: E402
import render  # noqa: E402
import tiktoken  # noqa: E402

from oxide.core import capsule  # noqa: E402

CAP_USD = 0.20  # the whole study (preregistration.md § Spending)
RENDERER = "compact"
DEADLINE = capsule.DEADLINE  # production's 2.0 s, unchanged
USD = adapter.MANIFEST["usd_per_million_input_tokens"] / 1e6
MANIFEST = {**adapter.MANIFEST, "deadline_s": DEADLINE}
MANIFEST_DIGEST = (
    "sha256:" + hashlib.sha256(json.dumps(MANIFEST, sort_keys=True).encode()).hexdigest()
)
ENCODING = tiktoken.get_encoding("o200k_base")


def spent(ledger: Path) -> float:
    return float(json.loads(ledger.read_text())["usd"]) if ledger.exists() else 0.0


def digests(out: Path) -> dict[str, str]:
    """sha256 of every request file: what is sent must be what is scored."""
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((out / "requests").glob("*.json"))
    }


def decide(reply: dict[str, Any], caps: list[Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The request's judgments by digest, or none unless complete and on time."""
    got, record = adapter.judgments(reply, caps)
    if record["status"] == 200 and reply["latency_s"] > DEADLINE:
        return {}, record | {"status": "late"}
    if record["status"] == 200 and len(got) != len(caps):
        return {}, record | {"status": "incomplete"}
    return got, record


def run(
    out: Path, arm: str, allowed: set[str], ledger: Path, url: str, key: str, sent: Path | None
) -> dict[str, Any]:
    reqs = adapter.requests(out)
    missing = sorted({r["corpus"] for r in reqs} - allowed)
    assert not missing, f"not authorized to send source of: {missing}"
    if sent is not None:  # a repeat arm resends exactly the primary's request files
        assert digests(out) == json.loads(sent.read_text())["requests_sha256"], "requests changed"
    bodies = []
    for r in reqs:
        caps = adapter.capsules(r)
        body = adapter.body(caps, RENDERER, True)
        bodies.append((r, caps, body, len(ENCODING.encode(json.dumps(body))) * USD))
    total = sum(b[3] for b in bodies)
    assert spent(ledger) + total <= CAP_USD, ("arm estimate over cap", spent(ledger), total)
    raw = out / "raw" / arm
    raw.mkdir(parents=True, exist_ok=True)
    per_task: dict[str, dict[str, Any]] = {}
    records: dict[str, dict[str, Any]] = {}
    for r, caps, body, estimate in bodies:
        per_task[r["task"]] = {}
        if spent(ledger) + estimate > CAP_USD:
            records[r["task"]] = {"status": "not sent: cost cap", "latency_s": 0.0}
            continue
        reply = adapter.call(url, body, key, DEADLINE)
        (raw / f"{r['task']}.json").write_text(json.dumps(reply) + "\n")
        per_task[r["task"]], records[r["task"]] = decide(reply, caps)
        billed = records[r["task"]].get("input_tokens")
        records[r["task"]]["usd"] = billed * USD if billed else estimate
        ledger.write_text(json.dumps({"usd": spent(ledger) + records[r["task"]]["usd"]}) + "\n")
        print(r["task"], records[r["task"]]["status"], f"{reply['latency_s']:.2f}s", flush=True)
    flat: dict[str, Any] = {}
    for got in per_task.values():
        assert not flat.keys() & got.keys(), "a capsule digest in two requests"
        flat |= got
    result = {
        "manifest": MANIFEST,
        "manifest_digest": MANIFEST_DIGEST,
        "renderer": render.version(RENDERER, True),
        "order": "digest",
        "estimate_usd": total,
        "requests_sha256": digests(out),
        "requests": records,
        "per_task": per_task,
        "judgments": flat,
    }
    (out / "judgments").mkdir(exist_ok=True)
    (out / "judgments" / f"{arm}.json").write_text(json.dumps(result, indent=1) + "\n")
    print(f"{arm}: estimate ${total:.4f}; ledger ${spent(ledger):.4f} of ${CAP_USD}")
    return result


SOURCE = b"def a():\n    return 1\ndef b():\n    return 2\ndef c():\n    return 3\n"


def _candidates() -> list[Any]:
    from oxide.core.packing import Candidate
    from oxide.models import Entity, SourceRef

    out = []
    for n, name in enumerate("abc"):
        start = SOURCE.index(f"def {name}".encode())
        end = SOURCE.index(b"\n", SOURCE.index(b"return", start)) + 1
        ref = SourceRef("m.py", start, end, "sha256:x")
        entity = Entity(f"symbol:m.py#{name}", "symbol", "function", name, None, ref, False)
        out.append(Candidate(entity, 0, n + 1, False, score=3.0 - n, origins=("entry",)))
    return out


def selfcheck() -> None:
    """Localhost only (no source leaves the machine)."""
    late_sent = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            sent = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            path = self.path.strip("/")
            if path == "hang":
                time.sleep(10)
                return
            if path == "busy":
                self.send_response(529)
                self.end_headers()
                self.wfile.write(b'{"detail":{"error_type":"system_overloaded"}}')
                return
            questions = list(sent["questions"])[: -1 if path == "partial" else None]
            data = {
                "model": "jev-latest" if path == "wrong" else MANIFEST["model"],
                "answers": {q: {"type": "noul", "noul": 0.9} for q in questions},
                "usage": {"input_tokens": 1000},
            }
            if path == "late":
                time.sleep(DEADLINE + 0.5)
            try:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())
            except OSError:
                pass  # the client was killed: the late case
            if path == "late":
                late_sent.set()

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/"
    cands = _candidates()
    caps = sorted(
        (b for c in cands if (b := capsule.build("task", "d", c, lambda r: SOURCE))),
        key=lambda c: c.digest,
    )
    body = adapter.body(caps, RENDERER, True)

    # 1. The adapter: only a complete, valid, on-time answer judges.
    outcomes = {}
    for path in ("ok", "late", "hang", "busy", "wrong", "partial"):
        reply = adapter.call(base + path, body, "test", DEADLINE)
        outcomes[path] = (reply, *decide(reply, caps))
    reply, got, rec = outcomes["ok"]
    assert rec["status"] == 200 and len(got) == 3 and reply["latency_s"] < DEADLINE, rec
    expected = {"late": "killed", "hang": "killed", "busy": 529, "wrong": "wrong model"}
    for path, status in {**expected, "partial": "incomplete"}.items():
        reply, got, rec = outcomes[path]
        assert rec["status"] == status and got == {}, (path, rec)
        if status == "killed":
            assert DEADLINE <= reply["latency_s"] < DEADLINE + 0.25, (path, reply)
            assert "body" not in reply, reply
    assert late_sent.wait(5), "the late server never answered"
    assert outcomes["late"][1] == {}, "a late answer reached the decision"

    # 2. v2's hybrid, unchanged, on the recorded outcomes: on time orders by JEV;
    #    late keeps production's heuristic for the whole request.
    import harness as v2

    admitted = {c.key: c.digest for c in caps}
    on_time = v2.hybrid(outcomes["ok"][1], admitted)(caps)
    assert len(on_time) == 3 and all(abs(v - (0.4 + 0.59 * 0.9)) < 1e-12 for v in on_time.values())
    assert v2.hybrid(outcomes["late"][1], admitted)(caps) == {}

    # 3. Production's seam with a live adapter against the late server: the decision is
    #    the heuristic, made by the deadline, and the answer sent afterwards changes nothing.
    from oxide.core import packing

    def live(sent: Any) -> dict[str, Any]:
        reply = adapter.call(base + "late", adapter.body(list(sent), RENDERER, True), "t", DEADLINE)
        return decide(reply, list(sent))[0]

    judge = capsule.CapsuleJudge("jev", "task", "d", lambda r: SOURCE, live, DEADLINE)
    late_sent.clear()
    start = time.monotonic()
    values = packing.judged(judge, cands)
    decided = time.monotonic() - start
    frozen = dict(values)
    heuristic = {c.entity.key: packing.value(c)[0] for c in cands}
    assert {k: v[0] for k, v in values.items()} == heuristic, values
    assert all("judged by" not in v[1] for v in values.values()), values
    assert decided < DEADLINE + 0.25, decided
    assert late_sent.wait(5) and values == frozen, "the late answer changed the decision"

    # 4. Spending: an arm over the cap is refused unsent; a request whose estimate would
    #    cross the cap is not sent; billed tokens are charged, the estimate if unreported.
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"ds3-selfcheck-{os.getpid()}"
    (tmp / "requests").mkdir(parents=True)
    for t in ("a", "b"):
        req = {"task": t, "corpus": "local", "query": "task"}
        req["capsules"] = [c.canonical.decode() for c in caps]
        (tmp / "requests" / f"{t}.json").write_text(json.dumps(req) + "\n")
    est = len(ENCODING.encode(json.dumps(body))) * USD
    assert est < 1000 * USD, "the fake bill must exceed the estimate"
    ledger = tmp / "ledger.json"
    ledger.write_text(json.dumps({"usd": CAP_USD - 1.5 * est}) + "\n")
    try:
        run(tmp, "over", {"local"}, ledger, base + "ok", "test", None)
        raise AssertionError("an arm over the cap was sent")
    except AssertionError as error:
        assert "over cap" in str(error.args[0]), error
    assert not (tmp / "raw" / "over").exists()
    ledger.write_text(json.dumps({"usd": CAP_USD - 2 * est - 1e-9}) + "\n")
    result = run(tmp, "edge", {"local"}, ledger, base + "ok", "test", None)
    assert result["requests"]["b"]["status"] == "not sent: cost cap", result["requests"]
    assert spent(ledger) <= CAP_USD - 2 * est + 1000 * USD + 1e-12
    ledger.write_text(json.dumps({"usd": 0.0}) + "\n")
    run(tmp, "late", {"local"}, ledger, base + "late", "test", None)
    assert abs(spent(ledger) - 2 * est) < 1e-12, "a killed request is charged its estimate"
    shutil.rmtree(tmp)
    server.shutdown()
    lat = {k: round(v[0]["latency_s"], 3) for k, v in outcomes.items()}
    print(
        f"selfcheck ok: deadline {DEADLINE} s enforced (late answer killed, never read, "
        f"request falls back; seam decided in {decided:.2f} s and unchanged after the late "
        f"answer); latencies {lat}; cost cap enforced; manifest {MANIFEST_DIGEST}"
    )


def estimate(out: Path) -> None:
    """Sends nothing: the corpora a run would send and its tiktoken cost estimate."""
    reqs = adapter.requests(out)
    tokens = [
        len(ENCODING.encode(json.dumps(adapter.body(adapter.capsules(r), RENDERER, True))))
        for r in reqs
    ]
    print(
        f"{len(reqs)} requests; tokens/request median {sorted(tokens)[len(tokens) // 2]}, "
        f"max {max(tokens)}; estimate ${sum(tokens) * USD:.4f} per arm, cap ${CAP_USD}"
    )
    print("corpora:", ",".join(sorted({r["corpus"] for r in reqs})))


def main(argv: list[str]) -> None:
    if argv[0] == "selfcheck":
        selfcheck()
    elif argv[0] == "estimate":
        estimate(Path(argv[1]))
    elif argv[0] == "run":
        allowed = set(argv[argv.index("--authorize") + 1].split(","))
        ledger = Path(argv[argv.index("--ledger") + 1])
        sent = Path(argv[argv.index("--sent") + 1]) if "--sent" in argv else None
        key = os.environ[adapter.KEY_ENV]
        run(Path(argv[1]), argv[2], allowed, ledger, adapter.MANIFEST["endpoint"], key, sent)


if __name__ == "__main__":
    main(sys.argv[1:])
