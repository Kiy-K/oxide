"""The JEV judge adapter for Decision Study v1 (preregistration.md § Model configuration).

    uv run python adapter.py selfcheck                      # localhost only, no source sent
    uv run python adapter.py dry-run OUT RENDERER [--norank] [--reversed]
    uv run python adapter.py run OUT RENDERER ARM --authorize CORPUS[,CORPUS…] \
        [--norank] [--reversed]                             # needs TYPESAFE_API_KEY

``run`` sends OUT/requests/*.json to TypeSafe JEV, one call per request, and writes
OUT/judgments/ARM.json (judgments by capsule digest, for ``study.py ARM=…``) and the raw
responses to OUT/raw/ARM/. It refuses unless every request's corpus is named in
``--authorize``: the capsules carry repository source. ``dry-run`` writes the exact bodies
it would send to OUT/dryrun/ and sends nothing.

The network call runs in a child process that is killed at the deadline, so a stalled
connection cannot outlive it (a thread timeout would only stop waiting).
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import render

from oxide.core.capsule import Capsule

MANIFEST: dict[str, Any] = {
    "provider": "typesafe",
    "endpoint": "https://api.typesafe.ai/v1/systemone",
    "model": "jev-1.14.0",
    "long_context": False,
    "deadline_s": 60.0,
    "retries": 0,
    "questions": render.QUESTIONS_DIGEST,
    "usd_per_million_input_tokens": 0.042,
}
MANIFEST_DIGEST = (
    "sha256:" + hashlib.sha256(json.dumps(MANIFEST, sort_keys=True).encode()).hexdigest()
)
KEY_ENV = "TYPESAFE_API_KEY"

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


def capsules(request: dict[str, Any]) -> list[Capsule]:
    """Rebuild the request's capsules; their canonical form must round-trip exactly."""
    out = []
    for text in request["capsules"]:
        data = {k: tuple(v) if isinstance(v, list) else v for k, v in json.loads(text).items()}
        c = Capsule(**data)
        assert c.canonical.decode() == text, "capsule does not round-trip"
        out.append(c)
    assert [c.digest for c in out] == sorted(c.digest for c in out), "not in digest order"
    return out


def body(caps: list[Capsule], name: str, rank: bool) -> dict[str, Any]:
    return {
        "model": MANIFEST["model"],
        "state": render.render(name, caps, rank),
        "questions": render.questions(len(caps)),
    }


def call(url: str, payload: dict[str, Any], key: str, deadline: float) -> dict[str, Any]:
    """One request in a child process, killed at ``deadline``: status, latency, body."""
    job = json.dumps({"url": url, "body": payload, "key": key, "deadline": deadline})
    start = time.monotonic()
    try:
        done = subprocess.run(
            [sys.executable, "-I", "-c", CHILD],
            input=job,
            capture_output=True,
            text=True,
            timeout=deadline,  # kills the child on expiry
            env={"PATH": os.environ.get("PATH", "")},
        )
    except subprocess.TimeoutExpired:
        return {"status": "killed", "latency_s": time.monotonic() - start}
    latency = time.monotonic() - start
    if done.returncode != 0 or not done.stdout.strip():
        return {"status": "error", "latency_s": latency, "detail": done.stderr[-500:]}
    reply = json.loads(done.stdout)
    return {"status": reply["status"], "latency_s": latency, "body": reply["body"]}


