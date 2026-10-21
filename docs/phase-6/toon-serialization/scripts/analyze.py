"""Criteria and tables (preregistration.md § Acceptance criteria).

analyze.py     results/{sizes,agent,conformance,package}.json -> results/report.json
"""

from __future__ import annotations

import json
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

RESULTS = Path(__file__).resolve().parents[1] / "results"
SEARCH = ("search20", "search100", "edge_search20", "edge_empty")
BUNDLES = ("bundle", "edge_bundle")
TOKENS = "o200k_base"


def load(name: str) -> Any:
    return json.loads((RESULTS / name).read_text())


def saving(rows: list[dict[str, Any]], a: str, b: str, tok: str = TOKENS) -> float:
    return statistics.median(1 - r["sizes"][a][tok] / r["sizes"][b][tok] for r in rows)


def per_case(
    agent: list[dict[str, Any]], arm: str, kinds: tuple[str, ...]
) -> dict[str, list[bool]]:
    out: dict[str, list[bool]] = defaultdict(list)
    for r in agent:
        if r["arm"] == arm and r["kind"] in kinds and "correct" in r:
            out[r["id"]] += r["correct"]
    return out


def diff(agent: list[dict[str, Any]], pairs: list[tuple[str, str, tuple[str, ...]]],
         samples: int = 10_000) -> dict[str, Any]:  # fmt: skip
    """Question-weighted accuracy difference (x - y) over paired cases, with a case bootstrap."""
    units: list[tuple[list[bool], list[bool]]] = []
    for x, y, kinds in pairs:
        xs, ys = per_case(agent, x, kinds), per_case(agent, y, kinds)
        units += [(xs[c], ys[c]) for c in sorted(xs.keys() & ys.keys())]
    if not units:
        return {"cases": 0, "diff": None}

    def point(sample: list[tuple[list[bool], list[bool]]]) -> float:
        nx, ny = sum(len(a) for a, _ in sample), sum(len(b) for _, b in sample)
        return sum(sum(a) for a, _ in sample) / nx - sum(sum(b) for _, b in sample) / ny

    rng = random.Random(20261021)
    boot = sorted(point(rng.choices(units, k=len(units))) for _ in range(samples))
    return {
        "cases": len(units),
        "x_accuracy": round(sum(sum(a) for a, _ in units) / sum(len(a) for a, _ in units), 4),
        "y_accuracy": round(sum(sum(b) for _, b in units) / sum(len(b) for _, b in units), 4),
        "diff": round(point(units), 4),
        "ci95": [round(boot[int(0.025 * samples)], 4), round(boot[int(0.975 * samples) - 1], 4)],
    }


def accuracy(agent: list[dict[str, Any]], arm: str, kinds: tuple[str, ...]) -> float | None:
    flat = [
        c for r in agent if r["arm"] == arm and r["kind"] in kinds for c in r.get("correct", [])
    ]
    return round(sum(flat) / len(flat), 4) if flat else None


def provider(agent: list[dict[str, Any]], x: str, y: str,
             kinds: tuple[str, ...] | None = None) -> float | None:  # fmt: skip
    """Median per-call cut in provider-reported prompt tokens, x vs y, same case and rep."""
    tokens = {(r["id"], r["rep"], r["arm"]): r["prompt_tokens"] for r in agent
              if r["prompt_tokens"] and (kinds is None or r["kind"] in kinds)}  # fmt: skip
    cuts = [1 - tokens[(i, rep, x)] / t for (i, rep, arm), t in tokens.items()
            if arm == y and (i, rep, x) in tokens]  # fmt: skip
    return round(statistics.median(cuts), 4) if cuts else None


