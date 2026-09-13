#!/usr/bin/env python3
"""Same condition-blind judge as judge.py, pointed at Study 2's results/tasks.
Reuses redact()/judge_answer() unchanged -- only the file paths differ."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from judge import judge_answer, redact  # noqa: E402

RESULTS_PATH = HERE / "results_study2" / "results.jsonl"
JUDGED_PATH = HERE / "results_study2" / "judged.jsonl"


def load_tasks() -> dict:
    return {t["id"]: t for t in json.loads((HERE / "tasks_study2.json").read_text())}


def main() -> None:
    tasks = load_tasks()
    records = [json.loads(line) for line in RESULTS_PATH.read_text().splitlines() if line.strip()]
    records = [r for r in records if r.get("final_answer") is not None and r.get("task") in tasks]

    done = set()
    if JUDGED_PATH.exists():
        for line in JUDGED_PATH.read_text().splitlines():
            if line.strip():
                d = json.loads(line)
                done.add((d["task"], d["condition"], d["rep"]))

    with JUDGED_PATH.open("a") as sink:
        for rec in records:
            key = (rec["task"], rec["condition"], rec["rep"])
            if key in done:
                continue
            redacted = redact(rec["final_answer"])
            verdict = judge_answer(tasks[rec["task"]], redacted)
            out = {"task": rec["task"], "condition": rec["condition"], "rep": rec["rep"], **verdict}
            sink.write(json.dumps(out) + "\n")
            sink.flush()
            print(f'{rec["task"]:<40} {rec["condition"]:<13} rep{rec["rep"]} -> {verdict}')


if __name__ == "__main__":
    main()
