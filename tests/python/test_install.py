"""``oxide install`` and ``oxide setup``: client configuration in private homes.

The Claude and Codex CLIs are replaced by small scripts that read and write the same files
the real commands use (``$CLAUDE_CONFIG_DIR/.claude.json``, a Codex state file), so the
installer's real command sequence runs without any client installed."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from oxide.cli import main
from oxide.install import server_command

posix_only = pytest.mark.skipif(os.name == "nt", reason="fake clients are POSIX scripts")

FAKE_CLAUDE = """\
import json, os, sys
path = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], ".claude.json")
data = json.load(open(path)) if os.path.exists(path) else {}
servers = data.setdefault("mcpServers", {})
args = sys.argv[1:]
if args[:2] == ["mcp", "get"]:
    if "oxide" not in servers:
        print("No MCP server found with name: oxide", file=sys.stderr)
        sys.exit(1)
    entry = servers["oxide"]
    print(f"oxide:\\n  Scope: User config (available in all your projects)\\n"
          f"  Command: {entry['command']}\\n  Args: {' '.join(entry['args'])}")
    sys.exit(0)
if args[:2] == ["mcp", "remove"]:
    servers.pop("oxide", None)
elif args[:2] == ["mcp", "add"]:
    command = args[args.index("--") + 1:]
    servers["oxide"] = {"type": "stdio", "command": command[0], "args": command[1:]}
else:
    sys.exit(2)
json.dump(data, open(path, "w"))
"""

FAKE_CODEX = """\
import json, os, sys
path = os.environ["FAKE_CODEX_STATE"]
servers = json.load(open(path)) if os.path.exists(path) else {}
args = sys.argv[1:]
if args[:2] == ["mcp", "get"]:
    if "oxide" not in servers:
        print("No MCP server named 'oxide' found.", file=sys.stderr)
        sys.exit(1)
    entry = servers["oxide"]
    print(json.dumps({"name": "oxide", "transport": {"type": "stdio", **entry}}))
    sys.exit(0)
if args[:2] == ["mcp", "remove"]:
    servers.pop("oxide", None)
elif args[:2] == ["mcp", "add"]:
    command = args[args.index("--") + 1:]
    servers["oxide"] = {"command": command[0], "args": command[1:]}
else:
    sys.exit(2)
json.dump(servers, open(path, "w"))
"""

ENTRY = {"command": sys.executable, "args": ["-m", "oxide", "mcp"]}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return home


def _fake_client(bin_dir: Path, name: str, body: str) -> None:
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / name
    script.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def clients(tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    _fake_client(bin_dir, "claude", FAKE_CLAUDE)
    _fake_client(bin_dir, "codex", FAKE_CODEX)
    config = tmp_path / "claude-config"
    config.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("FAKE_CODEX_STATE", str(tmp_path / "codex.json"))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return tmp_path


def test_server_command_runs_the_installing_interpreter() -> None:
    assert server_command() == [sys.executable, "-m", "oxide", "mcp"]


@posix_only
def test_claude_registers_user_scope_once(
    clients: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["install", "--client", "claude"]) == 0
    assert "claude: added" in capsys.readouterr().out
    servers = json.loads((clients / "claude-config" / ".claude.json").read_text())["mcpServers"]
    assert servers["oxide"]["command"] == ENTRY["command"]
    assert servers["oxide"]["args"] == ENTRY["args"]

    assert main(["install", "--client", "claude"]) == 0
    assert "claude: unchanged" in capsys.readouterr().out


@posix_only
def test_claude_replaces_stale_entry_and_keeps_other_settings(
    clients: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = clients / "claude-config" / ".claude.json"
    stale = {"command": "/old/python", "args": ["-m", "oxide", "mcp", "--repo", "/old"]}
    other = {"command": "npx", "args": ["-y", "memory"]}
    config.write_text(
        json.dumps({"numStartups": 3, "mcpServers": {"oxide": stale, "memory": other}})
    )

    assert main(["install", "--client", "claude"]) == 0
    assert "claude: updated" in capsys.readouterr().out
    data = json.loads(config.read_text())
    assert data["numStartups"] == 3
    assert data["mcpServers"]["memory"] == other
    assert data["mcpServers"]["oxide"]["command"] == ENTRY["command"]


@posix_only
def test_codex_registers_global_entry_once(
    clients: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["install", "--client", "codex"]) == 0
    assert "codex: added" in capsys.readouterr().out
    assert json.loads((clients / "codex.json").read_text())["oxide"] == ENTRY

    assert main(["install", "--client", "codex"]) == 0
    assert "codex: unchanged" in capsys.readouterr().out


def test_cursor_keeps_other_servers_and_leaves_no_temp_file(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = home / ".cursor" / "mcp.json"
    config.parent.mkdir()
    other = {"command": "npx", "args": ["-y", "github"]}
    config.write_text(json.dumps({"mcpServers": {"github": other}}))

    assert main(["install", "--client", "cursor"]) == 0
    assert "cursor: added" in capsys.readouterr().out
    servers = json.loads(config.read_text())["mcpServers"]
    assert servers["github"] == other
    assert servers["oxide"] == {"type": "stdio", **ENTRY}
    assert not config.with_name("mcp.json.oxide-tmp").exists()

    assert main(["install", "--client", "cursor"]) == 0
    assert "cursor: unchanged" in capsys.readouterr().out


def test_cursor_refuses_malformed_config_and_keeps_it(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = home / ".cursor" / "mcp.json"
    config.parent.mkdir()
    config.write_text("{not json", encoding="utf-8")

    assert main(["install", "--client", "cursor"]) == 1
    assert config.read_text(encoding="utf-8") == "{not json"
    assert "not valid JSON" in capsys.readouterr().err


def test_detects_cursor_without_a_cli(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (home / ".cursor").mkdir()
    monkeypatch.setenv("PATH", str(home))  # no claude or codex executable
    assert main(["install"]) == 0
    assert json.loads((home / ".cursor" / "mcp.json").read_text())["mcpServers"]["oxide"]


def test_no_client_found_is_an_error(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PATH", str(home))
    assert main(["install"]) == 1
    assert "no MCP client found" in capsys.readouterr().err


def test_setup_fetches_the_pinned_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("oxide.cli.setup", lambda: tmp_path / "fts.lbug_extension")
    assert main(["setup"]) == 0
    assert str(tmp_path / "fts.lbug_extension") in capsys.readouterr().out
