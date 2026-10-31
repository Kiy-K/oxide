"""Stage wall times and peak RSS of one cold (or warm) ``index()``, in this interpreter.

    OXIDE_DATA_DIR=SCRATCH uv run python profile.py REPO [--gc-freeze] [--db-threads N]

``--db-threads N`` gives the bulk load's database N threads instead of 1; ``lexical``
is then a digest of BM25 results for a fixed query set, to compare outputs across runs.

Wraps each stage with a wall clock (no profiler, so Python-heavy stages are not
distorted); prints one JSON line. Run it under the heavy-job caps
(``systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0 -p CPUQuota=200%``).
"""

from __future__ import annotations

import functools
import gc
import hashlib
import json
import resource
import sys
import time
from typing import Any

import oxide
from oxide import api
from oxide.core import indexing
from oxide.core.languages import go_resolve, python_resolve, rust_resolve, typescript_resolve
from oxide.storage import ladybug, store

times: dict[str, float] = {}
QUERIES = ["parse", "config", "test", "request handler", "error", "get value", "init", "render"]
QUERIES += ["command", "client", "server", "editor", "model", "token", "cache", "stream"]


def timed(owner: Any, name: str, label: str) -> None:
    inner = getattr(owner, name)

    @functools.wraps(inner)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        try:
            return inner(*args, **kwargs)
        finally:
            times[label] = times.get(label, 0.0) + time.perf_counter() - start

    setattr(owner, name, wrapper)


def statement(query: str) -> str:
    head = query.split("(")[0].split()[:2]
    if head[0] == "CALL":
        return "fts_create"
    if head[0] == "COPY":
        return f"copy_{head[1]}"
    return {"MATCH": "readback", "CHECKPOINT": "checkpoint", "COMMIT": "commit"}.get(head[0], "ddl")


def main() -> None:
    if "--gc-freeze" in sys.argv:
        gc.disable()
    if "--db-threads" in sys.argv:
        threads = int(sys.argv[sys.argv.index("--db-threads") + 1])

        class Threaded(store.ladybug.Database):  # type: ignore[misc]
            def __init__(self, path: str, **kwargs: Any) -> None:
                if kwargs.get("max_num_threads") == 1 and path.endswith(".staging/graph.lbug"):
                    kwargs["max_num_threads"] = threads
                super().__init__(path, **kwargs)

        store.ladybug.Database = Threaded  # type: ignore[misc]
    timed(api, "capture", "capture")
    for lang in ("python", "typescript", "go", "rust"):
        module = getattr(indexing, lang)
        timed(module, "parse", f"parse_{lang}")
        timed(module, "encode", "encode")
        timed(module, "decode", "decode")
    for lang, module in (
        ("python", python_resolve),
        ("typescript", typescript_resolve),
        ("go", go_resolve),
        ("rust", rust_resolve),
    ):
        timed(module.Resolver, "derive", f"resolve_{lang}")
    timed(api, "derive", "derive")
    timed(api, "validate", "validate")
    timed(store.Store, "publish", "publish")
    timed(store.Store, "_load", "load")
    timed(store.Store, "_seal", "seal")
    timed(store, "document", "fts_terms")
    timed(store, "_write_csv", "csv")
    timed(ladybug.View, "facts", "facts_read")
    rows = store._rows

    def classified(conn: Any, query: str, params: Any = None) -> Any:
        start = time.perf_counter()
        try:
            return rows(conn, query, params)
        finally:
            label = "db_" + statement(query)
            times[label] = times.get(label, 0.0) + time.perf_counter() - start

    store._rows = classified  # type: ignore[assignment]
    gc_time = [0.0, 0.0]

    def on_gc(phase: str, info: dict[str, Any]) -> None:
        if phase == "start":
            gc_time[1] = time.perf_counter()
        else:
            gc_time[0] += time.perf_counter() - gc_time[1]

    gc.callbacks.append(on_gc)
    lexical = None
    start = time.perf_counter()
    with oxide.open_repository(sys.argv[1]) as repo:
        report = repo.index()
        times["total"] = time.perf_counter() - start
        view = repo._view()
        if view.lexical_unavailable is None:
            hits = [view.lexical(q, 50) for q in QUERIES]
            lexical = hashlib.sha256(repr(hits).encode()).hexdigest()[:16]
    times["gc"] = gc_time[0]
    out: dict[str, Any] = {k: round(v, 3) for k, v in sorted(times.items())}
    out["lexical"] = lexical
    out["rss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
    out |= {"status": report.status, "files": report.files, "entities": report.entities}
    out |= {"relations": report.relations, "parsed": report.parsed, "reused": report.reused}
    print(json.dumps(out))


if __name__ == "__main__":
    main()
