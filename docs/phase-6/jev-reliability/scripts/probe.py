"""Fresh JEV calls for the operational reliability study (preregistration.md § Protocol).

    uv run python docs/phase-6/jev-reliability/scripts/probe.py selfcheck   # localhost only
    uv run python docs/phase-6/jev-reliability/scripts/probe.py window OUT W \
        --dev V2_DEV_DIR --ledger LEDGER.json                               # TYPESAFE_API_KEY

One window sends ``BLOCKS`` (concurrency, logical requests) in order. Each logical request
is Decision Study v2's primary body (``compact``, rank, digest order) for one of the 15
dev tasks (V2_DEV_DIR/requests), cycled in a per-window shuffled order. A retryable first
attempt (429, 5xx, killed, transport error) is retried once, immediately, whatever time
remains: the 2.0 s deadline policies are applied afterwards by ``analyze.py`` over the
recorded times. Every attempt is appended to OUT/attempts.jsonl with its status, client
wall time, in-child network time and overload-relevant response headers. Raw bodies are
not kept. Sending stops at ``CAP_USD`` of billed spend.
"""

from __future__ import annotations

import http.server
import json
import os
import random
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "decision-study-v1" / "scripts"))

import adapter

CAP_USD = 0.40  # the whole study (user authorization, 2026-10-24)
BLOCKS = ((1, 17), (2, 17), (4, 17))
KILL_S = 60.0  # observe true tails; the 2.0 s deadline is applied in analysis
HEADERS = ("retry-after", "x-request-id", "request-id", "x-ratelimit-remaining", "date")
USD = adapter.MANIFEST["usd_per_million_input_tokens"] / 1e6
DEV_CORPORA = {  # the v2 dev corpora the user authorized; never held-out
    "fixture:py_repo",
    "mwaskom__seaborn@54cab15bdacf",
    "pallets__flask@7ee9ceb71e86",
    "psf__requests@0192aac24123",
    "psf__requests@091991be0da1",
    "psf__requests@22623bd8c265",
    "psf__requests@39d0fdd9096f",
    "psf__requests@5f7a3a74aab1",
    "psf__requests@847735553aed",
}

# adapter.CHILD plus response headers and the time spent inside urlopen/read.
CHILD = """
import json, sys, time, urllib.error, urllib.request
job = json.load(sys.stdin)
req = urllib.request.Request(job["url"], data=json.dumps(job["body"]).encode(), method="POST",
    headers={"Content-Type": "application/json", "Authorization": "Bearer " + job["key"]})
t = time.monotonic()
try:
    with urllib.request.urlopen(req, timeout=job["deadline"]) as r:
        out = {"status": r.status, "body": r.read().decode(), "headers": dict(r.headers)}
except urllib.error.HTTPError as e:
    out = {"status": e.code, "body": e.read().decode(errors="replace"), "headers": dict(e.headers)}
out["net_s"] = time.monotonic() - t
print(json.dumps(out))
"""


