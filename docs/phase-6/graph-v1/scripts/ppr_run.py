"""Personalized PageRank selection experiment on ContextBench dev tasks (preregistered in
docs/phase-6/graph-v1/preregistration.md). Patches the policy in-process; src/ is unchanged.

    uv run python docs/phase-6/graph-v1/scripts/ppr_run.py {py|ts} {B|D|P} OUT

B: unpatched (must reproduce the frozen dev results). D and P value a routed-only symbol
0.3 + 0.2 * q, where q is its percentile among the routed-only symbols of the query, so
the top half clears INCLUDE_FLOOR 0.4 and all stay below lexical hits (>= 0.5). D ranks
them by routed depth then key (the current order); P by Personalized PageRank over the
routed subgraph, restarting at the entry points weighted 1/rank.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "tests" / "python"))

from oxide.core import packing, routing

DAMPING = 0.85
ITERATIONS = 50
FOLLOW = ("contains", *routing.FOLLOW)
_scores: dict[str, float] = {}


def ppr(view: Any, keys: list[str], entries: list[str]) -> dict[str, float]:
    """Power iteration in sorted key order over the undirected routed subgraph."""
    nodes = sorted(set(keys))
    inside = set(nodes)
    adjacency = view.adjacency(nodes)
    neighbors: dict[str, list[str]] = {k: [] for k in nodes}
    for k in nodes:
        for kind, other, _ in (*adjacency[k][0], *adjacency[k][1]):
            if kind in FOLLOW and other in inside and other != k:
                neighbors[k].append(other)
    neighbors = {k: sorted(set(v)) for k, v in neighbors.items()}
    weights = {k: 1.0 / (n + 1) for n, k in enumerate(dict.fromkeys(entries)) if k in inside}
    total = sum(weights.values()) or 1.0
    restart = {k: weights.get(k, 0.0) / total for k in nodes}
    rank = dict(restart)
    for _ in range(ITERATIONS):
        nxt = {k: (1 - DAMPING) * restart[k] for k in nodes}
        for k in nodes:
            if neighbors[k]:
                share = DAMPING * rank[k] / len(neighbors[k])
                for other in neighbors[k]:
                    nxt[other] += share
            else:  # dangling mass returns to the entry points
                for r, w in restart.items():
                    nxt[r] += DAMPING * rank[k] * w
        rank = nxt
    return rank


def patch(variant: str) -> None:
    original_route, original_value = routing.route, packing.value

    def route(view: Any, entry_keys: list[str]) -> routing.Route:
        found = original_route(view, entry_keys)
        entries = set(entry_keys)
        only = [c for c in found.candidates if c.key not in entries and c.key.startswith("symbol:")]
        if variant == "P":
            score = ppr(view, [c.key for c in found.candidates], entry_keys)
            order = sorted(only, key=lambda c: (-score[c.key], c.depth, c.key))
        else:
            order = sorted(only, key=lambda c: (c.depth, c.key))
        _scores.clear()
        for n, c in enumerate(order):
            _scores[c.key] = 1.0 - n / (len(order) - 1) if len(order) > 1 else 1.0
        return found

    def value(c: packing.Candidate) -> tuple[float, str]:
        if (
            c.entity.category == "symbol"
            and not c.seeded
            and c.rank is None
            and c.entity.key in _scores
        ):
            return 0.3 + 0.2 * _scores[c.entity.key], f"routed {variant} {c.depth}"
        return original_value(c)

    routing.route = route  # type: ignore[assignment]
    packing.value = value


def main(language: str, variant: str, out: Path) -> None:
    import contextbench as cb
    import contextbench_ts as ts

    harness: Any = cb if language == "py" else ts
    if variant != "B":
        patch(variant)
    tasks = harness.load_tasks()
    tasks["tasks"] = [t for t in tasks["tasks"] if t["split"] == "dev"]
    used = {t["corpus"] for t in tasks["tasks"]}
    tasks["corpora"] = {k: v for k, v in tasks["corpora"].items() if k in used}
    with tempfile.TemporaryDirectory(prefix="oxide-graph-v1-") as data:
        evaluated = harness.evaluate(tasks, Path(data))
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(evaluated["results"], indent=1) + "\n")
    metrics = harness.aggregate(evaluated["results"])
    (out / "metrics.json").write_text(json.dumps(metrics, indent=1) + "\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], Path(sys.argv[3]))
