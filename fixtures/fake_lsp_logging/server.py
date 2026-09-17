#!/usr/bin/env python3
"""Minimal fake LSP server that logs every request/notification method (plus,
for document notifications, the URI) to the file named by the LOG_PATH
environment variable, one per line. Used by tests that need to observe
protocol traffic (didOpen/didChange/didClose) precisely, which a real
server's responses don't expose."""
import sys, json, os

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

log_path = os.environ["LOG_PATH"]
log = open(log_path, "a")

while True:
    msg = read_message()
    if msg is None:
        break
    method = msg.get("method")
    uri = (msg.get("params") or {}).get("textDocument", {}).get("uri", "")
    log.write(f"{method} {uri}\n")
    log.flush()
    if method == "initialize":
        write_message({"jsonrpc": "2.0", "id": msg["id"], "result": {"capabilities": {
            "positionEncoding": "utf-8",
        }}})
    elif method == "exit":
        break
    elif "id" in msg:
        write_message({"jsonrpc": "2.0", "id": msg["id"], "result": None})