def judgments(reply: dict[str, Any], caps: list[Capsule]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Judgments by digest and the request record; a malformed reply judges nothing."""
    record = {k: reply[k] for k in ("status", "latency_s")}
    if reply["status"] != 200:
        record["detail"] = str(reply.get("body", reply.get("detail", "")))[:500]
        return {}, record
    try:
        data = json.loads(reply["body"])
        record |= {"model": data["model"], **data.get("usage", {})}
        if data["model"] != MANIFEST["model"]:
            return {}, record | {"status": "wrong model"}
        found = {}
        for i, c in enumerate(caps, 1):
            answer = data["answers"].get(f"c{i}")
            if answer is not None:
                found[c.digest] = answer["noul"]
        return found, record
    except (ValueError, KeyError, TypeError) as error:
        return {}, record | {"status": f"unparsed ({type(error).__name__})"}


def requests(out: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted((out / "requests").glob("*.json"))]


def ordered(caps: list[Capsule], reverse: bool) -> list[Capsule]:
    return caps[::-1] if reverse else caps


def run(out: Path, name: str, arm: str, authorized: set[str], rank: bool, reverse: bool) -> None:
    reqs = requests(out)
    missing = sorted({r["corpus"] for r in reqs} - authorized)
    assert not missing, f"not authorized to send source of: {missing}"
    key = os.environ[KEY_ENV]
    raw = out / "raw" / arm
    raw.mkdir(parents=True, exist_ok=True)
    found: dict[str, Any] = {}
    records = {}
    for r in reqs:
        caps = ordered(capsules(r), reverse)
        reply = call(MANIFEST["endpoint"], body(caps, name, rank), key, MANIFEST["deadline_s"])
        (raw / f"{r['task']}.json").write_text(json.dumps(reply) + "\n")
        got, records[r["task"]] = judgments(reply, caps)
        found |= got
        print(r["task"], records[r["task"]]["status"], f"{reply['latency_s']:.2f}s", flush=True)
    (out / "judgments").mkdir(exist_ok=True)
    result = {
        "manifest": MANIFEST,
        "manifest_digest": MANIFEST_DIGEST,
        "renderer": render.version(name, rank),
        "order": "reversed" if reverse else "digest",
        "requests": records,
        "judgments": found,
    }
    (out / "judgments" / f"{arm}.json").write_text(json.dumps(result, indent=1) + "\n")


def dry_run(out: Path, name: str, rank: bool, reverse: bool) -> None:
    target = out / "dryrun" / render.version(name, rank)
    target.mkdir(parents=True, exist_ok=True)
    reqs = requests(out)
    for r in reqs:
        caps = ordered(capsules(r), reverse)
        (target / f"{r['task']}.json").write_text(json.dumps(body(caps, name, rank)) + "\n")
    print(f"wrote {target} (nothing sent); corpora: {sorted({r['corpus'] for r in reqs})}")


def selfcheck() -> None:
    """Localhost only: a hung server is killed at the deadline; a valid reply parses."""
    release = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            sent = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/hang":
                release.wait(30)
                return
            model = MANIFEST["model"] if self.path == "/ok" else "jev-latest"
            answers = {q: {"type": "noul", "noul": 0.25} for q in sent["questions"]}
            data = json.dumps({"model": model, "answers": answers, "usage": {"input_tokens": 7}})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(data.encode())

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    payload = {"model": MANIFEST["model"], "state": {}, "questions": render.questions(2)}
    hung = call(base + "/hang", payload, "test", 1.0)
    assert hung["status"] == "killed" and hung["latency_s"] < 1.5, hung
    ok = call(base + "/ok", payload, "test", 5.0)
    _, record = judgments(ok, [])
    assert ok["status"] == 200 and record["model"] == MANIFEST["model"], record
    assert record["input_tokens"] == 7, record
    _, wrong = judgments(call(base + "/wrong", payload, "test", 5.0), [])
    assert wrong["status"] == "wrong model", wrong
    release.set()
    server.shutdown()
    print(
        f"selfcheck ok: hung request killed after {hung['latency_s']:.2f}s; valid reply "
        f"parsed; wrong model rejected; manifest {MANIFEST_DIGEST}"
    )


def main(argv: list[str]) -> None:
    rank, reverse = "--norank" not in argv, "--reversed" in argv
    if argv[0] == "selfcheck":
        selfcheck()
    elif argv[0] == "dry-run":
        dry_run(Path(argv[1]), argv[2], rank, reverse)
    elif argv[0] == "run":
        allowed = set(argv[argv.index("--authorize") + 1].split(","))
        run(Path(argv[1]), argv[2], argv[3], allowed, rank, reverse)


if __name__ == "__main__":
    main(sys.argv[1:])
