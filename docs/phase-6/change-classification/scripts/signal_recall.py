"""signal_recall.py SIGNALS.json: Part D, gold-file recall@k per signal, macro over tasks,
overall and per repository (``loc_run.py ... signals`` output)."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

KS = (1, 3, 10, 20)


def recall(rows: list[dict], signal: str, k: int) -> float:
    per = [
        sum((r or 10**9) <= k for r in t["rank"][signal].values()) / len(t["gold"]) for t in rows
    ]
    return sum(per) / len(per)


def main() -> None:
    tasks = [t for t in json.loads(Path(sys.argv[1]).read_text()) if t["gold"]]
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in tasks:
        groups["ALL"].append(t)
        groups[t["corpus"].split("@")[0]].append(t)
    for name, rows in groups.items():
        print(f"[{name} n={len(rows)}]")
        for s in rows[0]["rank"]:
            print(f"  {s}", " ".join(f"@{k}={recall(rows, s, k):.3f}" for k in KS))


if __name__ == "__main__":
    main()
