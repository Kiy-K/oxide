"""analyze.py RESULTS_DIR OUT.json: the preregistered criteria, computed as written.

RESULTS_DIR holds nav.json (graded navigation calls), context/{py,ts}-{dev,heldout}/
(context_run.py output) and extract/ (extract.py reports). Missing inputs leave their
criteria "not run".
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
FROZEN = {"py": REPO / "docs/phase-6/baseline-v9", "ts": REPO / "docs/phase-6/typescript-v6"}
ARMS = ("B", "C")
TOLERANCE = 0.02


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 6) if xs else None


def per_task(rows, metric):
    """{(arm, task): mean over repetitions}; a failed or invalid call scores 0."""
    found = defaultdict(list)
    for r in rows:
        value = 0.0 if r["failed"] or not r["valid"] else r[metric]
        found[(r["arm"], r["task"])].append(value)
    return {k: sum(v) / len(v) for k, v in found.items()}


def navigation(rows) -> dict:
    out: dict = {}
    tasks = {(r["task"], r["bench"], r["split"]) for r in rows}
    for metric in ("recall@4096", "recall@2048", "recall", "gold_file_hit", "precision"):
        scores = per_task(rows, metric)
        table: dict = {}
        for split in ("dev", "heldout"):
            for bench in ("py", "ts", "all"):
                chosen = [t for t, b, s in tasks if s == split and bench in (b, "all")]
                table[f"{split}/{bench}"] = {
                    arm: mean(scores.get((arm, t)) for t in chosen)
                    for arm in ("N", "A", "F", "B", "C")
                }
        out[metric] = table
    # Rep-to-rep noise of A on dev: mean |r1 - r2| per task.
    reps = defaultdict(dict)
    for r in rows:
        if r["arm"] == "A" and r["split"] == "dev":
            reps[r["task"]][r["rep"]] = 0.0 if r["failed"] or not r["valid"] else r["recall@4096"]
    pairs = [abs(v["r1"] - v["r2"]) for v in reps.values() if {"r1", "r2"} <= set(v)]
    out["noise_A_dev"] = mean(pairs)
    scores = per_task(rows, "recall@4096")
    dev = sorted({t for t, _, s in tasks if s == "dev"})
    out["dev_task_deltas"] = {
        arm: {t: round(scores[(arm, t)] - scores[("A", t)], 6) for t in dev if (arm, t) in scores}
        for arm in ARMS
    }
    out["failures"] = {
        arm: mean(
            [1.0 if (r["failed"] or not r["valid"]) else 0.0 for r in rows if r["arm"] == arm]
        )
        for arm in ("N", "A", "F", "B", "C")
    }
    out["units"] = {
        arm: {
            "overview_p50": statistics.median(
                [r["overview_units"] for r in rows if r["arm"] == arm] or [0]
            ),
            "read_p50": statistics.median(
                [r["read_units"] for r in rows if r["arm"] == arm] or [0]
            ),
            "cost_usd": round(sum(r["cost_usd"] or 0 for r in rows if r["arm"] == arm), 4),
        }
        for arm in ("N", "A", "F", "B", "C")
    }
    return out


def nav_criteria(nav) -> dict:
    r = nav["recall@4096"]
    r2 = nav["recall@2048"]
    noise = nav["noise_A_dev"] or 0.0
    verdicts = {}
    for arm in ARMS:
        d = lambda t, k=r: None if k[t][arm] is None else round(k[t][arm] - k[t]["A"], 6)  # noqa: E731
        deltas = nav["dev_task_deltas"][arm].values()
        up = sum(x > 0.10 for x in deltas)
        down = sum(x < -0.10 for x in deltas)
        held = d("heldout/all")
        verdicts[arm] = {
            "N1 dev gain >= 0.10, no benchmark below A": {
                "value": [d("dev/all"), d("dev/py"), d("dev/ts")],
                "pass": d("dev/all") is not None
                and d("dev/all") >= 0.10
                and min(d("dev/py"), d("dev/ts")) >= 0,
            },
            "N2 held-out gain >= 0.05, no benchmark below A - 0.02": {
                "value": [held, d("heldout/py"), d("heldout/ts")],
                "pass": held is not None
                and held >= 0.05
                and min(d("heldout/py"), d("heldout/ts")) >= -TOLERANCE,
            },
            "N3 dev breadth: >= 3 tasks up by 0.10 and up >= 2 x down": {
                "value": [up, down],
                "pass": up >= 3 and up >= 2 * down,
            },
            "N4 dev gain > 2 x A repetition noise": {
                "value": [d("dev/all"), round(2 * noise, 6)],
                "pass": d("dev/all") is not None and d("dev/all") > 2 * noise,
            },
            "N5 recall@2048 >= A - 0.05 (dev and held-out)": {
                "value": [d("dev/all", r2), d("heldout/all", r2)],
                "pass": all(
                    x is not None and x >= -0.05 for x in (d("dev/all", r2), d("heldout/all", r2))
                ),
            },
            "V technical failures <= 10%": {
                "value": nav["failures"][arm],
                "pass": (nav["failures"][arm] or 0) <= 0.10,
            },
        }
    return verdicts


def context(results: Path) -> dict:
    out: dict = {}
    for run in sorted((results / "context").glob("*-*")):
        bench, split = run.name.split("-")
        rows = {f.stem: json.loads(f.read_text()) for f in run.glob("*.*.json")}
        frozen_rows = json.loads((FROZEN[bench] / "results.json").read_text())
        frozen_rows = frozen_rows["results"] if isinstance(frozen_rows, dict) else frozen_rows
        frozen = {r["task"]: r["compat"] for r in frozen_rows}
        a = rows.get("A.U", [])
        report: dict = {
            "A.U equals frozen compat": f"{sum(r['compat'] == frozen.get(r['task']) for r in a)}/{len(a)}",
        }
        for cond in ("U", "H"):
            base = {r["task"]: r for r in rows.get(f"A.{cond}", [])}
            for arm in ("A0", "B", "C"):
                mine = rows.get(f"{arm}.{cond}", [])
                same = sum(
                    r["compat"]["entries"] == base[r["task"]]["compat"]["entries"]
                    and r["compat"]["routed"] == base[r["task"]]["compat"]["routed"]
                    for r in mine
                )
                entry: dict = {"entries+routing identical to A": f"{same}/{len(mine)}"}
                for budget in (256, 1024, 4096):
                    for metric in ("line_recall", "span_recall", "file_recall"):
                        key = f"{metric}@{budget}"
                        vals = [
                            r["verified"]["metrics"][key]
                            - base[r["task"]]["verified"]["metrics"][key]
                            for r in mine
                            if r["verified"]
                        ]
                        entry[f"delta {key}"] = mean(vals)
                    lost = sum(
                        r["verified"]["metrics"][f"line_recall@{budget}"]
                        < base[r["task"]]["verified"]["metrics"][f"line_recall@{budget}"]
                        for r in mine
                        if r["verified"]
                    )
                    entry[f"tasks losing line_recall@{budget}"] = lost
                    if arm in ARMS:
                        entry[f"pointer_recall@{budget}"] = mean(
                            (r["pointer"] or {}).get(str(budget), {}).get("pointer_recall")
                            for r in mine
                        )
                entry["median seconds ratio to A"] = (
                    round(
                        statistics.median([r["seconds"] for r in mine])
                        / statistics.median([r["seconds"] for r in base.values()]),
                        3,
                    )
                    if mine
                    else None
                )
                report[f"{arm}.{cond}"] = entry
        for cond in ("U", "H"):
            report[f"A.{cond} absolute"] = {
                f"line_recall@{b}": mean(
                    r["verified"]["metrics"][f"line_recall@{b}"]
                    for r in rows.get(f"A.{cond}", [])
                    if r["verified"]
                )
                for b in (256, 1024, 4096)
            }
        out[run.name] = report
    return out


def payload_criteria(ctx: dict) -> dict:
    """P: an outline in the payload. Gain > TOLERANCE at some budget/condition, no
    loss > TOLERANCE anywhere, and the gain exceeds A0's (budget freed alone)."""
    verdicts = {}
    for arm in ARMS:
        gains, losses, beats = [], [], []
        for run, report in ctx.items():
            if not run.endswith("dev"):
                continue
            for cond in ("U", "H"):
                for b in (1024, 4096):
                    x = report[f"{arm}.{cond}"][f"delta line_recall@{b}"]
                    a0 = report[f"A0.{cond}"][f"delta line_recall@{b}"]
                    gains.append(x)
                    losses.append(x)
                    beats.append(x > a0 + TOLERANCE)
        verdicts[arm] = {
            "value": {"max gain": max(gains, default=None), "min": min(losses, default=None)},
            "pass": bool(gains)
            and max(gains) > TOLERANCE
            and min(losses) >= -TOLERANCE
            and any(beats),
        }
    return verdicts


