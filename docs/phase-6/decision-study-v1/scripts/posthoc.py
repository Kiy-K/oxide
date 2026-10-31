"""Post-hoc diagnostic, NOT preregistered and never promotable: does JEV's ranking raise
packed recall once its probability scale is separated from the heuristic's floor?

    uv run --with tiktoken python docs/phase-6/decision-study-v1/scripts/posthoc.py OUT \
        NAME=JUDGMENTS.json ...

Runs ``study.py`` with two extra replays per recorded arm (no new model calls):

  NAME~order     candidates the heuristic values at or above the floor get 0.4 + 0.59 * noul,
                 the others abstain (heuristic). Production's admitted set, JEV's order:
                 the analogue of graph-v3 ``oracle_order``.
  NAME~quantile  JEV's ranks within the request mapped onto that request's heuristic values
                 (same multiset, so as many candidates clear the floor as in production):
                 JEV chooses which. A failed request keeps the heuristic for all.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import study

from oxide.core import packing

real_arms = study.arms


def arms(task: str, by_key: dict[str, Any], gold: dict[str, int]) -> dict[str, Any]:
    out = real_arms(task, by_key, gold)

    def heuristic(c: Any) -> float:
        return packing.value(by_key[c.key])[0]

    for name, data in study.recorded.items():
        j = data["judgments"]

        def order(caps, j=j):  # type: ignore[no-untyped-def]
            return {
                c.digest: 0.4 + 0.59 * j[c.digest]
                if c.digest in j and heuristic(c) >= packing.INCLUDE_FLOOR
                else None
                for c in caps
            }

        def quantile(caps, j=j):  # type: ignore[no-untyped-def]
            if any(c.digest not in j for c in caps):
                return {}
            values = sorted((heuristic(c) for c in caps), reverse=True)
            ranked = sorted(caps, key=lambda c: (-j[c.digest], c.digest))  # ties: digest
            return {c.digest: v for c, v in zip(ranked, values, strict=True)}

        out[f"{name}~order"], out[f"{name}~quantile"] = order, quantile

    def shuffled(seed: int):  # type: ignore[no-untyped-def]
        """Control for ``~order``: production's admitted set in a random order."""

        def judge(caps):  # type: ignore[no-untyped-def]
            return {
                c.digest: 0.4 + 0.59 * study.random.Random(f"{seed}:{c.digest}").random()
                if heuristic(c) >= packing.INCLUDE_FLOOR
                else None
                for c in caps
            }

        return judge

    out |= {f"random~order_s{s}": shuffled(s) for s in study.SEEDS}
    return out


if __name__ == "__main__":
    study.arms = arms
    study.main(sys.argv[1:])
