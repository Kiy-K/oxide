#!/usr/bin/env python3
"""Experiment-only MCP stdio server exposing `agent_nav find|expand|route` over one
pinned store as three tools (variant E, D's operations as native agent tools).
Not OXIDE's MCP surface: that belongs to the TypeScript control plane.

  nav_mcp.py <agent_nav binary> <store dir> <call log>
"""
import json
import os
import subprocess
import sys

BIN, STORE, LOG = sys.argv[1:4]
REF = "a path, or path::Qualified.name as printed in OXIDE output"
TOOLS = [
    {"name": "oxide_find", "description": "OXIDE: declarations named exactly NAME in this checkout, "
     "with path::Qualified.name [line range]. Read-only; locations only.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "oxide_expand", "description": "OXIDE: typed relations of an entity (callers/callees, "
     "references, imports, tests, contained members), each with path::Qualified.name [line range] and "
     "whether it is syntactic, resolved or heuristic. Read-only; no source bodies.",
     "inputSchema": {"type": "object", "properties": {
         "ref": {"type": "string", "description": REF},
         "kind": {"type": "string", "enum": ["calls", "refs", "imports", "tests", "contains", "implements", "all"]},
         "direction": {"type": "string", "enum": ["in", "out", "both"]}}, "required": ["ref"]}},
    {"name": "oxide_route", "description": "OXIDE: a fresh ranked navigation list (locations, previews, "
     "relations) for another query. Read-only.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
]


for _tool in TOOLS:  # read-only, so the agent may call them without approval
    _tool["annotations"] = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}


def call(name, args):
    if name == "oxide_find":
        argv = ["find", STORE, args["name"]]
    elif name == "oxide_expand":
        argv = ["expand", STORE, args["ref"], args.get("kind", "all"), args.get("direction", "both")]
    elif name == "oxide_route":
        argv = ["route", STORE, args["query"]]
    else:
        return f"unknown tool {name}", True
    r = subprocess.run([BIN, *argv], capture_output=True, text=True, timeout=120,
                       env={**os.environ, "OXIDE_NAV_LOG": LOG})
    return (r.stdout if r.returncode == 0 else r.stdout + r.stderr[-2000:]), r.returncode != 0


def main():
    for line in sys.stdin:
        msg = json.loads(line)
        if "id" not in msg:
            continue  # notifications
        method, result = msg.get("method"), None
        if method == "initialize":
            result = {"protocolVersion": msg["params"].get("protocolVersion", "2025-06-18"),
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "oxide-nav", "version": "0"}}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            text, err = call(msg["params"]["name"], msg["params"].get("arguments") or {})
            result = {"content": [{"type": "text", "text": text}], "isError": err}
        reply = {"jsonrpc": "2.0", "id": msg["id"]}
        if result is None:
            reply["error"] = {"code": -32601, "message": f"unsupported method {method}"}
        else:
            reply["result"] = result
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