def main() -> None:
    sizes, agent = load("sizes.json"), load("agent.json")
    conformance, package = load("conformance.json"), load("package.json")
    kinds = sorted({r["kind"] for r in sizes})
    by_kind = {k: [r for r in sizes if r["kind"] == k] for k in kinds}
    bundles = [r for r in sizes if r["kind"] in BUNDLES]

    size_table = {
        k: {
            "n": len(rs),
            "C_o200k_median": statistics.median(r["sizes"]["C"][TOKENS] for r in rs),
            **{
                f"{a}_vs_{b}": round(saving(rs, a, b), 4)
                for a, b in (("T", "C"), ("Ttab", "C"), ("P", "C"), ("T", "P"))
            },  # fmt: skip
            "T_vs_C_cl100k": round(saving(rs, "T", "C", "cl100k_base"), 4),
            "T_vs_C_bytes": round(saving(rs, "T", "C", "bytes"), 4),
            "T_vs_C_units": round(saving(rs, "T", "C", "units"), 4),
        }
        for k, rs in by_kind.items()
    }
    envelope = {f"{a}_vs_{b}": round(saving(bundles, a, b), 4)
                for a, b in (("ET", "EC"), ("ETtab", "EC"), ("HT", "HC"), ("HC", "M"))}  # fmt: skip

    nav = [r for r in agent if r["kind"] == "nav"]
    nav_scores = {
        arm: {m: round(statistics.mean(r[m] for r in nav if r["arm"] == arm), 4)
              for m in ("hit", "file_recall")}
        for arm in ("C", "T", "P") if any(r["arm"] == arm for r in nav)
    }  # fmt: skip
    declared: dict[str, int] = defaultdict(int)  # T replies that report the stale [N]
    for r in agent:
        if r["kind"] == "truncated" and r.get("answers"):
            got = r["answers"].get("q1")
            declared[f"{r['arm']}_calls"] += 1
            declared[f"{r['arm']}_reported_declared_N"] += got in (100, 9)
    validity = {}
    for arm in sorted({r["arm"] for r in agent}):
        rs = [r for r in agent if r["arm"] == arm]
        bad = sum(r["failed"] or not r["valid"] or r["tool_ran"] for r in rs)
        validity[arm] = {"calls": len(rs), "invalid": bad, "rate": round(bad / len(rs), 4)}
    latency = {arm: round(statistics.median(r["seconds"] for r in agent if r["arm"] == arm), 2)
               for arm in validity}  # fmt: skip
    timing = {
        k: {a: round(max(r["timing"][a]["p95_ms"] for r in rs), 3) for a in ("C", "P", "T", "Ttab")}
        for k, rs in by_kind.items() if all("timing" in r for r in rs)
    }  # fmt: skip
    per_kind = {
        k: {arm: accuracy(agent, arm, (k,)) for arm in ("C", "T", "P", "HC", "HT")} for k in kinds
    }
    kind_diffs = {k: diff(agent, [("T", "C", (k,))], 2000)["diff"] for k in kinds}
    kind_diffs |= {f"{k}:HT-HC": diff(agent, [("HT", "HC", (k,))], 2000)["diff"] for k in BUNDLES}

    a1 = diff(agent, [("T", "C", SEARCH), ("HT", "HC", BUNDLES)])
    a4 = diff(agent, [("T", "C", ("truncated",))])
    roundtrip = all(all(r["round_trip"].values()) for r in sizes if "round_trip" in r)
    extras_only = all("extra ==" in r for r in package["requires"] or [])
    s2 = provider(agent, "T", "C")
    criteria: dict[str, dict[str, Any]] = {
        "S1": {
            "search20_T_vs_C": size_table["search20"]["T_vs_C"],
            "envelope_ET_vs_EC": envelope["ET_vs_EC"],
            "pass": size_table["search20"]["T_vs_C"] >= 0.10 and envelope["ET_vs_EC"] >= 0.10,
        },
        "S2": {
            "all": s2,
            "search": provider(agent, "T", "C", SEARCH),
            "envelope_HT_vs_HC": provider(agent, "HT", "HC"),
            "pass": s2 is not None and s2 >= 0.05,
        },
        "A1": a1
        | {
            "pass": a1["diff"] is not None and a1["diff"] >= -0.02 and a1["ci95"][0] >= -0.05
        },  # fmt: skip
        "A2": {
            "diffs": kind_diffs,
            "pass": all(d is None or d >= -0.05 for d in kind_diffs.values()),
        },  # fmt: skip
        "A3": nav_scores
        | {
            "pass": all(
                nav_scores["T"][m] - nav_scores["C"][m] >= -0.05 for m in ("hit", "file_recall")
            )
        },
        "A4": a4 | dict(declared) | {"pass": a4["diff"] is not None and a4["diff"] >= -0.10},
        "R1": {
            "round_trip": roundtrip,
            "fixtures": f"{conformance['passed']}/{conformance['tests']}",
            "pass": roundtrip and not conformance["failed"],
        },  # fmt: skip
        "R2": {
            "search20_max_p95_ms": timing["search20"]["T"],
            "extras_only": extras_only,
            "pass": timing["search20"]["T"] <= 5 and extras_only,
        },  # fmt: skip
        "V": {"arms": validity, "pass": all(v["rate"] <= 0.05 for v in validity.values())},
    }
    passes = {k: bool(v["pass"]) for k, v in criteria.items()}
    if all(passes.values()):
        verdict = "PASS"
    elif (not passes["S1"] or (a1["diff"] is not None and a1["diff"] < -0.05)
          or not passes["A2"] or not passes["A3"] or not passes["R1"]):  # fmt: skip
        verdict = "REJECT"
    else:
        verdict = "INCONCLUSIVE"
    report = {
        "verdict": verdict,
        "passes": passes,
        "criteria": criteria,
        "sizes": size_table,
        "envelope": envelope,
        "accuracy_per_kind": per_kind,
        "latency_median_s": latency,
        "encode_p95_ms_max": timing,
        "package": package,
        "calls": len(agent),
        "cost_usd": round(sum(r["cost_usd"] or 0 for r in agent), 4),
        "prompt_tokens_median": {
            arm: statistics.median(
                r["prompt_tokens"] for r in agent if r["arm"] == arm and r["prompt_tokens"]
            )
            for arm in validity
        },
    }
    (RESULTS / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
