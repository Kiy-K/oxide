"""Supplement: same end-to-end harness for non-algorithm host knobs."""
import json, os, random, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gitenv, stage_b
gitenv.ALGOS["ihc3"] = [("diff.interHunkContext", "3")]
stage_b.RUN_ALGOS = ["myers", "ihc3", "myers_noindent"]
CODE_EXT = {"py","ts","tsx","js","rs","go","md"}
def code_only(p): return {k: v for k, v in p.items() if k.rsplit(".",1)[-1] in CODE_EXT}
norm = lambda p: json.loads(json.dumps(p))
r = sys.argv[1]; S = os.getcwd()
d = json.load(open(f"{S}/stage_a_{r}.json"))
random.seed(35)
cands = []
for x in d:
    c = x["commit"]
    t = subprocess.run(gitenv.diff_argv(f"{c}^..{c}"), cwd=f"{S}/repos/{r}", env=gitenv.env_for("ihc3"), capture_output=True).stdout.decode("utf-8","replace")
    if norm(code_only(gitenv.parse_unified(t))) != norm(code_only(x["algos"]["myers"]["parsed"])) or x["algos"]["myers"]["code_parsed"] != x["algos"]["myers_noindent"]["code_parsed"]:
        cands.append(c)
pick = random.sample(cands, min(10, len(cands)))
json.dump(pick, open(f"{S}/sel2_{r}.json", "w"))
print(r, len(cands), "candidates; running", len(pick), flush=True)
stage_b.real_repo(f"{S}/repos/{r}", f"{S}/sel2_{r}.json", f"{S}/b2")
