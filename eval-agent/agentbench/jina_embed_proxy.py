#!/usr/bin/env python3
"""Tiny local proxy so OXIDE's HttpEmbedder (which sends a plain
{"model", "input"} POST with no auth header -- src/embeddings.rs's
embed_batch_raw) can use Jina's hosted /v1/embeddings API, which requires an
`Authorization: Bearer` header plus Jina-specific `task`/`normalized` fields.

Not a change to OXIDE itself: this is benchmark-harness-scoped plumbing, kept
here rather than touching src/embeddings.rs's request shape (which is shared
by every other embedder OXIDE supports).

`task` is fixed to "retrieval.passage" for every request -- OXIDE's embedder
abstraction doesn't distinguish "embedding a symbol for the index" from
"embedding a search query" at the HTTP layer (both go through the same
embed()/embed_batch_raw()), so an asymmetric passage/query split isn't
possible without changing OXIDE itself. This is a known, documented
simplification, not a bug: it costs a little retrieval quality, not
correctness.

Usage:
    JINA_API_KEY=... python3 jina_embed_proxy.py [--port 8192]
    # then: export OXIDE_EMBED_URL=http://127.0.0.1:8192/v1/embeddings
    #       export OXIDE_EMBED_MODEL=jina-embeddings-v5-omni-nano
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

JINA_URL = "https://api.jina.ai/v1/embeddings"


def load_dotenv_key(env_path: Path) -> str | None:
    if not env_path.exists():
        return None
    for line in env_path.read_text().splitlines():
        if line.startswith("JINA_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


class Handler(BaseHTTPRequestHandler):
    api_key: str = ""

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[jina-proxy] {fmt % args}\n")

    def do_POST(self):
        if self.path.rstrip("/") != "/v1/embeddings":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            incoming = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return

        jina_body = {
            "model": incoming.get("model", "jina-embeddings-v5-omni-nano"),
            "task": "retrieval.passage",
            "normalized": True,
            "input": incoming.get("input", []),
        }
        data = json.dumps(jina_body).encode()

        # OXIDE fires embedding requests faster than Jina's free-tier rate
        # limit allows (observed: sustained 429s indexing uv, ~2k symbols).
        # Retry with backoff INSIDE the proxy so OXIDE's client -- which
        # treats any failure as an empty vector and does not retry itself
        # (src/embeddings.rs's embed_batch_raw) -- just sees a slower
        # success instead of `oxide index` aborting mid-run.
        max_attempts = 8
        backoff = 1.0
        for attempt in range(max_attempts):
            req = urllib.request.Request(
                JINA_URL,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    body = r.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
                return
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < max_attempts - 1:
                    retry_after = e.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else backoff
                    self.log_message("429, retrying in %.1fs (attempt %d/%d)",
                                      delay, attempt + 1, max_attempts)
                    time.sleep(delay)
                    backoff = min(backoff * 2, 60.0)
                    continue
                err_body = e.read()
                self.send_response(e.code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(err_body)
                return
            except Exception as e:  # noqa: BLE001 -- surface any failure to the caller as a 502
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
                return


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8192)
    args = ap.parse_args()

    key = os.environ.get("JINA_API_KEY") or load_dotenv_key(
        Path(__file__).resolve().parents[2] / ".env"
    )
    if not key:
        print("JINA_API_KEY not set and not found in .env", file=sys.stderr)
        raise SystemExit(1)

    Handler.api_key = key
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[jina-proxy] listening on http://127.0.0.1:{args.port}/v1/embeddings", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
