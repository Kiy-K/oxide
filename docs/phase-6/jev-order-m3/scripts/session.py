"""ADR-0009 M3: the judge in long-running `oxide mcp` sessions, through the MCP surface.

    uv run python session.py soak  WORK OUT.json   # localhost provider with faults
    uv run python session.py sizes WORK OUT.json   # localhost, the larger corpora
    TYPESAFE_API_KEY=... uv run python session.py live WORK OUT.json  # sends; authorized

Every mode starts one real `oxide mcp` stdio server (``server.py``: the CLI plus a request
ledger and a hard cap) and calls ``oxide_search`` mode=context through a FastMCP client,
as an agent would. The reference for every fallback is the default bundle computed with
the public API before the server starts. ``soak`` cycles the provider through every
failure it can show, checks consent changes made while the session runs, and samples
the server's memory, threads, file descriptors and child processes (Linux /proc).
``sizes`` and ``live`` run fresh queries over three public open-source corpora, none of
them a benchmark corpus. Nothing is scored: no quality claim.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import os
import shutil
import statistics
import subprocess
import sys
import sysconfig
import threading
import time
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

import oxide
from oxide import jev

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
OXIDE = str(Path(sys.executable).parent / "oxide")
BUDGET = 4096
LIVE_CAP = 70  # 60 planned requests; the rest is headroom that is never planned
STDLIB = Path(sysconfig.get_paths()["stdlib"])
PURELIB = Path(sysconfig.get_paths()["purelib"])
CORPORA = {  # public open-source code, none used by any OXIDE benchmark
    "cpython-stdlib": (
        STDLIB,
        (
            "how does the thread pool executor shut down its worker threads",
            "where are HTTP response headers parsed",
            "how does json decode escaped unicode in strings",
            "where is the logging handler lock acquired before emitting",
            "how are zip file entries decompressed when read",
            "how does asyncio cancel a task that is waiting on a future",
            "where does the email parser split multipart messages",
            "how does shutil copy file metadata and permissions",
            "where are TLS certificates verified in the ssl module",
            "how does argparse report an unknown option",
        ),
    ),
    "pip": (
        PURELIB / "pip",
        (
            "how does pip resolve conflicting requirement versions",
            "where are wheels downloaded and cached",
            "how is the index URL authenticated with credentials from keyring",
            "where are installed distributions uninstalled and files removed",
            "how does pip parse a requirements file with options",
            "how are hash mismatches reported during install",
            "where is the progress bar rendered for downloads",
            "how does pip build a wheel from an sdist with PEP 517",
            "where are environment markers evaluated",
            "how does pip retry a failed HTTP request",
        ),
    ),
    "fastmcp": (
        PURELIB / "fastmcp",
        (
            "how are tool call arguments validated against the schema",
            "where does the stdio transport start the server subprocess",
            "how is a tool error turned into an MCP error result",
            "where are resources listed for a client",
            "how does the HTTP transport handle session ids",
            "how are prompts rendered with arguments",
            "where is OAuth token verification done",
            "how does the client reconnect after the server exits",
            "where are middleware hooks called around a tool",
            "how is server instruction text sent to the client",
        ),
    ),
}
FIXTURE_QUERIES = (
    "where failed HTTP requests are retried",
    "how is the access token refreshed when it expires",
    "where are cached values evicted",
    "send a notification when a request fails",
)
FAULTS = {  # provider mode -> the outcome the bundle must report
    "busy": "http 529",
    "limited": "http 429",
    "hang": "deadline",
    "late": "deadline",
    "garbage": "unparsable",
    "wrong": "wrong_model",
    "partial": "incomplete",
    "reset": "transport",
}
Spans = list[tuple[str, int, int, int]]


class Provider:
    """A localhost JEV whose behaviour is switched by ``mode``."""

    def __init__(self) -> None:
        self.mode = "ok"
        self.received = 0
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                sent = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.received += 1
                mode = owner.mode
                if mode == "hang":
                    time.sleep(5)
                if mode == "late":
                    time.sleep(2.5)
                if mode == "reset":
                    self.connection.close()
                    return
                names = list(sent["questions"])[: -1 if mode == "partial" else None]
                data = {
                    "model": "jev-latest" if mode == "wrong" else jev.MODEL,
                    "answers": {q: {"type": "noul", "noul": 0.5} for q in names},
                }
                reply = b"not json" if mode == "garbage" else json.dumps(data).encode()
                status = {"busy": 529, "limited": 429}.get(mode, 200)
                try:
                    self.send_response(status)
                    self.end_headers()
                    self.wfile.write(reply)
                except OSError:
                    pass  # the child was killed

            def log_message(self, *args: object) -> None:
                pass

        self.http = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.http.server_port}/"


def spans(bundle: dict[str, Any]) -> Spans:
    return [
        (i["source"]["path"], i["source"]["start"], i["source"]["end"], i["units"])
        for i in bundle["items"]
    ]


def api_spans(bundle: oxide.ContextBundle) -> Spans:
    return [(i.source.path, i.source.start, i.source.end, i.units) for i in bundle.items]


def outcome(bundle: dict[str, Any]) -> str:
    if "judge" in bundle["versions"]:
        return "judged"
    fell = [d for d in bundle["degradations"] if d.startswith("judge ")]
    return fell[0].rsplit(": ", 1)[1] if fell else "off"


def summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "p50": round(statistics.median(ordered), 3),
        "p95": round(ordered[round(0.95 * (len(ordered) - 1))], 3),
        "max": round(ordered[-1], 3),
    }


def resources(pid: int) -> dict[str, int]:
    proc = Path(f"/proc/{pid}")
    rss = next(int(ln.split()[1]) for ln in (proc / "status").open() if ln.startswith("VmRSS"))
    tasks = list((proc / "task").iterdir())
    return {
        "rss_kb": rss,
        "threads": len(tasks),
        "fds": len(list((proc / "fd").iterdir())),
        "children": sum(len((t / "children").read_text().split()) for t in tasks),
    }


def server_pid(ledger: Path) -> int:
    for proc in Path("/proc").iterdir():
        try:
            if proc.name.isdigit() and str(ledger).encode() in (proc / "cmdline").read_bytes():
                return int(proc.name)
        except OSError:
            continue
    raise LookupError("server not found")


def cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([OXIDE, *args], capture_output=True, text=True)


class Session:
    def __init__(
        self, endpoint: str, ledger: Path, cap: int, roots: list[Path], data: Path,
        env: dict[str, str],
    ) -> None:  # fmt: skip
        argv = [str(HERE / "server.py"), endpoint, str(ledger), str(cap), "--", "mcp"]
        for root in roots:
            argv += ["--repo", str(root)]
        argv += ["--data-dir", str(data)]
        self.client = Client(StdioTransport(sys.executable, argv, env=env, keep_alive=False))

    async def context(self, root: Path, query: str) -> tuple[dict[str, Any] | str, float]:
        start = time.perf_counter()
        try:
            result = await self.client.call_tool(
                "oxide_search",
                {"query": query, "repository": str(root), "mode": "context", "budget": BUDGET},
            )
            value: dict[str, Any] | str = result.structured_content or {}
        except Exception as error:  # a tool error is a failed check, not a crash
            value = f"error: {error}"
        return value, time.perf_counter() - start


def defaults(
    roots: dict[str, Path], queries: dict[str, tuple[str, ...]], data: Path
) -> dict[tuple[str, str], Spans]:
    """The default bundle's spans for every (corpus, query), indexing first."""
    out = {}
    for name, root in roots.items():
        with oxide.open_repository(root, data_dir=data) as repo:
            repo.index()
            repo.judge(False)
            for q in queries[name]:
                out[name, q] = api_spans(repo.build_context(q, BUDGET))
    return out


