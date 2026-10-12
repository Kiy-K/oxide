#!/usr/bin/env python3
"""Failure attribution over the frozen held-out runs (follow-up.md § Task 2). Read-only.

For every failed run it measures what evidence the agent was actually shown, separating
the file from the regions inside it:

- edit regions: ContextBench gold_context regions that overlap a gold-patch hunk;
- support regions: the other gold_context regions (callers, helpers, options, tests);
- shown: share of a region's distinctive lines (stripped, >= 12 chars) that appear
  verbatim in the prompt (B's payload included) or in any command output.

Categories (rule stated in follow-up.md):
  integration   non-zero exit, timeout, failed MCP call, empty or unapplied patch
  localization  patch touches no gold file and the edit regions were shown < 0.5
  not_inspected edit regions shown >= 0.5, patch in a gold file, some support region shown < 0.5
  fix_wrong     edit regions shown >= 0.5, patch in a gold file, every support region shown >= 0.5
  indeterminate anything else
Splitting fix_wrong / not_inspected into "misunderstood" vs "incorrect patch despite
sufficient evidence" needs reading the patches; that is done by hand in follow-up.md.

  attribution.py heldout 1 2 3 > results/attribution-heldout.json
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import harness as h  # noqa: E402

GRADING = h.DATA / "grading/logs/run_evaluation"


def hunk_lines(patch):
    """file -> base-file line numbers the gold patch removes, or inserts before."""
    out, f, old = defaultdict(set), None, 0
    for ln in patch.splitlines():
        if ln.startswith("--- "):
            f = ln[6:] if ln.startswith("--- a/") else None
        elif ln.startswith("+++ "):
            continue
        elif ln.startswith("@@"):
            old = int(re.match(r"@@ -(\d+)", ln).group(1))
        elif f and ln[:1] == "-":
            out[f].add(old)
            old += 1
        elif f and ln[:1] == "+":
            out[f].add(old)
        elif f and ln[:1] == " ":
            old += 1
    return out


def distinctive(content):
    return {s for s in (x.strip() for x in content.splitlines()) if len(s) >= 12}


def grading_report(meta):
    inst = meta["instance_id"]
    p = (GRADING / f"agentnav-{meta['variant']}-r{meta['rep']}-heldout" / f"{meta['variant']}-r{meta['rep']}"
         / inst / "report.json")
    return json.loads(p.read_text())[inst] if p.exists() else None


def analyze(run_dir):
    meta = json.loads((run_dir / "meta.json").read_text())
    rep = grading_report(meta)
    if rep is not None and rep["resolved"]:
        return None
    gold = json.loads((h.DATA / "tasks" / meta["task"] / "gold.json").read_text())
    hunks = hunk_lines(gold["patch"])
    shown, files_named, mcp_errors, ran_code = [(run_dir / "prompt.txt").read_text()], set(), 0, False
    for ln in (run_dir / "events.jsonl").read_text().splitlines():
        e = json.loads(ln) if ln.startswith("{") else {}
        it = e.get("item", {})
        if e.get("type") != "item.completed":
            continue
        if it.get("type") == "command_execution":
            shown.append(it.get("aggregated_output") or "")
            files_named |= set(re.findall(r"[\w./-]+\.py", it.get("command", "")))
            ran_code |= bool(re.search(r"pytest|python3? (-c|-m|-)|runtests|<<", it.get("command", "")))
        elif it.get("type") == "mcp_tool_call" and it.get("status") != "completed":
            mcp_errors += 1
    blob = "\n".join(shown)
    regions = []
    for g in gold["gold_context"]:
        lines = distinctive(g["content"])
        edit = any(g["start_line"] <= n <= g["end_line"] + 1 for n in hunks.get(g["file"], ()))
        regions.append({"file": g["file"], "lines": [g["start_line"], g["end_line"]], "edit": edit,
                        "shown": round(sum(s in blob for s in lines) / len(lines), 2) if lines else 1.0})
    patch = (run_dir / "patch.diff").read_text()
    patched = set(re.findall(r"^\+\+\+ b/(\S+)", patch, re.M))
    gold_files = {g["file"] for g in gold["gold_context"]} | set(hunks)
    edit_shown = min([r["shown"] for r in regions if r["edit"]] or [0.0])
    support = [r for r in regions if not r["edit"]]
    in_gold = bool(patched & gold_files)
    applied = rep is not None and rep["patch_successfully_applied"]
    if meta["exit_code"] or meta["timed_out"] or mcp_errors or not patch.strip() or not applied:
        cat = "integration"
    elif not in_gold and edit_shown < 0.5:
        cat = "localization"
    elif in_gold and edit_shown >= 0.5:
        cat = "not_inspected" if any(r["shown"] < 0.5 for r in support) else "fix_wrong"
    else:
        cat = "indeterminate"
    tests = rep["tests_status"] if rep else {"FAIL_TO_PASS": {"success": [], "failure": []},
                                             "PASS_TO_PASS": {"success": [], "failure": []}}
    return {"task": meta["task"], "instance": meta["instance_id"], "variant": meta["variant"],
            "rep": meta["rep"], "category": cat, "empty_patch": not patch.strip(),
            "patch_touches_gold_file": in_gold,
            "gold_file_named_in_commands": bool(files_named & gold_files),
            "edit_region_shown_min": edit_shown, "support_regions": len(support),
            "support_regions_shown_ge_0.5": sum(r["shown"] >= 0.5 for r in support),
            "f2p_failed": len(tests["FAIL_TO_PASS"]["failure"]),
            "f2p_total": len(tests["FAIL_TO_PASS"]["failure"]) + len(tests["FAIL_TO_PASS"]["success"]),
            "p2p_broken": len(tests["PASS_TO_PASS"]["failure"]), "ran_code": ran_code, "regions": regions}


def main(split, reps):
    ids = {t["id"] for t in h.tasks() if t["split"] == split}
    rows = [r for rep in reps for v in "ABCE"
            for m in sorted((h.DATA / "runs" / f"r{rep}" / v).glob("*/meta.json"))
            if m.parent.name in ids and (r := analyze(m.parent))]
    by_task = defaultdict(list)
    for r in rows:
        by_task[r["instance"]].append(r)
    summary = {
        "failed_runs": len(rows),
        "category": dict(Counter(r["category"] for r in rows)),
        "category_by_variant": {v: dict(Counter(r["category"] for r in rows if r["variant"] == v)) for v in "ABCE"},
        "empty_patch": sum(r["empty_patch"] for r in rows),
        "patch_touches_gold_file": sum(r["patch_touches_gold_file"] for r in rows),
        "gold_file_named_in_commands": sum(r["gold_file_named_in_commands"] for r in rows),
        "edit_region_shown_ge_0.5": sum(r["edit_region_shown_min"] >= 0.5 for r in rows),
        "ran_code": sum(r["ran_code"] for r in rows),
        "per_task": {t: {"failed": len(rs), "category": dict(Counter(r["category"] for r in rs)),
                         "edit_shown_median": sorted(r["edit_region_shown_min"] for r in rs)[len(rs) // 2],
                         "support_shown": f'{sum(r["support_regions_shown_ge_0.5"] for r in rs)}/'
                                          f'{sum(r["support_regions"] for r in rs)}',
                         "f2p_failed_of_total": sorted({(r["f2p_failed"], r["f2p_total"]) for r in rs}),
                         "p2p_broken_max": max(r["p2p_broken"] for r in rs),
                         "ran_code": sum(r["ran_code"] for r in rs)}
                     for t, rs in sorted(by_task.items())},
    }
    print(json.dumps({"summary": summary, "runs": rows}, indent=1))


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(sys.argv[1], [int(x) for x in sys.argv[2:]])
