#!/usr/bin/env python3
"""Minimal fake LSP server for the evidence-coordinator compatibility gate's
`lsp_enabled` condition: answers `initialize` and `textDocument/diagnostic`
with a single fixed, byte-stable diagnostic on
`oxidepy/retry.py#RetryPolicy.should_retry` (fixtures/py_repo's real seed for
the "where is retry logic" query). Everything else (prepareCallHierarchy,
references) responds `null`, which the client already treats as "no
results" -- this fixture exists to prove --lsp's wiring is byte-identical
run to run, not to exercise every LSP method; a real `ty` session is
covered separately by the lsp-integration CI job.

Not derived from fake_lsp_no_references/server.py: that fixture's whole
point is withholding referencesProvider for the capability-fallback test,
a different, unrelated scenario."""
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
            "referencesProvider": True,
            "diagnosticProvider": {"interFileDependencies": False, "workspaceDiagnostics": False},
        }}})
    elif method == "textDocument/diagnostic":
        write_message({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "kind": "full",
            "items": [{
                "range": {"start": {"line": 32, "character": 8}, "end": {"line": 32, "character": 20}},
                "severity": 4,
                "message": "fixture diagnostic (deterministic)",
            }],
        }})
    elif method == "exit":
        break
    elif "id" in msg:
        write_message({"jsonrpc": "2.0", "id": msg["id"], "result": None})
