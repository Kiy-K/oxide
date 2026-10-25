"""The AGC trial's MCP server: `agc.py`'s tools over stdio, one arm's set per process.

    python server.py --repo PATH --data-dir DIR --tools graph,tiers

Experimental and isolated: not `oxide mcp`, never installed, used only by `trial.py`.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from collections.abc import Callable
from typing import Annotated, Literal

from agc import Agc
from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

GRAPH = (
    "Callers or callees of a function, method or class in this repository, from OXIDE's "
    "code graph. Each entry gives the source line that links it. For callers it then "
    "lists the code occurrences of the name that no graph edge explains, so you can "
    "check them instead of searching; for callees, the calls the graph could not "
    "resolve. The graph is not exhaustive. Use depth 2 for indirect callers or callees."
)
TIERS = (
    "Read this repository at the level of detail you need, one step at a time: "
    "'architecture' (packages and files, no argument), 'module' (a file's classes and "
    "functions with line ranges), 'symbol' (signature, docstring, members of a name) or "
    "'source' (exact numbered source of a symbol or of path:start-end). Start broad only "
    "when you do not know where to look; open source only for code you must read."
)
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
_lock = threading.Lock()  # the native binding is not thread-safe


def build(agc: Agc, tools: set[str], log: str | None = None) -> FastMCP:
    mcp = FastMCP("oxide-agc")

    def logged(tool: str, args: dict, call: Callable[[], str]) -> str:
        with _lock:
            began = time.perf_counter()
            text = call()
            ms = round((time.perf_counter() - began) * 1000, 1)
        if log:
            with open(log, "a") as out:
                row = {"tool": tool, "args": args, "chars": len(text), "ms": ms}
                out.write(json.dumps(row) + "\n")
        return text

    if "graph" in tools:

        @mcp.tool(description=GRAPH, annotations=READ_ONLY)
        def oxide_graph(
            symbol: Annotated[
                str,
                Field(
                    description="Name, `Class.method`, dotted path or `path.py:line`",
                    max_length=300,
                ),
            ],
            direction: Literal["callers", "callees"] = "callers",
            depth: Annotated[int, Field(ge=1, le=3)] = 1,
        ) -> str:
            args = {"symbol": symbol, "direction": direction, "depth": depth}
            return logged("graph", args, lambda: agc.graph(symbol, direction, depth))

    if "tiers" in tools:

        @mcp.tool(description=TIERS, annotations=READ_ONLY)
        def oxide_tiers(
            level: Literal["architecture", "module", "symbol", "source"],
            target: Annotated[
                str,
                Field(
                    description="File path (module), name (symbol, "
                    "source) or path:start-end (source)",
                    max_length=300,
                ),
            ] = "",
        ) -> str:
            args = {"level": level, "target": target}
            return logged("tiers", args, lambda: agc.tiers(level, target))

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--tools", required=True)
    parser.add_argument("--log")
    args = parser.parse_args()
    tools = set(args.tools.split(","))
    assert tools and tools <= {"graph", "tiers"}, tools
    build(Agc(args.repo, args.data_dir), tools, args.log).run(show_banner=False)


if __name__ == "__main__":
    main()
