"""Decision Study v1 harness (preregistration.md). No network; src/ is unchanged.

    uv run --with tiktoken python docs/phase-6/decision-study-v1/scripts/study.py OUT \
        [NAME=JUDGMENTS.json ...] [--split heldout --gate DEV_GATE.txt]

Runs the unchanged scorer (``cb.score``) over Python ContextBench dev, records every
``packing.pack`` call, and re-packs the same pool at every budget with each arm as a
``CapsuleJudge`` adapter: heuristic (must reproduce production exactly), the two graph-v3
oracles, seeded random and permutation controls, and any recorded JEV judgments (by
capsule digest, from ``adapter.py``) with their permutations.

Writes OUT/requests/<task>.json (the capsules adapter.py may send; no gold),
OUT/results.json (per task and arm: recall, used, candidate values and labels),
OUT/costs.json (rendered tokens per renderer) and OUT/truncation.json.
"""

from __future__ import annotations

import json
import random
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "tests" / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import contextbench as cb
import render
import tiktoken

from oxide.core import capsule, packing, retrieval

SEEDS = range(5)
DEADLINE = 61.0  # the adapter's hard kill (60 s) fires first
ENCODINGS = {n: tiktoken.get_encoding(n) for n in ("o200k_base", "cl100k_base")}
JEV_REQUEST, JEV_QUESTION = 64_000, 32_000  # default context limits, tokens

real_pack, real_retrieve, real_score = packing.pack, retrieval.retrieve, cb.score
state: dict[str, Any] = {}
calls: dict[int, tuple[Any, ...]] = {}
recorded: dict[str, dict[str, Any]] = {}  # arm name -> judgment file
out_dir = Path()
rows: list[dict[str, Any]] = []
costs: list[dict[str, Any]] = []
truncation: list[dict[str, Any]] = []


def retrieve(view, query, hints, **kw):  # type: ignore[no-untyped-def]
    state["query"], state["derivation"] = query, view.snapshot.derivation_id
    return real_retrieve(view, query, hints, **kw)


def pack(pool, scopes, budget, source, declarations, judge=None):  # type: ignore[no-untyped-def]
    found = real_pack(pool, scopes, budget, source, declarations, judge)
    calls[budget] = (pool, scopes, source, declarations, found)
    return found


def covered(items: list[Any]) -> set[tuple[str, int]]:
    return {(i.source.path, n) for i in items for n in range(i.lines[0], i.lines[1] + 1)}


def holders(pool: list[Any], lines: Any, task: dict[str, Any]) -> tuple[dict[str, int], set[Any]]:
    """graph-v3's holder rule: pool key -> verified gold lines it carries; all gold lines."""
    spans = {
        c.entity.key: (c.entity.category, lines.span(c.entity.source))
        for c in pool
        if c.entity.source is not None
    }
    carried: dict[str, set[tuple[str, int]]] = {}
    gold: set[tuple[str, int]] = set()
    for g in (g for g in task["gold"] if g["verified"]):
        code = lines.code(g["file"], g["start"], g["end"])
        module = code & lines.module(g["file"])
        for n in code:
            gold.add((g["file"], n))
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
                carried.setdefault(holder, set()).add((g["file"], n))
    return {k: len(v) for k, v in carried.items()}, gold


def arms(task: str, by_key: dict[str, Any], gold: dict[str, int]) -> dict[str, Any]:
    """Each arm as a capsule adapter (capsules -> {digest: value or None})."""

    def heuristic(caps):  # type: ignore[no-untyped-def]
        return {c.digest: packing.value(by_key[c.key])[0] for c in caps}

    def oracle(floor: bool):  # type: ignore[no-untyped-def]
        def judge(caps):  # type: ignore[no-untyped-def]
            return {
                c.digest: 0.99
                if c.key in gold
                and (not floor or packing.value(by_key[c.key])[0] >= packing.INCLUDE_FLOOR)
                else None
                for c in caps
            }

        return judge

    def rand(seed: int):  # type: ignore[no-untyped-def]
        return lambda caps: {c.digest: random.Random(f"{seed}:{c.digest}").random() for c in caps}

    def permuted(base, seed: int):  # type: ignore[no-untyped-def]
        def judge(caps):  # type: ignore[no-untyped-def]
            found = base(caps)
            keys = [c.digest for c in caps if c.digest in found]
            values = [found[k] for k in keys]
            random.Random(f"{seed}:{task}").shuffle(values)
            return dict(zip(keys, values, strict=True))

        return judge

    out: dict[str, Any] = {
        "heuristic": heuristic,
        "oracle_order": oracle(True),
        "oracle_pick": oracle(False),
    }
    out |= {f"random_s{s}": rand(s) for s in SEEDS}
    out |= {f"perm-heuristic_s{s}": permuted(heuristic, s) for s in SEEDS}
    for name, data in recorded.items():
        model = capsule.Recorded(data["judgments"])
        out[name] = model
        out |= {f"perm-{name}_s{s}": permuted(model, s) for s in SEEDS}
    return out


def mentions(query: str, names: set[str]) -> str:
    """Where the first mention of a name falls: within the 1,024-byte clip, beyond, or absent."""
    data = query.encode()
    ends = [
        m.end()
        for n in names
        if n
        for m in re.finditer(rb"(?<![\w])" + re.escape(n.encode()) + rb"(?![\w])", data)
    ]
    if not ends:
        return "absent"
    return "within" if min(ends) <= capsule.MAX_QUERY_BYTES else "beyond"


def identifiers(key: str) -> set[str]:
    """A candidate's name (last qualified segment) and its file stem."""
    path = key.split(":", 1)[1].split("#", 1)
    stem = Path(path[0]).stem
    return {path[1].rsplit(".", 1)[-1], stem} if len(path) > 1 else {stem}


