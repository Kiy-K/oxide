"""Paired agent evaluation: the same agent and model fix executable bugs with and without OXIDE.

    uv run --with pyarrow python scripts/agent_eval fetch PARQUET   # once: pinned dataset rows
    uv run python scripts/agent_eval verify [--jobs 4]              # tasks whose tests run here
    uv run python scripts/agent_eval run --model M [--tasks 20 --reps 3 --jobs 4]
    uv run python scripts/agent_eval report RESULTS.jsonl

Protocol and the pass/fail rule are in ``fixtures/eval/agent-swe/README.md``; fix them before
reading results. Arms differ in one thing: the ``oxide mcp`` tool (and one sentence naming it).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import diagnose
import report
import run
import tasks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="narrow the dataset parquet to buildable repos")
    fetch.add_argument("parquet", type=Path)
    fetch.add_argument("--out", type=Path, default=tasks.CACHE / "candidates.json")
    verify = commands.add_parser("verify", help="keep instances whose gold patch passes here")
    verify.add_argument("--candidates", type=Path, default=tasks.CACHE / "candidates.json")
    verify.add_argument("--jobs", type=int, default=4)
    verify.add_argument("--only", action="append", default=[], help="instance id (repeatable)")
    go = commands.add_parser("run", help="run every task in every arm")
    go.add_argument("--model", required=True, help="opencode provider/model")
    go.add_argument("--tasks", type=int, default=20, help="first N tasks in the predeclared order")
    go.add_argument("--reps", type=int, default=3)
    go.add_argument("--jobs", type=int, default=4)
    go.add_argument("--timeout", type=int, default=1200, help="seconds per agent run")
    go.add_argument("--out", type=Path, default=None)
    go.add_argument("--only", action="append", default=[], help="instance id (repeatable)")
    go.add_argument("--split", choices=("dev", "heldout"), default="dev")
    go.add_argument(
        "--max-tokens", type=int, default=None, help="start no run once this much is spent"
    )
    go.add_argument("--final", action="store_true", help="open a held-out set: one look per set")
    summary = commands.add_parser("report", help="paired comparison of a results file")
    summary.add_argument("results", type=Path)
    diag = commands.add_parser("diagnose", help="classify OXIDE-arm runs from retained traces")
    diag.add_argument("results", type=Path)
    args = parser.parse_args()
    if args.command == "fetch":
        tasks.fetch(args.parquet, args.out)
    elif args.command == "verify":
        tasks.verify(args.candidates, args.jobs, args.only)
    elif args.command == "run":
        run.run_all(args)
    elif args.command == "report":
        report.report(args.results)
    else:
        print(diagnose.diagnose(args.results))


if __name__ == "__main__":
    main()
