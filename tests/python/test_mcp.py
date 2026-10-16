"""``oxide mcp`` through FastMCP's real client: in memory, over stdio and over stateless
HTTP, against actually indexed repositories with private data locations."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import pytest
from conftest import env, write
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

import oxide
from oxide import mcp_server
from oxide.mcp_server import server

pytestmark = pytest.mark.anyio
RETRY = "symbol:oxidepy/retry.py#RetryPolicy"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def indexed(repo_copy: Path, fts: Path) -> Path:
    oxide.index_repository(repo_copy)
    return repo_copy


async def call(client: Client[Any], tool: str, **arguments: Any) -> Any:
    return await client.call_tool(tool, arguments, raise_on_error=False)


def text(result: Any) -> str:
    return str(result.content[0].text)


class Gate:
    """``mcp_server._lock`` that signals when a call waits for it and when one left it."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.waiting = threading.Event()
        self.left = threading.Event()

    def __enter__(self) -> None:
        self.waiting.set()
        self.lock.acquire()

    def __exit__(self, *exc: object) -> None:
        self.lock.release()
        self.left.set()


def assert_unlocked(repo: Path, data: Path | None = None) -> None:
    """No call left the store owned: a new owner (as `oxide index` would be) gets it."""
    with oxide.open_repository(repo, data_dir=data):
        pass


async def test_tools_are_discoverable_with_bounded_schemas(indexed: Path) -> None:
    async with Client(server([str(indexed)])) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert set(tools) == {"oxide_search", "oxide_context"}
    for tool in tools.values():
        assert tool.annotations is not None and tool.annotations.read_only_hint
        assert tool.description and tool.output_schema
        assert tool.input_schema["required"] == ["query"]
    search = tools["oxide_search"].input_schema["properties"]
    assert search["limit"]["maximum"] == mcp_server.MAX_RESULTS
    assert search["query"]["maxLength"] == 2000 and search["symbols"]["maxItems"] == 32
    budget = tools["oxide_context"].input_schema["properties"]["budget"]
    assert budget["maximum"] == mcp_server.MAX_BUDGET


async def test_search_and_context_return_structured_evidence(indexed: Path) -> None:
    async with Client(server([str(indexed)])) as client:
        found = await call(client, "oxide_search", query="RetryPolicy", limit=5)
        bundle = await call(client, "oxide_context", query="RetryPolicy", budget=300)
    assert not found.is_error
    entry = found.structured_content["entries"][0]
    assert entry["entity"]["key"] == RETRY
    assert entry["entity"]["source"]["path"] == "oxidepy/retry.py"
    assert found.structured_content["lexical"]["state"] == "ran"
    assert "self.max_attempts = max_attempts" not in text(found)  # locations, not bodies
    assert not bundle.is_error
    data = bundle.structured_content
    assert data["used"] <= data["budget"] == 300 and data["items"]
    assert data["snapshot"] == found.structured_content["snapshot"]
    payload = "".join(item["text"] for item in data["items"])
    assert text(bundle).startswith("# oxide context: ") and text(bundle).endswith(payload)
    assert "### oxidepy/retry.py:" in payload


async def test_empty_results_are_successes(indexed: Path) -> None:
    async with Client(server([str(indexed)])) as client:
        found = await call(client, "oxide_search", query="zzqqxxvv")
        bundle = await call(client, "oxide_context", query="zzqqxxvv")
    assert not found.is_error and found.structured_content["entries"] == []
    assert not bundle.is_error and bundle.structured_content["items"] == []


async def test_bad_arguments_and_repositories_are_tool_errors(
    indexed: Path, tmp_path: Path, private_data: Path
) -> None:
    unindexed = write(tmp_path / "fresh", {"a.py": "def a(): pass\n"})
    async with Client(server([str(indexed)])) as served:
        for arguments in (
            {"query": "x", "limit": 101},
            {"query": "x" * 2001},
            {"query": "x", "symbols": ["s"] * 33},
            {"limit": 3},
            {"query": "x", "repository": str(unindexed)},
        ):
            result = await call(served, "oxide_search", **arguments)
            assert result.is_error, arguments
        result = await call(served, "oxide_context", query="x", budget=mcp_server.MAX_BUDGET + 1)
        assert result.is_error
    async with Client(server()) as open_server:
        for arguments, expected in (
            ({}, "`repository` is required"),
            ({"repository": str(tmp_path / "missing")}, "error[invalid_path]"),
            ({"repository": str(unindexed)}, "error[not_indexed]"),
        ):
            result = await call(open_server, "oxide_search", query="x", **arguments)
            assert result.is_error and expected in text(result), text(result)
            assert str(private_data) not in text(result)
    # Read-only calls never create a store: only the indexed repository has one.
    assert len(list((private_data / "repos").iterdir())) == 1


