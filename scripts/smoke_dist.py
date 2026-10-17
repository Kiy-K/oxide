"""Smoke test of an installed OXIDE distribution (wheel or sdist), not the checkout.

    VENV/python -I scripts/smoke_dist.py FIXTURES_DIR
    python scripts/smoke_dist.py --environment   # this platform's record, as JSON

Run with the interpreter of a fresh environment that has only the built artifact and
its locked dependencies installed, after ``oxide prepare``. It drives the installed
``oxide`` console script in subprocesses against a copy of ``fixtures/py_repo`` and
``fixtures/ts_repo`` plus a non-ASCII, CRLF file, in a private data location whose
path has spaces and non-ASCII characters:

- the package is imported from the environment, never the source tree;
- ``oxide index`` builds, a second run reopens the persisted store as ``unchanged``,
  and an edit is published as a new generation;
- ``search`` runs the FTS lexical channel and finds a non-ASCII declaration, and
  ``context`` honours a ``--file`` hint within budget;
- ``oxide mcp`` answers ``initialize`` and ``oxide_search`` over stdio and exits when
  stdin closes;
- the store lock is exclusive across processes and released by its owner.

Exits non-zero with a message on the first failure. Stdlib only.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from importlib.metadata import version
from pathlib import Path
from typing import Any

MESSAGES = [
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "smoke", "version": "0"},
        },
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "oxide_search", "arguments": {"query": "TTLCache"}},
    },
]
HOLD = (
    "import sys, oxide\n"
    "with oxide.open_repository(sys.argv[1], data_dir=sys.argv[2]):\n"
    "    print('held', flush=True); sys.stdin.read()"
)


def check(condition: bool, message: str) -> None:
    if not condition:
        sys.exit(f"smoke: FAILED: {message}")


def main(fixtures: Path) -> None:
    scripts = sysconfig.get_path("scripts")
    exe = shutil.which("oxide", path=scripts)
    if exe is None:
        sys.exit(f"smoke: FAILED: no installed oxide console script in {scripts}")
    where = subprocess.run(
        [sys.executable, "-I", "-c", "import oxide; print(oxide.__file__)"],
        capture_output=True,
        text=True,
        cwd=tempfile.gettempdir(),
    )
    check(where.returncode == 0, f"import oxide failed: {where.stderr}")
    origin = Path(where.stdout.strip()).resolve()
    check(
        not origin.is_relative_to(fixtures.resolve().parent),
        f"oxide imported from the checkout ({origin}), not the installed artifact",
    )
    print(f"smoke: oxide at {origin}")

    with tempfile.TemporaryDirectory(prefix="oxide smoke ü ") as temp:
        root, data = Path(temp) / "repo ü", Path(temp) / "data dir é"
        shutil.copytree(fixtures / "py_repo", root)
        shutil.copytree(fixtures / "ts_repo", root / "web")
        (root / "naïve_ünicode.py").write_bytes(b"def caf\xc3\xa9_total():\r\n    return 1\r\n")
        env = {**os.environ, "OXIDE_DATA_DIR": str(data)}

        def oxide(*args: str, ok: bool = True) -> subprocess.CompletedProcess[str]:
            run = subprocess.run(
                [exe, *args], env=env, capture_output=True, text=True, encoding="utf-8"
            )
            if ok:
                check(run.returncode == 0, f"oxide {' '.join(args)}: {run.stderr}")
            return run

        def result(*args: str) -> Any:
            return json.loads(oxide(*args, "--json").stdout)

        built = result("index", str(root))
        check(built["status"] == "built", f"first index: {built['status']}")
        check(built["lexical"] == "available", f"FTS unavailable: {built['lexical']}")
        check(not built["partial"], f"partial parses: {built['partial']}")
        check(result("index", str(root))["status"] == "unchanged", "reopened store rebuilt")
        (root / "oxidepy" / "retry.py").write_text("# emptied\n", encoding="utf-8")
        edited = result("index", str(root))
        check(edited["status"] == "built", "an edit was not published")
        check(edited["modified"] == ["oxidepy/retry.py"], f"modified: {edited['modified']}")
        print(f"smoke: indexed {edited['files']} files, {edited['entities']} entities")

        found = result("search", str(root), "RetryPolicy")
        check(found["lexical"]["state"] == "ran", f"lexical channel: {found['lexical']}")
        keys = [e["entity"]["key"] for e in found["entries"]]
        check(not any(k.startswith("symbol:oxidepy/retry.py#") for k in keys), "stale search")
        keys = [e["entity"]["key"] for e in result("search", str(root), "café total")["entries"]]
        check("symbol:naïve_ünicode.py#café_total" in keys, f"non-ASCII entity: {keys}")
        hint = ("--file", "oxidepy/cache.py", "--budget", "400")
        context = result("context", str(root), "cache", *hint)
        check(0 < context["used"] <= 400, f"context used {context['used']} of 400")
        first = context["items"][0]["source"]["path"]
        check(first == "oxidepy/cache.py", f"the hinted file did not come first: {first}")
        print(f"smoke: search and context ok ({context['used']}/400 units)")

        server = subprocess.Popen(
            [exe, "mcp", "--repo", str(root), "--data-dir", str(data)],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        assert server.stdin is not None and server.stdout is not None
        server.stdin.write("".join(json.dumps(m) + "\n" for m in MESSAGES))
        server.stdin.flush()
        replies = [json.loads(server.stdout.readline()) for _ in range(2)]
        # Closing stdin is how a host shuts the server down.
        rest, stderr = server.communicate(timeout=60)
        check(server.returncode == 0 and rest == "", f"mcp exit {server.returncode}: {stderr}")
        check([r.get("id") for r in replies] == [1, 2], f"mcp replies: {replies}")
        entries = replies[1]["result"]["structuredContent"]["entries"]
        check(entries[0]["entity"]["key"].endswith("#TTLCache"), f"mcp search: {entries[:1]}")
        print("smoke: mcp stdio ok")

        holder = subprocess.Popen(
            [sys.executable, "-I", "-c", HOLD, str(root), str(data)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        assert holder.stdout is not None and holder.stdin is not None
        check(holder.stdout.readline().strip() == "held", "holder could not open the store")
        blocked = oxide("index", str(root), ok=False)
        holder.stdin.close()
        holder.wait(timeout=60)
        check(
            blocked.returncode == 2 and "ownership_conflict" in blocked.stderr,
            f"second owner: exit {blocked.returncode}, {blocked.stderr}",
        )
        oxide("index", str(root))  # the lock is released with its owner
        print("smoke: store lock exclusive and released")
    print("smoke: OK")


def environment() -> dict[str, Any]:
    from oxide.storage.ladybug import fts_location, fts_unavailable

    return {
        "platform": f"{sys.platform}-{platform.machine()}",
        "os": platform.platform(),
        "python": platform.python_version(),
        "packages": {p: version(p) for p in ("oxide", "ladybug", "tree-sitter", "fastmcp")},
        "fts": fts_unavailable(fts_location()) or "available",
    }


if __name__ == "__main__":
    if sys.argv[1] == "--environment":
        print(json.dumps(environment(), indent=1))
    else:
        main(Path(sys.argv[1]))
