"""Stage B: end-to-end `oxide review` / `oxide context --git` per diff algorithm on one
worktree state (HEAD=C^, index+worktree=C, i.e. `git diff HEAD` == C^..C)."""
import gzip, json, os, subprocess, sys, time, hashlib, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gitenv import env_for
OXIDE = os.environ.get("OXIDE_BIN", os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../target/release/oxide"))
RUN_ALGOS = ["default", "myers", "histogram", "patience", "myers#2"]

def oxide(cwd, algo, args):
    e = env_for(algo.split("#")[0])
    e.update(OXIDE_EMBED_NATIVE="hashed", NO_COLOR="1", OXIDE_TELEMETRY="0")
    e.pop("OXIDE_DEBUG_DUMP_KEPT", None)
    t0 = time.perf_counter()
    r = subprocess.run([OXIDE] + args, cwd=cwd, env=e, capture_output=True)
    dt = time.perf_counter() - t0
    if r.returncode: raise RuntimeError(f"{args}: {r.stderr.decode()}")
    return r.stdout.decode(), dt

def run_state(cwd, queries, outdir, ctx_variants):
    os.makedirs(outdir, exist_ok=True)
    res = {}
    for a in RUN_ALGOS:
        o = {}
        o["review_json"], o["t_review"] = oxide(cwd, a, ["review", "--diff", "", "--json"])
        o["review_human"], _ = oxide(cwd, a, ["review", "--diff", ""])
        for qi, q in enumerate(queries):
            for vname, vargs in ctx_variants:
                k = f"ctx{qi}_{vname}"
                o[k + "_json"], o["t_" + k] = oxide(cwd, a, ["context", q, "--git", "--json"] + vargs)
                o[k + "_human"], _ = oxide(cwd, a, ["context", q, "--git"] + vargs)
        with gzip.open(os.path.join(outdir, a.replace("#", "_") + ".json.gz"), "wt") as f:
            json.dump(o, f)
        res[a] = o
    return res

def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, env=env_for("default"), check=True, capture_output=True)

def real_repo(src, commits_file, outroot):
    name = os.path.basename(src.rstrip("/"))
    wd = os.path.join(outroot, "work", name)
    if not os.path.exists(wd):
        subprocess.run(["git", "clone", "-q", src, wd], env=env_for("default"), check=True)
    sel = json.load(open(commits_file))
    times = {}
    for c in sel:
        git(wd, "reset", "-q", "--hard")
        git(wd, "checkout", "-q", "--detach", c)
        subj = subprocess.run(["git", "log", "-1", "--format=%s", c], cwd=wd, env=env_for("default"),
                              capture_output=True, text=True).stdout.strip()
        git(wd, "reset", "-q", "--soft", c + "^")
        t0 = time.perf_counter()
        oxide(wd, "default", ["index", "."])
        times[c] = time.perf_counter() - t0
        run_state(wd, [subj], os.path.join(outroot, "out", name, c[:12]), [("bal", [])])
        print(name, c[:12], "indexed in %.1fs" % times[c], flush=True)
    git(wd, "reset", "-q", "--hard")

if __name__ == "__main__":
    real_repo(sys.argv[1], sys.argv[2], sys.argv[3])
