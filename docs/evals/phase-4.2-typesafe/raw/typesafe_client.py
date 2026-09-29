#!/usr/bin/env python3
"""Minimal TypeSafe System One HTTP client (stdlib only -- no new dependency).

Reads TYPESAFE_API_KEY from the repo's .env file. Never logs, prints, or
returns the key itself; callers only ever see call() results.
"""
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
ENDPOINT = "https://api.typesafe.ai/v1/systemone"


def _load_api_key() -> str:
    env_path = ROOT / ".env"
    text = env_path.read_text()
    m = re.search(r"^TYPESAFE_API_KEY=(.*)$", text, re.MULTILINE)
    if not m:
        raise RuntimeError("TYPESAFE_API_KEY not found in .env")
    key = m.group(1).strip().strip('"').strip("'")
    if not key:
        raise RuntimeError("TYPESAFE_API_KEY is empty in .env")
    return key


_API_KEY = None


def api_key() -> str:
    global _API_KEY
    if _API_KEY is None:
        _API_KEY = _load_api_key()
    return _API_KEY


def noul(state, instructions: str, criteria: dict | None = None,
         model: str = "jev-latest", max_retries: int = 4) -> dict:
    """Ask one Noul (yes/no probability) question. Returns
    {"noul": float, "input_tokens": int, "output_tokens": int, "latency_s": float}.
    """
    question = {"type": "noul", "instructions": instructions}
    if criteria:
        question["criteria"] = criteria
    body = json.dumps({
        "state": state,
        "model": model,
        "questions": {"q": question},
    }).encode()

    req = urllib.request.Request(
        ENDPOINT, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {api_key()}",
            "Content-Type": "application/json",
        },
    )

    delay = 1.0
    for attempt in range(max_retries):
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                elapsed = time.perf_counter() - start
                data = json.loads(resp.read())
                answer = data["answers"]["q"]
                usage = data.get("usage", {})
                return {
                    "noul": answer["noul"],
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "latency_s": elapsed,
                    "model": data.get("model"),
                }
        except urllib.error.HTTPError as e:
            if e.code in (429, 529) and attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError(f"TypeSafe API error {e.code}: {e.read().decode()[:500]}") from e
    raise RuntimeError("TypeSafe API: exhausted retries")
