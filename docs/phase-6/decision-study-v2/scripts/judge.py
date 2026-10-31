"""JEV calls for Decision Study v2 (preregistration.md § Model configuration and cost).

    uv run --with tiktoken python docs/phase-6/decision-study-v2/scripts/judge.py \
        OUT ARM --authorize CORPUS[,CORPUS…] --ledger LEDGER.json [--norank] [--reversed]

v1's adapter (``decision-study-v1/scripts/adapter.py``: pinned ``jev-1.14.0`` manifest,
``ds1-compact-v1`` renderer, child process killed at 60 s, no retries, model check) sent
over OUT/requests/*.json, one call per request, writing OUT/judgments/ARM.json and the raw
responses to OUT/raw/ARM/. Before sending anything it refuses unless every corpus is
authorized and the ledger's billed spend plus this arm's tiktoken estimate stays within
``CAP_USD``; it stops sending once the billed spend reaches the cap.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "decision-study-v1" / "scripts"))

import adapter
import render
import tiktoken

CAP_USD = 0.30  # the whole study, dev and held-out (preregistration.md)
RENDERER = "compact"
USD = adapter.MANIFEST["usd_per_million_input_tokens"] / 1e6


def spent(ledger: Path) -> float:
    return json.loads(ledger.read_text())["usd"] if ledger.exists() else 0.0


def main(argv: list[str]) -> None:
    out, arm = Path(argv[0]), argv[1]
    rank, reverse = "--norank" not in argv, "--reversed" in argv
    allowed = set(argv[argv.index("--authorize") + 1].split(","))
    ledger = Path(argv[argv.index("--ledger") + 1])
    reqs = adapter.requests(out)
    missing = sorted({r["corpus"] for r in reqs} - allowed)
    assert not missing, f"not authorized to send source of: {missing}"
    enc = tiktoken.get_encoding("o200k_base")
    bodies = []
    for r in reqs:
        caps = adapter.ordered(adapter.capsules(r), reverse)
        bodies.append((r, caps, adapter.body(caps, RENDERER, rank)))
    estimate = sum(len(enc.encode(json.dumps(b))) for *_, b in bodies) * USD
    assert spent(ledger) + estimate <= CAP_USD, (spent(ledger), estimate, CAP_USD)
    key = os.environ[adapter.KEY_ENV]
    raw = out / "raw" / arm
    raw.mkdir(parents=True, exist_ok=True)
    found: dict[str, Any] = {}
    records: dict[str, Any] = {}
    for r, caps, body in bodies:
        if spent(ledger) >= CAP_USD:
            records[r["task"]] = {"status": "not sent: cost cap", "latency_s": 0.0}
            continue
        reply = adapter.call(
            adapter.MANIFEST["endpoint"], body, key, adapter.MANIFEST["deadline_s"]
        )
        (raw / f"{r['task']}.json").write_text(json.dumps(reply) + "\n")
        got, records[r["task"]] = adapter.judgments(reply, caps)
        found |= got
        usd = spent(ledger) + (records[r["task"]].get("input_tokens") or 0) * USD
        ledger.write_text(json.dumps({"usd": usd}) + "\n")
        print(r["task"], records[r["task"]]["status"], f"{reply['latency_s']:.2f}s", flush=True)
    (out / "judgments").mkdir(exist_ok=True)
    result = {
        "manifest": adapter.MANIFEST,
        "manifest_digest": adapter.MANIFEST_DIGEST,
        "renderer": render.version(RENDERER, rank),
        "order": "reversed" if reverse else "digest",
        "estimate_usd": estimate,
        "requests": records,
        "judgments": found,
    }
    (out / "judgments" / f"{arm}.json").write_text(json.dumps(result, indent=1) + "\n")
    print(f"{arm}: estimate ${estimate:.4f}; ledger ${spent(ledger):.4f} of ${CAP_USD}")


if __name__ == "__main__":
    main(sys.argv[1:])
