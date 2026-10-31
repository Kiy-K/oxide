"""graph-navigation's impact arm (I) on its frozen truth, against the store at argv[1].

Same claims as ``graph-navigation/scripts/arms.py`` (module hops count as their file);
prints macro precision / recall / F1 for Q1 (depth 1) and Q2 (depth 2).
"""

import json
import sys
from pathlib import Path
from typing import Any

import oxide

HERE = Path(__file__).resolve().parents[2] / "graph-navigation"
REPOS = Path.home() / "Projects/oxide-eval-data/oxide-structural-trial/repos"


def claim(entity: Any) -> str:
    if entity.category == "module" and entity.source is not None:
        return f"file:{entity.source.path}"
    return str(entity.key)


def prf(claims: set[str], truth: set[str]) -> tuple[float, float, float]:
    tp = len(claims & truth)
    p, r = (tp / len(claims) if claims else 0.0), tp / len(truth)
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def main() -> None:
    truth = json.loads((HERE / "results/truth.json").read_text())
    store, opened, rows = sys.argv[1], {}, []
    for t in truth:
        if t["corpus"] not in opened:
            path = str(REPOS / t["corpus"])
            opened[t["corpus"]] = oxide.open_repository(path, data_dir=store, create=False)
        repo, row = opened[t["corpus"]], {"key": t["key"], "kind": t["kind"]}
        for depth, q in ((1, "q1"), (2, "q2")):
            hops = repo.impact(t["key"], depth=depth).hops
            row[q] = prf({claim(h.entity) for h in hops}, set(t[q]))
        rows.append(row)
    for q in ("q1", "q2"):
        for kind in ("all", "function", "method"):
            sel = [r[q] for r in rows if kind in ("all", r["kind"])]
            macro = [round(sum(x[i] for x in sel) / len(sel), 3) for i in range(3)]
            print(Path(store).name, q, kind, "P/R/F1", macro)
    print(json.dumps({r["key"]: r["q1"] for r in rows}))


if __name__ == "__main__":
    main()
