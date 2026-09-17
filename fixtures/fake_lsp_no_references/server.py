#!/usr/bin/env python3
"""Minimal fake LSP server: answers `initialize` with every capability
OXIDE's client requests EXCEPT `referencesProvider`, so the capability-
fallback test can assert a clean degrade instead of a crash."""
import sys, json

def read_message():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line or line in (b"\r\n", b"\n"):
            break
        if b":" in line:
            k, v = line.decode().split(":", 1)
            headers[k.strip().lower()] = v.strip()
    length = int(headers.get("content-length", 0))
    return json.loads(sys.stdin.buffer.read(length))

def write_message(obj):
    body = json.dumps(obj).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    sys.stdout.buffer.flush()

while True:
    msg = read_message()
    if msg is None:
        break
    method = msg.get("method")
    if method == "initialize":
        write_message({"jsonrpc": "2.0", "id": msg["id"], "result": {"capabilities": {
            "positionEncoding": "utf-8", "definitionProvider": True,
            "callHierarchyProvider": True, "implementationProvider": True,
            "diagnosticProvider": {"interFileDependencies": False, "workspaceDiagnostics": False},
        }}})
    elif method == "textDocument/references":
        write_message({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "not supported"}})
    elif method == "exit":
        break
    elif "id" in msg:
        write_message({"jsonrpc": "2.0", "id": msg["id"], "result": None})
