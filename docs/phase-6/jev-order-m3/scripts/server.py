"""`oxide mcp` with a request ledger and a hard request cap (ADR-0009 M3).

    python server.py ENDPOINT LEDGER CAP -- <oxide CLI arguments>

ENDPOINT is ``live`` (the pinned TypeSafe endpoint) or a localhost URL. Each judge
request appends one JSON line to LEDGER: its size, candidate count, status, latency and
billed input tokens. No source, key or reply text is written. Request CAP+1 and later
are not sent and fall back as ``transport``. That is the spend bound.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from oxide import jev
from oxide.cli import main

endpoint, ledger, cap, sep, *argv = sys.argv[1:]
assert sep == "--", sys.argv
if endpoint != "live":
    assert endpoint.startswith("http://127.0.0.1:"), endpoint
    jev.ENDPOINT = endpoint
sent = jev.call


def recorded(url: str, payload: dict[str, Any], key: str, deadline: float) -> dict[str, Any]:
    path = Path(ledger)
    count = len(path.read_text().splitlines()) if path.exists() else 0
    row: dict[str, Any] = {
        "request_bytes": len(json.dumps(payload).encode()),
        "candidates": len(payload["questions"]),
    }
    if count >= int(cap):
        reply: dict[str, Any] = {"status": "error", "latency_s": 0.0}
        row |= {"status": "capped"}
    else:
        reply = sent(url, payload, key, deadline)
        row |= {"status": reply["status"], "latency_s": round(reply["latency_s"], 3)}
        try:
            row["billed_input_tokens"] = int(json.loads(reply["body"])["usage"]["input_tokens"])
        except (KeyError, TypeError, ValueError):
            pass
    with path.open("a") as out:
        out.write(json.dumps(row) + "\n")
    return reply


jev.call = recorded
sys.exit(main(argv))
