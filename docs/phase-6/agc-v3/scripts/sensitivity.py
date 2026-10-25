"""Descriptive, not preregistered: D1 re-graded with transitive callers as neutral.

    python3 sensitivity.py     # reads results/runs.json, prints success/F1/false by arm

D1 asks which functions contain "a call that can execute `Variable.reduce`". The truth
lists direct dispatch only, but the wording also admits functions that reach it through
another `reduce` (README § Threats). Those claims become neutral here; nothing else moves.
"""

import json
import statistics
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[1] / "results"
G, R = "xarray/core/groupby.py#", "xarray/core/rolling.py#"
TRANSITIVE = [  # each reaches Variable.reduce through DataArray/Dataset.reduce
    G + "DataArrayGroupBy.reduce",
    G + "DatasetGroupBy.reduce",
    G + "DataArrayGroupBy._reduce_method",
    G + "DatasetGroupBy._reduce_method",
    G + "GroupBy._first_or_last",
    R + "DataArrayRolling.reduce",
    R + "DatasetRolling.reduce",
    "xarray/core/resample.py#DatasetResample.reduce",
    "xarray/core/rolling_exp.py#RollingExp.mean",
    "xarray/core/common.py#ImplementsDatasetReduce._reduce_method",
]
TRUTH_N = 3


def lenient(claim: str) -> bool:
    k = claim.removeprefix("symbol:").split("~")[0]
    return any(k == t or k.startswith(t + ".") for t in TRANSITIVE)


rows = [r for r in json.loads((RESULTS / "runs.json").read_text()) if r["rep"].startswith("r")]
for arm in ("B", "D", "G", "F"):
    out = []
    for r in rows:
        if r["arm"] != arm or r["task"] != "D1":
            continue
        false = [c for c in r["false"] if not lenient(c)]
        p = r["tp"] / (r["tp"] + len(false)) if r["tp"] + len(false) else 0.0
        rec = r["tp"] / TRUTH_N
        f1 = 2 * p * rec / (p + rec) if p + rec else 0.0
        ok = not r["technical_failure"] and f1 >= 0.8 and not false
        out.append((ok, f1, len(false), r["success"], r["f1"], r["false_claims"]))
    means = [round(statistics.fmean(x[i] for x in out), 2) for i in range(6)]
    print(arm, "D1 strict success/F1/false", means[3:], "lenient", means[:3])
