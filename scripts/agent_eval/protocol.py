"""Protocol guards for the paired agent evaluation: what a results file is bound to, how much
it may spend, and how often a held-out task set may be opened.

Pure functions only, so the rules are tested without running an agent.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

PROTOCOL = "agent-swe-v2"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def spend(tokens: dict[str, int]) -> int:
    """Tokens billed for one run, unweighted: every input, output and cache token. Prices vary
    by provider and cache tier, so the cap is in tokens; USD is the token counts times a price
    the operator applies to the recorded counts."""
    return sum(tokens.get(k, 0) for k in ("input", "output", "cache_read", "cache_write"))


def row_has_spend(row: dict[str, Any]) -> bool:
    return "spend" in row or isinstance(row.get("tokens"), dict)


def row_spend(row: dict[str, Any]) -> int:
    """A recorded run's spend: the stored figure, or its tokens for rows written before it."""
    if "spend" in row:
        return int(row["spend"])
    return spend(row["tokens"]) if isinstance(row.get("tokens"), dict) else 0


def spent(rows: list[dict[str, Any]]) -> int:
    return sum(row_spend(r) for r in rows)


def admit(spent_so_far: int, in_flight: int, observed_max: int | None, cap: int | None) -> bool:
    """Whether one more run may start under the token cap.

    Capped runs are serialized: an estimate cannot bound multiple simultaneous unknown costs.
    One run calibrates the estimate; a later run starts only if its estimated cost fits.
    Even serialized admission cannot enforce a hard limit on an individual run."""
    if cap is None:
        return True
    if in_flight:
        return False
    if observed_max is None:
        return spent_so_far < cap
    return spent_so_far + observed_max <= cap


def check_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Write the manifest beside a new results file, or refuse to resume under a different one.

    A results file is only comparable to runs made under the same tasks, code, prompt, arm
    configuration and agent binary. Resuming under a changed protocol mixes two experiments."""
    if not path.exists():
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return
    recorded = json.loads(path.read_text(encoding="utf-8"))
    diff = sorted(k for k in set(recorded) | set(manifest) if recorded.get(k) != manifest.get(k))
    if diff:
        raise SystemExit(
            f"{path} was recorded under a different protocol ({', '.join(diff)} changed); "
            "start a new results file instead of resuming"
        )


def heldout_open(log: Path, tasks_sha: str, out: Path) -> None:
    """A held-out task set is opened once. Resuming the same results file is allowed; a second
    file on the same set is a second look, which the protocol forbids."""
    entries = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    for entry in entries:
        if entry["tasks_sha256"] == tasks_sha and entry["out"] != str(out):
            raise SystemExit(
                f"held-out tasks {tasks_sha[:12]} were already opened by {entry['out']}; "
                "a held-out set is one look, so write a new held-out set instead"
            )
    if not any(e["tasks_sha256"] == tasks_sha and e["out"] == str(out) for e in entries):
        with log.open("a", encoding="utf-8") as file:
            file.write(json.dumps({"tasks_sha256": tasks_sha, "out": str(out)}) + "\n")
