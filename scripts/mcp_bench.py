#!/usr/bin/env python3
"""Repeated MCP tool-call latency: spawn `oxide mcp` in a repository, run the
protocol handshake, then N `search` + N `query` calls for one query and
report first-call latency, the median of the rest, the minimum, and the
server's RSS. `OXIDE_EMBED_NATIVE` defaults to `hashed` (offline).

usage: scripts/mcp_bench.py <oxide binary> <repo_path> "<query>" [n] [--json]
                            [--tools search,query] [--limit 10]

`--json` prints one object with every raw sample (ms, in call order) per
tool plus the server's VmRSS and VmHWM, for `scripts/corpus_load_baseline.py`,
which runs this once per *first-call* sample (a fresh server per sample —
call 0 here is the cache miss that loads the corpus) and once for a
steady-state series. `--tools` restricts which tools are called; the
`query` tool is `oxide query` (`context`)."""
import json, os, subprocess, sys, time
argv, flags, it = [], {}, iter(sys.argv[1:])
for a in it:
    if a == "--json": flags["json"] = True
    elif a in ("--tools", "--limit"): flags[a[2:]] = next(it)   # value-taking flags consume their value
    elif a.startswith("--"): raise SystemExit(f"unknown flag {a}")
    else: argv.append(a)
binary, repo, query = argv[:3]
binary = os.path.abspath(binary)
n = int(argv[3]) if len(argv) > 3 else 8
as_json = flags.get("json", False)
tools = flags.get("tools", "search,query").split(",")
limit = int(flags.get("limit", "10"))
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
args_for = {"search": {"query": query, "limit": limit}, "query": {"task": query, "budget_tokens": 4096}}
res, raw = {}, {}
for tool in tools:
    lat = []
    for i in range(n):
        t = time.perf_counter()
        r = call({"jsonrpc":"2.0","id":100+i,"method":"tools/call","params":{"name":tool,"arguments":args_for[tool]}})
        elapsed = (time.perf_counter()-t)*1000
        # A failed call is not a latency sample: a JSON-RPC error or a tool
        # result flagged `isError` (e.g. index validation refusing the
        # request) aborts the run with a non-zero exit so a driver cannot
        # mistake it for a measurement.
        if "error" in r or r.get("result", {}).get("isError"):
            raise SystemExit(f"mcp {tool} call {i} failed: {json.dumps(r)[:500]}")
        lat.append(elapsed)
    lat_rest = sorted(lat[1:]) if len(lat) > 1 else lat
    res[tool] = (lat[0], lat_rest[len(lat_rest)//2], min(lat))
    raw[tool] = lat
status = open(f"/proc/{p.pid}/status").read()
def kb(key): return int(status.split(key + ":")[1].split()[0])
rss, hwm = kb("VmRSS"), kb("VmHWM")
p.stdin.close(); p.wait()
if as_json:
    print(json.dumps({"query": query, "n": n, "samples_ms": raw, "rss_kb": rss, "vm_hwm_kb": hwm}))
else:
    for tool,(first,med,mn) in res.items():
        print(f"mcp {tool} first={first:.1f}ms median_rest={med:.1f}ms min={mn:.1f}ms")
    print(f"mcp rss={rss}KB hwm={hwm}KB")
