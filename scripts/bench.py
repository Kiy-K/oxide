"""Reproducible OXIDE v2 latency and memory benchmark (Python engine, ADR-0011).

    uv run python scripts/bench.py CORPUS [--iterations 5] [--warm 20] [--budget 1024]
                                          [--query TEXT ...]

Every iteration is a fresh interpreter with a fresh private data location (a new
temporary directory; real user data is never read or written), so each one starts
cold. Within an iteration:

- ``import``: ``import oxide`` in the fresh interpreter.
- ``index_cold``: ``open_repository`` + ``index()`` into the empty store (capture,
  derive, publish, FTS build).
- ``index_unchanged``: a second ``index()`` (capture + freshness check, no publish).
- ``reopen``: close, ``open_repository`` and the first read (view + FTS load).
- ``search_cold``/``context_cold``: the first call per query after reopen.
- ``search_warm``/``context_warm``: the median of ``--warm`` repeats of that call.
- ``peak_rss_mib``: the interpreter's ``ru_maxrss`` at the end.

The report (JSON on stdout) records the corpus identity (path, snapshot id, files,
bytes), the environment (Python, platform, CPUs available, package versions, OXIDE
commit, FTS availability) and min/median/max over iterations. Run it under the
same limits as recorded numbers, e.g. ``systemd-run --user --scope -p
CPUQuota=200% -p MemoryMax=3G``; the limits are not detectable from inside.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

QUERIES = ["where failed requests are retried", "parse configuration file", "RetryPolicy"]


def child(corpus: str, data: str, queries: list[str], warm: int, budget: int) -> dict[str, Any]:
    started = time.perf_counter()
    import oxide

    out: dict[str, Any] = {"import": time.perf_counter() - started}
    started = time.perf_counter()
    repo = oxide.open_repository(corpus, data_dir=data)
    report = repo.index()
    out["index_cold"] = time.perf_counter() - started
    out["status"], out["entities"], out["relations"] = (
        report.status,
        report.entities,
        report.relations,
    )
    started = time.perf_counter()
    out["unchanged"] = repo.index().status
    out["index_unchanged"] = time.perf_counter() - started
    repo.close()
    started = time.perf_counter()
    repo = oxide.open_repository(corpus, data_dir=data)
    repo.snapshot  # noqa: B018 - the first read opens the view and loads FTS
    out["reopen"] = time.perf_counter() - started
    for name, call in (
        ("search", lambda q: repo.search(q)),
        ("context", lambda q: repo.build_context(q, budget)),
    ):
        cold, hot = [], []
        for query in queries:
            started = time.perf_counter()
            call(query)
            cold.append(time.perf_counter() - started)
            times = []
            for _ in range(warm):
                started = time.perf_counter()
                call(query)
                times.append(time.perf_counter() - started)
            hot.append(statistics.median(times))
        out[f"{name}_cold"], out[f"{name}_warm"] = statistics.median(cold), statistics.median(hot)
    repo.close()
    out["peak_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    return out


def environment(repo: Path) -> dict[str, Any]:
    from oxide.storage.ladybug import fts_location, fts_unavailable

    def git(*args: str) -> str:
        run = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
        return run.stdout.strip()

    packages = ("oxide", "ladybug", "tree-sitter", "tree-sitter-python", "pathspec")
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "cpus_available": len(os.sched_getaffinity(0)),
        "packages": {name: version(name) for name in packages},
        "oxide_commit": git("rev-parse", "HEAD"),
        "oxide_dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
        "fts": fts_unavailable(fts_location()) or "available",
    }


def corpus_identity(corpus: Path) -> dict[str, Any]:
    from oxide.capture import capture

    snapshot = capture(corpus)
    return {
        "path": str(corpus),
        "snapshot_id": snapshot.snapshot_id,
        "files": len(snapshot.files),
        "bytes": sum(len(f.data) for f in snapshot.files),
        "skipped": len(snapshot.skipped),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--warm", type=int, default=20)
    parser.add_argument("--budget", type=int, default=1024)
    parser.add_argument("--query", action="append", dest="queries")
    parser.add_argument("--child", nargs=1, metavar="DATA", help=argparse.SUPPRESS)
    args = parser.parse_args()
    queries = args.queries or QUERIES
    if args.child:
        result = child(str(args.corpus), args.child[0], queries, args.warm, args.budget)
        print(json.dumps(result))
        return
    corpus = args.corpus.resolve(strict=True)
    runs = []
    for _ in range(args.iterations):
        with tempfile.TemporaryDirectory(prefix="oxide-bench-") as private:
            data = Path(private) / "data"
            env = {**os.environ, "OXIDE_DATA_DIR": str(data), "XDG_DATA_HOME": private}
            command = [sys.executable, __file__, str(corpus), "--child", str(data)]
            command += ["--warm", str(args.warm), "--budget", str(args.budget)]
            command += [part for q in queries for part in ("--query", q)]
            done = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
            runs.append(json.loads(done.stdout))
    numeric = [k for k, v in runs[0].items() if isinstance(v, float)]
    summary = {
        k: {
            "min": min(r[k] for r in runs),
            "median": statistics.median(r[k] for r in runs),
            "max": max(r[k] for r in runs),
        }
        for k in numeric
    }
    report = {
        "corpus": corpus_identity(corpus),
        "environment": environment(Path(__file__).resolve().parents[1]),
        "method": {
            "iterations": args.iterations,
            "warm_repeats": args.warm,
            "budget": args.budget,
            "queries": queries,
            "units": "seconds, except peak_rss_mib",
        },
        "counts": {k: runs[0][k] for k in ("status", "unchanged", "entities", "relations")},
        "summary": summary,
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
