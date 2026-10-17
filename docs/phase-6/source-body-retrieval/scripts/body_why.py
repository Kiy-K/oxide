"""body_why.py DIR BENCH DIAG.json [CUT=20]: why body arms lose or fail to lift gold (dev only).

For each verified span and body arm, from the real-FTS probes (gold rank without a cut,
the top-1000 keys of each arm) and the diagnostic's per-span term data (DIAG.json,
`distinct_span`: query terms in at most 5 % of files that occur in the gold text):

- displaced: in the top 20 under `none`, below it under the arm;
- lifted: below 20 under `none`, in the top 20 under the arm;
- still lost: below 20 under both.

Displaced and still-lost spans get the preregistered classes, first match wins:
  localization    the gold file is in the top 20 files under no arm
  discrimination  the gold text shares no distinctive query term
  body noise      otherwise; the share of entities above the gold that rose past it
                  (absent from, or below the gold in, `none`) is reported
"""

import json
import sys
from collections import Counter
from pathlib import Path

root, bench, diag = Path(sys.argv[1]), sys.argv[2], json.loads(Path(sys.argv[3]).read_text())
terms = {(t["task"], s["file"], tuple(s["lines"])): s for t in diag for s in t["spans"]}


def load(arm):
    p = json.loads((root / f"{bench}-{arm}" / "probe.json").read_text())
    spans = {(s["task"], s["file"], tuple(s["lines"])): s for s in p["spans"]}
    return spans, p["tasks"]


arms = {a: load(a) for a in ("none", "full", "own")}
none_spans, none_tasks = arms["none"]
IN = int(sys.argv[4]) if len(sys.argv) > 4 else 20  # the entry cut being examined


def top(r):
    return r is not None and r <= IN


for arm in ("full", "own"):
    spans, tasks = arms[arm]
    moved, classes, rose_share = Counter(), Counter(), []
    per_repo, repo_classes = Counter(), Counter()
    for key, s in spans.items():
        a, b = none_spans[key]["rank"], s["rank"]
        if top(a) and top(b):
            state = "kept"
        elif top(a):
            state = "displaced"
        elif top(b):
            state = "lifted"
        else:
            state = "still lost"
        moved[state] += 1
        per_repo[(s["corpus"].split("@")[0], state)] += 1
        if state not in ("displaced", "still lost"):
            continue
        files = [arms[x][0][key]["file_rank"] for x in arms]
        if not any(top(f) for f in files):
            cls = "localization"
        elif not terms.get(key, {}).get("distinct_span"):
            cls = "discrimination"
        else:
            cls = "body noise"
        classes[(state, cls)] += 1
        repo_classes[(s["corpus"].split("@")[0], cls)] += 1
        if b is not None and state == "displaced":
            above = tasks[s["task"]]["top"][: b - 1]
            base = {k: i for i, k in enumerate(none_tasks[s["task"]]["top"], 1)}
            rose = sum(base.get(k, 10**9) > a for k in above)
            rose_share.append(rose / max(1, len(above)))
    print(f"== {arm} ({len(spans)} verified dev spans) ==")
    print("  moves:", dict(moved))
    print("  by repo:", {f"{r}:{s}": n for (r, s), n in sorted(per_repo.items())})
    print("  classes:", {f"{s}/{c}": n for (s, c), n in sorted(classes.items())})
    print("  classes by repo:", {f"{r}/{c}": n for (r, c), n in sorted(repo_classes.items())})
    if rose_share:
        rose_share.sort()
        print(
            "  displaced: share of entities now above the gold that rose past it, "
            f"median {rose_share[len(rose_share) // 2]:.2f} (n={len(rose_share)})"
        )
