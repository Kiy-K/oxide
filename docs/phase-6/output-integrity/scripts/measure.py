"""Deterministic measurements (preregistration.md § Deterministic measurements).

    measure.py mcp      real MCP stdio round trip of every arm for every task -> results/mcp.json
    measure.py sizes    tokens, bytes, units and render time per arm -> results/sizes.json

Run with ``uv run --with tiktoken`` (nothing is added to the lock). ``mcp`` starts
``oxide mcp`` (C) and ``variant_mcp.py`` (the other arms) as real stdio servers, and
compares what a client receives with the renderer and the in-process API value.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import tiktoken
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from pydantic import TypeAdapter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpora import STORE, mcp_query, tasks
from render import SEARCH_ARMS, context_text, counted, plain, search_text

import oxide
from oxide.core.packing import units
from oxide.mcp_server import DEFAULT_BUDGET, DEFAULT_LIMIT, MAX_BUDGET, MAX_RESULTS
from oxide.models import ContextBundle, SearchResult

REPO = Path(__file__).resolve().parents[4]
RESULTS = Path(__file__).resolve().parents[1] / "results"
ENCODINGS = {name: tiktoken.get_encoding(name) for name in ("o200k_base", "cl100k_base")}
MAX_LINES, MAX_BYTES = 2000, 51_200  # OpenCode 2.0.32 tool-output defaults (read in its bundle)
CALLS = (("search", DEFAULT_LIMIT), ("search", MAX_RESULTS),
         ("context", DEFAULT_BUDGET), ("context", MAX_BUDGET))  # fmt: skip
SCHEMA = TypeAdapter(SearchResult | ContextBundle)


def clip(text: str) -> str:
    """What OpenCode 2.0.32 delivers for a tool output: whole lines only, up to its limits,
    then a marker (``ToolOutput.truncate`` in the shipped bundle, transcribed)."""
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    if len(lines) <= MAX_LINES and len(text.encode()) <= MAX_BYTES:
        return text
    kept: list[str] = []
    size = 0
    for line in lines[:MAX_LINES]:
        size += len(line.encode()) + (1 if kept else 0)
        if size > MAX_BYTES:
            break
        kept.append(line)
    shown = f"lines 1-{len(kept)}" if kept else "0 lines"
    return "\n".join(kept) + f"[showing {shown} of {len(lines)}; full output saved to …]"


def status_kept(delivered: str, mode: str, arm: str) -> bool:
    """Whether the server's own completeness status reached the model."""
    if mode == "context":  # the text's summary line, or code mode's structured omissions
        return "# oxide context:" in delivered or '"omissions"' in delivered
    if arm == "E" and "# oxide search:" in delivered:
        return True
    return ('"more_available"' if arm == "CM" else '"truncated"') in delivered


