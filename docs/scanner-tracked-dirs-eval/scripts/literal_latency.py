#!/usr/bin/env python3
"""Literal-search latency, main vs tracked vs narrow. `oxide search --mode
literal` walks the repository on every request (`scanner::scan_repo_text`),
so it is where the challenger's per-walk `git ls-files` is paid at request
time. No index needed. Arms interleaved per repeat; median of N.

usage: literal_latency.py <challenger oxide> <out.json> <N> <checkout>=<pattern>[,<pattern>...] ...
"""
import json
import os
import statistics
import subprocess
import sys
import time

ARMS = ("main", "tracked", "narrow")


def env(arm):
    e = {k: v for k, v in os.environ.items() if k != "OXIDE_SCANNER_POLICY"}
    if arm != "main":
        e["OXIDE_SCANNER_POLICY"] = arm
    return e


def main():
    oxide, out, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
    res = {}
    for spec in sys.argv[4:]:
        repo, pats = spec.split("=", 1)
        for pat in pats.split(","):
            walls = {a: [] for a in ARMS}
            hits = {}
            for i in range(n + 1):  # first round is warm-up, discarded
                for arm in ARMS[i % 3:] + ARMS[:i % 3]:
                    t = time.perf_counter()
                    p = subprocess.run([oxide, "search", pat, "--mode", "literal", "--limit", "200",
                                        "--json"], cwd=repo, env=env(arm), capture_output=True,
                                       text=True, check=True)
                    w = time.perf_counter() - t
                    if i:
                        walls[arm].append(w)
                    hits[arm] = len(json.loads(p.stdout).get("hits", []))
            key = f"{os.path.basename(repo)}:{pat}"
            res[key] = {a: dict(median_s=round(statistics.median(walls[a]), 4),
                                min_s=round(min(walls[a]), 4), hits=hits[a]) for a in ARMS}
            print(key, {a: res[key][a]["median_s"] for a in ARMS}, flush=True)
    json.dump(res, open(out, "w"), indent=1)


if __name__ == "__main__":
    main()