def call(url: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
    job = json.dumps({"url": url, "body": payload, "key": key, "deadline": KILL_S})
    start = time.monotonic()
    try:
        done = subprocess.run(
            [sys.executable, "-I", "-c", CHILD],
            input=job,
            capture_output=True,
            text=True,
            timeout=KILL_S,
            env={"PATH": os.environ.get("PATH", "")},
        )
    except subprocess.TimeoutExpired:
        return {"status": "killed", "latency_s": time.monotonic() - start}
    latency = time.monotonic() - start
    if done.returncode != 0 or not done.stdout.strip():
        return {"status": "error", "latency_s": latency, "detail": done.stderr[-500:]}
    reply = json.loads(done.stdout)
    headers = {k.lower(): v for k, v in reply["headers"].items()}
    return {
        "status": reply["status"],
        "latency_s": latency,
        "net_s": reply["net_s"],
        "headers": {h: headers[h] for h in HEADERS if h in headers},
        "body": reply["body"],
    }


def retryable(status: Any) -> bool:
    if isinstance(status, int):
        return status == 429 or status >= 500
    return status in ("killed", "error")


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path, self.lock = path, threading.Lock()
        self.usd = json.loads(path.read_text())["usd"] if path.exists() else 0.0

    def open(self) -> bool:
        with self.lock:
            return self.usd < CAP_USD

    def bill(self, tokens: int) -> None:
        with self.lock:
            self.usd += tokens * USD
            self.path.write_text(json.dumps({"usd": self.usd}) + "\n")


class Probe:
    def __init__(self, out: Path, url: str, key: str, ledger: Ledger) -> None:
        self.out, self.url, self.key, self.ledger = out, url, key, ledger
        self.lock = threading.Lock()
        self.epoch = time.monotonic()

    def attempt(self, meta: dict[str, Any], n: int, body: dict[str, Any], caps: Any) -> Any:
        if not self.ledger.open():
            self.write(meta | {"attempt": n, "status": "not sent: cost cap"})
            return "not sent: cost cap"
        started = time.monotonic() - self.epoch
        wall = time.time()
        reply = call(self.url, body, self.key)
        _, record = adapter.judgments(reply, caps)
        self.ledger.bill(record.get("input_tokens") or 0)
        self.write(
            meta
            | {
                "attempt": n,
                "start_s": started,
                "wall_utc": wall,
                "status": record["status"],
                "latency_s": reply["latency_s"],
                "net_s": reply.get("net_s"),
                "headers": reply.get("headers", {}),
                "input_tokens": record.get("input_tokens"),
                "detail": None if record["status"] == 200 else record.get("detail"),
            }
        )
        return record["status"]

    def logical(self, meta: dict[str, Any], body: dict[str, Any], caps: Any) -> None:
        if retryable(self.attempt(meta, 1, body, caps)):
            self.attempt(meta, 2, body, caps)

    def write(self, row: dict[str, Any]) -> None:
        with self.lock, (self.out / "attempts.jsonl").open("a") as f:
            f.write(json.dumps(row) + "\n")


def window(out: Path, w: int, dev: Path, ledger: Path, url: str, key: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    bodies = []
    for r in adapter.requests(dev):
        assert r["corpus"] in DEV_CORPORA, f"not authorized: {r['corpus']}"
        caps = adapter.capsules(r)
        bodies.append((r["task"], caps, adapter.body(caps, "compact", True)))
    assert len(bodies) == 15, len(bodies)
    order = random.Random(f"jev-reliability-{w}").sample(bodies, len(bodies))
    probe, i = Probe(out, url, key, Ledger(ledger)), 0
    for conc, n in BLOCKS:
        jobs = []
        for _ in range(n):
            task, caps, body = order[i % len(order)]
            jobs.append(
                ({"window": w, "concurrency": conc, "logical": i, "task": task}, body, caps)
            )
            i += 1
        with ThreadPoolExecutor(conc) as pool:
            list(pool.map(lambda j: probe.logical(*j), jobs))
        print(f"window {w} c={conc} done; ledger ${probe.ledger.usd:.4f}", flush=True)


def selfcheck() -> None:
    """Localhost: a 529 is retried once, a 200 is not; headers, times and spend are kept."""
    seen: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            sent = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(self.path)
            if self.path == "/busy" and seen.count("/busy") == 1:
                self.send_response(529)
                self.send_header("Retry-After", "1")
                self.end_headers()
                self.wfile.write(b'{"detail":{"error_type":"system_overloaded"}}')
                return
            answers = {q: {"type": "noul", "noul": 0.5} for q in sent["questions"]}
            data = {
                "model": adapter.MANIFEST["model"],
                "answers": answers,
                "usage": {"input_tokens": 1000},
            }
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"jev-reliability-selfcheck-{os.getpid()}"
    tmp.mkdir(parents=True)
    body = {"model": adapter.MANIFEST["model"], "state": {}, "questions": {}}
    ledger = Ledger(tmp / "ledger.json")
    for path in ("/busy", "/ok"):
        meta = {"window": 0, "concurrency": 1, "logical": 0, "task": path}
        Probe(tmp, base + path, "test", ledger).logical(meta, body, [])
    rows = [json.loads(line) for line in (tmp / "attempts.jsonl").read_text().splitlines()]
    assert [(r["task"], r["attempt"], r["status"]) for r in rows] == [
        ("/busy", 1, 529),
        ("/busy", 2, 200),
        ("/ok", 1, 200),
    ], rows
    assert rows[0]["headers"]["retry-after"] == "1" and rows[0]["detail"], rows[0]
    assert all(0 < r["net_s"] <= r["latency_s"] for r in rows), rows
    assert abs(json.loads((tmp / "ledger.json").read_text())["usd"] - 2000 * USD) < 1e-12
    assert retryable(529) and retryable(429) and retryable("killed") and not retryable(400)
    overhead = sorted(r["latency_s"] - r["net_s"] for r in rows)
    for f in tmp.iterdir():
        f.unlink()
    tmp.rmdir()
    server.shutdown()
    print(f"selfcheck ok; client spawn overhead {overhead[0]:.3f}-{overhead[-1]:.3f} s/attempt")


def main(argv: list[str]) -> None:
    if argv[0] == "selfcheck":
        selfcheck()
    elif argv[0] == "window":
        dev = Path(argv[argv.index("--dev") + 1])
        ledger = Path(argv[argv.index("--ledger") + 1])
        key = os.environ[adapter.KEY_ENV]
        window(Path(argv[1]), int(argv[2]), dev, ledger, adapter.MANIFEST["endpoint"], key)


if __name__ == "__main__":
    main(sys.argv[1:])
