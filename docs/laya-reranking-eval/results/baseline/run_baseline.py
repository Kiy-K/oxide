#!/usr/bin/env python3
"""Post-#14 baseline: one-shot `oxide query --json` per ContextBench task at the
pinned HEAD. Per task: 1 untimed warm-up, then REPS timed cold-process runs
(wall clock + child peak RSS via wait4). Captures the pack JSON and the exact
pre-allocation `kept` pool (OXIDE_DEBUG_DUMP_KEPT)."""
import json, os, subprocess, sys, time, statistics
R = '/home/khoi/Work/oxide'; OX = f'{R}/target/release/oxide'
HERE = os.path.dirname(os.path.abspath(__file__)); REPS = int(sys.argv[1]) if len(sys.argv) > 1 else 3
def run(args, env):
    t0 = time.perf_counter()
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    out, err = p.communicate()
    _, status, ru = os.wait4(p.pid, 0) if False else (None, p.returncode, None)
    return time.perf_counter() - t0, out, err, p.returncode
def run_rss(args, env):
    t0 = time.perf_counter()
    pid = os.fork()
    if pid == 0:
        fd = os.open(os.devnull, os.O_WRONLY); os.dup2(fd, 1); os.dup2(fd, 2)
        os.execve(args[0], args, env)
    _, status, ru = os.wait4(pid, 0)
    return time.perf_counter() - t0, ru.ru_maxrss / 1024.0, status
rows = []
for l in open(f'{R}/docs/ranking-fusion-eval/results/cb-tasks.jsonl'):
    t = json.loads(l); env = dict(os.environ)
    kept = f"{HERE}/kept/{t['id']}.json"; env['OXIDE_DEBUG_DUMP_KEPT'] = kept
    args = [OX, 'query', '--json', '--path', t['path'], t['query']]
    _, out, err, rc = run(args, env)
    assert rc == 0, err[-400:]
    json.dump(json.loads(out), open(f"{HERE}/kept/{t['id']}.pack.json", 'w'))
    env.pop('OXIDE_DEBUG_DUMP_KEPT')
    walls, rss = [], []
    for _ in range(REPS):
        w, m, st = run_rss(args, env); assert st == 0; walls.append(w); rss.append(m)
    rows.append({'id': t['id'], 'wall_s': walls, 'maxrss_mb': rss})
    print(t['id'][:50], f"{statistics.median(walls)*1000:.0f} ms", f"{max(rss):.0f} MB", file=sys.stderr)
json.dump(rows, open(f'{HERE}/timing-oxide-query.json', 'w'), indent=1)
allw = [statistics.median(r['wall_s']) for r in rows]; allm = [max(r['maxrss_mb']) for r in rows]
print(f"oxide query one-shot: median-of-medians {statistics.median(allw)*1000:.0f} ms, p90 {sorted(allw)[int(0.9*len(allw))]*1000:.0f} ms, max {max(allw)*1000:.0f} ms; peak RSS median {statistics.median(allm):.0f} MB max {max(allm):.0f} MB")
