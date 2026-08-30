#!/usr/bin/env python3
"""Refines Phase 3.1's discovery-efficiency / first-action metrics.

results.jsonl's `first_action` and `native_explore_calls` fields treat
every native `read` before an OXIDE call as equally "avoidable
exploration." That's wrong: a `read AGENTS.md -> load OXIDE skill ->
oxide context` sequence is healthy activation, not a delayed-activation
failure, and reading a README or the task's own explicitly-named file
isn't "exploration" either.

This script re-parses the raw per-run event logs (logs/*.jsonl -- already-
recorded evidence, untouched) and classifies every pre-OXIDE tool call
into:

  INSTRUCTION_READ           - AGENTS.md/CLAUDE.md/SKILL.md, or a `skill`
                                tool call (loading policy/instructions,
                                OXIDE's own skill or otherwise)
  PROJECT_ORIENTATION_READ   - README, package manifests, or a bare
                                directory listing
  DIRECT_TARGET_READ         - a file explicitly named in the task prompt
  IMPLEMENTATION_EXPLORATION_READ - a source file opened hunting for the
                                implementation (the only `read` type that
                                still counts as avoidable exploration)
  OTHER_NATIVE_DISCOVERY     - grep/glob/list/non-oxide bash (unchanged
                                from the original native_explore_calls
                                definition)

Does not modify results.jsonl. Only the derived discovery-efficiency and
first-action tables in results.md / transport-selection.md are affected.
"""
import glob
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
LOG_DIR = ROOT / "docs/evals/phase-3.1/logs"

OXIDE_CMD_RE = re.compile(r"\boxide\s+(context|search)\b")
NATIVE_GREP_RE = re.compile(r"\b(grep|rg|ag)\b")

INSTRUCTION_BASENAMES = {"agents.md", "claude.md", "skill.md"}
ORIENTATION_BASENAMES = {
    "readme.md", "readme", "package.json", "pyproject.toml",
    "tsconfig.json", "cargo.toml", ".gitignore", "package-lock.json",
}
SOURCE_EXT = (".py", ".ts", ".tsx", ".js", ".rs")

# Bucket C tasks name an exact file in the prompt -- reading it is
# following the task literally, not "exploration". Bucket A/B tasks name
# no file, so this map only has entries where it matters.
DIRECT_TARGETS = {
    "C1": {"oxidepy/cache.py"},
    "C2": {"src/ui/Button.tsx"},
    "C3": {"oxidepy/http_client.py"},
    "C4": {"src/net/retry.ts"},
}


def classify_read(task_id: str, filepath: str) -> str:
    p = filepath.rstrip("/")
    basename = p.rsplit("/", 1)[-1].lower()
    if basename in INSTRUCTION_BASENAMES or "/skills/" in filepath.lower():
        return "INSTRUCTION_READ"
    if basename in ORIENTATION_BASENAMES:
        return "PROJECT_ORIENTATION_READ"
    if not any(p.lower().endswith(ext) for ext in SOURCE_EXT):
        # No recognizable source extension -> treat as a directory /
        # metadata probe, not implementation exploration.
        return "PROJECT_ORIENTATION_READ"
    for target in DIRECT_TARGETS.get(task_id, ()):
        if p.endswith(target):
            return "DIRECT_TARGET_READ"
    return "IMPLEMENTATION_EXPLORATION_READ"


def parse_calls(log_path: Path):
    calls = []
    for line in log_path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            j = json.loads(line)
        except json.JSONDecodeError:
            continue
        part = j.get("part", {})
        if part.get("type") == "tool":
            calls.append((part.get("tool", ""), part.get("state", {}).get("input", {}) or {}))
    return calls


