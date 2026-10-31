"""Index wall time and peak RSS, HEAD's ``src`` against the working tree's, interleaved.

    index_time.py HEAD_SRC FIX_SRC      # writes ../results/index_time.json

Each measurement is a fresh interpreter with one ``src`` first on ``sys.path``, indexing
one repository into a fresh scratch store (outside git). Order alternates per repetition.
"""

import json
import resource
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

EVAL = Path.home() / "Projects/oxide-eval-data"
REPOS = EVAL / "oxide-structural-trial/repos"
SCRATCH = EVAL / "oxide-gnav2-eval/timing"
OUT = Path(__file__).resolve().parents[1] / "results/index_time.json"
NAMES = ("flask", "requests", "pytest", "xarray", "seaborn")
REPS = 5


def worker(label: str, repo: str) -> None:
    import oxide
    from oxide.core.languages import python

    data = SCRATCH / f"{label}-{repo}"
    shutil.rmtree(data, ignore_errors=True)
    began = time.perf_counter()
    report = oxide.index_repository(str(REPOS / repo), data_dir=str(data))
    seconds = time.perf_counter() - began
    shutil.rmtree(data, ignore_errors=True)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(json.dumps({"index_s": seconds, "rss_mb": rss, "version": python.VERSION,
                      "derivation": report.snapshot.derivation_id}))  # fmt: skip


def main(head: str, fix: str) -> None:
    rows = []
    for rep in range(REPS):
        trees = [("head", head), ("fix", fix)][:: 1 if rep % 2 == 0 else -1]
        for repo in NAMES:
            for label, src in trees:
                done = subprocess.run(
                    [sys.executable, __file__, "worker", label, repo],
                    env={"PYTHONPATH": src, "PATH": "/usr/bin", "HOME": str(Path.home())},
                    capture_output=True, text=True, check=True,
                )  # fmt: skip
                rows.append({"tree": label, "repo": repo, "rep": rep}
                            | json.loads(done.stdout.splitlines()[-1]))  # fmt: skip
                print(rows[-1], flush=True)
    summary = {}
    for repo in NAMES:
        med = {t: {k: statistics.median(r[k] for r in rows if r["tree"] == t and r["repo"] == repo)
                   for k in ("index_s", "rss_mb")} for t in ("head", "fix")}  # fmt: skip
        before, after = med["head"], med["fix"]
        summary[repo] = med | {
            "delta_s": after["index_s"] - before["index_s"],
            "delta_pct": 100 * (after["index_s"] / before["index_s"] - 1),
            "delta_rss_mb": after["rss_mb"] - before["rss_mb"],
        }
    OUT.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1) + "\n")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    if sys.argv[1] == "worker":
        worker(sys.argv[2], sys.argv[3])
    else:
        main(sys.argv[1], sys.argv[2])
