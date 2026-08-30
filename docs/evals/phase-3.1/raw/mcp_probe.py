#!/usr/bin/env python3
"""Scripted stdio JSON-RPC client against `oxide mcp` (the real rmcp server).

Not an agent harness -- this is deterministic protocol-level evidence for
Phase 3.1 sections 0 (schema sizes), 16 (malformed-arg + protocol-error
behavior), and 17 (lifecycle/version negotiation). Every prior phase's
schema-size numbers came from the pre-rmcp hand-rolled adapter; this script
re-measures against the live server so §0 numbers are not copied forward.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OXIDE_BIN = ROOT / "target/release/oxide"


class McpClient:
    def __init__(self, cwd):
        self.proc = subprocess.Popen(
            [str(OXIDE_BIN), "mcp"],
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._id = 0
        self.raw_log = []

    def _next_id(self):
        self._id += 1
        return self._id

    def send(self, method, params=None, msg_id=True):
        req = {"jsonrpc": "2.0", "method": method}
        if msg_id:
            req["id"] = self._next_id()
        if params is not None:
            req["params"] = params
        line = json.dumps(req)
        self.raw_log.append({"dir": "send", "bytes": len(line.encode()), "body": req})
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        if not msg_id:
            return None
        return self._read()

    def _read(self):
        line = self.proc.stdout.readline()
        if not line:
            err = self.proc.stderr.read()
            raise RuntimeError(f"no response (stderr: {err})")
        self.raw_log.append({"dir": "recv", "bytes": len(line.encode()), "body": json.loads(line)})
        return json.loads(line)

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        self.proc.wait(timeout=5)


def chars_tokens(obj):
    s = json.dumps(obj, separators=(",", ":"))
    return len(s), round(len(s) / 4, 1)


def run_probe(repo_dir, label, index_first=True):
    result = {"label": label}
    if index_first:
        subprocess.run([str(OXIDE_BIN), "index", str(repo_dir), "--json"], capture_output=True, text=True, timeout=60)

    c = McpClient(repo_dir)
    try:
        init = c.send(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "oxide-phase-3.1-probe", "version": "0.1.0"},
            },
        )
        result["initialize_response"] = init
        c.send("notifications/initialized", msg_id=False)

        tools = c.send("tools/list")
        result["tools_list_response"] = tools
        tl_chars, tl_tokens = chars_tokens(tools["result"]["tools"])
        result["tools_schema_chars"] = tl_chars
        result["tools_schema_tokens_est"] = tl_tokens
        result["server_instructions"] = init["result"].get("instructions")
        if result["server_instructions"]:
            ic, it = chars_tokens(result["server_instructions"])
            result["instructions_chars"] = ic
            result["instructions_tokens_est"] = it

        # malformed args: missing required field
        malformed = c.send("tools/call", {"name": "context", "arguments": {}})
        result["malformed_missing_required"] = malformed

        # malformed args: wrong type
        malformed2 = c.send("tools/call", {"name": "context", "arguments": {"task": 5}})
        result["malformed_wrong_type"] = malformed2

        # unknown tool
        unknown = c.send("tools/call", {"name": "nonexistent_tool", "arguments": {}})
        result["unknown_tool"] = unknown

        # real context call -> response size
        ctx = c.send("tools/call", {"name": "context", "arguments": {"task": "how does retry backoff work"}})
        result["context_call_response"] = ctx
        if ctx.get("result"):
            cc, ct = chars_tokens(ctx["result"])
            result["context_response_chars"] = cc
            result["context_response_tokens_est"] = ct

        search = c.send("tools/call", {"name": "search", "arguments": {"query": "retry"}})
        result["search_call_response"] = search
        if search.get("result"):
            sc, st = chars_tokens(search["result"])
            result["search_response_chars"] = sc
            result["search_response_tokens_est"] = st

        # RepositoryService failure path: point at a dir with no index
        empty_dir = repo_dir / "_no_index_subdir"
        empty_dir.mkdir(exist_ok=True)
        missing_idx = c.send("tools/call", {"name": "context", "arguments": {"task": "x", "path": str(empty_dir)}})
        result["index_missing_error"] = missing_idx

        # concurrent-ish sequential repeat (session reuse) for §17
        repeat = c.send("tools/call", {"name": "search", "arguments": {"query": "retry"}})
        result["repeat_call_response_matches"] = repeat == search

    finally:
        result["raw_log"] = c.raw_log
        c.close()
    return result


if __name__ == "__main__":
    import tempfile
    import shutil

    src = ROOT / "fixtures/py_repo"
    with tempfile.TemporaryDirectory(prefix="oxide-mcp-probe-") as td:
        repo = Path(td) / "repo"
        shutil.copytree(src, repo)
        out = run_probe(repo, "rmcp-live-2024-11-05")

        # second probe: client requests a different protocol version, to
        # observe rmcp's negotiated-echo behavior (post-migration change
        # documented in docs/mcp-phase-2-report.md).
        repo2 = Path(td) / "repo2"
        shutil.copytree(src, repo2)
        c2 = McpClient(repo2)
        subprocess.run([str(OXIDE_BIN), "index", str(repo2), "--json"], capture_output=True, text=True, timeout=60)
        init2 = c2.send(
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "probe2", "version": "0"}},
        )
        c2.close()

        out["version_negotiation_probe"] = {
            "requested": "2025-06-18",
            "negotiated_response": init2,
        }

        out_path = ROOT / "docs/evals/phase-3.1/raw/mcp_probe_result.json"
        out_path.write_text(json.dumps(out, indent=2, default=str))
        print(f"wrote {out_path}")
        print(f"tools/list schema: {out['tools_schema_chars']}c / {out['tools_schema_tokens_est']}t")
        if "instructions_chars" in out:
            print(f"server instructions: {out['instructions_chars']}c / {out['instructions_tokens_est']}t")
