"""Paired comparison: per-task resolve rate in each arm, bootstrap interval over tasks."""

from __future__ import annotations

import json
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

BOOTSTRAP = 10_000


def interval(diffs: list[float]) -> tuple[float, float]:
    """95% percentile bootstrap of the mean paired difference, resampling tasks."""
    rng = random.Random(0)
    means = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(BOOTSTRAP))
    return means[int(0.025 * BOOTSTRAP)], means[int(0.975 * BOOTSTRAP) - 1]


def plan_block(path: Path, rows: list[dict[str, Any]], scored: list[dict[str, Any]]) -> str | None:
    """None when every planned task, arm, and repetition is scored. Without a manifest the plan is
    unknown, so a partial run could not be told from a complete one."""
    side = path.with_name(path.stem + ".manifest.json")
    if not side.is_file():
        return "provisional: no manifest beside the results, so completeness is unknown"
    try:
        manifest = json.loads(side.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "invalid: unreadable manifest"
    if not isinstance(manifest, dict):
        return "invalid: unreadable manifest"
    arms = manifest.get("arms")
    tasks_n = manifest.get("tasks")
    reps = manifest.get("reps")
    if (
        not isinstance(arms, list)
        or not arms
        or not isinstance(tasks_n, int)
        or isinstance(tasks_n, bool)
        or tasks_n < 0
        or not isinstance(reps, int)
        or isinstance(reps, bool)
        or reps < 1
        or any(not isinstance(arm, str) or not arm for arm in arms)
    ):
        return "invalid: manifest missing tasks, arms, or reps"
    planned = [arm for arm in arms if isinstance(arm, str)]
    task_ids = {row["task"] for row in rows}
    scored_keys = {(row["task"], row["arm"], row["rep"]) for row in scored}
    total = tasks_n * len(planned) * reps
    got = sum(
        1
        for _task, arm, rep in scored_keys
        if arm in planned and isinstance(rep, int) and not isinstance(rep, bool) and 0 <= rep < reps
    )
    # shortcut: same-size task swap still passes, upgrade when manifest lists ids
    if len(task_ids) == tasks_n and got == total:
        return None
    return f"provisional: incomplete planned matrix ({got}/{total} scored)"


def report(path: Path, test: str = "oxide", reference: str = "control") -> None:
    attempts = [json.loads(line) for line in path.read_text().splitlines()]
    rows = list({(r["task"], r["arm"], r["rep"]): r for r in attempts}.values())
    errors = [
        r for r in rows if r["resolved"] is None or r.get("status") not in (None, "ok", "timeout")
    ]
    scored = [r for r in rows if r not in errors]
    rate: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for r in scored:
        rate[r["task"]][r["arm"]].append(bool(r["resolved"]))
    both = {t: arms for t, arms in rate.items() if arms[test] and arms[reference]}
    diffs = [statistics.fmean(a[test]) - statistics.fmean(a[reference]) for a in both.values()]
    print(f"{len(scored)} scored runs, {len(errors)} harness errors, {len(both)} paired tasks")
    if any("provider.auth:" in str(r.get("error", "")) for r in errors):
        print("verdict: invalid: provider authentication failure")
        return
    if rows and len(errors) / len(rows) > 0.10:
        print("verdict: invalid: more than 10% harness errors")
        return
    blocked = plan_block(path, rows, scored)
    if blocked and not blocked.startswith("provisional:"):
        print("verdict:", blocked)
        return
    if not diffs:
        print("verdict:", blocked or "no paired tasks")
        return
    for arm in (reference, test):
        runs = [r for r in scored if r["arm"] == arm]
        print(
            f"{arm:12} resolved {sum(r['resolved'] for r in runs)}/{len(runs)}"
            f"  median seconds {statistics.median(r['seconds'] for r in runs):.0f}"
            f"  median output tokens {statistics.median(r['tokens']['output'] for r in runs):.0f}"
            f"  median steps {statistics.median(r['steps'] for r in runs):.0f}"
        )
    test_runs = [r for r in scored if r["arm"] == test]
    used = sum(any(k.startswith("oxide") for k in r["calls"]) for r in test_runs)
    print(
        f"oxide tool used in {used}/{len(test_runs)} {test} runs; "
        f"median index {statistics.median(r.get('index_seconds', 0) for r in test_runs):.1f} s"
    )
    low, high = interval(diffs)
    better, worse = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
    print(
        f"paired resolve-rate difference ({test} - {reference}): {statistics.fmean(diffs):+.3f}"
        f"  95% CI [{low:+.3f}, {high:+.3f}]  tasks better/worse/tied "
        f"{better}/{worse}/{len(diffs) - better - worse}"
    )
    if blocked:
        print("verdict:", blocked)
        return
    verdict = f"{test} improves task success" if low > 0 else "no demonstrated improvement"
    print("verdict:", verdict)
