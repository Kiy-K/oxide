"""Parallel vs sequential parsing inside a real cold ``index()``.

    OXIDE_DATA_DIR=SCRATCH uv run python par_parse.py REPO --workers N [--check]

``--workers 0`` is today's sequential path, untouched. ``N > 0`` parses every source
file in a ``ProcessPoolExecutor`` (forkserver) before ``derive``; ``derive`` then
reads those results through the same ``parse`` names, so resolution, validation and
publication are unchanged. ``--check`` also parses sequentially and asserts the pool
produced equal ``Parsed`` values. Prints stage times and the scope's cgroup
``memory.peak`` (parent and workers together), so runs compare at the same caps.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any

import oxide
from oxide import api
from oxide.core import indexing

LANGS = ("python", "typescript", "go", "rust")


def parse_one(item: tuple[str, bytes]) -> Any:
    lang, data = item
    if lang in ("typescript", "tsx", "javascript"):
        return indexing.typescript.parse(data, language=lang)
    return getattr(indexing, lang).parse(data)


def cgroup_peak() -> int | None:
    rel = Path("/proc/self/cgroup").read_text().strip().split("::")[-1]
    peak = Path("/sys/fs/cgroup") / rel.lstrip("/") / "memory.peak"
    return int(peak.read_text()) // (1 << 20) if peak.exists() else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo")
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--chunksize", type=int, default=16)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    times: dict[str, float] = {}
    table: dict[tuple[str, bytes], Any] = {}
    originals = {lang: getattr(indexing, lang).parse for lang in LANGS}
    real_derive = api.derive

    def pooled(lang: str, data: bytes, language: str | None = None) -> Any:
        return table[(language or lang, data)]

    def derive(capture: Any, repo_id: str, cached: Any = None) -> Any:
        started = time.perf_counter()
        items = [(lang, f.data) for f in capture.files if (lang := indexing.dialect(f.path))]
        context = multiprocessing.get_context("forkserver")
        with ProcessPoolExecutor(args.workers, mp_context=context) as pool:
            results = pool.map(parse_one, items, chunksize=args.chunksize)
            table.update(zip(items, results, strict=True))
        times["pool_parse"] = time.perf_counter() - started
        if args.check:
            for item, value in table.items():
                assert parse_one(item) == value, f"pool parse differs for a {item[0]} file"
        for lang in LANGS:
            getattr(indexing, lang).parse = partial(pooled, lang)
        started = time.perf_counter()
        try:
            return real_derive(capture, repo_id, cached)
        finally:
            times["derive_after_pool"] = time.perf_counter() - started
            for lang in LANGS:
                getattr(indexing, lang).parse = originals[lang]

    if args.workers:
        api.derive = derive  # type: ignore[assignment]
    started = time.perf_counter()
    with oxide.open_repository(args.repo) as repo:
        report = repo.index()
    times["total"] = time.perf_counter() - started
    out: dict[str, Any] = {k: round(v, 3) for k, v in times.items()}
    out |= {"workers": args.workers, "memory_peak_mb": cgroup_peak(), "files": report.files}
    out |= {"entities": report.entities, "relations": report.relations, "checked": args.check}
    print(json.dumps(out))


if __name__ == "__main__":
    main()