def classify_run(task_id: str, calls):
    """Returns (classified_sequence, first_oxide_idx, refined_first_action,
    avoidable_pre_oxide_count, avoidable_total_count)."""
    classified = []
    first_oxide_idx = None
    for i, (tool, inp) in enumerate(calls):
        tl = tool.lower()
        cmd = inp.get("command", "") if isinstance(inp, dict) else ""
        is_oxide = ("oxide" in tl) or (tool == "bash" and OXIDE_CMD_RE.search(cmd))
        if is_oxide and first_oxide_idx is None:
            first_oxide_idx = i

        if tool == "read":
            fp = inp.get("filePath", "") if isinstance(inp, dict) else ""
            cls = classify_read(task_id, fp)
        elif tool == "skill":
            cls = "INSTRUCTION_READ"
        elif is_oxide:
            cls = "OXIDE_CALL"
        elif tool == "bash":
            cls = "OTHER_NATIVE_DISCOVERY" if not OXIDE_CMD_RE.search(cmd) else "OXIDE_CALL"
        elif tool in ("grep", "glob", "list"):
            cls = "OTHER_NATIVE_DISCOVERY"
        else:
            cls = "OTHER"
        classified.append((tool, cls))

    avoidable_kinds = {"IMPLEMENTATION_EXPLORATION_READ", "OTHER_NATIVE_DISCOVERY"}
    pre_oxide = classified[:first_oxide_idx] if first_oxide_idx is not None else classified
    avoidable_pre_oxide = sum(1 for _, c in pre_oxide if c in avoidable_kinds)
    avoidable_total = sum(1 for _, c in classified if c in avoidable_kinds)

    # Refined first action: skip INSTRUCTION_READ / PROJECT_ORIENTATION_READ
    # / DIRECT_TARGET_READ preamble -- those don't compete with OXIDE, they
    # lead into it. First action is the first OXIDE call, or (if none) the
    # first avoidable-exploration action, or (if neither) whatever's first.
    refined_first_action = None
    for tool, cls in classified:
        if cls == "OXIDE_CALL":
            refined_first_action = f"oxide:{tool}"
            break
        if cls in avoidable_kinds:
            refined_first_action = f"{cls}:{tool}"
            break
    if refined_first_action is None and classified:
        refined_first_action = f"preamble-only:{classified[0][1]}"
    elif not classified:
        refined_first_action = "no-tool-calls"

    return classified, first_oxide_idx, refined_first_action, avoidable_pre_oxide, avoidable_total


def main():
    results = defaultdict(list)  # (bucket, condition) -> list of run stats
    pattern = re.compile(r"^([A-Za-z0-9]+)-([ABCDE])-r(\d+)\.jsonl$")
    for log_path in sorted(LOG_DIR.glob("*.jsonl")):
        m = pattern.match(log_path.name)
        if not m:
            continue
        task_id, condition, rep = m.group(1), m.group(2), m.group(3)
        bucket = task_id[0] if task_id[0] in "ABC" and task_id[1:].isdigit() else None
        if bucket != "A":  # this refinement's headline table is Bucket A
            continue
        calls = parse_calls(log_path)
        if not calls:
            continue  # timed-out / empty run, already excluded elsewhere
        classified, first_oxide_idx, refined_first_action, avoid_pre, avoid_total = classify_run(task_id, calls)
        results[condition].append(dict(
            task=task_id, rep=rep, total_calls=len(calls),
            avoidable_pre_oxide=avoid_pre, avoidable_total=avoid_total,
            refined_first_action=refined_first_action,
            used_oxide=first_oxide_idx is not None,
        ))

    print("=== refined first_action distribution, Bucket A ===")
    for cond in "ABCDE":
        runs = results.get(cond, [])
        dist = defaultdict(int)
        for r in runs:
            dist[r["refined_first_action"]] += 1
        print(f"  {cond} (n={len(runs)}): {dict(dist)}")
    print()

    print("=== refined discovery efficiency, Bucket A (mean avoidable exploration, total vs pre-oxide-only) ===")
    for cond in "ABCDE":
        runs = results.get(cond, [])
        if not runs:
            continue
        mean_total_calls = sum(r["total_calls"] for r in runs) / len(runs)
        mean_avoid_total = sum(r["avoidable_total"] for r in runs) / len(runs)
        mean_avoid_pre = sum(r["avoidable_pre_oxide"] for r in runs) / len(runs)
        print(f"  {cond}: n={len(runs)} mean_total_calls={mean_total_calls:.1f} "
              f"mean_avoidable_total={mean_avoid_total:.1f} mean_avoidable_pre_oxide={mean_avoid_pre:.1f}")


if __name__ == "__main__":
    main()