def extraction(results: Path) -> dict:
    out: dict = {}
    conf = results / "extract/conformance.json"
    if conf.exists():
        out["conformance"] = json.loads(conf.read_text())["totals"]
    for f in sorted((results / "extract").glob("corpus-*.json")):
        r = json.loads(f.read_text())
        out[r["corpus"]] = {
            k: r[k]
            for k in (
                "totals",
                "failures",
                "imports",
                "provenance",
                "nondeterministic",
                "seconds",
                "units",
                "index_seconds",
                "astgrep_max_rss_mb",
            )
        }
        out[r["corpus"]]["top classes"] = dict(list(r["classes"].items())[:12])
    return out


def extraction_criteria(ext: dict) -> dict:
    verdicts = {}
    corpora = [v for k, v in ext.items() if k != "conformance"]
    for arm in ARMS:
        conf = ext.get("conformance", {}).get(arm, {})
        prov = [c["provenance"][arm] for c in corpora]
        files = sum(c["totals"].get("files", 0) for c in corpora)
        failed = sum(n for c in corpora for k, n in c["failures"].items() if k.startswith(arm))
        named = sum(p.get("named", 0) for p in prov)
        verdicts[arm] = {
            "E1 conformance recall >= 0.90": {
                "value": round(conf["found"] / conf["required"], 6) if conf else None,
                "pass": bool(conf) and conf["found"] / conf["required"] >= 0.90,
            },
            "E2 ranges inside file 100%, name in range >= 99%": {
                "value": [
                    sum(p.get("inside", 0) for p in prov),
                    sum(p.get("entries", 0) for p in prov),
                    sum(p.get("name in range", 0) for p in prov),
                    named,
                ],
                "pass": all(p.get("inside", 0) == p.get("entries", 0) for p in prov)
                and (named == 0 or sum(p.get("name in range", 0) for p in prov) / named >= 0.99),
            },
            "E3 deterministic": {
                "value": sum(len(c["nondeterministic"][arm]) for c in corpora),
                "pass": all(not c["nondeterministic"][arm] for c in corpora),
            },
            "E4 failures <= 1% of parsed files": {
                "value": [failed, files],
                "pass": files > 0 and failed / files <= 0.01,
            },
        }
    return verdicts


