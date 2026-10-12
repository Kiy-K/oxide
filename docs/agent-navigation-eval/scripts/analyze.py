#!/usr/bin/env python3
"""Per-run metrics, paired A/B/C/D comparisons and the preregistered gates
(docs/agent-navigation-eval/preregistration.md).

  analyze.py SPLIT [REP...]   -> results/SPLIT-runs.json (raw rows) and a markdown report on stdout

Stdlib only. Paired unit: the task (repetitions are averaged within a task first).
"""
import json
import math
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import DATA, HERE, VARIANTS, tasks  # noqa: E402

BOOT, SEED = 10_000, 0x0A9E47
TOKEN = re.compile(r"[\s'\"`;|&<>(){}=,]+")


def repo_files(task_id):
    src = DATA / "tasks" / task_id / "src"
    return {str(p.relative_to(src)) for p in src.rglob("*") if p.is_file()}


def files_in(command, files, prefix):
    """Repository files a shell command names (a proxy for what the agent read)."""
    found = set()
    for tok in TOKEN.split(command):
        tok = tok.removeprefix(prefix).removeprefix("./")
        tok = re.sub(r":\d+(-\d+)?(:\d+)?$", "", tok)  # path:line[:col]
        if tok in files:
            found.add(tok)
    return found


def patched_files(patch):
    return set(re.findall(r"^diff --git a/(\S+) b/", patch, flags=re.M))


def resolved(meta, split):
    v, rep, iid = meta["variant"], meta["rep"], meta["instance_id"]
    report = (DATA / "grading/logs/run_evaluation" / f"agentnav-{v}-r{rep}-{split}"
              / f"{v}-r{rep}" / iid / "report.json")
    if not report.exists():
        return None if meta["patch_bytes"] else False  # an empty patch is unresolved, never graded
    return bool(json.loads(report.read_text())[iid]["resolved"])


def row(run_dir, split, files_cache):
    meta = json.loads((run_dir / "meta.json").read_text())
    tid = meta["task"]
    files = files_cache.setdefault(tid, repo_files(tid))
    gold = json.loads((DATA / "tasks" / tid / "gold.json").read_text())
    gold_files = {g["file"] for g in gold["gold_context"]}
    usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0}
    commands, edits, oxide_calls, inspected, errors = 0, 0, 0, set(), 0
    prefix = str(run_dir / "repo") + "/"
    for line in (run_dir / "events.jsonl").read_text().split("\n"):
        if not line:
            continue
        e = json.loads(line)
        if e["type"] == "turn.completed":
            for k in usage:
                usage[k] += e["usage"].get(k, 0)
        elif e["type"] in ("turn.failed", "error"):
            errors += 1
        elif e["type"] == "item.completed":
            item = e["item"]
            if item["type"] == "command_execution":
                commands += 1
                oxide_calls += "oxide-nav" in item["command"]
                inspected |= files_in(item["command"], files, prefix)
            elif item["type"] == "file_change":
                edits += 1
            elif item["type"] == "mcp_tool_call":
                commands += 1
                oxide_calls += item.get("server") == "oxide"
    patched = patched_files((run_dir / "patch.diff").read_text())
    nav_log = run_dir / "nav-calls.jsonl"
    nav = [json.loads(x) for x in nav_log.read_text().split("\n") if x] if nav_log.exists() else []
    prep = json.loads((DATA / "tasks" / tid / "prep.json").read_text())
    units = {"A": 0, "B": prep["bundle"]["bundle_units"]}.get(meta["variant"], prep["bundle"]["nav_units"])
    return {
        **{k: meta[k] for k in ("task", "instance_id", "variant", "rep", "timed_out", "exit_code", "wall_s")},
        "split": split, "resolved": resolved(meta, split), "errors": errors,
        **usage, "uncached_input_tokens": usage["input_tokens"] - usage["cached_input_tokens"],
        "commands": commands, "edits": edits, "tool_calls": commands + edits, "oxide_calls": oxide_calls,
        "oxide_call_ms": sum(n["total_ms"] for n in nav),
        "oxide_call_peak_rss_kib": max((n["peak_rss_kib"] for n in nav), default=0),
        "oxide_prompt_units": units,
        "inspected_files": len(inspected), "gold_files": len(gold_files),
        "gold_files_inspected": len(inspected & gold_files),
        "gold_file_coverage": len((inspected | patched) & gold_files) / len(gold_files) if gold_files else None,
        "unnecessary_files": len(inspected - gold_files - patched),
        "patched_files": sorted(patched), "patch_touches_gold": bool(patched & gold_files),
    }


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else float("nan")