def measure_costs(task: str, caps: list[capsule.Capsule], query: str) -> None:
    variants: list[tuple[str, bool, str | None]] = [
        (n, r, None) for n in render.RENDERERS for r in (True, False)
    ]
    variants.append(("shared", True, query))
    for name, rank, full in variants:
        st = render.render(name, caps, rank, full)
        whole, one = render.text(st, len(caps)), render.text(st, 1)
        row: dict[str, Any] = {"task": task, "renderer": render.version(name, rank, bool(full))}
        row["bytes"] = len(whole.encode())
        for enc, e in ENCODINGS.items():
            row[enc] = len(e.encode(whole))
            row[f"{enc}_per_question"] = len(e.encode(one))
        row["fits_default"] = (
            row["o200k_base"] <= JEV_REQUEST and row["o200k_base_per_question"] <= JEV_QUESTION
        )
        costs.append(row)


def trace(lines: Any, task: dict[str, Any], result: dict[str, Any]) -> None:
    query, derivation = state["query"], state["derivation"]
    pool, _, source, declarations, _ = calls[4096]
    by_key = {c.entity.key: c for c in pool}
    asked = [  # the candidates pack asks a judge about
        c
        for c in pool
        if c.entity.source is not None
        and (c.entity.category == "symbol" or c.entity.key in declarations)
    ]
    gold, gold_lines = holders(pool, lines, task)
    probe = capsule.CapsuleJudge("probe", query, derivation, source, capsule.Recorded({}))
    caps = probe.capsules(asked)
    request = {
        "task": task["id"],
        "corpus": task["corpus"],
        "query": query,
        "capsules": [c.canonical.decode() for c in caps],
    }
    (out_dir / "requests" / f"{task['id']}.json").write_text(json.dumps(request) + "\n")
    measure_costs(task["id"], caps, query)
    where = {c.key: mentions(query, identifiers(c.key)) for c in caps}
    truncation.append(
        {
            "task": task["id"],
            "query_bytes": len(query.encode()),
            "clipped": len(query.encode()) > capsule.MAX_QUERY_BYTES,
            "holders": {k: where.get(k, "not sent") for k in gold},
            "others": {
                w: sum(1 for k, v in where.items() if k not in gold and v == w)
                for w in ("within", "beyond", "absent")
            },
        }
    )
    if not gold_lines:
        return
    row: dict[str, Any] = {"task": task["id"], "gold_lines": len(gold_lines), "arms": {}}
    for name, adapter in arms(task["id"], by_key, gold).items():
        judge = capsule.CapsuleJudge(name, query, derivation, source, adapter, DEADLINE)
        values = packing.judged(judge, asked)
        arm: dict[str, Any] = {
            "candidates": [
                [values[c.entity.key][0], c.entity.key in gold, values[c.entity.key][1]]
                for c in asked
            ]
        }
        for budget in cb.BUDGETS:
            p, s, src, decl, (base, _) = calls[budget]
            items = real_pack(p, s, budget, src, decl, judge)[0]
            used = sum(i.units for i in items)
            touching = sum(i.units for i in items if covered([i]) & gold_lines)
            arm[str(budget)] = {
                "line_recall": round(len(gold_lines & covered(items)) / len(gold_lines), 6),
                "used": used,
                "gold_unit_share": round(touching / used, 6) if used else 0.0,
            }
            assert used <= budget, (task["id"], name, budget)
            if name == "heuristic":  # validity: the heuristic replay is production
                spans = [(i.source, i.lines, i.units) for i in items]  # reasons name the judge
                assert spans == [(i.source, i.lines, i.units) for i in base], (task["id"], budget)
                scored = result["verified"]["metrics"][f"line_recall@{budget}"]
                assert arm[str(budget)]["line_recall"] == scored, (task["id"], budget)
        row["arms"][name] = arm
    rows.append(row)


def score(repo, lines, task):  # type: ignore[no-untyped-def]
    calls.clear()
    result = real_score(repo, lines, task)
    trace(lines, task, result)
    return result


def main(argv: list[str]) -> None:
    global out_dir
    split = argv[argv.index("--split") + 1] if "--split" in argv else "dev"
    if split != "dev":
        gate = Path(argv[argv.index("--gate") + 1]) if "--gate" in argv else None
        assert gate and "DEV GATE: PASS" in gate.read_text(), "held-out is sealed"
    out_dir = Path(argv[0])
    (out_dir / "requests").mkdir(parents=True, exist_ok=True)
    for arg in argv[1:]:
        if "=" in arg:
            name, path = arg.split("=", 1)
            recorded[name] = json.loads(Path(path).read_text())
    retrieval.retrieve, packing.pack, cb.score = retrieve, pack, score
    tasks = cb.load_tasks()
    tasks["tasks"] = [t for t in tasks["tasks"] if t["split"] == split]
    used = {t["corpus"] for t in tasks["tasks"]}
    tasks["corpora"] = {k: v for k, v in tasks["corpora"].items() if k in used}
    with tempfile.TemporaryDirectory(prefix="oxide-ds1-") as data:
        cb.evaluate(tasks, Path(data))
    meta = {n: {k: v for k, v in d.items() if k != "judgments"} for n, d in recorded.items()}
    results = {"split": split, "recorded": meta, "tasks": rows}
    for name, data in (("results", results), ("costs", costs), ("truncation", truncation)):
        (out_dir / f"{name}.json").write_text(json.dumps(data, indent=1) + "\n")
    print(f"{len(rows)} tasks, {len(costs)} cost rows -> {out_dir}")


if __name__ == "__main__":
    main(sys.argv[1:])
