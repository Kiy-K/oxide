#!/usr/bin/env python3
"""Aggregates results.jsonl + judged.jsonl + index_cost.json into RESULTS.md:
every run shown (not just averages), paired per-task median deltas (oxide -
baseline), the indexing-cost table, and an amortized 1/5/20-task view.

Usage:
    eval-agent/.venv/bin/python eval-agent/agentbench/report.py
"""
import json
import statistics
from pathlib import Path

HERE = Path(__file__).parent
RESULTS_PATH = HERE / "results" / "results.jsonl"
JUDGED_PATH = HERE / "results" / "judged.jsonl"
INDEX_COST_PATH = HERE / "index_cost.json"
TASKS_PATH = HERE / "tasks.json"
OUT_PATH = HERE / "RESULTS.md"

METRICS = ["quality", "input_tokens", "tool_calls_total", "unique_files_inspected", "wall_s"]


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def quality(judged: dict | None) -> float | None:
    if not judged or "accuracy" not in judged or "completeness" not in judged:
        return None
    return (judged["accuracy"] + judged["completeness"]) / 2


def median_or_none(vals: list[float]) -> float | None:
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None


def main() -> None:
    tasks = {t["id"]: t for t in json.loads(TASKS_PATH.read_text())}
    results = load_jsonl(RESULTS_PATH)
    judged = {(j["task"], j["condition"], j["rep"]): j for j in load_jsonl(JUDGED_PATH)}
    index_cost = json.loads(INDEX_COST_PATH.read_text()) if INDEX_COST_PATH.exists() else {}

    by_task: dict[str, dict[str, list[dict]]] = {}
    for r in results:
        by_task.setdefault(r["task"], {}).setdefault(r["condition"], []).append(r)
        r["_judged"] = judged.get((r["task"], r["condition"], r["rep"]))
        r["_quality"] = quality(r["_judged"])

    lines = ["# Phase 0 pilot results\n",
             f"{len(results)} runs captured "
             f"({len(set(r['task'] for r in results))} tasks x "
             f"{len(set(r['condition'] for r in results))} conditions).\n"]

    total_oxide_calls = sum(r.get("oxide_calls") or 0 for r in results if r["condition"] == "oxide")
    if total_oxide_calls == 0:
        lines.append(
            "> **Headline finding: the agent never called the OXIDE MCP tool.** "
            "Across all 18 \"oxide\"-condition runs, `oxide_calls` is 0 -- the tool was "
            "available (verified working in a separate smoke test) but gpt-5.6-luna via "
            "Codex never chose to use it on any of these 6 tasks, preferring its native "
            "shell tools (`rg`, `cat`, `sed`) throughout. Every delta below is therefore "
            "run-to-run noise from re-running the same underlying agent behavior twice, "
            "**not an effect of OXIDE** -- this pilot did not exercise OXIDE's value "
            "proposition at all under this exact setup. See Methodology problems.\n"
        )

    lines.append("## Per-task, all runs\n")
    for task_id, conds in by_task.items():
        lines.append(f"### {task_id} ({tasks.get(task_id, {}).get('shape', '?')})\n")
        lines.append("| condition | rep | quality (acc+comp/2) | grounded | input_tokens | "
                      "tool_calls | oxide_calls | files | wall_s | error |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for cond in sorted(conds):
            for r in sorted(conds[cond], key=lambda x: x["rep"]):
                j = r["_judged"] or {}
                lines.append(
                    f"| {cond} | {r['rep']} | {r['_quality']} | {j.get('grounded')} | "
                    f"{r.get('input_tokens')} | {r.get('tool_calls_total')} | "
                    f"{r.get('oxide_calls')} | {r.get('unique_files_inspected')} | "
                    f"{r.get('wall_s')} | {r.get('error') is not None} |"
                )
        lines.append("")

    # Paired per-task median deltas (oxide - baseline), then median-of-medians overall.
    per_task_deltas: dict[str, list[float | None]] = {m: [] for m in METRICS}
    lines.append("## Paired per-task median deltas (oxide - baseline)\n")
    lines.append("| task | " + " | ".join(METRICS) + " |")
    lines.append("|---|" + "---|" * len(METRICS))
    for task_id, conds in by_task.items():
        base = conds.get("baseline", [])
        oxi = conds.get("oxide", [])
        row = [task_id]
        for m in METRICS:
            key = "_quality" if m == "quality" else m
            b = median_or_none([r.get(key) for r in base])
            o = median_or_none([r.get(key) for r in oxi])
            delta = (o - b) if (o is not None and b is not None) else None
            per_task_deltas[m].append(delta)
            row.append(f"{delta:+.1f}" if delta is not None else "n/a")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    lines.append("## Overall (median of per-task deltas)\n")
    overall = {m: median_or_none(per_task_deltas[m]) for m in METRICS}
    regressions = [m for m, v in overall.items() if v is not None and v < 0 and m != "wall_s"]
    regressions += [m for m in ("wall_s", "input_tokens", "tool_calls_total") if overall.get(m) and overall[m] > 0]
    if regressions:
        lines.append(f"**Regressions to look at first:** {', '.join(sorted(set(regressions)))}\n")
    lines.append("| metric | median delta (oxide - baseline) |")
    lines.append("|---|---|")
    for m in METRICS:
        v = overall[m]
        lines.append(f"| {m} | {f'{v:+.2f}' if v is not None else 'n/a'} |")
    lines.append("")

    lines.append("## Indexing cost (one-time, excluded from agent-time comparisons above)\n")
    lines.append("| repo | wall_s | peak_rss_kb | index_db_bytes | symbols |")
    lines.append("|---|---|---|---|---|")
    for repo, c in index_cost.items():
        symbols = c.get("status", {}).get("symbols")
        lines.append(f"| {repo} | {c.get('wall_s')} | {c.get('peak_rss_kb')} | "
                      f"{c.get('index_db_bytes')} | {symbols} |")
    lines.append("\n_Repos not listed above were already indexed from prior OXIDE eval work "
                  "on this machine -- their cold-index cost was not re-measured (would require "
                  "deleting and rebuilding a perfectly good index for no benefit but a number)._\n")

    total_index_s = sum(c.get("wall_s", 0) or 0 for c in index_cost.values())
    avg_agent_s = median_or_none([r["wall_s"] for r in results if r.get("condition") == "oxide"]) or 0
    lines.append("## Amortized end-to-end view (indexing cost spread over N tasks)\n")
    lines.append("| tasks | one-time index cost | + agent time (median oxide run) | amortized index_s/task |")
    lines.append("|---|---|---|---|")
    for n in (1, 5, 20):
        lines.append(f"| {n} | {total_index_s:.0f}s | {avg_agent_s:.0f}s | {total_index_s / n:.1f}s |")
    lines.append("")

    lines.append("## Methodology problems discovered\n")
    if total_oxide_calls == 0:
        lines.append("- **The core comparison never happened.** OXIDE was available but unused "
                      "in every oxide-condition run (see headline finding above) -- these 6 "
                      "tasks, on well-known/memorized open-source code, apparently didn't read "
                      "as needing discovery tools to this model via Codex. Candidate fixes for "
                      "a next pass: tasks against less-memorized/newer/larger repos where "
                      "the model can't answer from pretraining alone, an explicit mention in "
                      "the system/task framing that a code-search tool is available (still "
                      "without naming or forcing OXIDE specifically), or trying a model/harness "
                      "combination less biased toward its own native shell tools.")
    lines.append("- Run order was NOT randomized/interleaved as the brief specified -- "
                  "`run_pilot.py` runs all baseline reps then all oxide reps per task, "
                  "sequentially. A time-of-day or API-load effect could confound condition "
                  "with when it ran; a future pass should interleave.")
    lines.append("- \"Unique files inspected\" under Codex is a regex heuristic over raw shell "
                  "command strings (Codex has no structured read-file tool like opencode's), "
                  "so it under/over-counts relative to a true file-access log.")
    lines.append("- Cold index time/RSS was only measured for tailwindcss (freshly indexed); "
                  "flask/seaborn/darkreader were already indexed from prior OXIDE eval work, "
                  "so their cold-start cost is not in this report.")
    lines.append("- Several tasks are well-known open-source code (Flask's exception handling, "
                  "seaborn's plot pipeline) that a large model may answer correctly from "
                  "pretraining alone without reading the repo at all -- low tool-call counts on "
                  "some baseline runs may reflect memorization, not efficient comprehension. "
                  "A harder future pilot should weight toward less-memorized/newer code.")
    lines.append("- No superiority claim is made from this pilot (6 tasks, single model, "
                  "single machine) -- see the brief.")

    OUT_PATH.write_text("\n".join(lines) + "\n")
    print(f"wrote {OUT_PATH} ({len(results)} runs, {len(judged)} judged)")


if __name__ == "__main__":
    main()