def median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return float("nan")
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def as_value(r, key):
    return (1.0 if r[key] else 0.0) if key == "resolved" else r[key]  # ungraded counts as unresolved


def per_task(rows, key):
    """task -> variant -> mean of key over repetitions."""
    out = {}
    for r in rows:
        out.setdefault(r["task"], {}).setdefault(r["variant"], []).append(as_value(r, key))
    return {t: {v: mean(xs) for v, xs in d.items()} for t, d in out.items()}


def paired(rows, x, y, key, ratio=False):
    """Mean paired difference x - y, or geometric-mean ratio x / y, with a
    95% percentile bootstrap CI over tasks."""
    pt = per_task(rows, key)
    common = [t for t, d in pt.items() if x in d and y in d]
    if not common:
        return None
    if ratio:
        ds = [math.log(max(pt[t][x], 1) / max(pt[t][y], 1)) for t in common]
        stat = lambda s: math.exp(mean(s))  # noqa: E731
    else:
        ds = [pt[t][x] - pt[t][y] for t in common]
        stat = mean
    rng, n = random.Random(f"{SEED}/{x}/{y}/{key}"), len(ds)
    vals = sorted(stat([ds[rng.randrange(n)] for _ in range(n)]) for _ in range(BOOT))
    return {"n": n, "estimate": stat(ds), "lo": vals[int(0.025 * BOOT)], "hi": vals[int(0.975 * BOOT) - 1]}


def gates(rows, inter):
    """The preregistered decision (preregistration.md § Decision rule) over C and the
    interactive arm `inter` (E on held-out: D's operations as MCP tools)."""
    succ = {v: mean([as_value(r, "resolved") for r in rows if r["variant"] == v]) for v in VARIANTS}
    failed = {v: mean([1.0 if (r["timed_out"] or r["errors"] or r["exit_code"]) else 0.0
                       for r in rows if r["variant"] == v]) for v in VARIANTS}
    reasons, promoted = [], []
    for v in "C" + inter:
        ds = {b: paired(rows, v, b, "resolved") for b in "AB"}
        tok = {b: paired(rows, v, b, "input_tokens", ratio=True) for b in "AB"}
        calls = {b: paired(rows, v, b, "tool_calls", ratio=True) for b in "AB"}
        superior = all(ds[b]["lo"] > 0 for b in "AB")
        non_inferior = all(ds[b]["lo"] >= -0.10 for b in "AB")
        efficient = all(tok[b]["hi"] < 0.85 or calls[b]["hi"] < 0.85 for b in "AB")
        reliable = all(failed[v] <= failed[b] + 0.10 for b in "AB")
        reasons.append(f"{v}: superior={superior} non_inferior={non_inferior} "
                       f"efficient={efficient} reliable={reliable}")
        if reliable and (superior or (non_inferior and efficient)):
            promoted.append(v)
    if all(succ["A"] >= succ[v] for v in "C" + inter):
        reasons.append(f"A >= C and A >= {inter} on success: OXIDE's navigation value has not been demonstrated")
    if all(succ["B"] > succ[v] for v in "C" + inter):
        reasons.append(f"B > C and B > {inter} on success: keep one-shot packing as the preferred mode")
    if promoted:
        return "PROMOTE " + "+".join(promoted), reasons
    no_signal = all(succ[v] <= max(succ["A"], succ["B"])
                    and all(paired(rows, v, b, "input_tokens", ratio=True)["estimate"] >= 0.95 for b in "AB")
                    for v in "C" + inter)
    return ("REJECT" if no_signal else "INCONCLUSIVE"), reasons