def authorize(root: Path, data: Path, enable: bool = True) -> None:
    with oxide.open_repository(root, data_dir=data) as repo:
        repo.judge(enable)


async def soak(work: Path) -> dict[str, Any]:
    provider = Provider()
    data, ledger = work / "data", work / "ledger.jsonl"
    roots = {n: Path(shutil.copytree(REPO / "fixtures" / "py_repo", work / n)) for n in "AB"}
    plain = defaults(roots, {n: FIXTURE_QUERIES for n in roots}, data)
    authorize(roots["A"], data)
    env = dict(os.environ, TYPESAFE_API_KEY="offline")
    session = Session(provider.url, ledger, 10**6, list(roots.values()), data, env)
    calls: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    failures: list[str] = []

    async def call(name: str, q: str, expect: str, tag: str) -> None:
        before = provider.received
        bundle, took = await session.context(roots[name], q)
        got = outcome(bundle) if isinstance(bundle, dict) else bundle
        sent = provider.received - before
        calls.append({"tag": tag, "expect": expect, "got": got, "s": round(took, 3), "sent": sent})
        ok = got == expect and isinstance(bundle, dict)
        if ok and expect != "judged":  # every fallback, and the off path, is the default
            ok = spans(bundle) == plain[name, q]
        if ok and expect in ("off", "not_authorized", "no_credential"):
            ok = sent == 0
        if not ok:
            failures.append(f"{tag}: expected {expect}, got {got}, sent {sent}")

    q, d, a = FIXTURE_QUERIES[0], str(data), str(roots["A"])
    async with session.client:
        pid = server_pid(ledger)
        samples.append({"after": 0, **resources(pid)})
        for i in range(20):
            await call("A", FIXTURE_QUERIES[i % 4], "judged", "warm")
        samples.append({"after": len(calls), **resources(pid)})
        for _ in range(5):
            for mode, expect in FAULTS.items():
                provider.mode = mode
                for i in range(5):
                    await call("A", FIXTURE_QUERIES[i % 4], expect, f"fault {mode}")
                provider.mode = "ok"
                for i in range(5):  # recovery: the very next request is judged
                    await call("A", FIXTURE_QUERIES[i % 4], "judged", f"recover {mode}")
            samples.append({"after": len(calls), **resources(pid)})
        await call("B", q, "off", "consent: a served repository never authorized")
        cli("judge", a, "--disable", "--data-dir", d)
        await call("A", q, "off", "consent: revoked by the CLI mid-session")
        cli("judge", a, "--enable", "--data-dir", d)
        await call("A", q, "judged", "consent: re-enabled by the CLI mid-session")
        consent = next((data / "repos").glob("*/judge.json"))
        saved = consent.read_bytes()
        consent.write_text(json.dumps({**json.loads(saved), "manifest": "sha256:" + "0" * 64}))
        await call("A", q, "not_authorized", "consent: a record for another manifest")
        consent.write_text("{not json")
        await call("A", q, "not_authorized", "consent: an unreadable record")
        consent.write_bytes(saved)
        target = roots["A"] / "oxidepy" / "retry.py"
        target.write_text(target.read_text() + "\n# changed\n")
        reindex = cli("index", a, "--data-dir", d)
        await call("A", q, "judged", "consent: kept across a re-index mid-session")
        # A change while a request is in flight: the request holds the store.
        provider.mode = "hang"
        inflight = asyncio.ensure_future(session.context(roots["A"], q))
        await asyncio.sleep(0.5)
        blocked = cli("judge", a, "--disable", "--data-dir", d)
        await inflight
        provider.mode = "ok"
        retried = cli("judge", a, "--disable", "--data-dir", d)
        with oxide.open_repository(roots["A"], data_dir=data) as repo:
            plain["A", q] = api_spans(repo.build_context(q, BUDGET))  # the source changed
        await call("A", q, "off", "consent: revoked after the in-flight request")
        other = work / "other-data"
        shutil.copytree(data, other)
        cli("judge", a, "--enable", "--data-dir", str(other))
        await call("A", q, "off", "consent: enabled under another data location only")
        await asyncio.sleep(1.0)
        samples.append({"after": len(calls), "settled": True, **resources(pid)})
    # The key is read from the server's own environment: a server started without one.
    authorize(roots["A"], data)
    nokey = {k: v for k, v in os.environ.items() if k != jev.KEY_ENV}
    session = Session(provider.url, ledger, 10**6, [roots["A"]], data, nokey)
    async with session.client:
        await call("A", q, "no_credential", "consent: enabled, no key in the server")
    provider.http.shutdown()
    faults = [c for c in calls if c["tag"].startswith("fault ")]
    return {
        "mode": "soak",
        "calls": len(calls),
        "provider_requests": provider.received,
        "failures": failures,
        "outcomes": {
            o: sum(c["got"] == o for c in calls) for o in sorted({c["got"] for c in calls})
        },
        "latency_s": {
            "judged": summary([c["s"] for c in calls if c["got"] == "judged"]),
            **{
                f"fault {m}": summary([c["s"] for c in faults if c["tag"] == f"fault {m}"])
                for m in FAULTS
            },
        },
        "consent": [c for c in calls if c["tag"].startswith("consent")],
        "reindex_exit": reindex.returncode,
        "cli_during_inflight": {"exit": blocked.returncode, "stderr": blocked.stderr.strip()},
        "cli_after_inflight": {"exit": retried.returncode},
        "resources": samples,
    }


