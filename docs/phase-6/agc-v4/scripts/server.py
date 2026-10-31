"""AGC v4's MCP server: production `oxide_search`, extended or paired, over stdio.

    python server.py --repo PATH --data-dir DIR --shape single|split [--log FILE]

Experimental and isolated: never installed, used only by `trial.py`. Both shapes take
production's `oxide_search` function from `oxide.mcp_server.server` and call it for
mode=search and mode=context, so those modes are production's.

- single: one tool, `oxide_search`, whose `mode` adds `callers`, `callees` and `outline`
  (AGC v2's graph and tiered views, `../../agc-v2/scripts/server.py` `Context`).
- split: production's `oxide_search` (its description and output schema), plus AGC v2's
  `oxide_context` tool with its v2 description.

The graph and outline text is the same in both shapes; only the packaging differs.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.tools import ToolResult
from mcp.types import ToolAnnotations
from pydantic import Field

from oxide import mcp_server

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "agc2_server", HERE.parents[1] / "agc-v2/scripts/server.py"
)
assert _spec and _spec.loader
v2 = importlib.util.module_from_spec(_spec)
sys.modules["agc2_server"] = v2
_spec.loader.exec_module(v2)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
# The single shape's addition to production's description: v2's tool text, as modes.
GRAPH_MODES = (
    " mode=callers or mode=callees takes a symbol in `query` (name, `Class.method`, dotted "
    "path or `path:line`): what the index resolved about it, each caller or callee with its "
    "hop count from that symbol and the source line linking them, then for callers the "
    "occurrences of the name no edge explains (check those instead of searching), for "
    "callees the calls it could not resolve. Not exhaustive; `depth` (1-3, default 1) walks "
    "further from the same symbol. mode=outline: the package map (empty `query`), a file's "
    "classes and functions with line ranges (a path), or a symbol's signature, docstring and "
    "members. Use these for relationship questions and orientation; for where a single name "
    "is defined, search or grep."
)
Mode = Literal["search", "context", "callers", "callees", "outline"]


class Log:
    def __init__(self, path: str | None) -> None:
        self.path = path

    def write(self, tool: str, mode: str, query: str, depth: int, text: str, ms: float) -> None:
        if not self.path:
            return
        relation = mode if mode in ("callers", "callees") else ""
        row = {"tool": tool, "mode": mode, "chars": len(text), "ms": ms,
               "args": {"target": query, "relation": relation, "depth": depth}}  # fmt: skip
        with open(self.path, "a") as out:
            out.write(json.dumps(row) + "\n")


def build(repo: str, data_dir: str, shape: str, log: Log) -> FastMCP:
    # Production's app, settings and instructions; its oxide_search is re-registered
    # below (FastMCP 4.1: `get_tool` is async; `local_provider.remove_tool` removes).
    app = mcp_server.server([repo], data_dir)
    search = asyncio.run(app.get_tool("oxide_search"))
    app.local_provider.remove_tool("oxide_search")
    context = v2.Context(repo, data_dir)

    def graph(mode: str, query: str, depth: int) -> str:
        began = time.perf_counter()
        with context.lock:
            text = str(context(query, "" if mode == "outline" else mode, depth))
        log.write(tool, mode, query, depth, text, round((time.perf_counter() - began) * 1000, 1))
        return text

    def production(**kwargs: Any) -> ToolResult:
        began = time.perf_counter()
        result: ToolResult = search.fn(**kwargs)
        text = "".join(getattr(c, "text", "") for c in result.content)
        ms = round((time.perf_counter() - began) * 1000, 1)
        log.write("oxide_search", kwargs["mode"], kwargs["query"], 0, text, ms)
        return result

    if shape == "split":
        tool = "oxide_context"

        @app.tool(description=search.description, annotations=READ_ONLY,
                  output_schema=search.output_schema)  # fmt: skip
        def oxide_search(
            query: mcp_server.Query,
            repository: mcp_server.Repo = None,
            mode: mcp_server.Mode = "search",
            limit: Annotated[int | None, Field(ge=0, le=mcp_server.MAX_RESULTS)] = None,
            budget: Annotated[int | None, Field(ge=0, le=mcp_server.MAX_BUDGET)] = None,
            paths: mcp_server.Paths = (),
            symbols: mcp_server.Symbols = (),
        ) -> ToolResult:
            return production(query=query, repository=repository, mode=mode, limit=limit,
                              budget=budget, paths=paths, symbols=symbols)  # fmt: skip

        @app.tool(description=v2.CONTEXT, annotations=READ_ONLY)
        def oxide_context(
            target: Annotated[str, Field(description="Symbol name, `Class.method`, dotted path "
                                         "or `path.py:line`; a file path for its outline; "
                                         "empty for the package map", max_length=300)] = "",
            relation: Literal["", "callers", "callees"] = "",
            depth: Annotated[int, Field(ge=1, le=3)] = 1,
        ) -> str:  # fmt: skip
            return graph(relation or "outline", target, depth)

        return app

    tool = "oxide_search"
    # v2's closing hints name `oxide_context`; in this shape the views are modes
    v2.NEXT.update(
        architecture="Next: oxide_search(mode='outline', query=<file path>) for a file's outline.",
        module="Next: oxide_search(mode='outline', query=<name>) for a symbol, or read the "
        "lines you need.",
    )

    @app.tool(description=(search.description or "") + GRAPH_MODES, annotations=READ_ONLY,
              output_schema=None)  # fmt: skip
    def oxide_search(
        query: mcp_server.Query,
        repository: mcp_server.Repo = None,
        mode: Annotated[Mode, Field(description="search (default), context, callers, callees "
                                    "or outline")] = "search",
        limit: Annotated[int | None, Field(ge=0, le=mcp_server.MAX_RESULTS)] = None,
        budget: Annotated[int | None, Field(ge=0, le=mcp_server.MAX_BUDGET)] = None,
        paths: mcp_server.Paths = (),
        symbols: mcp_server.Symbols = (),
        depth: Annotated[int, Field(ge=1, le=3, description="callers/callees only")] = 1,
    ) -> ToolResult | str:  # fmt: skip
        if mode in ("search", "context"):
            return production(query=query, repository=repository, mode=mode, limit=limit,
                              budget=budget, paths=paths, symbols=symbols)  # fmt: skip
        return graph(mode, query, depth)

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--shape", choices=("single", "split"), required=True)
    parser.add_argument("--log")
    args = parser.parse_args()
    build(args.repo, args.data_dir, args.shape, Log(args.log)).run(show_banner=False)


if __name__ == "__main__":
    main()
