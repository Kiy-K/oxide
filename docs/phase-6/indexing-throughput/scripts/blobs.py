"""Medians of a ``warm.sh`` A/B (README § 8, § 9): ``blobs.py BEFORE.jsonl AFTER.jsonl``.

Each line is one ``warm.sh`` record. Prints the cold and warm total, seal and publish per
corpus for both arms, and whether all runs of both arms produced one digest (equal output).
"""

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

STAGES = ("total", "seal", "publish", "rss_mb")
by: dict[str, list[list[dict[str, Any]]]] = defaultdict(lambda: [[], []])
for arm, path in enumerate(sys.argv[1:3]):
    for line in Path(path).read_text().splitlines():
        row = json.loads(line)
        by[row["corpus"].split("__")[0]][arm].append(row)
for corpus, (head, new) in by.items():
    cells = []
    for run, stage in [(run, s) for run in ("cold", "result") for s in STAGES]:
        h, n = (statistics.median(r[run][stage] for r in rows) for rows in (head, new))
        name = f"{'warm' if run == 'result' else run} {stage}"
        cells.append(f"{name} {h:.2f} -> {n:.2f} ({(n - h) / h:+.1%})")
    same = [len({json.dumps(r[k], sort_keys=True) for r in head + new}) == 1 for k in
            ("cold_digest", "warm_digest")]  # fmt: skip
    print(f"{corpus} (n={len(head)}/{len(new)}):\n  " + "\n  ".join(cells))
    print(f"  one cold digest {same[0]}, one warm digest {same[1]}")