async def corpora(work: Path, endpoint: str, cap: int, repeats: int) -> dict[str, Any]:
    provider = Provider() if endpoint == "local" else None
    data, ledger = work / "data", work / "ledger.jsonl"
    roots = {name: root for name, (root, _) in CORPORA.items()}
    queries = {name: qs for name, (_, qs) in CORPORA.items()}
    start = time.perf_counter()
    plain = defaults(roots, queries, data)
    index_s = round(time.perf_counter() - start, 1)
    for root in roots.values():
        authorize(root, data)
    env = dict(os.environ)
    if provider:
        env[jev.KEY_ENV] = "offline"
    session = Session(
        provider.url if provider else "live", ledger, cap, [*roots.values()], data, env
    )
    rows, failures = [], []
    async with session.client:
        pid = server_pid(ledger)
        before = resources(pid)
        for r in range(repeats):
            for name, qs in queries.items():
                for q in qs:
                    bundle, took = await session.context(roots[name], q)
                    got = outcome(bundle) if isinstance(bundle, dict) else bundle
                    if got != "judged" and not (
                        isinstance(bundle, dict) and spans(bundle) == plain[name, q]
                    ):
                        failures.append(f"{name} {q!r}: {got} is not the default bundle")
                    rows.append({"corpus": name, "repeat": r, "got": got, "s": round(took, 3)})
        await asyncio.sleep(1.0)
        after = resources(pid)
    for root in roots.values():
        authorize(root, data, enable=False)
    sent = [json.loads(line) for line in ledger.read_text().splitlines()]
    tokens = [s["billed_input_tokens"] for s in sent if s.get("billed_input_tokens")]
    if provider:
        provider.http.shutdown()
    return {
        "mode": "sizes" if provider else "live",
        "corpora": {
            n: {"root": str(p.relative_to(p.parents[1])), "py_files": len(list(p.rglob("*.py")))}
            for n, p in roots.items()
        },
        "index_and_defaults_s": index_s,
        "budget": BUDGET,
        "calls": len(rows),
        "failures": failures,
        "outcomes": {o: sum(r["got"] == o for r in rows) for o in sorted({r["got"] for r in rows})},
        "build_context_via_mcp_s": {
            "judged": summary([r["s"] for r in rows if r["got"] == "judged"]),
            "all": summary([r["s"] for r in rows]),
        },
        "adapter_call_s": summary([s["latency_s"] for s in sent if s["status"] != "capped"]),
        "requests_sent": sum(s["status"] != "capped" for s in sent),
        "statuses": {
            k: sum(str(s["status"]) == k for s in sent) for k in {str(s["status"]) for s in sent}
        },
        "candidates": summary([float(s["candidates"]) for s in sent]),
        "request_bytes": summary([float(s["request_bytes"]) for s in sent]),
        "billed_input_tokens": summary([float(t) for t in tokens]),
        "billed_input_tokens_total": sum(tokens),
        "usd": round(sum(tokens) * jev.MANIFEST["usd_per_million_input_tokens"] / 1e6, 6),
        "resources": {"before": before, "after": after},
        "rows": rows,
    }


def main(mode: str, work: Path, out: Path) -> None:
    work.mkdir(parents=True)  # fresh: no earlier ledger is counted against the cap
    try:
        if mode == "soak":
            result = asyncio.run(soak(work))
        elif mode == "sizes":
            result = asyncio.run(corpora(work, "local", 10**6, 1))
        elif mode == "live":
            if not os.environ.get(jev.KEY_ENV):
                sys.exit(f"live mode needs {jev.KEY_ENV}")
            result = asyncio.run(corpora(work, "live", LIVE_CAP, 2))
        else:
            sys.exit(f"unknown mode {mode}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    out.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]))
