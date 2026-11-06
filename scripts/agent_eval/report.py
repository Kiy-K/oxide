"""Paired comparison: per-task resolve rate in each arm, bootstrap interval over tasks."""

from __future__ import annotations

import json
import random
import statistics
from collections import defaultdict
from pathlib import Path

BOOTSTRAP = 10_000


def interval(diffs: list[float]) -> tuple[float, float]:
    """95% percentile bootstrap of the mean paired difference, resampling tasks."""
    rng = random.Random(0)
    means = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(BOOTSTRAP))
    return means[int(0.025 * BOOTSTRAP)], means[int(0.975 * BOOTSTRAP) - 1]


def report(path: Path) -> None:
    attempts = [json.loads(line) for line in path.read_text().splitlines()]
    rows = list({(r["task"], r["arm"], r["rep"]): r for r in attempts}.values())
    errors = [
        r for r in rows if r["resolved"] is None or r.get("status") not in (None, "ok", "timeout")
    ]
    scored = [r for r in rows if r not in errors]
    rate: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for r in scored:
        rate[r["task"]][r["arm"]].append(bool(r["resolved"]))
    both = {t: arms for t, arms in rate.items() if arms["control"] and arms["oxide"]}
    diffs = [statistics.fmean(a["oxide"]) - statistics.fmean(a["control"]) for a in both.values()]
    print(f"{len(scored)} scored runs, {len(errors)} harness errors, {len(both)} paired tasks")
    if any("provider.auth:" in str(r.get("error", "")) for r in errors):
        print("verdict: invalid: provider authentication failure")
        return
    if rows and len(errors) / len(rows) > 0.10:
        print("verdict: invalid: more than 10% harness errors")
        return
    if not diffs:
        print("verdict: no paired tasks")
        return
    for arm in ("control", "oxide"):
        runs = [r for r in scored if r["arm"] == arm]
        print(
            f"{arm:8} resolved {sum(r['resolved'] for r in runs)}/{len(runs)}"
            f"  median seconds {statistics.median(r['seconds'] for r in runs):.0f}"
            f"  median output tokens {statistics.median(r['tokens']['output'] for r in runs):.0f}"
            f"  median steps {statistics.median(r['steps'] for r in runs):.0f}"
        )
    oxide_runs = [r for r in scored if r["arm"] == "oxide"]
    used = sum(any(k.startswith("oxide") for k in r["calls"]) for r in oxide_runs)
    print(
        f"oxide tool used in {used}/{len(oxide_runs)} oxide runs; "
        f"median index {statistics.median(r.get('index_seconds', 0) for r in oxide_runs):.1f} s"
    )
    low, high = interval(diffs)
    better, worse = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
    print(
        f"paired resolve-rate difference (oxide - control): {statistics.fmean(diffs):+.3f}"
        f"  95% CI [{low:+.3f}, {high:+.3f}]  tasks better/worse/tied "
        f"{better}/{worse}/{len(diffs) - better - worse}"
    )
    verdict = "OXIDE improves task success" if low > 0 else "no demonstrated improvement"
    print("verdict:", verdict)
