"""AGC v2's MCP server: one adaptive tool, `oxide_context`, over stdio.

    python server.py --repo PATH --data-dir DIR [--log FILE]

Experimental and isolated: not `oxide mcp`, never installed, used only by `trial.py`.
The index is opened on the first call, so the MCP handshake is not held up by it.
The graph and outline logic is the AGC v1 code (`../../agc/scripts/agc.py`), unchanged.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agc/scripts"))

CONTEXT = (
    "Structural context for this repository from OXIDE's code index. Use it for "
    "relationship questions (who calls X, what X calls, call chains, impact of a change) "
    "and to get oriented in unfamiliar code. For a simple lookup (where is X defined, "
    "which function does Y), grep and read directly instead.\n"
    "- relation 'callers' or 'callees' with a symbol: what the index resolved, each with "
    "the source line linking it (depth 2-3 for indirect ones). For callers it then lists "
    "code occurrences of the name that no edge explains, so you can check those instead "
    "of searching; for callees, the calls it could not resolve. Not exhaustive.\n"
    "- no relation: an outline at the level of the target. No target: the package map; "
    "a file path: its classes and functions with line ranges; a symbol: its signature, "
    "docstring, members and line range.\n"
    "Then read only the lines you need."
)
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
NEXT = {
    "architecture": "Next: oxide_context(target=<file path>) for a file's outline.",
    "module": "Next: oxide_context(target=<name>) for a symbol, or read the lines you need.",
    "symbol": "Next: read the lines you need.",
}


class Context:
    """`oxide_context`'s policy: which AGC v1 view answers a call."""

    def __init__(self, repo: str, data_dir: str) -> None:
        self.repo, self.data_dir, self._agc = repo, data_dir, None
        self.lock = threading.Lock()  # the native binding is not thread-safe

    @property
    def agc(self) -> Any:
        if self._agc is None:
            from agc import Agc

            self._agc = Agc(self.repo, self.data_dir)
        return self._agc

    def __call__(self, target: str = "", relation: str = "", depth: int = 1) -> str:
        target = target.strip().strip("`")
        if relation:
            if not target:
                return "relation needs a symbol target."
            return str(self.agc.graph(target, relation, depth))
        if not target:
            return self._next(self.agc.architecture(), NEXT["architecture"])
        if target.endswith(".py") or "/" in target:
            return self._next(self.agc.module(target), NEXT["module"])
        if self.agc.resolve(target):
            return self._next(self.agc.symbol(target), NEXT["symbol"])
        return self._next(self.agc.module(target), NEXT["module"])  # a dotted module name

    @staticmethod
    def _next(text: str, hint: str) -> str:
        """v1's closing hint names v1's tool; name this one instead."""
        rows = [r for r in str(text).split("\n") if not r.startswith("Next: tiers(")]
        return "\n".join([*rows, hint])


def build(context: Context, log: str | None = None) -> FastMCP:
    mcp = FastMCP("oxide-agc2")

    @mcp.tool(description=CONTEXT, annotations=READ_ONLY)
    def oxide_context(
        target: Annotated[
            str,
            Field(
                description="Symbol name, `Class.method`, dotted path or `path.py:line`; "
                "a file path for its outline; empty for the package map",
                max_length=300,
            ),
        ] = "",
        relation: Literal["", "callers", "callees"] = "",
        depth: Annotated[int, Field(ge=1, le=3)] = 1,
    ) -> str:
        args = {"target": target, "relation": relation, "depth": depth}
        with context.lock:
            began = time.perf_counter()
            text = context(target, relation, depth)
            ms = round((time.perf_counter() - began) * 1000, 1)
        if log:
            with open(log, "a") as out:
                out.write(json.dumps({"tool": "context", "args": args, "chars": len(text),
                                      "ms": ms}) + "\n")  # fmt: skip
        return text

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--log")
    args = parser.parse_args()
    build(Context(args.repo, args.data_dir), args.log).run(show_banner=False)


if __name__ == "__main__":
    main()
