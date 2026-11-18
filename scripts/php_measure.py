"""Deterministic PHP slice measurements: `uv run python scripts/php_measure.py`."""

from __future__ import annotations

import json
import time
from pathlib import Path

from oxide.capture import capture
from oxide.core.indexing import derive, validate

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/php_repo"


def main() -> None:
    started = time.perf_counter()
    found = derive(capture(FIXTURE), "php-fixture")
    validate(found)
    elapsed = time.perf_counter() - started
    symbols = [e for e in found.entities if e.category == "symbol"]
    modules = [e for e in found.entities if e.category == "module"]
    relations = [e for e in found.edges if e.kind in ("references", "calls", "imports")]
    labels = {
        "proven": sum(e.resolution == "resolved" and e.basis == "resolved" for e in relations),
        "heuristic": sum(e.resolution == "resolved" and e.basis == "heuristic" for e in relations),
        "ambiguous": sum(e.resolution == "ambiguous" for e in relations),
        "unresolved": sum(e.resolution == "unresolved" for e in relations),
    }
    print(
        json.dumps(
            {
                "fixture": str(FIXTURE.relative_to(FIXTURE.parents[1])),
                "index_seconds": round(elapsed, 4),
                "files": len(found.files),
                "php_files": sum(f.language == "php" for f in found.files),
                "symbols": len(symbols),
                "modules": len(modules),
                "relations": len(relations),
                "labels": labels,
                "coverage": {
                    "complete": sum(f.coverage == "complete" for f in found.files),
                    "partial": sum(f.coverage == "partial" for f in found.files),
                },
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