def report(rows, split):
    out = [f"## {split}: {len({r['task'] for r in rows})} tasks, reps "
           f"{sorted({r['rep'] for r in rows})}, {len(rows)} runs\n"]
    cols = ["resolved", "input_tokens", "uncached_input_tokens", "output_tokens", "tool_calls",
            "wall_s", "inspected_files", "gold_file_coverage", "unnecessary_files", "oxide_prompt_units",
            "oxide_calls"]
    out.append("Cells: mean (median); resolved and coverage are means.\n")
    out.append("| variant | runs | " + " | ".join(cols) + " | timeouts | ungraded |")
    out.append("|---|---|" + "---|" * len(cols) + "---|---|")
    for v in VARIANTS:
        rs = [r for r in rows if r["variant"] == v]
        if not rs:
            continue
        cells = []
        for c in cols:
            xs = [as_value(r, c) for r in rs]
            cells.append(f"{mean(xs):.3f}" if c in ("resolved", "gold_file_coverage")
                         else f"{mean(xs):.0f} ({median(xs):.0f})")
        out.append(f"| {v} | {len(rs)} | " + " | ".join(cells) +
                   f" | {sum(r['timed_out'] for r in rs)} | {sum(r['resolved'] is None for r in rs)} |")
    out.append("\nPaired by task, 95% bootstrap CI over tasks (success: difference; others: geometric-mean ratio):\n")
    out.append("| pair | success diff | input tokens | tool calls | wall time |")
    out.append("|---|---|---|---|---|")
    for x, y in [("B", "A"), ("C", "A"), ("D", "A"), ("C", "B"), ("D", "B"), ("D", "C"),
                 ("E", "A"), ("E", "B"), ("E", "C")]:
        s = paired(rows, x, y, "resolved")
        if not s:
            continue
        d = lambda p: f"{p['estimate']:+.3f} [{p['lo']:+.3f}, {p['hi']:+.3f}]"  # noqa: E731
        q = lambda p: f"{p['estimate']:.2f} [{p['lo']:.2f}, {p['hi']:.2f}]"  # noqa: E731
        out.append(f"| {x}-{y} (n={s['n']}) | {d(s)} | {q(paired(rows, x, y, 'input_tokens', True))} | "
                   f"{q(paired(rows, x, y, 'tool_calls', True))} | {q(paired(rows, x, y, 'wall_s', True))} |")
    out.append("\nPer task (1 resolved, 0 not, ? ungraded; one character per repetition):\n")
    out.append("| task | " + " | ".join(VARIANTS) + " |")
    out.append("|---|" + "---|" * len(VARIANTS))
    for tid in sorted({r["task"] for r in rows}):
        rs = sorted((r for r in rows if r["task"] == tid), key=lambda r: r["rep"])
        cell = lambda v: "".join("?" if r["resolved"] is None else str(int(r["resolved"]))  # noqa: E731
                                 for r in rs if r["variant"] == v) or "-"
        out.append(f"| {rs[0]['instance_id']} | " + " | ".join(cell(v) for v in VARIANTS) + " |")
    ungraded = sum(r["resolved"] is None for r in rows)
    if ungraded:
        out.append(f"\nGates not evaluated: {ungraded} runs are ungraded.")
    else:
        present = {r["variant"] for r in rows}
        inter = "E" if "E" in present and "D" not in present else "D"
        if set("ABC" + inter) <= present:
            verdict, reasons = gates(rows, inter)
            out.append(f"\nGates (C and {inter}): **{verdict}**\n")
            out += [f"- {x}" for x in reasons]
    return "\n".join(out)


def selftest():
    """Synthetic rows: a clearly better C promotes; identical arms reject."""
    def rows(succ):
        return [{"task": f"t{i}", "variant": v, "rep": 1, "resolved": succ(v, i), "input_tokens": 1000,
                 "tool_calls": 10, "wall_s": 10, "timed_out": False, "errors": 0, "exit_code": 0}
                for i in range(30) for v in "ABCE"]
    assert gates(rows(lambda v, i: v == "C" or i < 10), "E")[0] == "PROMOTE C"
    assert gates(rows(lambda v, i: i < 10), "E")[0] == "REJECT"
    print("selftest ok")


def main():
    if sys.argv[1] == "--selftest":
        return selftest()
    split, reps = sys.argv[1], [int(x) for x in sys.argv[2:]] or [1]
    ids = {t["id"] for t in tasks() if t["split"] == split}
    files_cache, rows = {}, []
    for rep in reps:
        for v in VARIANTS:
            for meta in sorted((DATA / "runs" / f"r{rep}" / v).glob("*/meta.json")):
                if meta.parent.name in ids:
                    rows.append(row(meta.parent, split, files_cache))
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / f"{split}-runs.json").write_text(json.dumps(rows, indent=1) + "\n")
    print(report(rows, split))


if __name__ == "__main__":
    main()
