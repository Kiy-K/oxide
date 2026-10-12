#!/usr/bin/env python3
"""MCP integration audit for variant E (follow-up.md § Task 1). Diagnostic only: its
runs are never scored or mixed into the A/B/C/E comparison.

  mcp_audit.py direct TASK_ID          protocol + tool outputs checked against the source
  mcp_audit.py agent TASK_ID OUT_DIR   two Codex runs with E's exact wiring:
                                       discover (list tools, call none) and forced (call one)

The agent mode copies the frozen CODEX_HOME (config + auth link) so the frozen one is
not modified.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import harness as h  # noqa: E402  (frozen constants: BIN, MODEL, EFFORT, AGENT_ENV, DATA)

LOC = re.compile(r"([\w./-]+\.py)::([\w.]+) \[(\d+)-(\d+)\]")
HUNK_DEF = re.compile(r"^@@ .*@@ (?:async )?def (\w+)", re.M)


def rpc(server, msgs):
    p = subprocess.Popen(server, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    out = []
    for m in msgs:
        p.stdin.write(json.dumps(m) + "\n")
        p.stdin.flush()
        if "id" in m:
            out.append(json.loads(p.stdout.readline()))
    p.stdin.close()
    p.wait()
    return out


def text(reply):
    return reply["result"]["content"][0]["text"]


def check_locations(output, src):
    """Every printed `path::Q.name [a-b]` must open with a def/class of that name (decorators allowed)."""
    bad, n = [], 0
    for path, qual, a, b in LOC.findall(output):
        name = qual.rsplit(".", 1)[-1]
        head = (src / path).read_text(errors="replace").splitlines()[int(a) - 1:int(b)][:40]
        n += 1
        if not any(re.match(rf"\s*(async\s+)?(def|class)\s+{re.escape(name)}\b", ln) for ln in head):
            bad.append(f"{path}::{qual} [{a}-{b}]")
    return n, bad


def cmd_direct(task_id):
    d = h.DATA / "tasks" / task_id
    log = Path("/tmp") / f"mcp-audit-{task_id}.jsonl"
    log.unlink(missing_ok=True)
    gold = json.loads((d / "gold.json").read_text())
    gold_files = sorted({c["file"] for c in gold["gold_context"]})
    # Probe names come from the gold patch's hunk headers, not from OXIDE output.
    names = sorted(set(HUNK_DEF.findall(gold["patch"])))[:3] or ["__init__"]
    server = ["python3", str(h.HERE / "scripts/nav_mcp.py"), str(h.BIN), str(d / "store"), str(log)]
    init = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "audit", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"}]
    out = rpc(server, init + [{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}] +
              [{"jsonrpc": "2.0", "id": 10 + i, "method": "tools/call",
                "params": {"name": "oxide_find", "arguments": {"name": n}}} for i, n in enumerate(names)])
    found = "\n".join(text(r) for r in out[2:])
    refs = [f"{p}::{q}" for p, q, _, _ in LOC.findall(found) if p in gold_files][:3]
    out2 = rpc(server, init +
               [{"jsonrpc": "2.0", "id": 30 + i, "method": "tools/call",
                 "params": {"name": "oxide_expand", "arguments": {"ref": r, "kind": "all", "direction": "both"}}}
                for i, r in enumerate(refs)] +
               [{"jsonrpc": "2.0", "id": 40, "method": "tools/call",
                 "params": {"name": "oxide_expand", "arguments": {"ref": "no/such_file.py::nothing"}}}])
    expanded = "\n".join(text(r) for r in out2[1:-1])
    n, bad = check_locations(found + "\n" + expanded, d / "src")
    print(json.dumps({
        "task": task_id, "server": out[0]["result"]["serverInfo"],
        "tools": [t["name"] for t in out[1]["result"]["tools"]],
        "probe_names": names, "gold_files": gold_files, "find_hits_in_gold_files": refs,
        "locations_checked": n, "locations_wrong": bad,
        "expand_nonempty": all(text(r).strip() for r in out2[1:-1]),
        "missing_entity_isError": out2[-1]["result"]["isError"],
        "missing_entity_text": text(out2[-1])[:160],
        "calls_made": len(names) + len(refs) + 1,
        "call_log_lines": len(log.read_text().splitlines()) if log.exists() else 0,
    }, indent=1))


DISCOVER = ("Diagnostic, not a coding task. Do not run shell commands and do not edit files. "
            "List the exact names of every MCP tool available to you whose name starts with 'oxide', "
            "one per line, then stop. Do not call any tool.")
FORCED = ("Diagnostic, not a coding task. Do not edit files. Call the MCP tool oxide_find with "
          "name={name!r} exactly once, then reply with its output verbatim.")


def cmd_agent(task_id, out_dir):
    d = h.DATA / "tasks" / task_id
    out = Path(out_dir)
    shutil.rmtree(out, ignore_errors=True)
    home = out / "codex-home"
    home.mkdir(parents=True)
    shutil.copy(h.CODEX_HOME / "config.toml", home / "config.toml")
    (home / "auth.json").symlink_to((h.CODEX_HOME / "auth.json").resolve())
    (out / "home").mkdir()
    env = {**h.AGENT_ENV, "CODEX_HOME": str(home), "HOME": str(out / "home")}
    gold = json.loads((d / "gold.json").read_text())
    name = (HUNK_DEF.findall(gold["patch"]) or ["__init__"])[0]
    summary = {}
    for mode, prompt in (("discover", DISCOVER), ("forced", FORCED.format(name=name))):
        run = out / mode
        repo = run / "repo"
        shutil.copytree(d / "src", repo, symlinks=True)
        log = run / "nav-calls.jsonl"
        server = [str(h.HERE / "scripts/nav_mcp.py"), str(h.BIN), str(d / "store"), str(log)]
        # The same command as harness.run_one for variant E, apart from the prompt and a 10-minute cap.
        cmd = ["timeout", "600", "codex", "exec", "--json", "--ephemeral", "--skip-git-repo-check",
               "-m", h.MODEL, "-c", f'model_reasoning_effort="{h.EFFORT}"', "-C", str(repo),
               "-o", str(run / "last.txt"), "-c", 'mcp_servers.oxide.command="python3"',
               "-c", f"mcp_servers.oxide.args={json.dumps(server)}", prompt]
        with open(run / "events.jsonl", "w") as o, open(run / "stderr.txt", "w") as e:
            code = subprocess.run(cmd, stdout=o, stderr=e, stdin=subprocess.DEVNULL, env=env).returncode
        shutil.rmtree(repo)
        events = [json.loads(ln) for ln in (run / "events.jsonl").read_text().splitlines() if ln.startswith("{")]
        items = [e.get("item", {}) for e in events if e.get("type") == "item.completed"]
        last = (run / "last.txt").read_text() if (run / "last.txt").exists() else ""
        summary[mode] = {
            "exit": code,
            "item_types": sorted({str(i.get("type")) for i in items}),
            "mcp_tool_calls": [{k: i.get(k) for k in ("server", "tool", "status", "arguments")}
                               for i in items if i.get("type") == "mcp_tool_call"],
            "nav_log_lines": len(log.read_text().splitlines()) if log.exists() else 0,
            "oxide_tools_named_in_reply": sorted(set(re.findall(r"oxide_\w+", last))),
            "reply_head": last[:600],
        }
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    cmd, *args = sys.argv[1:] or ["help"]
    if cmd == "direct":
        cmd_direct(*args)
    elif cmd == "agent":
        cmd_agent(*args)
    else:
        sys.exit(__doc__)
