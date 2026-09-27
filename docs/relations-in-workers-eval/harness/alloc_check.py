#!/usr/bin/env python3
"""Research-only (issue #29): is the single-file-edit RSS delta allocator
behavior or retained data? Same scenarios as `bench.py ab`, interleaved
baseline/challenger with alternating order, under three glibc malloc
settings: default; MALLOC_ARENA_MAX=1 (one arena, so relation work moving
from the main thread to a worker thread cannot spread across per-thread
arenas); and #24's pinned mmap/trim thresholds.

  alloc_check.py <probe-A> <probe-B> <out.jsonl> [reps] [repo,...]
"""
import json, sys
import bench as b29
b24 = b29.b24

ENVS = {
    "default": {},
    "arena_max_1": {"MALLOC_ARENA_MAX": "1"},
    "pinned_thresholds": {"MALLOC_MMAP_THRESHOLD_": "131072", "MALLOC_TRIM_THRESHOLD_": "131072"},
}

a, b, out = sys.argv[1:4]
reps = int(sys.argv[4]) if len(sys.argv) > 4 else 5
only = set(sys.argv[5].split(",")) if len(sys.argv) > 5 else None
variants = [("baseline", a), ("challenger", b)]
with open(out, "a") as fo:
    for name, p, r, exts in b24.REPOS:
        if only and name not in only:
            continue
        median, largest = b24.edit_targets(p, exts)
        for rep in range(reps):
            for env_name, env in ENVS.items():
                order = variants if rep % 2 == 0 else variants[::-1]
                for variant, exe in order:
                    for res in b29.scenarios(exe, name, p, median, largest, env=env):
                        res.update(rep=rep, variant=variant, malloc=env_name)
                        res.pop("sites", None)
                        fo.write(json.dumps(res) + "\n")
                        fo.flush()
        print(f"{name} done", file=sys.stderr)