async def round_trip(arm: str, roots: list[str], calls: list[tuple[str, str, dict[str, Any]]]
                     ) -> list[dict[str, Any]]:  # fmt: skip
    if arm == "C":
        command, args = str(REPO / ".venv/bin/oxide"), ["mcp"]
    else:
        command = str(REPO / ".venv/bin/python")
        args = [str(Path(__file__).resolve().parent / "variant_mcp.py"), arm]
    for root in roots:
        args += ["--repo", root]
    args += ["--data-dir", str(STORE)]
    out = []
    async with Client(StdioTransport(command=command, args=args)) as client:
        for task, root, params in calls:
            began = time.perf_counter()
            result = await client.call_tool("oxide_search", params | {"repository": root})
            seconds = time.perf_counter() - began
            out.append({"task": task, "params": params, "arm": arm, "seconds": seconds,
                        "text": result.content[0].text,
                        "structured": result.structured_content})  # fmt: skip
    return out


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def mcp() -> None:
    found = tasks()
    roots = sorted({str(root) for _, root in found})
    calls, values = [], {}
    for task, root in found:
        with oxide.open_repository(root, data_dir=STORE, create=False) as repo:
            for mode, size in CALLS:
                key = (task["id"], mode, size)
                if mode == "search":
                    values[key] = plain(repo.search(mcp_query(task), limit=size))
                    params = {"query": mcp_query(task), "mode": mode, "limit": size}
                else:
                    values[key] = plain(repo.build_context(mcp_query(task), size))
                    params = {"query": mcp_query(task), "mode": mode, "budget": size}
                calls.append((task["id"], str(root), params))
    rows = []
    for arm in SEARCH_ARMS:
        for got in asyncio.run(round_trip(arm, roots, calls)):
            params = got["params"]
            mode, size = params["mode"], params.get("limit", params.get("budget"))
            v, s = values[(got["task"], mode, size)], got["structured"]
            searched = mode == "search"
            added = {"entries_returned", "more_available"} if arm == "CM" and searched else set()
            base = {k: x for k, x in s.items() if k not in added}
            SCHEMA.validate_python(base)
            if searched:
                expect_text = search_text(v, arm)
                expect_structured = counted(v) if arm == "CM" else v
            else:
                expect_text, expect_structured = context_text(v, "E" if arm == "E" else "C"), v
            direct, code = clip(got["text"]), clip(json.dumps(s, indent=2, ensure_ascii=False))
            rows.append({
                "task": got["task"], "mode": mode, "size": size, "arm": arm,
                "seconds": round(got["seconds"], 4),
                "text_equals_render": got["text"] == expect_text,
                "structured_equals": s == expect_structured, "base_equals_api": base == v,
                "text_is_json_of_structured": _json(got["text"]) == s if searched else None,
                "contains_production": None if searched
                else context_text(v, "C") in got["text"],
                "bytes": len(got["text"].encode()), "lines": got["text"].count("\n") + 1,
                "more": bool(v["lexical"]["truncated"]) if searched else bool(v["omissions"]),
                "direct": {"clipped": direct != got["text"], "kept_bytes": len(direct.encode()),
                           "status_kept": status_kept(direct, mode, arm)},
                "code": {"clipped": "[showing" in code[-200:], "kept_bytes": len(code.encode()),
                         "status_kept": status_kept(code, mode, arm)},
            })  # fmt: skip
        print(arm, file=sys.stderr)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "mcp.json").write_text(json.dumps(rows, indent=1) + "\n")


def count(text: str) -> dict[str, int]:
    out = {name: len(enc.encode(text, disallowed_special=())) for name, enc in ENCODINGS.items()}
    return out | {"bytes": len(text.encode()), "units": units(text)}


def p95_ms(render: Any, value: Any, arm: str, runs: int = 200) -> float:
    seconds = []
    for _ in range(runs):
        began = time.perf_counter()
        render(value, arm)
        seconds.append(time.perf_counter() - began)
    return 1e3 * sorted(seconds)[int(0.95 * runs) - 1]


def sizes() -> None:
    rows = []
    for task, root in tasks():
        with oxide.open_repository(root, data_dir=STORE, create=False) as repo:
            for mode, size in CALLS:
                if mode == "search":
                    v = plain(repo.search(mcp_query(task), limit=size))
                    render, arms, payload = search_text, SEARCH_ARMS, None
                else:
                    v = plain(repo.build_context(mcp_query(task), size))
                    render, arms = context_text, ("C", "E")
                    payload = count("".join(i["text"] for i in v["items"]))
                rows.append({"task": task["id"], "mode": mode, "size": size,
                             "sizes": {arm: count(render(v, arm)) for arm in arms},
                             "render_p95_ms": {arm: p95_ms(render, v, arm) for arm in arms},
                             "payload": payload})  # fmt: skip
        print(task["id"], file=sys.stderr)
    summary: dict[str, Any] = {}
    for mode, size in CALLS:
        rs = [r for r in rows if r["mode"] == mode and r["size"] == size]
        ratio = statistics.median
        summary[f"{mode}{size}"] = {
            arm: {
                "o200k_median": statistics.median(r["sizes"][arm]["o200k_base"] for r in rs),
                "bytes_max": max(r["sizes"][arm]["bytes"] for r in rs),
                "overhead_vs_C": round(ratio(r["sizes"][arm]["o200k_base"]
                                             / r["sizes"]["C"]["o200k_base"] - 1 for r in rs), 4),
                "units_per_o200k": round(ratio(r["sizes"][arm]["units"]
                                               / r["sizes"][arm]["o200k_base"] for r in rs), 3),
                "render_p95_ms_max": round(max(r["render_p95_ms"][arm] for r in rs), 4),
            }
            for arm in rs[0]["sizes"]
        }  # fmt: skip
        if mode == "context":
            summary[f"{mode}{size}"]["payload_units_per_o200k"] = round(ratio(
                r["payload"]["units"] / r["payload"]["o200k_base"] for r in rs), 3)  # fmt: skip
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "sizes.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=1) + "\n"
    )
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    {"mcp": mcp, "sizes": sizes}[sys.argv[1]]()
