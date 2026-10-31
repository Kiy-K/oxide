"""Shared paths and the site → enclosing-entity mapping (stdlib only: both venvs import it)."""

import bisect
import os
from pathlib import Path

CORPORA = ("flask", "requests", "pytest")
TRIAL = Path(
    os.environ.get(
        "OXIDE_TRIAL_DATA", Path.home() / "Projects/oxide-eval-data/oxide-structural-trial"
    )
)
DATA = Path(
    os.environ.get(
        "OXIDE_GRAPHNAV_DATA", Path.home() / "Projects/oxide-eval-data/oxide-graphnav-eval"
    )
)
HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent / "results"


def checkout(corpus: str) -> Path:
    return TRIAL / "repos" / corpus


def open_repo(corpus: str):  # type: ignore[no-untyped-def]
    import oxide

    return oxide.open_repository(str(checkout(corpus)), data_dir=str(DATA / "data"))


class Enclosing:
    """Innermost symbol entity whose span contains a byte offset, else the file entity."""

    def __init__(self, rows: list[list]) -> None:  # type: ignore[type-arg]
        self.by_path: dict[str, list[tuple[int, int, str]]] = {}
        self.file: dict[str, str] = {}
        self.info = {r[0]: r for r in rows}
        for key, category, _kind, _name, path, start, end, _test in rows:
            if category == "file":
                self.file[path] = key
            elif start is not None and start >= 0:
                self.by_path.setdefault(path, []).append((start, end, key))
        for spans in self.by_path.values():
            spans.sort()

    def at(self, path: str, offset: int) -> str | None:
        spans = self.by_path.get(path, [])
        best = None
        for start, end, key in spans[: bisect.bisect_right(spans, (offset, 1 << 62, ""))]:
            if start <= offset < end and (best is None or end - start < best[1] - best[0]):
                best = (start, end, key)
        return best[2] if best else self.file.get(path)
