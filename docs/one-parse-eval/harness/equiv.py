#!/usr/bin/env python3
"""Research-only (issue #24): baseline-vs-challenger index equivalence.

  equiv.py <baseline-bin-dir> <challenger-bin-dir> <out-dir>

For each corpus (8 pinned repos + fixtures/py_repo + fixtures/ts_repo) and
each binary set, on a fresh copy, runs the same scenario sequence and after
every step records `oxide index --json` stdout and a full `sqlite3 .dump`
of index.db (only meta.index_id — random per database — and meta.root — the
copy's path — are masked; rowids, index_generation, embeddings, relations
and lexical tables are compared verbatim). Rows are compared as a sorted
multiset: the baseline's own physical insertion order already differs
run-to-run (hash-map iteration order of the changed-file set), which a
baseline-vs-baseline control confirms; exact-order equality is still
reported per step:

  full -> noop -> edit(median file) -> watch(median file, via the probe's
  update_base_for_files path) -> delete(median file) -> add(copy of the
  largest file under a new name) -> noop
"""
import hashlib, json, os, pathlib, shutil, subprocess, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import bench

ENV = {**os.environ, "OXIDE_EMBED_NATIVE": "hashed"}


def dump(db):
    out = subprocess.run(["sqlite3", str(db), ".dump"], capture_output=True, text=True, check=True).stdout
    keep = [l for l in out.splitlines()
            if not l.startswith("INSERT INTO meta VALUES('index_id'")
            and not l.startswith("INSERT INTO meta VALUES('root'")]
    return "\n".join(keep) + "\n"


def corpora():
    for name, p, r, exts in bench.REPOS:
        yield name, p, exts, True
    for fx, exts in [("py_repo", {".py"}), ("ts_repo", {".ts", ".tsx"})]:
        yield fx, bench.ROOT / "fixtures" / fx, exts, False


def prepare(src, dst, is_git):
    if is_git:
        subprocess.run(["git", "clone", "-q", "--shared", str(src), str(dst)], check=True)
    else:
        shutil.copytree(src, dst)
        subprocess.run(["git", "init", "-q"], cwd=dst, check=True)
        subprocess.run(["git", "add", "-A"], cwd=dst, check=True)


def run(bindir, name, src, exts, is_git, outdir):
    tmp = pathlib.Path(tempfile.mkdtemp())
    cfg = tmp / "cfg"
    env = {**ENV, "XDG_CONFIG_HOME": str(cfg)}
    copy = tmp / name
    prepare(src, copy, is_git)
    files = sorted((os.path.getsize(copy / f), f) for f in bench.tracked(copy) if pathlib.Path(f).suffix in exts)
    median, largest = files[len(files) // 2][1], files[-1][1]
    steps = []

    def index(label):
        o = subprocess.run([f"{bindir}/oxide", "index", "--json"], cwd=copy, env=env,
                           capture_output=True, text=True, check=True).stdout
        steps.append((label, o, dump(copy / ".oxide/index.db")))

    index("full")
    index("noop")
    with open(copy / median, "a") as f:
        f.write("\n")
    index("edit")
    with open(copy / median, "a") as f:
        f.write("\n")
    w = subprocess.run([f"{bindir}/parse-probe", "watch", str(copy), median], env=env,
                       capture_output=True, text=True, check=True).stdout
    rep = json.loads(w.strip().splitlines()[-1])["extra"]["report"]
    rep.pop("duration_ms", None)
    steps.append(("watch", json.dumps(rep, sort_keys=True), dump(copy / ".oxide/index.db")))
    os.remove(copy / median)
    index("delete")
    newp = pathlib.Path(largest)
    shutil.copy(copy / largest, copy / newp.with_name("oxide_probe_added" + newp.suffix))
    index("add")
    index("noop2")
    shutil.rmtree(tmp)
    for label, j, d in steps:
        (outdir / f"{name}.{label}.json").write_text(j)
        (outdir / f"{name}.{label}.dump.sql").write_text(d)
    return steps


def main():
    base, chal, out = sys.argv[1:4]
    out = pathlib.Path(out)
    ok = True
    for name, src, exts, is_git in corpora():
        res = {}
        for tag, b in [("baseline", base), ("challenger", chal)]:
            d = out / tag
            d.mkdir(parents=True, exist_ok=True)
            res[tag] = run(b, name, src, exts, is_git, d)
        for (l, j1, d1), (_, j2, d2) in zip(res["baseline"], res["challenger"]):
            s1, s2 = sorted(d1.splitlines()), sorted(d2.splitlines())
            same = j1 == j2 and s1 == s2
            ok &= same
            canon = hashlib.sha256("\n".join(s1).encode()).hexdigest()[:12]
            print(f"{name:10} {l:7} json={'=' if j1 == j2 else 'DIFF'} db_rows={'=' if s1 == s2 else 'DIFF'} "
                  f"db_order={'=' if d1 == d2 else 'differs'} canon_sha={canon} rows={len(s1)}")
    print("ALL IDENTICAL" if ok else "DIFFERENCES FOUND")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
