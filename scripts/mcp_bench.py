#!/usr/bin/env python3
"""Repeated MCP tool-call latency: spawn `oxide mcp` in a repository, run the
protocol handshake, then N `search` + N `context` calls for one query and
report first-call latency, the median of the rest, the minimum, and the
server's RSS. `OXIDE_EMBED_NATIVE` defaults to `hashed` (offline).

usage: scripts/mcp_bench.py <oxide binary> <repo_path> "<query>" [n]"""
import json, os, subprocess, sys, time
binary, repo, query = sys.argv[1:4]
binary = os.path.abspath(binary)
n = int(sys.argv[4]) if len(sys.argv) > 4 else 8
env = dict(os.environ, OXIDE_EMBED_NATIVE=os.environ.get("OXIDE_EMBED_NATIVE", "hashed"))
env.pop("OXIDE_EMBED_URL", None); env.pop("OXIDE_EMBED_MODEL", None)
p = subprocess.Popen([binary, "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env, cwd=repo)
def call(msg):
    p.stdin.write((json.dumps(msg) + "\n").encode()); p.stdin.flush()
    while True:
        line = p.stdout.readline()
        if not line: raise SystemExit("mcp died")
        r = json.loads(line)
        if r.get("id") == msg.get("id"): return r
call({"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"bench","version":"0"}}})
p.stdin.write((json.dumps({"jsonrpc":"2.0","method":"notifications/initialized"})+"\n").encode()); p.stdin.flush()
res = {}
for tool, args in [("search", {"query": query, "limit": 10}), ("query", {"task": query, "budget_tokens": 4096})]:
    lat = []
    for i in range(n):
        t = time.perf_counter()
        r = call({"jsonrpc":"2.0","id":100+i,"method":"tools/call","params":{"name":tool,"arguments":args}})
        lat.append((time.perf_counter()-t)*1000)
        if r.get("result", {}).get("isError"): print("ERR", r["result"]); break
    lat_rest = sorted(lat[1:]) if len(lat) > 1 else lat
    res[tool] = (lat[0], lat_rest[len(lat_rest)//2], min(lat))
rss = int(open(f"/proc/{p.pid}/status").read().split("VmRSS:")[1].split()[0])
p.stdin.close(); p.wait()
for tool,(first,med,mn) in res.items():
    print(f"mcp {tool} first={first:.1f}ms median_rest={med:.1f}ms min={mn:.1f}ms")
print(f"mcp rss={rss}KB")
