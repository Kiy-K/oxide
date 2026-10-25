"""Post-merge review of ADR-0012: 530c6c5 (NetworkX) against 0381565 (before), end to end.

    python review.py OLD_SRC NEW_SRC      # writes ../results/review.jsonl

Each measurement runs in a fresh interpreter with one implementation on `sys.path`:
cold import, indexing each repository into a fresh scratch store (3 repetitions; wall
time, peak RSS, snapshot and derivation ids), and 400 impact walks per repository whose
full output is hashed. Parity means equal ids and equal walk hashes.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import resource
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

EVAL = Path.home() / "Projects/oxide-eval-data"
REPOS = EVAL / "oxide-structural-trial/repos"
SCRATCH = EVAL / "oxide-networkx-eval/review"
OUT = Path(__file__).resolve().parents[1] / "results/review.jsonl"
NAMES = ("flask", "requests", "pytest", "xarray", "seaborn")
SEED, REPS = 2026102505, 3


def worker(kind: str, repo: str, rep: str) -> dict:
    began = time.perf_counter()
    import oxide

    imported = time.perf_counter() - began
    if kind == "import":
        return {
            "import_s": imported,
            "networkx_loaded": "networkx" in sys.modules,
            "src": str(Path(oxide.__file__).parents[1]),
        }
    data = SCRATCH / f"{Path(oxide.__file__).parents[2].name}-{repo}-{rep}"
    shutil.rmtree(data, ignore_errors=True)  # this script's own scratch store
    began = time.perf_counter()
    report = oxide.index_repository(str(REPOS / repo), data_dir=str(data))
    out = {"index_s": time.perf_counter() - began, "snapshot": report.snapshot.snapshot_id,
           "derivation": report.snapshot.derivation_id}  # fmt: skip
    if kind == "walks":
        with oxide.open_repository(str(REPOS / repo), data_dir=str(data), create=False) as r:
            keys = sorted(row[0] for row in r._view()._query(
                "MATCH (e:Entity) WHERE e.category = 'symbol' RETURN e.key"))  # fmt: skip
            digest, began = hashlib.sha256(), time.perf_counter()
            for key in random.Random(SEED).sample(keys, min(100, len(keys))):
                for direction in ("upstream", "downstream"):
                    for depth, limit in ((3, 200), (8, 1000)):
                        found = r.impact(key, direction=direction, depth=depth, limit=limit)
                        rows = [(h.entity.key, h.depth, h.proven_depth, h.via, h.cyclic)
                                for h in found.hops]  # fmt: skip
                        digest.update(json.dumps([key, rows, found.stopped_by,
                                                  found.unresolved]).encode())  # fmt: skip
            out |= {"walks_s": time.perf_counter() - began, "walks_sha": digest.hexdigest()}
    shutil.rmtree(data, ignore_errors=True)
    out["peak_rss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    out["networkx_loaded"] = "networkx" in sys.modules
    return out


def run(src: str, *args: str) -> dict:
    """One measurement in a fresh interpreter; PYTHONPATH puts `src` ahead of the
    editable install."""
    env = {**os.environ, "PYTHONPATH": src}
    done = subprocess.run([sys.executable, __file__, *args], capture_output=True, text=True,
                          check=True, env=env)  # fmt: skip
    return json.loads(done.stdout.strip().splitlines()[-1])


def main() -> None:
    old, new = sys.argv[1], sys.argv[2]
    rows = []
    for label, src in (("before", old), ("after", new)):
        imports = [run(src, "worker", "import", "-", "-") for _ in range(7)]
        rows.append(
            {
                "impl": label,
                "what": "import",
                "import_ms_median": round(
                    statistics.median(i["import_s"] for i in imports) * 1000, 1
                ),
                "networkx_loaded_by_import": any(i["networkx_loaded"] for i in imports),
            }
        )
    for repo in NAMES:
        for label, src in (("before", old), ("after", new)):
            reps = [run(src, "worker", "walks" if n == 0 else "index", repo, str(n))
                    for n in range(REPS)]  # fmt: skip
            rows.append({"impl": label, "what": "repo", "repo": repo,
                         "index_s_median": round(statistics.median(x["index_s"] for x in reps), 3),
                         "index_s_all": [round(x["index_s"], 3) for x in reps],
                         "peak_rss_mb_max": round(max(x["peak_rss_mb"] for x in reps), 1),
                         "snapshot": reps[0]["snapshot"], "derivation": reps[0]["derivation"],
                         "walks_s": round(reps[0]["walks_s"], 2), "walks_sha": reps[0]["walks_sha"],
                         "networkx_loaded": reps[0]["networkx_loaded"]})  # fmt: skip
    with OUT.open("w") as out:
        for row in rows:
            out.write(json.dumps(row) + "\n")
    for repo in NAMES:
        b, a = [r for r in rows if r.get("repo") == repo]
        same = all(b[k] == a[k] for k in ("snapshot", "derivation", "walks_sha"))
        print(repo, "parity" if same else "DIFFERENT", b["index_s_median"], a["index_s_median"],
              b["peak_rss_mb_max"], a["peak_rss_mb_max"], b["walks_s"], a["walks_s"])  # fmt: skip
    print([r for r in rows if r["what"] == "import"])


if __name__ == "__main__":
    if sys.argv[1] == "worker":
        print(json.dumps(worker(*sys.argv[2:5])))
    else:
        main()
