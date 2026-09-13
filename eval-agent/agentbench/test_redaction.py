#!/usr/bin/env python3
"""Self-check gating judge.py: assert no case-insensitive "oxide" substring
survives redaction across every stored final answer. `judge.py` must never
run on unredacted text -- this is what proves it isn't."""
import json
import re
from pathlib import Path

from judge import redact

HERE = Path(__file__).parent
RESULTS_PATH = HERE / "results" / "results.jsonl"


def demo() -> None:
    assert redact("I used the Oxide search tool") == "I used the [tool] search tool"
    assert redact("no mentions here") == "no mentions here"
    assert redact("OXIDE_QUERY and oxide_search") == "[tool]_QUERY and [tool]_search"
    assert redact("") == ""
    assert redact(None) == ""

    if not RESULTS_PATH.exists():
        print("no results.jsonl yet -- unit assertions only, skipping corpus scan")
        return

    leaks = []
    n = 0
    for line in RESULTS_PATH.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        answer = rec.get("final_answer")
        if answer is None:
            continue
        n += 1
        redacted = redact(answer)
        if re.search(r"oxide", redacted, re.IGNORECASE):
            leaks.append((rec.get("task"), rec.get("condition"), rec.get("rep")))
    assert not leaks, f"redaction leaked \"oxide\" in {len(leaks)}/{n} stored answers: {leaks}"
    print(f"redaction self-check passed over {n} stored answers, 0 leaks")


if __name__ == "__main__":
    demo()
