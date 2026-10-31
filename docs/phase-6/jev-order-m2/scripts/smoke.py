"""ADR-0009 M2 smoke check of the in-tree JEV path, end to end through the public API.

    uv run python docs/phase-6/jev-order-m2/scripts/smoke.py offline OUT.json
    TYPESAFE_API_KEY=... uv run python docs/phase-6/jev-order-m2/scripts/smoke.py live OUT.json

Both modes copy the public fixture ``fixtures/py_repo`` into a private directory with a
private data location, index it, authorize it with ``Repository.judge(True)`` and time
``build_context`` with the judge disabled and enabled. ``offline`` points the adapter at
a localhost server that answers at once, so it measures OXIDE's own overhead: capsules,
rendering, the child process and a local round trip. ``live`` sends the fixture's source
to TypeSafe and needs the maintainer's authorization of spend and corpus; it also checks
two live fallbacks (an invalid key, a deadline too short to answer in) and reads billed
tokens from the replies. A compatibility and latency check, not a quality study: nothing
is scored.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import oxide
from oxide import jev

REPO = Path(__file__).resolve().parents[4]
FIXTURE = REPO / "fixtures" / "py_repo"
QUERIES = (
    "where failed HTTP requests are retried",
    "how is the access token refreshed when it expires",
    "where are cached values evicted",
    "send a notification when a request fails",
    "how is the retry backoff delay computed",
)
REPEATS = 2
BUDGET = 1024
MAX_REQUESTS = 14  # 10 judged + 2 invalid key + 2 deadline: the spend bound


def serve_instantly() -> str:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            sent = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            answers = {q: {"type": "noul", "noul": 0.5} for q in sent["questions"]}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"model": jev.MODEL, "answers": answers}).encode())

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}/"


def spans(bundle: oxide.ContextBundle) -> list[tuple[oxide.SourceRef, int]]:
    return [(i.source, i.units) for i in bundle.items]


def summary(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "p50": round(statistics.median(ordered), 3),
        "p95": round(ordered[round(0.95 * (len(ordered) - 1))], 3),
        "max": round(ordered[-1], 3),
    }


def main(mode: str, out: Path) -> None:
    assert mode in ("offline", "live"), mode
    replies: list[dict[str, Any]] = []
    sent = jev.call

    def recorded(url: str, payload: dict[str, Any], key: str, deadline: float) -> dict[str, Any]:
        assert len(replies) < MAX_REQUESTS, "spend bound reached"
        reply = sent(url, payload, key, deadline)
        replies.append({"request_bytes": len(json.dumps(payload).encode()), **reply})
        return reply

    jev.call = recorded
    if mode == "offline":
        jev.ENDPOINT = serve_instantly()
        os.environ[jev.KEY_ENV] = "offline"
    elif not os.environ.get(jev.KEY_ENV):
        sys.exit(f"live mode needs {jev.KEY_ENV}")
    key = os.environ[jev.KEY_ENV]
    work = Path(tempfile.mkdtemp(prefix="oxide-m2-"))
    checks: dict[str, list[dict[str, Any]]] = {}
    try:
        root = Path(shutil.copytree(FIXTURE, work / "py_repo"))
        with oxide.open_repository(root, data_dir=work / "data") as repo:
            repo.index()
            plain = {q: repo.build_context(q, BUDGET) for q in QUERIES}
            heuristic, judged, outcomes = [], [], []
            for _ in range(REPEATS):
                for q in QUERIES:
                    repo.judge(False)
                    start = time.perf_counter()
                    repo.build_context(q, BUDGET)
                    heuristic.append(time.perf_counter() - start)
                    repo.judge(True)
                    start = time.perf_counter()
                    bundle = repo.build_context(q, BUDGET)
                    judged.append(time.perf_counter() - start)
                    fell = [d for d in bundle.degradations if d.startswith("judge ")]
                    outcome = fell[0].rsplit(": ", 1)[1] if fell else "judged"
                    assert (outcome == "judged") == ("judge" in bundle.versions), bundle.versions
                    if outcome != "judged":  # a fallback packs production exactly
                        assert spans(bundle) == spans(plain[q]), q
                    outcomes.append(outcome)
            if mode == "live":
                os.environ[jev.KEY_ENV] = "oxide-m2-invalid-key"
                for name, deadline in (("invalid_key", None), ("deadline_0.3s", 0.3)):
                    for q in QUERIES[:2]:
                        start = time.perf_counter()
                        if deadline is None:
                            bundle = repo.build_context(q, BUDGET)
                        else:
                            short = jev._order(jev.Jev(key, jev.ENDPOINT, deadline))
                            bundle = repo._context(q, BUDGET, (), (), None, (), short)
                        took = time.perf_counter() - start
                        assert spans(bundle) == spans(plain[q]), (name, q)
                        time.sleep(0.1)  # the killed child's waiter thread ends
                        left = sum(t.name == "oxide-judge" for t in threading.enumerate())
                        checks.setdefault(name, []).append(
                            {
                                "degradation": bundle.degradations[-1],
                                "seconds": round(took, 3),
                                "judge_threads_left": left,
                            }
                        )
                os.environ[jev.KEY_ENV] = key
            repo.judge(False)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    tokens = []
    for reply in replies:
        try:
            tokens.append(int(json.loads(reply["body"])["usage"]["input_tokens"]))
        except (TypeError, ValueError, KeyError):
            pass
    result = {
        "mode": mode,
        "fixture": "fixtures/py_repo",
        "budget": BUDGET,
        "manifest": jev.MANIFEST_DIGEST,
        "outcomes": {o: outcomes.count(o) for o in sorted(set(outcomes))},
        "build_context_s": {"heuristic": summary(heuristic), "judged": summary(judged)},
        "adapter_call_s": summary([r["latency_s"] for r in replies[: len(judged)]]),
        "request_bytes": summary([float(r["request_bytes"]) for r in replies]),
        "statuses": [r["status"] for r in replies],
        "requests_sent": len(replies),
        "billed_input_tokens": tokens,
        "usd": round(sum(tokens) * jev.MANIFEST["usd_per_million_input_tokens"] / 1e6, 6),
        "fallback_checks": checks,
    }
    out.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
