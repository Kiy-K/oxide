#!/usr/bin/env python3
"""Run a command and report wall time plus two peak-memory figures.

GNU time is absent on the measurement host, so:
  max_single_rss_kb  getrusage(RUSAGE_CHILDREN).ru_maxrss — the largest
                     single descendant (rust-analyzer itself, typically)
  peak_tree_rss_kb   peak of the summed RSS of the whole process tree,
                     sampled from /proc every 50 ms — what the machine
                     actually had to hold (rust-analyzer + cargo check +
                     rustc + proc-macro server)

Usage: measure.py LABEL OUT.jsonl -- cmd args...   (cwd = current dir)
The full stderr is kept next to OUT as LABEL.stderr.
"""
import json
import os
import resource
import subprocess
import sys
import threading
import time
from pathlib import Path


def tree_rss_kb(root):
    kids = {}
    rss = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/stat") as f:
                ppid = int(f.read().rsplit(")", 1)[1].split()[1])
            with open(f"/proc/{pid}/statm") as f:
                rss[int(pid)] = int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") // 1024
            kids.setdefault(ppid, []).append(int(pid))
        except (OSError, IndexError, ValueError):
            continue
    total, stack = 0, [root]
    while stack:
        p = stack.pop()
        total += rss.get(p, 0)
        stack.extend(kids.get(p, []))
    return total


def main():
    label, out = sys.argv[1], Path(sys.argv[2])
    cmd = sys.argv[sys.argv.index("--") + 1:]
    t0 = time.perf_counter()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    peak = [0]
    done = threading.Event()

    def sample():
        while not done.is_set():
            peak[0] = max(peak[0], tree_rss_kb(p.pid))
            done.wait(0.05)

    th = threading.Thread(target=sample, daemon=True)
    th.start()
    stdout, stderr = p.communicate()
    wall = time.perf_counter() - t0
    done.set()
    th.join()
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    rec = dict(label=label, cmd=cmd, cwd=os.getcwd(), rc=p.returncode, wall_s=round(wall, 3),
               max_single_rss_kb=ru.ru_maxrss, peak_tree_rss_kb=peak[0],
               user_s=round(ru.ru_utime, 2), sys_s=round(ru.ru_stime, 2),
               stderr_tail=stderr[-3000:], ts=time.strftime("%Y-%m-%dT%H:%M:%S"))
    out.parent.mkdir(parents=True, exist_ok=True)
    (out.parent / f"{label}.stderr").write_text(stderr)
    with out.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    sys.stdout.write(stdout)
    print(json.dumps({k: rec[k] for k in ("label", "rc", "wall_s", "max_single_rss_kb",
                                          "peak_tree_rss_kb", "user_s")}), file=sys.stderr)
    sys.exit(p.returncode)


if __name__ == "__main__":
    main()