async def test_output_is_bounded(indexed: Path) -> None:
    async with Client(server([str(indexed)])) as client:
        found = await call(client, "oxide_search", query="retry token auth http", limit=3)
        bundle = await call(client, "oxide_context", query="retry", budget=40)
    assert len(found.structured_content["entries"]) <= 3
    assert bundle.structured_content["used"] <= 40


async def test_requests_hold_nothing_and_see_a_new_index(indexed: Path) -> None:
    async with Client(server([str(indexed)])) as client:
        first = await call(client, "oxide_search", query="brand_new_function")
        assert_unlocked(indexed)
        write(indexed, {"oxidepy/extra.py": "def brand_new_function():\n    return 1\n"})
        assert oxide.index_repository(indexed).status == "built"
        second = await call(client, "oxide_search", query="brand_new_function")
    keys = [e["entity"]["key"] for e in second.structured_content["entries"]]
    assert "symbol:oxidepy/extra.py#brand_new_function" in keys
    assert first.structured_content["snapshot"] != second.structured_content["snapshot"]


async def test_concurrent_calls_are_serialized_and_identical(indexed: Path) -> None:
    results: list[tuple[str, Any]] = []
    async with Client(server([str(indexed)])) as client:

        async def one(tool: str, arguments: dict[str, Any]) -> None:
            results.append((tool, await call(client, tool, **arguments)))

        async with anyio.create_task_group() as group:
            for _ in range(6):
                group.start_soon(one, "oxide_search", {"query": "RetryPolicy"})
                group.start_soon(one, "oxide_context", {"query": "RetryPolicy", "budget": 200})
    assert len(results) == 12 and not any(r.is_error for _, r in results)
    for tool in ("oxide_search", "oxide_context"):
        assert len({json.dumps(r.structured_content) for t, r in results if t == tool}) == 1
    assert_unlocked(indexed)


