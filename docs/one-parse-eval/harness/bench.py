#!/usr/bin/env python3
"""Research-only (issue #24): parse-count attribution and pipeline baselines.

  bench.py seam  <probe-debug> <out.json>           per-file parse counts by site/language
  bench.py index <probe-release> <out.jsonl> [reps] full / noop / edit / watch scenarios
  bench.py ab <probe-A> <probe-B> <out.jsonl> [reps] same, A and B interleaved per rep

Every scenario runs in a fresh process pinned to four distinct P-cores
(CPUS), against a `git clone --shared` copy of a pinned repo, with OXIDE's
offline hashed embedder so embedding cost stays small and deterministic.
"""
import json, os, pathlib, shutil, subprocess, sys, tempfile

HOME = pathlib.Path.home()
CB = HOME / ".cache/oxide-contextbench/repos"
S = pathlib.Path(os.environ.get("REPOS24", "/tmp/claude-1000/-home-khoi-Work-oxide/b8a8c498-1aa2-4377-9fb1-5abd0ea6344e/scratchpad/repos24"))
ROOT = pathlib.Path(__file__).resolve().parents[3]
CPUS = os.environ.get("BENCH_CPUS", "0,2,4,6")
# (name, path, pinned revision prefix, source extensions used to pick edit targets)
REPOS = [
    ("flask", CB / "flask@7ee9ceb71e86", "7ee9ceb71e86", {".py"}),
    ("darkreader", CB / "darkreader@a787eb511f45", "a787eb511f45", {".ts", ".tsx"}),
    ("axios", CB / "axios@0abc70564746", "0abc705", {".js"}),
    ("tokio", CB / "tokio@43c224ff47e4", "43c224ff47e4", {".rs"}),
    ("gin", S / "gin", "dcaa4296d111", {".go"}),
    ("gson", S / "gson", "8b4b55051489", {".java"}),
    ("zstd", CB / "zstd@823a28a1f4cb", "823a28a1f4cb", {".c", ".h"}),
    ("fmt", S / "fmt", "123913715afe", {".h", ".cc"}),
]


def rev(p):
    return subprocess.run(["git", "-C", str(p), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def tracked(p):
    return subprocess.run(["git", "-C", str(p), "ls-files"], capture_output=True, text=True, check=True).stdout.split()


def probe(exe, *args, env=None):
    out = subprocess.run(["taskset", "-c", CPUS, exe, *args], capture_output=True, text=True, check=True,
                         env={**os.environ, **(env or {})})
    return json.loads(out.stdout.strip().splitlines()[-1])


def seam(exe, out):
    work = pathlib.Path(tempfile.mkdtemp())
    man = work / "seam.jsonl"
    with open(man, "w") as f:
        for name, p, r, _ in REPOS:
            assert rev(p).startswith(r), (name, rev(p))
            for rel in tracked(p):
                f.write(json.dumps({"rel": rel, "path": str(p / rel)}) + "\n")
        conf = ROOT / "fixtures/conformance"
        for fp in sorted(conf.rglob("*")):
            if fp.is_file():
                f.write(json.dumps({"rel": str(fp.relative_to(conf)), "path": str(fp)}) + "\n")
    res = probe(exe, "seam", str(man), env={"PROBE_ATTRIB": "1"})
    json.dump(res, open(out, "w"), indent=1)
    shutil.rmtree(work)


def edit_targets(p, exts):
    files = [(os.path.getsize(p / f), f) for f in tracked(p) if pathlib.Path(f).suffix in exts]
    files.sort()
    return files[len(files) // 2][1], files[-1][1]


def index(exe, out, reps, variants=None):
    variants = variants or [("", exe)]
    with open(out, "a") as fo:
        for name, p, r, exts in REPOS:
            assert rev(p).startswith(r), (name, rev(p))
            median, largest = edit_targets(p, exts)
            for rep, (variant, exe) in ((r_, v) for r_ in range(reps) for v in variants):
                tmp = pathlib.Path(tempfile.mkdtemp())
                copy = tmp / name
                subprocess.run(["git", "clone", "-q", "--shared", str(p), str(copy)], check=True)
                steps = [("full", None), ("noop", None), ("edit_median", median),
                         ("edit_largest", largest), ("watch_median", median)]
                for scenario, target in steps:
                    if target:
                        with open(copy / target, "a") as f:
                            f.write("\n")
                    if scenario.startswith("watch"):
                        res = probe(exe, "watch", str(copy), target)
                    else:
                        res = probe(exe, "index", str(copy), scenario)
                    res.update(repo=name, rep=rep, scenario=scenario, target=target, variant=variant)
                    fo.write(json.dumps(res) + "\n")
                    fo.flush()
                shutil.rmtree(tmp)
                print(f"{name} rep {rep} done", file=sys.stderr)


if __name__ == "__main__":
    if sys.argv[1] == "seam":
        seam(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "ab":
        index(None, sys.argv[4], int(sys.argv[5]) if len(sys.argv) > 5 else 5,
              [("baseline", sys.argv[2]), ("challenger", sys.argv[3])])
    else:
        index(sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 5)
