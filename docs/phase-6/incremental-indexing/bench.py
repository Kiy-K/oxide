"""Indexing cost, before (a checkout of the previous commit) and after (this tree).

    uv run python docs/phase-6/incremental-indexing/bench.py REPO SCRATCH [--src SRC] [--runs N]

Every measured ``index()`` runs in a fresh interpreter, as ``oxide index`` does, and
prints its stage wall times and peak RSS as JSON. ``--src`` puts another engine
checkout's ``src/`` first on the path (the "before" engine). Scenarios edit a private
copy of REPO under SCRATCH; nothing else is touched.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

# Runs inside the child: wraps each stage with a wall clock (no profiler overhead).
CHILD = r"""
import functools, json, os, resource, sys, time
import oxide
from oxide import api
from oxide.core import indexing, python
from oxide.storage import ladybug
times = {}
def timed(owner, name, label):
    inner = getattr(owner, name)
    @functools.wraps(inner)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        try:
            return inner(*args, **kwargs)
        finally:
            times[label] = times.get(label, 0.0) + time.perf_counter() - start
    setattr(owner, name, wrapper)
timed(api, "capture", "capture")
timed(indexing.python, "parse", "parse")
timed(python.Resolver, "derive", "resolve")
timed(ladybug.Store, "_load", "store_load")
timed(ladybug.Store, "_seal", "seal")
if hasattr(python, "decode"):
    timed(indexing.python, "decode", "decode")
    timed(ladybug.View, "facts", "facts_read")
start = time.perf_counter()
with oxide.open_repository(sys.argv[1]) as repo:
    report = repo.index()
times["total"] = time.perf_counter() - start
times["rss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
print(json.dumps({"status": report.status, "files": report.files, **times}))
"""

CAPS = ["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=3G", "-p", "MemorySwapMax=0"]
CAPS += ["-p", "CPUQuota=200%"]


def index(repo: Path, data: Path, src: str | None) -> dict[str, float]:
    env = {**os.environ, "OXIDE_DATA_DIR": str(data)}
    if src:
        env["PYTHONPATH"] = src
    command = [*CAPS, sys.executable, "-c", CHILD, str(repo)]
    out = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
    result: dict[str, float] = json.loads(out.stdout.strip().splitlines()[-1])
    return result


def python_files(repo: Path) -> list[Path]:
    return sorted(p for p in repo.rglob("*.py") if p.is_file())


def edit(paths: list[Path]) -> None:
    for path in paths:
        with path.open("a") as out:
            out.write("\n\ndef _bench_added():\n    return 1\n")


def add_and_delete(repo: Path, files: list[Path]) -> None:
    (repo / "bench_new.py").write_text("def fresh():\n    return 2\n")
    files[1].unlink()


def scenarios(repo: Path) -> dict[str, Callable[[], None]]:
    files = python_files(repo)
    largest = max(files, key=lambda p: p.stat().st_size)
    return {
        "unchanged": lambda: None,
        "edit 1 file": lambda: edit([files[len(files) // 2]]),
        "edit largest file": lambda: edit([largest]),
        "edit 10% of files": lambda: edit(files[::10]),
        "add 1 + delete 1": lambda: add_and_delete(repo, files),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo", type=Path)
    parser.add_argument("scratch", type=Path)
    parser.add_argument("--src", help="engine src/ to measure instead of this tree")
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    results: dict[str, list[dict[str, float]]] = {}
    for run in range(args.runs):
        names = ["cold build", *scenarios(args.repo)]
        for name in names:
            work, data = args.scratch / "repo", args.scratch / "data"
            shutil.rmtree(args.scratch, ignore_errors=True)
            shutil.copytree(args.repo, work)
            if name != "cold build":
                index(work, data, args.src)
                scenarios(work)[name]()
            results.setdefault(name, []).append(index(work, data, args.src))
            print(run, name, results[name][-1], file=sys.stderr)
    shutil.rmtree(args.scratch, ignore_errors=True)
    summary = {}
    for name, runs in results.items():
        stages = sorted({k for r in runs for k in r} - {"status"})
        summary[name] = {"status": runs[0]["status"]} | {
            k: round(statistics.median(r.get(k, 0.0) for r in runs), 2) for k in stages
        }
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