async def test_cancelled_and_failed_calls_release_everything(
    indexed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    real_open = oxide.open_repository

    def counting_open(path: Any, **kwargs: Any) -> oxide.Repository:
        opened.append(str(path))
        return real_open(path, **kwargs)

    gate = Gate()
    monkeypatch.setattr(mcp_server, "open_repository", counting_open)
    monkeypatch.setattr(mcp_server, "_lock", gate)
    async with Client(server([str(indexed)])) as client:
        # A call cancelled while it waits for the in-process lock never opens the store.
        gate.lock.acquire()
        try:
            async with anyio.create_task_group() as group:
                group.start_soon(client.call_tool, "oxide_search", {"query": "RetryPolicy"})
                assert await anyio.to_thread.run_sync(gate.waiting.wait, 30)
                group.cancel_scope.cancel()
            # The client sends its cancel notification before the cancelled call returns,
            # and the server applies it in receive order: once a later request (uncached)
            # is answered, the queued call's scope is cancelled.
            assert await client.list_tools(cache_mode="bypass")
        finally:
            gate.lock.release()
        assert await anyio.to_thread.run_sync(gate.left.wait, 30)
        assert opened == []

        # An unexpected failure is masked, and the store is still released.
        def broken(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("native detail /secret/store/path")

        with monkeypatch.context() as patch:
            patch.setattr(oxide.Repository, "search", broken)
            failed = await call(client, "oxide_search", query="RetryPolicy")
        assert failed.is_error and "/secret" not in text(failed)
        assert_unlocked(indexed)
        again = await call(client, "oxide_search", query="RetryPolicy")
    assert not again.is_error and len(opened) == 2


def mcp_command(repo: Path, data: Path, *extra: str) -> list[str]:
    return ["-m", "oxide", "mcp", "--repo", str(repo), "--data-dir", str(data), *extra]


def cli(data: Path, *args: str) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "oxide", *args, "--data-dir", str(data)]
    return subprocess.run(command, env=env(data), capture_output=True, text=True, check=False)


async def test_stdio_server_releases_the_store_between_calls(
    repo_copy: Path, tmp_path: Path, fts: Path
) -> None:
    data = tmp_path / "stdio-data"
    assert cli(data, "index", str(repo_copy)).returncode == 0
    transport = StdioTransport(
        sys.executable, mcp_command(repo_copy, data), env=env(data), keep_alive=False
    )
    async with Client(transport) as client:
        assert {t.name for t in await client.list_tools()} == {"oxide_search", "oxide_context"}
        found = await call(client, "oxide_search", query="RetryPolicy")
        assert found.structured_content["entries"][0]["entity"]["key"] == RETRY
        # The server is alive and idle: the CLI can index (it needs the store's lock).
        assert cli(data, "index", str(repo_copy)).returncode == 0
        (graph,) = data.glob("repos/*/generations/*/graph.lbug")
        graph.write_bytes(b"\0" * graph.stat().st_size)
        damaged = await call(client, "oxide_context", query="RetryPolicy")
        assert damaged.is_error and "error[store_corrupt]" in text(damaged)
        assert str(data) not in text(damaged)
        rebuilt = cli(data, "index", str(repo_copy))
        assert rebuilt.returncode == 0 and rebuilt.stdout.startswith("built:")
        again = await call(client, "oxide_context", query="RetryPolicy", budget=300)
        assert not again.is_error and again.structured_content["used"] <= 300


def test_stdio_framing_and_shutdown(repo_copy: Path, tmp_path: Path, fts: Path) -> None:
    data = tmp_path / "stdio-data"
    assert cli(data, "index", str(repo_copy)).returncode == 0
    process = subprocess.Popen(
        [sys.executable, *mcp_command(repo_copy, data)],
        env=env(data),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "raw", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "oxide_search", "arguments": {"query": "RetryPolicy"}},
        },
    ]
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write("".join(json.dumps(m) + "\n" for m in messages))
    process.stdin.flush()
    replies = [json.loads(process.stdout.readline()) for _ in range(2)]
    # Closing stdin is how a host shuts the server down; nothing else reaches stdout.
    stdout, stderr = process.communicate(timeout=60)
    assert process.returncode == 0 and stdout == "", stderr
    assert [r["id"] for r in replies] == [1, 2]
    assert replies[1]["result"]["structuredContent"]["entries"][0]["entity"]["key"] == RETRY


@pytest.fixture
def http_server(repo_copy: Path, tmp_path: Path, fts: Path) -> Iterator[str]:
    data = tmp_path / "http-data"
    assert cli(data, "index", str(repo_copy)).returncode == 0
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, *mcp_command(repo_copy, data, "--http", "--port", str(port))],
        env=env(data),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(200):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
                break
            except OSError:
                assert process.poll() is None, "HTTP server exited during startup"
                time.sleep(0.05)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        process.terminate()
        process.wait(timeout=30)


async def test_stateless_http_answers_independent_clients(http_server: str) -> None:
    keys = []
    for _ in range(2):  # separate clients: nothing carries over between requests
        async with Client(http_server) as client:
            found = await call(client, "oxide_search", query="RetryPolicy")
            keys.append(found.structured_content["entries"][0]["entity"]["key"])
    assert keys == [RETRY, RETRY]
    async with Client(http_server) as client:
        bundle = await call(client, "oxide_context", query="RetryPolicy", budget=200)
    assert not bundle.is_error and bundle.structured_content["used"] <= 200


def test_http_rejects_foreign_hosts_and_origins(http_server: str) -> None:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode()
    for headers, status in (
        ({"Host": "evil.example"}, 421),
        ({"Origin": "http://evil.example"}, 403),
    ):
        request = urllib.request.Request(
            http_server,
            data=body,
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=10)
        assert error.value.code == status


def test_http_refuses_non_loopback_hosts(tmp_path: Path) -> None:
    result = cli(tmp_path / "http-data", "mcp", "--http", "--host", "0.0.0.0")
    assert result.returncode == 2 and "invalid choice" in result.stderr
