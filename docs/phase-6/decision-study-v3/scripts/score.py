"""Decision Study v3 harness (preregistration.md). No network; src/ is unchanged.

    uv run --with tiktoken python docs/phase-6/decision-study-v3/scripts/score.py OUT \
        [NAME=JUDGMENTS.json ...] [--split dev|v3] [--requests-only]

Runs v2's harness with its arms unchanged (``decision-study-v2/scripts/harness.py``:
heuristic asserted equal to production, oracles, ``random~order_s0..4``, and ``NAME~order``,
the frozen hybrid, over the recorded judgments NAME) on one split:

  dev  the baseline's dev tasks, to check that this code reproduces v2's dev gate
  v3   the sealed set (``tasks.json``, ``gold-audit.json``); refused unless every file of
       ``decision-study-v3/`` is committed and unmodified (the freeze)

``--requests-only`` builds the requests and computes no arm, so no outcome exists before
inference. Every run also writes OUT/requests.sha256.json (what is sent must be what is
scored) and OUT/leakcheck.json (no gold or task identity in any judge input).
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
V3 = HERE.parent
sys.path.insert(0, str(V3.parent / "decision-study-v2" / "scripts"))
sys.path.insert(0, str(HERE))

import call  # noqa: E402
import harness as v2  # noqa: E402  (v2's harness; it imports v1's study.py)

from oxide.core import capsule, packing, retrieval  # noqa: E402

study, cb = v2.study, v2.study.cb
REQUEST_KEYS = {"task", "corpus", "query", "capsules"}
CAPSULE_KEYS = {f.name for f in dataclasses.fields(capsule.Capsule) if f.init}


def frozen() -> None:
    status = subprocess.run(
        ["git", "-C", str(V3), "status", "--porcelain", "--untracked-files=all", "--", "."],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert not status.strip(), f"the v3 set is sealed until the freeze is committed:\n{status}"


def leakcheck(out: Path, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Every judge input is built from its request alone; no request carries gold, the
    task's benchmark identity, or anything but the query and canonical capsules."""
    by_id = {t["id"]: t for t in tasks}
    findings = []
    files = sorted((out / "requests").glob("*.json"))
    for path in files:
        request = json.loads(path.read_text())
        task = by_id[request["task"]]
        if set(request) != REQUEST_KEYS:
            findings.append((path.name, f"request keys {sorted(request)}"))
        if request["query"] != task["query"]:
            findings.append((path.name, "query is not the task's problem statement"))
        for text in request["capsules"]:
            if set(json.loads(text)) != CAPSULE_KEYS:
                findings.append((path.name, "capsule fields differ from oxide-capsule-v1"))
        caps = call.adapter.capsules(request)
        sent = json.dumps(call.adapter.body(caps, call.RENDERER, True), ensure_ascii=False)
        markers = [g["sha256"].removeprefix("sha256:") for g in task["gold"] if "sha256" in g]
        markers.append("full.parquet")
        if task["source"].startswith("ContextBench"):
            markers += [task["source"].split("#")[-1], task["id"]]
        findings += [(path.name, f"body contains {m[:24]}") for m in markers if m in sent]
    report = {"ok": not findings, "requests": len(files), "findings": findings}
    (out / "leakcheck.json").write_text(json.dumps(report, indent=1) + "\n")
    assert report["ok"], findings
    return report


def main(argv: list[str]) -> None:
    split = argv[argv.index("--split") + 1] if "--split" in argv else "dev"
    out = Path(argv[0])
    if split == "v3":
        frozen()
        cb.TASKS, cb.AUDIT = V3 / "tasks.json", V3 / "gold-audit.json"
    # As v2's main: v1's hooks call v2's arms, packer recorder and scorer. v1's main is
    # inlined below only because it refuses every split but the baseline's held-out.
    study.arms = (lambda *a: {}) if "--requests-only" in argv else v2.arms
    study.real_pack, study.score = v2.pack, v2.score
    study.out_dir = out
    (out / "requests").mkdir(parents=True, exist_ok=True)
    for arg in argv[1:]:
        if "=" in arg:
            name, path = arg.split("=", 1)
            study.recorded[name] = json.loads(Path(path).read_text())
    retrieval.retrieve, packing.pack, cb.score = study.retrieve, study.pack, study.score
    tasks = cb.load_tasks()
    tasks["tasks"] = [t for t in tasks["tasks"] if t["split"] == split]
    used = {t["corpus"] for t in tasks["tasks"]}
    tasks["corpora"] = {k: v for k, v in tasks["corpora"].items() if k in used}
    with tempfile.TemporaryDirectory(prefix="oxide-ds3-") as data:
        cb.evaluate(tasks, Path(data))
    skip = ("judgments", "per_task")
    meta = {n: {k: v for k, v in d.items() if k not in skip} for n, d in study.recorded.items()}
    rows = study.rows
    if "--requests-only" in argv:  # no outcome: the gold line count only
        rows = [{"task": r["task"], "gold_lines": r["gold_lines"]} for r in rows]
    results = {"split": split, "recorded": meta, "tasks": rows}
    for name, data in (("results", results), ("costs", study.costs)):
        (out / f"{name}.json").write_text(json.dumps(data, indent=1) + "\n")
    (out / "truncation.json").write_text(json.dumps(study.truncation, indent=1) + "\n")
    (out / "items.json").write_text(json.dumps(v2.items) + "\n")
    (out / "requests.sha256.json").write_text(json.dumps(call.digests(out), indent=1) + "\n")
    report = leakcheck(out, tasks["tasks"])
    print(
        f"{split}: {len(rows)} tasks with verified gold; {report['requests']} requests; "
        f"leak check ok -> {out}"
    )


if __name__ == "__main__":
    main(sys.argv[1:])
