"""Interface overhead of the DecisionProvider seam (infrastructure only, no quality claim).

    uv run python docs/phase-6/decision-seam/scripts/overhead.py [REPEATS]

Indexes ``fixtures/py_repo`` into a private data directory, records the real
``packing.pack`` arguments of one ``build_context`` call, then times ``pack`` on that
pool with no judge, a heuristic replay (same values: must pack the same bundle) and a
reversed replay (every value changed). Medians of REPEATS calls, in microseconds.
"""

from __future__ import annotations

import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

import oxide
from oxide.core import packing

ROOT = Path(__file__).resolve().parents[4]
QUERY = "where failed HTTP requests are retried"
captured: dict[str, Any] = {}
real_pack = packing.pack


def recording_pack(*args: Any, **kwargs: Any) -> Any:
    captured["args"] = args
    return real_pack(*args, **kwargs)


def median_us(call: Callable[[], object], repeats: int) -> float:
    times = []
    for _ in range(repeats):
        start = time.perf_counter_ns()
        call()
        times.append(time.perf_counter_ns() - start)
    return statistics.median(times) / 1000


def main() -> None:
    repeats = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    with (
        tempfile.TemporaryDirectory() as data,
        oxide.open_repository(ROOT / "fixtures" / "py_repo", data_dir=data) as repo,
    ):
        repo.index()
        packing.pack = recording_pack
        for budget in (1024, 4096):
            bundle = repo.build_context(QUERY, budget)
            pool, scopes, _, source, declarations = captured["args"]
            args = (pool, scopes, budget, source, declarations)
            asked = [c for c in pool if c.entity.source is not None]
            values = {c.entity.key: packing.value(c)[0] for c in asked}
            heuristic = packing.Replay("heuristic", values)
            reverse = packing.Replay("reversed", {k: 1 - v for k, v in values.items()})
            base, same = real_pack(*args), real_pack(*args, heuristic)
            assert [i.text for i in base[0]] == [i.text for i in same[0]]
            assert "".join(i.text for i in base[0]) == bundle.payload
            print(f"budget {budget}: pool {len(pool)}, with source {len(asked)}")
            build = partial(repo.build_context, QUERY, budget)
            print(f"  build_context          {median_us(build, max(1, repeats // 10)):9.1f} us")
            for name, judge in (("none", None), ("heuristic", heuristic), ("reversed", reverse)):
                us = median_us(partial(real_pack, *args, judge), repeats)
                print(f"  pack, judge {name:10} {us:9.1f} us")
            us = median_us(partial(packing.judged, heuristic, asked), repeats)
            print(f"  judged() alone         {us:9.1f} us")
        packing.pack = real_pack


if __name__ == "__main__":
    main()
