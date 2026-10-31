"""The real dev tasks of both ContextBench suites, and their private index.

    corpora.py index     index every dev corpus (fixtures excluded) into DATA/oxide-data
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "tests/python"))
import contextbench as cb  # noqa: E402
import contextbench_ts as cbts  # noqa: E402

import oxide  # noqa: E402

DATA = Path.home() / "Projects/oxide-eval-data/oxide-integrity-eval"
STORE = DATA / "oxide-data"
MCP_QUERY = 2000  # oxide_search's `query` max_length (src/oxide/mcp_server.py)


def mcp_query(task: dict[str, Any]) -> str:
    """The issue text as an MCP call can carry it: its first MCP_QUERY characters."""
    return str(task["query"][:MCP_QUERY])


def tasks() -> list[tuple[dict[str, Any], Path]]:
    """Every dev task on a real corpus, in suite then task order."""
    out = []
    for module in (cb, cbts):
        suite = module.load_tasks()
        for task in suite["tasks"]:
            if task["split"] == "dev" and not task["corpus"].startswith("fixture"):
                corpus = suite["corpora"][task["corpus"]]
                out.append((task, module.corpus_root(task["corpus"], corpus)))
    return out


def index() -> None:
    for root in sorted({str(root) for _, root in tasks()}):
        with oxide.open_repository(root, data_dir=STORE) as repo:
            report = repo.index()
        print(root, report.status, report.files, f"{report.seconds:.1f}s", flush=True)


if __name__ == "__main__":
    {"index": index}[sys.argv[1]]()