def ops_criteria(ext: dict, ctx: dict) -> dict:
    corpora = [v for k, v in ext.items() if k != "conformance"]
    p95 = {
        arm: max((c["seconds"][arm]["p95"] or 0) for c in corpora) if corpora else None
        for arm in ARMS
    }
    ratio = {
        arm: max(
            (r[f"{arm}.{c}"]["median seconds ratio to A"] or 0)
            for r in ctx.values()
            for c in ("U", "H")
        )
        if ctx
        else None
        for arm in ARMS
    }
    rss = max((c["astgrep_max_rss_mb"] for c in corpora), default=None)
    return {
        "C": {
            "O1 per-file p95 <= 0.050 s": {
                "value": p95["C"],
                "pass": p95["C"] is not None and p95["C"] <= 0.050,
            },
            "O2 context latency median <= 1.25 x A": {
                "value": ratio["C"],
                "pass": ratio["C"] is not None and ratio["C"] <= 1.25,
            },
        },
        "B": {
            "O1 per-file p95 <= 0.100 s": {
                "value": p95["B"],
                "pass": p95["B"] is not None and p95["B"] <= 0.100,
            },
            "O2 context latency median <= 1.25 x A": {
                "value": ratio["B"],
                "pass": ratio["B"] is not None and ratio["B"] <= 1.25,
            },
            "O3 ast-grep peak RSS <= 256 MB": {
                "value": rss,
                "pass": rss is not None and rss <= 256,
            },
        },
    }


def main() -> None:
    results, out = Path(sys.argv[1]), Path(sys.argv[2])
    report: dict = {}
    if (results / "nav.json").exists():
        report["navigation"] = navigation(json.loads((results / "nav.json").read_text()))
        report["N"] = nav_criteria(report["navigation"])
    if (results / "context").exists():
        report["context"] = context(results)
        report["P"] = payload_criteria(report["context"])
    if (results / "extract").exists():
        report["extraction"] = extraction(results)
        report["E"] = extraction_criteria(report["extraction"])
        report["O"] = ops_criteria(report["extraction"], report.get("context", {}))
    arms = {}
    for arm in ARMS:
        groups = [report.get(k, {}).get(arm, {}) for k in ("N", "E", "O")]
        checks = [c["pass"] for g in groups for c in g.values()]
        arms[arm] = "PASS" if checks and all(checks) else "FAIL"
    report["arms"] = arms
    out.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({k: report.get(k) for k in ("arms", "N", "P")}, indent=1))


if __name__ == "__main__":
    main()
