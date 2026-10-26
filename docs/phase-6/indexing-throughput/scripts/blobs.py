"""Medians of the source-blob reuse A/B (README § 8): ``blobs.py HEAD.jsonl NEW.jsonl``.

Each line is one ``warm.sh`` record. Prints warm total, seal and publish per corpus for
both arms, and whether all runs of both arms produced one digest (equal output).
"""

import json
import statistics
import sys
from collections import defaultdict
from typing import Any

by: dict[str, list[list[dict[str, Any]]]] = defaultdict(lambda: [[], []])
for arm, path in enumerate(sys.argv[1:3]):
    for line in open(path):
        row = json.loads(line)
        by[row["corpus"].split("__")[0]][arm].append(row)
for corpus, (head, new) in by.items():
    cells = []
    for stage in ("total", "seal", "publish", "rss_mb"):
        h, n = (statistics.median(r["result"][stage] for r in rows) for rows in (head, new))
        cells.append(f"{stage} {h:.2f} -> {n:.2f} ({(n - h) / h:+.1%})")
    same = [len({json.dumps(r[k], sort_keys=True) for r in head + new}) == 1 for k in
            ("cold_digest", "warm_digest")]  # fmt: skip
    print(f"{corpus} (n={len(head)}/{len(new)}): " + "; ".join(cells))
    print(f"  one cold digest {same[0]}, one warm digest {same[1]}")
