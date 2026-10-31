"""Controlled stage (preregistration.md § Agent): the TOON study's harness, reused.

    agent.py prompt CASE_ID ARM      print one prompt (no model call)
    agent.py run REP WORKERS         one call per case x arm, skipping finished ones
    agent.py grade                   score every finished call -> results/agent.json

OpenCode with a fixed model, a no-tools agent, an isolated config and one turn
(``toon-serialization/scripts/agent.py``: ``call``, ``reply``, ``correct``,
``prompt_tokens``). Prompts differ only in the output text; the format is never named.
"""

from __future__ import annotations

import importlib.util
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HARNESS = Path(__file__).resolve().parents[2] / "toon-serialization/scripts/agent.py"
_spec = importlib.util.spec_from_file_location("toon_agent", HARNESS)
assert _spec and _spec.loader
toon = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(toon)

DATA = Path.home() / "Projects/oxide-eval-data/oxide-integrity-eval"
RESULTS = Path(__file__).resolve().parents[1] / "results"
toon.OPENCODE = DATA / "opencode-quiz"
toon.ENV["XDG_CONFIG_HOME"] = str(toon.OPENCODE / "config")
QUIZ = toon.QUIZ.replace("output a code-search tool returned", "text a code-search tool "
                         "returned to you (as your client delivered it)")  # fmt: skip


def cases() -> list[dict[str, Any]]:
    return json.loads((DATA / "cases.json").read_text())


def prompt(case: dict[str, Any], arm: str) -> str:
    text = case["texts"][arm]
    if case["kind"] == "nav":
        return toon.NAV.format(issue=case["issue"], text=text)
    questions = "\n".join(f"q{n}: {q['text']}" for n, q in enumerate(case["questions"], 1))
    return QUIZ.format(text=text, questions=questions)


def path(rep: str, case: dict[str, Any], arm: str) -> Path:
    return DATA / "runs" / rep / arm / f"{case['id'].replace(':', '~')}.json"


def run(rep: str, workers: int) -> None:
    toon.configure()
    jobs = [(c, a) for c in cases() for a in c["arms"]]
    random.Random(f"{toon.SEED}:{rep}").shuffle(jobs)
    todo = []
    for case, arm in jobs:
        out = path(rep, case, arm)
        if not out.exists() or toon.limited(json.loads(out.read_text())):
            out.parent.mkdir(parents=True, exist_ok=True)
            todo.append((prompt(case, arm), out))
    print(f"{len(todo)} calls", file=sys.stderr)
    with ThreadPoolExecutor(workers) as pool:
        for n, _ in enumerate(pool.map(lambda job: toon.call(*job), todo), 1):
            if n % 50 == 0:
                print(f"{n}/{len(todo)}", file=sys.stderr, flush=True)


def correct(question: dict[str, Any], gold: Any, got: Any) -> bool:
    if question["grade"] in ("bool", "nullable"):
        return bool(toon.scalar(got, gold))
    return bool(toon.correct({"gold": gold, "grade": question["grade"]}, got))


def grade() -> None:
    rows = []
    for case in cases():
        for rep_dir in sorted((DATA / "runs").iterdir()):
            for arm in case["arms"]:
                file = path(rep_dir.name, case, arm)
                if not file.exists():
                    continue
                record = json.loads(file.read_text())
                ran = [t for t in record.get("tools", []) if t[1] == "completed"]
                answer = None if ran else toon.reply(record)
                row: dict[str, Any] = {
                    "id": case["id"], "kind": case["kind"], "condition": case["condition"],
                    "corpus": case["corpus"], "arm": arm, "rep": rep_dir.name,
                    "seconds": record.get("seconds"),
                    "prompt_tokens": toon.prompt_tokens(record),
                    "failed": record.get("exit") != 0 or bool(record.get("is_error")),
                    "tool_ran": bool(ran),
                }  # fmt: skip
                if case["kind"] == "nav" and not case["gold_files"]:
                    continue  # no verified gold file (all drift): ungradeable, disclosed
                if case["kind"] == "nav":
                    paths = answer.get("paths") if answer else None
                    valid = isinstance(paths, list)
                    chosen = (
                        {str(p).strip().removeprefix("./") for p in paths[:3]} if valid else set()
                    )
                    gold = set(case["gold_files"])
                    row |= {"valid": valid, "hit": float(bool(chosen & gold)),
                            "file_recall": len(chosen & gold) / len(gold)}  # fmt: skip
                else:
                    answers = answer.get("answers") if answer else None
                    valid = isinstance(answers, dict)
                    golds = case["golds"][arm]
                    row |= {
                        "valid": valid,
                        "correct": [valid and correct(q, g, answers.get(f"q{n}"))
                                    for n, (q, g) in enumerate(zip(case["questions"], golds,
                                                                   strict=True), 1)],
                        "answers": answers, "golds": golds,
                    }  # fmt: skip
                rows.append(row)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "agent.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
    print(len(rows), "graded calls")


def main() -> None:
    command = sys.argv[1]
    if command == "prompt":
        case = next(c for c in cases() if c["id"] == sys.argv[2])
        print(prompt(case, sys.argv[3]))
    elif command == "run":
        run(sys.argv[2], int(sys.argv[3]))
    elif command == "grade":
        grade()
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
