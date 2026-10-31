"""Criteria and tables (preregistration.md § Acceptance criteria).

analyze.py     results/{agent,sizes,mcp,live}.json -> results/report.json
"""

from __future__ import annotations

import json
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

RESULTS = Path(__file__).resolve().parents[1] / "results"
COMPLETE = ("full", "exhaust")
SAMPLES = 10_000


def load(name: str) -> Any:
    return json.loads((RESULTS / name).read_text())


def task(row: dict[str, Any]) -> str:
    return str(row["id"]).split(":")[0]


def said_cut(row: dict[str, Any]) -> bool | None:
    got = (row.get("answers") or {}).get("q1") if row["valid"] else None
    if isinstance(got, str):
        got = {"true": True, "false": False}.get(got.strip().lower())
    return got if isinstance(got, bool) else None


def detection(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cut = [r for r in rows if r["condition"] not in COMPLETE]
    whole = [r for r in rows if r["condition"] in COMPLETE]
    tp = sum(said_cut(r) is True for r in cut)
    fp = sum(said_cut(r) is True for r in whole)
    recall = tp / len(cut) if cut else None
    specificity = sum(said_cut(r) is False for r in whole) / len(whole) if whole else None
    both = recall is not None and specificity is not None
    return {
        "cut_calls": len(cut), "complete_calls": len(whole),
        "recall": recall, "specificity": specificity,
        "balanced": (recall + specificity) / 2 if both else None,  # type: ignore[operator]
        "precision": tp / (tp + fp) if tp + fp else None,
        "false_completeness": sum(said_cut(r) is False for r in cut) / len(cut) if cut else None,
        "false_alarm": fp / len(whole) if whole else None,
        "no_answer": sum(said_cut(r) is None for r in rows),
    }  # fmt: skip


def accuracy(rows: list[dict[str, Any]], questions: tuple[int, ...]) -> float | None:
    flat = [r["correct"][q] for r in rows for q in questions if q < len(r["correct"])]
    return sum(flat) / len(flat) if flat else None


def bootstrap(rows: list[dict[str, Any]], x: str, y: str, stat: Any) -> dict[str, Any]:
    """stat(x rows) - stat(y rows), point and 95 % CI, resampling tasks."""
    by_task: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["arm"] in (x, y):
            by_task[task(r)][r["arm"]].append(r)
    units = [v for v in by_task.values() if v[x] and v[y]]

    def point(sample: list[dict[str, list[dict[str, Any]]]]) -> float | None:
        a, b = stat([r for u in sample for r in u[x]]), stat([r for u in sample for r in u[y]])
        return None if a is None or b is None else a - b

    rng = random.Random(20261021)
    boot = sorted(d for d in (point(rng.choices(units, k=len(units))) for _ in range(SAMPLES))
                  if d is not None)  # fmt: skip
    diff = point(units)
    ci = [round(boot[int(0.025 * len(boot))], 4), round(boot[int(0.975 * len(boot)) - 1], 4)]
    return {"tasks": len(units), "diff": None if diff is None else round(diff, 4),
            "ci95": ci if boot else None}  # fmt: skip


def billed(rows: list[dict[str, Any]], x: str, y: str) -> float | None:
    """Median paired overhead in billed prompt tokens, x vs y, on complete outputs."""
    tokens = {(r["id"], r["rep"], r["arm"]): r["prompt_tokens"] for r in rows
              if r["prompt_tokens"] and r["condition"] in COMPLETE}  # fmt: skip
    pairs = [tokens[(i, rep, x)] / t - 1 for (i, rep, arm), t in tokens.items()
             if arm == y and (i, rep, x) in tokens]  # fmt: skip
    return round(statistics.median(pairs), 4) if pairs else None


def rnd(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, dict):
        return {k: rnd(v) for k, v in value.items()}
    return value


def failures(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-failure attribution of q1: wrong answers by arm, condition and what was said."""
    out: dict[str, Counter[str]] = defaultdict(Counter)
    for r in rows:
        if not r["correct"][0]:
            out[r["arm"]][f"{r['condition']}:said={said_cut(r)}"] += 1
    return {arm: dict(sorted(c.items())) for arm, c in sorted(out.items())}


def arm_table(rows: list[dict[str, Any]], arms: tuple[str, ...]) -> dict[str, Any]:
    conditions = sorted({r["condition"] for r in rows})

    def by(arm: str, q: int) -> dict[str, Any]:
        return {c: rnd(accuracy([r for r in rows if r["arm"] == arm and r["condition"] == c], (q,)))
                for c in conditions}  # fmt: skip

    return {
        arm: {
            "calls": sum(r["arm"] == arm for r in rows),
            "detection": rnd(detection([r for r in rows if r["arm"] == arm])),
            "q1_by_condition": by(arm, 0),
            "q2_more": rnd(accuracy([r for r in rows if r["arm"] == arm], (1,))),
            "q2_by_condition": by(arm, 1),
            "q3_count": rnd(accuracy([r for r in rows if r["arm"] == arm], (2,))),
            "q3_by_condition": by(arm, 2),
            "q4_file_or_copy": rnd(accuracy([r for r in rows if r["arm"] == arm], (3,))),
        }
        for arm in arms
    }


def mode_report(agent: list[dict[str, Any]], kind: str, candidate: str, arms: tuple[str, ...],
                sizes: dict[str, Any], mcp: list[dict[str, Any]]) -> dict[str, Any]:  # fmt: skip
    rows = [r for r in agent if r["kind"] == kind]

    def balanced(rs: list[dict[str, Any]]) -> float | None:
        return detection(rs)["balanced"]

    def task_quality(rs: list[dict[str, Any]]) -> float | None:
        return accuracy(rs, (2, 3))

    def more_reading(rs: list[dict[str, Any]]) -> float | None:
        return accuracy(rs, (1,))

    nav = [r for r in agent if r["kind"] == "nav"]
    nav_scores = {arm: {m: rnd(statistics.mean(r[m] for r in nav if r["arm"] == arm))
                        for m in ("hit", "file_recall")}
                  for arm in arms if any(r["arm"] == arm for r in nav)}  # fmt: skip
    r1 = bootstrap(rows, candidate, "C", balanced)
    r2 = bootstrap(rows, candidate, "C", more_reading)
    q1 = bootstrap(rows, candidate, "C", task_quality)
    mode = "search" if kind == "search" else "context"
    o200k = sizes["summary"]["search20" if mode == "search" else "context2048"][candidate][
        "overhead_vs_C"
    ]
    bill = billed(rows, candidate, "C")
    calls = [r for r in agent if r["arm"] == candidate
             and (r["kind"] == kind or (mode == "search" and r["kind"] == "nav"))]  # fmt: skip
    invalid = sum(r["failed"] or not r["valid"] or r["tool_ran"] for r in calls)
    served = [r for r in mcp if r["arm"] == candidate and r["mode"] == mode]
    if mode == "search":
        compatible = all(r["structured_equals"] and r["base_equals_api"]
                         and r["text_is_json_of_structured"] for r in served)  # fmt: skip
    else:
        compatible = all(r["base_equals_api"] and r["contains_production"] for r in served)
    p95 = max(s[candidate]["render_p95_ms_max"] for k, s in sizes["summary"].items()
              if k.startswith(mode))  # fmt: skip
    criteria: dict[str, dict[str, Any]] = {
        "R1": r1 | {"pass": r1["diff"] is not None and r1["diff"] >= 0.10 and r1["ci95"][0] > 0},
        "R2": r2
        | {
            "pass": r2["diff"] is not None and r2["diff"] >= -0.02 and r2["ci95"][0] >= -0.08
        },
        "Q1": q1
        | {
            "pass": q1["diff"] is not None and q1["diff"] >= -0.03 and q1["ci95"][0] >= -0.08
        },
        "T": {
            "o200k": o200k,
            "billed": bill,
            "pass": o200k <= 0.02 and bill is not None and bill <= 0.02,
        },
        "V": {
            "calls": len(calls),
            "invalid": invalid,
            "rate": rnd(invalid / len(calls)),
            "pass": invalid / len(calls) <= 0.05,
        },
        "K": {
            "mcp_rows": len(served),
            "compatible": compatible,
            "render_p95_ms": p95,
            "pass": compatible and bool(served) and p95 <= 1,
        },
    }
    if mode == "search":
        deltas = {m: round(nav_scores[candidate][m] - nav_scores["C"][m], 4)
                  for m in ("hit", "file_recall")}  # fmt: skip
        criteria["Q2"] = {"diffs": deltas, "pass": all(d >= -0.05 for d in deltas.values())}
    passes = {k: bool(v["pass"]) for k, v in criteria.items()}
    no_gain = r1["diff"] is None or r1["diff"] < 0.05
    harm = q1["diff"] is not None and q1["diff"] < -0.08
    if all(passes.values()):
        verdict = "PASS"
    elif no_gain or harm or not passes["T"] or not passes["K"]:
        verdict = "REJECT"
    else:
        verdict = "INCONCLUSIVE"
    comparators = {arm: {"R1": bootstrap(rows, arm, "C", balanced),
                         "R2": bootstrap(rows, arm, "C", more_reading),
                         "Q1": bootstrap(rows, arm, "C", task_quality)}
                   for arm in arms if arm not in ("C", candidate)}  # fmt: skip
    return {"candidate": candidate, "verdict": verdict, "passes": passes, "criteria": criteria,
            "arms": arm_table(rows, arms), "navigation": nav_scores if mode == "search" else None,
            "comparators_vs_C": comparators, "failures": failures(rows)}  # fmt: skip


def rate(rs: list[dict[str, Any]], f: Any) -> float | None:
    return round(sum(map(f, rs)) / len(rs), 4) if rs else None


def live_report(live: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in live:
        groups[f"{r['path']}:{r['mode']}:{r['arm']}"].append(r)
    out = {}
    for key, rs in sorted(groups.items()):
        called = [r for r in rs if r["called"]]
        clipped = [r for r in called if r["clipped"]]
        out[key] = {
            "calls": len(rs), "called": len(called),
            "clipped": rate(called, lambda r: r["clipped"]),
            "zero_lines": rate(called, lambda r: any("0 lines" in m for m in r["marker"])),
            "status_seen": rate(called, lambda r: r["status_seen"]),
            "first_seen": rate(called, lambda r: r["first_seen"]),
            "cut_correct": rate(called, lambda r: r["correct"]["cut"]),
            "more_correct": rate(called, lambda r: r["correct"]["more"]),
            "first_file_correct": rate(called, lambda r: r["correct"]["first_file"]),
            "claimed_complete_when_clipped": rate(
                clipped, lambda r: (r["answer"] or {}).get("cut") is False),
            "claimed_cut_when_whole": rate(
                [r for r in called if not r["clipped"]],
                lambda r: (r["answer"] or {}).get("cut") is True),
            "other_tools": rate(rs, lambda r: bool(r["other_tools"])),
            "query_edited": rate(called, lambda r: bool(r["server"].get("query_edited"))),
            "delivered_bytes_median": statistics.median(r["delivered_bytes"] for r in called)
            if called else None,
            "prompt_tokens_median": statistics.median(r["prompt_tokens"] for r in called)
            if called else None,
            "seconds_median": statistics.median(r["seconds"] for r in rs),
        }  # fmt: skip
    return out


def mcp_report(mcp: list[dict[str, Any]]) -> dict[str, Any]:
    exact = all(r["text_equals_render"] and r["structured_equals"] and r["base_equals_api"]
                for r in mcp)  # fmt: skip
    out: dict[str, Any] = {"calls": len(mcp), "all_exact": exact}
    for key in sorted({(r["arm"], r["mode"], r["size"]) for r in mcp}):
        rs = [r for r in mcp if (r["arm"], r["mode"], r["size"]) == key]
        out["/".join(map(str, key))] = {
            "server_limited": rate(rs, lambda r: r["more"]),
            "bytes_max": max(r["bytes"] for r in rs),
            **{f"{p}_{m}": rate(rs, lambda r, p=p, m=m: r[p][m])
               for p in ("direct", "code") for m in ("clipped", "status_kept")},
            "direct_zero_lines": rate(rs, lambda r: r["direct"]["clipped"]
                                      and r["direct"]["kept_bytes"] < 300),
            "seconds_median": round(statistics.median(r["seconds"] for r in rs), 4),
        }  # fmt: skip
    return out


def main() -> None:
    agent, sizes, mcp = load("agent.json"), load("sizes.json"), load("mcp.json")
    live = load("live.json") if (RESULTS / "live.json").exists() else []
    modes = {
        "search": mode_report(agent, "search", "CM", ("C", "CM", "P", "E"), sizes, mcp),
        "context": mode_report(agent, "bundle", "E", ("C", "E"), sizes, mcp),
    }
    verdicts = {m: r["verdict"] for m, r in modes.items()}
    if "PASS" in verdicts.values():
        overall = "PASS"
    elif all(v == "REJECT" for v in verdicts.values()):
        overall = "REJECT"
    else:
        overall = "INCONCLUSIVE"
    units = {k: {arm: v["units_per_o200k"] for arm, v in s.items() if isinstance(v, dict)}
             | {"payload": s.get("payload_units_per_o200k")}
             for k, s in sizes["summary"].items()}  # fmt: skip
    payload_min = min(r["payload"]["units"] / r["payload"]["o200k_base"]
                      for r in sizes["rows"] if r["payload"])  # fmt: skip
    report = {
        "verdict": overall, "verdicts": verdicts, "modes": modes, "calls": len(agent),
        "live": live_report(live), "mcp": mcp_report(mcp), "sizes": sizes["summary"],
        "units_per_o200k": units, "payload_units_per_o200k_min": round(payload_min, 3),
        "latency_median_s": {arm: statistics.median(r["seconds"] for r in agent if r["arm"] == arm)
                             for arm in sorted({r["arm"] for r in agent})},
    }  # fmt: skip
    (RESULTS / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    print(
        json.dumps(
            {
                "verdict": overall,
                "verdicts": verdicts,
                "passes": {m: r["passes"] for m, r in modes.items()},
            },
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
