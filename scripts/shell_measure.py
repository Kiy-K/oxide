"""Deterministic shell slice measurements: `uv run python scripts/shell_measure.py`."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import oxide
from oxide.capture import capture
from oxide.core.indexing import derive, validate

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/shell_repo"


def main() -> None:
    started = time.perf_counter()
    found = derive(capture(FIXTURE), "shell-fixture")
    validate(found)
    elapsed = time.perf_counter() - started
    symbols = [e for e in found.entities if e.category == "symbol"]
    relations = [e for e in found.edges if e.kind in ("references", "calls", "imports")]
    labels = {
        "proven": sum(e.resolution == "resolved" and e.basis == "resolved" for e in relations),
        "ambiguous": sum(e.resolution == "ambiguous" for e in relations),
        "unresolved": sum(e.resolution == "unresolved" for e in relations),
    }
    retrieval: dict[str, float | int] = {}
    with (
        tempfile.TemporaryDirectory(prefix="oxide-shell-measure-") as data_dir,
        oxide.open_repository(FIXTURE, data_dir=Path(data_dir)) as handle,
    ):
        handle.index()
        begin = time.perf_counter()
        entries = handle.search("log", limit=5).entries
        retrieval["search_seconds"] = round(time.perf_counter() - begin, 4)
        retrieval["search_entries"] = len(entries)
        begin = time.perf_counter()
        items = handle.build_context("log", 512).items
        retrieval["context_seconds"] = round(time.perf_counter() - begin, 4)
        retrieval["context_items"] = len(items)
    print(
        json.dumps(
            {
                "fixture": str(FIXTURE.relative_to(FIXTURE.parents[1])),
                "index_seconds": round(elapsed, 4),
                "files": len(found.files),
                "shell_files": sum(f.language in ("bash", "sh") for f in found.files),
                "symbols": len(symbols),
                "relations": len(relations),
                "labels": labels,
                "coverage": {
                    "complete": sum(f.coverage == "complete" for f in found.files),
                    "partial": sum(f.coverage == "partial" for f in found.files),
                    "unsupported": sum(f.coverage == "unsupported" for f in found.files),
                },
                "retrieval": retrieval,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
