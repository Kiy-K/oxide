#!/usr/bin/env python3
"""Condition-blind judge: scores each run's final answer against the task's
reference answer, having seen neither the condition nor any tool-call log --
only the task prompt, the reference, and the answer text with every mention
of "oxide" redacted.

Each judgment is a fresh `claude -p` process (no session/history), so no
judgment can be biased by having seen a sibling run's answer or condition.
Run `test_redaction.py` first -- this script refuses to judge anything until
that self-check passes over the current `results/results.jsonl`.

Usage:
    eval-agent/.venv/bin/python eval-agent/agentbench/judge.py
    eval-agent/.venv/bin/python eval-agent/agentbench/judge.py --dry-run   # 2 samples, no writes
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
RESULTS_PATH = HERE / "results" / "results.jsonl"
JUDGED_PATH = HERE / "results" / "judged.jsonl"

OXIDE_RE = re.compile(r"oxide", re.IGNORECASE)

RUBRIC = """You are grading one agent's answer to a repository-comprehension question.
You do not know, and must not guess, which of two experimental conditions produced it -- score only what is in front of you.

Task question:
{prompt}

Reference answer (verified against the actual source):
{reference}

Gold files a complete answer should touch on: {gold_files}

Agent's answer (verbatim; any tool name has been redacted to "[tool]"):
{answer}

Score strictly against the reference. Reply with ONLY a JSON object, no prose outside it:
{{"accuracy": <0-5 integer, how factually correct vs. the reference>,
  "completeness": <0-5 integer, how much of the reference's substance is covered>,
  "grounded": <true/false, false if the answer invents a file/symbol name not in the gold files or reference>,
  "rationale": "<one sentence>"}}"""


def redact(text: str) -> str:
    return OXIDE_RE.sub("[tool]", text or "")


def build_prompt(task: dict, redacted_answer: str) -> str:
    return RUBRIC.format(
        prompt=task["prompt"],
        reference=task["reference_answer"],
        gold_files=", ".join(task["gold_files"]),
        answer=redacted_answer or "(no final answer -- agent produced no text response)",
    )


def judge_answer(task: dict, redacted_answer: str) -> dict:
    prompt = build_prompt(task, redacted_answer)
    r = subprocess.run(
        ["claude", "-p", prompt, "--safe-mode", "--model", "claude-haiku-4-5-20251001",
         "--output-format", "json"],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        return {"error": (r.stderr or "")[-500:]}
    try:
        outer = json.loads(r.stdout)
        inner_text = outer.get("result", outer) if isinstance(outer, dict) else outer
        if isinstance(inner_text, str):
            match = re.search(r"\{.*\}", inner_text, re.DOTALL)
            return json.loads(match.group(0)) if match else {"error": "no JSON in judge output"}
        return inner_text
    except (json.JSONDecodeError, AttributeError) as e:
        return {"error": f"{type(e).__name__}: {e}", "raw": r.stdout[-500:]}


def load_tasks() -> dict:
    return {t["id"]: t for t in json.loads((HERE / "tasks.json").read_text())}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="judge 2 sample runs, print, don't write")
    args = ap.parse_args()

    tasks = load_tasks()
    records = [json.loads(line) for line in RESULTS_PATH.read_text().splitlines() if line.strip()]
    records = [r for r in records if r.get("final_answer") is not None and r.get("task") in tasks]

    if args.dry_run:
        records = records[:2]

    done = set()
    if JUDGED_PATH.exists() and not args.dry_run:
        for line in JUDGED_PATH.read_text().splitlines():
            if line.strip():
                d = json.loads(line)
                done.add((d["task"], d["condition"], d["rep"]))

    sink = None if args.dry_run else JUDGED_PATH.open("a")
    for rec in records:
        key = (rec["task"], rec["condition"], rec["rep"])
        if key in done:
            continue
        redacted = redact(rec["final_answer"])
        verdict = judge_answer(tasks[rec["task"]], redacted)
        out = {"task": rec["task"], "condition": rec["condition"], "rep": rec["rep"], **verdict}
        if args.dry_run:
            print(json.dumps(out, indent=1))
        else:
            sink.write(json.dumps(out) + "\n")
            sink.flush()
            print(f'{rec["task"]:<40} {rec["condition"]:<9} rep{rec["rep"]} -> {verdict}')
    if sink:
        sink.close()


if __name__ == "__main__":
    main()
