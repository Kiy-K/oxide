"""Loss attribution and counterfactual packing over the routed pool, ContextBench dev.

    uv run python docs/phase-6/graph-v3/scripts/pool_trace.py {py|ts} OUT.json

Runs the unchanged production path (``cb.score``), records every ``packing.pack`` call,
then replays counterfactual packings over the *same* pool at the same budget. src/ is
unchanged; every patch is undone before the next call. Verified gold only.

Counterfactuals (line recall per task):
  base          the recorded production bundle (asserted equal to the scorer's)
  inf_capped    same pool, floor, order; unlimited budget, item cap kept
  inf           unlimited budget and item cap               -> pool ceiling under the floor
  nofloor_inf   floor 0, unlimited budget and item cap      -> routed ceiling
  oracle_order  gold holders above the floor packed first   -> ordering ceiling at budget
  oracle_pick   gold holders packed first even below floor  -> selection+order ceiling
  noitemcap     MAX_ITEMS unlimited at budget
and a cap sweep: MAX_REGIONS (and GRAPH_NODES) at 16/32/48/96/128, production policy.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "tests" / "python"))

import contextbench as cb

from oxide.core import packing, routing

BUDGETS = (1024, 4096)
CAPS = (16, 32, 48, 96, 128)
INF = 10**9

real_pack, real_route, real_value, real_score = packing.pack, routing.route, packing.value, cb.score
calls: dict[int, tuple[Any, ...]] = {}
routes: list[routing.Route] = []


def recording_pack(pool, scopes, budget, source, declarations):  # type: ignore[no-untyped-def]
    out = real_pack(pool, scopes, budget, source, declarations)
    calls[budget] = (pool, scopes, source, declarations, out)
    return out


def recording_route(view, entry_keys):  # type: ignore[no-untyped-def]
    out = real_route(view, entry_keys)
    routes.append(out)
    return out


packing.pack = recording_pack
routing.route = recording_route


def covered(items: list[Any]) -> set[tuple[str, int]]:
    return {(i.source.path, n) for i in items for n in range(i.lines[0], i.lines[1] + 1)}


def replay(call: tuple[Any, ...], budget: int, **patch: Any) -> list[Any]:
    pool, scopes, source, declarations, _ = call
    saved = {k: getattr(packing, k) for k in patch}
    try:
        for k, v in patch.items():
            setattr(packing, k, v)
        return real_pack(pool, scopes, budget, source, declarations)[0]
    finally:
        for k, v in saved.items():
            setattr(packing, k, v)


def boosted(holders: set[str], floor: bool):  # type: ignore[no-untyped-def]
    def value(c: packing.Candidate) -> tuple[float, str]:
        score, basis = real_value(c)
        if c.entity.key in holders and (not floor or score >= packing.INCLUDE_FLOOR):
            return 0.99, "oracle"
        return score, basis

    return value


def kind(origin: str) -> str:
    """``calls:in:<key>`` -> ``calls:in``; ``child:<key>`` -> ``child``; ``entry``."""
    parts = origin.split(":")
    return ":".join(parts[:2]) if len(parts) > 2 and parts[1] in ("in", "out") else parts[0]


def trace(repo: Any, lines: Any, task: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    pool = calls[4096][0]
    spans = {
        c.entity.key: (c.entity.category, lines.span(c.entity.source))
        for c in pool
        if c.entity.source is not None
    }
    units: dict[str, set[tuple[str, int]]] = {}  # pool key -> verified gold lines it carries
    gold_lines: set[tuple[str, int]] = set()
    for g in (g for g in task["gold"] if g["verified"]):
        code = lines.code(g["file"], g["start"], g["end"])
        module = code & lines.module(g["file"])
        for n in code:
            gold_lines.add((g["file"], n))
            if n in module:
                key = f"file:{g['file']}"
                holder = key if key in spans else None
            else:
                inner = [
                    (r[2] - r[1], k)
                    for k, (c, r) in spans.items()
                    if c == "symbol" and r[0] == g["file"] and r[1] <= n <= r[2]
                ]
                holder = min(inner)[1] if inner else None
            if holder:
                units.setdefault(holder, set()).add((g["file"], n))
    if not gold_lines:
        return {}

    def recall(items: list[Any]) -> float:
        return round(len(gold_lines & covered(items)) / len(gold_lines), 6)

    route = routes[-1]
    origins = {r.key: r.origins for r in route.candidates}
    out: dict[str, Any] = {
        "gold_lines": len(gold_lines),
        "routed_lines": len(set().union(*units.values())) if units else 0,
        "route": {"regions": route.regions, "stopped_by": route.stopped_by, "pool": len(pool)},
        "cf": {},
    }
    holders = set(units)
    for b in BUDGETS:
        call = calls[b]
        base = call[4][0]
        assert recall(base) == result["verified"]["metrics"][f"line_recall@{b}"], task["id"]
        out["cf"][str(b)] = {
            "base": recall(base),
            "inf_capped": recall(replay(call, INF)),
            "inf": recall(replay(call, INF, MAX_ITEMS=INF)),
            "nofloor_inf": recall(replay(call, INF, MAX_ITEMS=INF, INCLUDE_FLOOR=0.0)),
            "oracle_order": recall(replay(call, b, value=boosted(holders, True))),
            "oracle_pick": recall(replay(call, b, value=boosted(holders, False))),
            "noitemcap": recall(replay(call, b, MAX_ITEMS=INF)),
        }

    # Candidate table at 4,096: value, eligibility, packed state, size, gold carried.
    _, _, source, declarations, (items, omissions) = calls[4096]
    sizer = packing._Packer(INF, source, declarations)
    omitted = {o.key: f"{o.stage}: {o.reason}" for o in omissions}
    shown = {e.key: ("full" if e.complete else "header") for i in items for e in i.entities}
    rows = []
    for position, c in enumerate(pool):
        v, _ = real_value(c)
        size = None
        if c.entity.source is not None and (
            c.entity.category == "symbol" or c.entity.key in declarations
        ):
            view = sizer.view(c.entity, False, "")
            rendered = sizer.render(view) if view else None
            size = sum(i.units for i in rendered) if rendered else None
        rows.append(
            {
                "key": c.entity.key,
                "cat": c.entity.category,
                "depth": c.depth,
                "rank": c.rank,
                "seeded": c.seeded,
                "value": round(v, 3),
                "position": position,
                "origins": sorted({kind(o) for o in origins.get(c.entity.key, ())}),
                "parents": list(origins.get(c.entity.key, ())),
                "units": size,
                "state": shown.get(c.entity.key, omitted.get(c.entity.key, "absent")),
                "gold": len(units.get(c.entity.key, ())),
            }
        )
    out["candidates"] = rows
    out["items"] = [
        {
            "units": i.units,
            "gold": len(gold_lines & covered([i])),
            "reasons": [e.reason for e in i.entities],
        }
        for i in items
    ]

    # Cap sweep, production policy (routing and the graph node limit move together).
    sweep = {}
    for cap in CAPS:
        saved = routing.MAX_REGIONS, packing.GRAPH_NODES
        routing.MAX_REGIONS = packing.GRAPH_NODES = cap
        try:
            bundle = repo.build_context(task["query"], 4096, symbols=task["symbols"])
            sweep[str(cap)] = {
                "line_recall": recall(list(bundle.items)),
                "regions": routes[-1].regions,
                "used": bundle.used,
            }
        finally:
            routing.MAX_REGIONS, packing.GRAPH_NODES = saved
    out["caps"] = sweep
    return out


def score(repo, lines, task):  # type: ignore[no-untyped-def]
    calls.clear()
    routes.clear()
    result = real_score(repo, lines, task)
    result["trace"] = trace(repo, lines, task, result)
    return result


cb.score = score


def main(language: str, out: Path) -> None:
    if language == "ts":
        import contextbench_ts as harness
    else:
        harness = cb
    tasks = harness.load_tasks()
    tasks["tasks"] = [t for t in tasks["tasks"] if t["split"] == "dev"]
    used = {t["corpus"] for t in tasks["tasks"]}
    tasks["corpora"] = {k: v for k, v in tasks["corpora"].items() if k in used}
    with tempfile.TemporaryDirectory(prefix="oxide-graph-v3-") as data:
        evaluated = harness.evaluate(tasks, Path(data))
    rows = [
        {k: r[k] for k in ("task", "split", "corpus", "gold", "route", "compat", "trace")}
        for r in evaluated["results"]
    ]
    out.write_text(json.dumps(rows, indent=1) + "\n")


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
