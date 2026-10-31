"""ADR-0009 milestone M1: offline parity of the in-tree JEV ordering with the evidence.

    uv run python docs/phase-6/jev-order-m1/scripts/parity.py SET [SET ...]

SET is ``v3`` (Decision Study v3's primary and repeat), ``v2-dev`` or ``v2-heldout``
(Decision Study v2's primary). These sets are consumed: this checks equivalence, not
quality, and makes no network call. For each set:

1. **Bodies.** Every recorded request's capsules, rendered by ``oxide.jev.body``, give the
   exact JSON the study adapter (``decision-study-v1/scripts/adapter.py``) sent.
2. **Bundles.** The unchanged ContextBench scorer runs with ``Repository.build_context``
   routed through the in-tree judged path (``Repository._context``, ``jev.NAME``,
   ``AdmittedOrder``) and the study's recorded judgments replayed by capsule digest
   (``capsule.Recorded``). Every task's ``line_recall`` and ``used`` at every budget
   must equal the study's scored arm, and every judged bundle must name the policy.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
PHASE6 = ROOT / "docs" / "phase-6"
sys.path.insert(0, str(ROOT / "tests" / "python"))
sys.path.insert(0, str(PHASE6 / "decision-study-v1" / "scripts"))

import adapter  # noqa: E402  (the study adapter: the bytes the studies sent)
import contextbench as cb  # noqa: E402

import oxide  # noqa: E402
from oxide import jev  # noqa: E402
from oxide.core import ordering  # noqa: E402
from oxide.core.capsule import CapsuleJudge, Recorded  # noqa: E402
from oxide.core.ordering import AdmittedOrder  # noqa: E402

DATA = Path.home() / "Projects" / "oxide-eval-data"
D2, D3 = DATA / "oxide-decision-study-v2", DATA / "oxide-decision-study-v3"
V3 = PHASE6 / "decision-study-v3"
# name: (split, task files or None for the baseline's, requests, {arm: judgments}, scored)
SETS: dict[str, tuple[str, tuple[Path, Path] | None, Path, dict[str, Path], Path]] = {
    "v3": (
        "v3",
        (V3 / "tasks.json", V3 / "gold-audit.json"),
        D3 / "v3",
        {a: D3 / "v3" / "judgments" / f"{a}.json" for a in ("compact", "compact-repeat")},
        D3 / "v3-scored" / "results.json",
    ),
    "v2-dev": (
        "dev",
        None,
        D2 / "dev",
        {"compact": D2 / "dev" / "judgments" / "compact.json"},
        D2 / "dev-scored" / "results.json",
    ),
    "v2-heldout": (
        "heldout",
        None,
        D2 / "heldout",
        {"compact": D2 / "heldout" / "judgments" / "compact.json"},
        D2 / "heldout-scored" / "results.json",
    ),
}


def bodies(requests: Path) -> int:
    n = 0
    for r in adapter.requests(requests):
        caps = adapter.capsules(r)
        sent = json.dumps(adapter.body(caps, "compact", True))
        assert json.dumps(jev.body(caps)) == sent, f"{r['task']}: body differs"
        n += 1
    return n


def replay(split: str, files: tuple[Path, Path] | None, judgments: Path) -> list[dict[str, Any]]:
    recorded = Recorded(json.loads(judgments.read_text())["judgments"])

    def by(query: str, derivation: str, source: Any) -> AdmittedOrder:
        return AdmittedOrder(
            CapsuleJudge(jev.NAME, query, derivation, source, recorded), jev.MANIFEST_DIGEST
        )

    def judged(self: oxide.Repository, query: str, budget: int, **hints: Any) -> Any:
        return self._context(
            query,
            budget,
            hints.get("paths", ()),
            hints.get("symbols", ()),
            hints.get("selection"),
            hints.get("changed", ()),
            by,
        )

    if files is not None:
        cb.TASKS, cb.AUDIT = files
    tasks = cb.load_tasks()
    tasks["tasks"] = [t for t in tasks["tasks"] if t["split"] == split]
    used = {t["corpus"] for t in tasks["tasks"]}
    tasks["corpora"] = {k: v for k, v in tasks["corpora"].items() if k in used}
    plain = oxide.Repository.build_context
    oxide.Repository.build_context = judged  # type: ignore[assignment,method-assign]
    try:
        with tempfile.TemporaryDirectory(prefix="oxide-m1-") as data:
            results: list[dict[str, Any]] = cb.evaluate(tasks, Path(data))["results"]
    finally:
        oxide.Repository.build_context = plain  # type: ignore[method-assign]
    return results


def compare(results: list[dict[str, Any]], scored: Path, arm: str) -> dict[str, Any]:
    study = {t["task"]: t["arms"][f"{arm}~order"] for t in json.loads(scored.read_text())["tasks"]}
    checked, fallbacks, macro = 0, 0, {b: 0.0 for b in cb.BUDGETS}
    for r in results:
        if r["verified"] is None:
            continue
        expected = study[r["task"]]
        fell: list[str] = []
        for b in cb.BUDGETS:
            got = (r["verified"]["metrics"][f"line_recall@{b}"], r["bundles"][str(b)]["used"])
            want = (expected[str(b)]["line_recall"], expected[str(b)]["used"])
            assert got == want, (r["task"], b, got, want)
            macro[b] += got[0]
            fell = [d for d in r["bundles"][str(b)]["degradations"] if "fell back" in d]
            fallbacks += bool(fell)
        assert fell or r["versions"].get("order") == ordering.POLICY, r["versions"]
        checked += 1
    assert checked == len(study), (checked, len(study))
    return {
        "tasks": checked,
        "fallback_bundles": fallbacks,
        "macro_line_recall": {b: round(v / checked, 4) for b, v in macro.items()},
    }


def main(names: list[str]) -> None:
    for name in names:
        split, files, requests, arms, scored = SETS[name]
        print(f"{name}: {bodies(requests)} request bodies identical to the study adapter's")
        for arm, judgments in arms.items():
            summary = compare(replay(split, files, judgments), scored, arm)
            print(f"{name} {arm}: every task and budget equal to the study's arm; {summary}")


if __name__ == "__main__":
    main(sys.argv[1:])
