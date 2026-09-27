#!/usr/bin/env python3
"""Research-only (issue #29): #24's harness (docs/one-parse-eval/harness/bench.py)
with the same pinned corpora, scenarios, pinned cores and hashed embedder.

  bench.py ab <probe-A> <probe-B> <out.jsonl> [reps]   interleaved A/B, order alternates per rep
  bench.py attrib <probe-debug> <label> <out.jsonl>    PROBE_ATTRIB per-site/thread parse counts,
                                                        full/noop/edit/watch on every repo
  bench.py langseam <probe> <out.json>                 parses/file by language via the real pipeline
  bench.py dump <probe> <out.jsonl>                    per-file extraction dump (worker seam)

Differences from #24's `ab`: baseline and challenger alternate which one
runs first in each rep (#24 always ran baseline first), and every row also
carries the probe's per-stage wall/CPU and per-parse-worker figures.
"""
import json, os, pathlib, shutil, subprocess, sys, tempfile

import importlib.util  # noqa: E402

# #24's harness (REPOS, ROOT, rev, tracked, edit_targets), loaded under its
# own name: it is also called `bench`, so a plain import from a script that
# imported this module as `bench` would get this module back.
_spec = importlib.util.spec_from_file_location(
    "bench24", pathlib.Path(__file__).resolve().parents[2] / "one-parse-eval/harness/bench.py")
b24 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(b24)

CPUS = os.environ.get("BENCH_CPUS", "0,2,4,6")


def probe(exe, *args, env=None):
    out = subprocess.run(["taskset", "-c", CPUS, exe, *args], capture_output=True, text=True, check=True,
                         env={**os.environ, **(env or {})})
    return json.loads(out.stdout.strip().splitlines()[-1])


def scenarios(exe, name, p, median, largest, env=None):
    tmp = pathlib.Path(tempfile.mkdtemp())
    copy = tmp / name
    subprocess.run(["git", "-c", "advice.detachedHead=false", "clone", "-q", "--shared", str(p), str(copy)], check=True)
    steps = [("full", None), ("noop", None), ("edit_median", median),
             ("edit_largest", largest), ("watch_median", median)]
    try:
        for scenario, target in steps:
            if target:
                with open(copy / target, "a") as f:
                    f.write("\n")
            if scenario.startswith("watch"):
                res = probe(exe, "watch", str(copy), target, env=env)
            else:
                res = probe(exe, "index", str(copy), scenario, env=env)
            res.update(repo=name, scenario=scenario, target=target)
            yield res
    finally:
        shutil.rmtree(tmp)


def ab(exe_a, exe_b, out, reps):
    variants = [("baseline", exe_a), ("challenger", exe_b)]
    with open(out, "a") as fo:
        for name, p, r, exts in b24.REPOS:
            assert b24.rev(p).startswith(r), (name, b24.rev(p))
            median, largest = b24.edit_targets(p, exts)
            for rep in range(reps):
                order = variants if rep % 2 == 0 else variants[::-1]
                for variant, exe in order:
                    for res in scenarios(exe, name, p, median, largest):
                        res.update(rep=rep, variant=variant, first=order[0][0])
                        fo.write(json.dumps(res) + "\n")
                        fo.flush()
                print(f"{name} rep {rep} done", file=sys.stderr)


def attrib(exe, label, out):
    with open(out, "a") as fo:
        for name, p, r, exts in b24.REPOS:
            median, largest = b24.edit_targets(p, exts)
            for res in scenarios(exe, name, p, median, largest, env={"PROBE_ATTRIB": "1"}):
                res.update(variant=label)
                fo.write(json.dumps(res) + "\n")


def manifest(path):
    with open(path, "w") as f:
        for name, p, r, _ in b24.REPOS:
            assert b24.rev(p).startswith(r), (name, b24.rev(p))
            for rel in b24.tracked(p):
                f.write(json.dumps({"rel": f"{name}/{rel}", "path": str(p / rel)}) + "\n")
        conf = b24.ROOT / "fixtures/conformance"
        for fp in sorted(conf.rglob("*")):
            if fp.is_file():
                f.write(json.dumps({"rel": "conformance/" + str(fp.relative_to(conf)), "path": str(fp)}) + "\n")


def langseam(exe, out):
    work = pathlib.Path(tempfile.mkdtemp())
    manifest(work / "m.jsonl")
    res = probe(exe, "langseam", str(work / "m.jsonl"), str(work / "repos"), env={"PROBE_ATTRIB": "1"})
    json.dump(res, open(out, "w"), indent=1)
    shutil.rmtree(work)


def dump(exe, out):
    work = pathlib.Path(tempfile.mkdtemp())
    manifest(work / "m.jsonl")
    subprocess.run([exe, "dump", str(work / "m.jsonl"), out], check=True)
    shutil.rmtree(work)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "ab":
        ab(sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]) if len(sys.argv) > 5 else 7)
    elif cmd == "attrib":
        attrib(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == "langseam":
        langseam(sys.argv[2], sys.argv[3])
    elif cmd == "dump":
        dump(sys.argv[2], sys.argv[3])
