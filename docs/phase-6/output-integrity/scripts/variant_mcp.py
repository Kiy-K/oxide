"""Experiment-only MCP server: production ``oxide_search``, its result re-rendered as an arm.

    variant_mcp.py ARM [--repo PATH ...] --data-dir DIR      (stdio)

The production ``oxide.mcp_server.server()`` plus one FastMCP middleware that rewrites
only a successful tool result. The tool, its schema, description, arguments, limits and
errors are the production ones. ``ARM=C`` returns the production result untouched.
Search arms change the text; ``CM`` also adds its two fields to the structured content.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from fastmcp.server.middleware import Middleware
from fastmcp.tools import ToolResult

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render import context_text, counted, search_text

from oxide.mcp_server import server


class Variant(Middleware):
    def __init__(self, arm: str) -> None:
        self.arm = arm

    async def on_call_tool(self, context: Any, call_next: Any) -> ToolResult:
        result: ToolResult = await call_next(context)
        v = result.structured_content
        if self.arm == "C" or not isinstance(v, dict):
            return result
        if "entries" in v:
            structured = counted(v) if self.arm == "CM" else v
            return ToolResult(content=search_text(v, self.arm), structured_content=structured)
        if self.arm == "E":
            return ToolResult(content=context_text(v, "E"), structured_content=v)
        return result  # CM, CL and P change search only


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("arm", choices=("C", "CM", "CL", "P", "E"))
    parser.add_argument("--repo", action="append", default=[])
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()
    app = server(args.repo, args.data_dir)
    app.add_middleware(Variant(args.arm))
    app.run(show_banner=False)


if __name__ == "__main__":
    main()
