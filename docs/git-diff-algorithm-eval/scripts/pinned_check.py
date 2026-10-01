import json, os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gitenv
from gitenv import env_for, parse_unified
S=os.path.dirname(os.path.abspath(__file__))  # hostile_ext.sh / hostile_attrs live next to this script
HOSTILE=[("diff.algorithm","patience"),("diff.indentHeuristic","false"),("diff.interHunkContext","5"),("diff.renames","false"),
         ("diff.external",f"{S}/hostile_ext.sh"),("core.attributesFile",f"{S}/hostile_attrs"),("diff.hostile.textconv","rev"),
         ("diff.mnemonicPrefix","true"),("diff.noprefix","true"),("diff.suppressBlankEmpty","true"),("color.ui","always"),
         ("diff.relative","true"),("diff.context","9"),("core.quotePath","false")]
gitenv.ALGOS["hostile"]=HOSTILE
BASE=["git","diff","--unified=0","--no-color","--src-prefix=a/","--dst-prefix=b/"]
PIN=["--diff-algorithm=myers","--indent-heuristic","--inter-hunk-context=0","--find-renames","--no-ext-diff","--no-textconv"]
# full production argv of gitutil::diff_text
PINNED=BASE+PIN
def pinned_env(algo): return env_for(algo)
def ok(p):
    # the baseline and pinned runs must succeed; empty stdout from a failed
    # git would otherwise count as a valid (empty) diff
    assert p.returncode == 0, p.stderr.decode()
    return p.stdout
print("legend: first 100 `rev-list --no-merges` commits per repo (range C^..C).",
      "current_argv_hostile_parsed_differs = parse_unified ranges differ, hostile vs isolated default, pre-pin argv;",
      "pinned_argv_hostile_raw_differs = raw bytes differ, hostile vs isolated default, pinned argv;",
      "pinned_vs_unpinned_default_raw_differs = raw bytes differ, pinned vs pre-pin argv, both isolated default.")
res={}
for r in ["flask","pylint","cobra","zod","oxide"]:
    d=json.load(open(f"stage_a_{r}.json"))[:100]
    c={"current_argv_hostile_parsed_differs":0,"pinned_argv_hostile_raw_differs":0,"pinned_vs_unpinned_default_raw_differs":0,"n":0}
    for x in d:
        rng=f"{x['commit']}^..{x['commit']}"; c["n"]+=1
        iso=ok(subprocess.run(BASE+[rng],cwd=f"repos/{r}",env=env_for("default"),capture_output=True))
        cur=subprocess.run(BASE+[rng],cwd=f"repos/{r}",env=env_for("hostile"),capture_output=True).stdout
        pin_h=ok(subprocess.run(PINNED+[rng],cwd=f"repos/{r}",env=pinned_env("hostile"),capture_output=True))
        pin_i=ok(subprocess.run(PINNED+[rng],cwd=f"repos/{r}",env=pinned_env("default"),capture_output=True))
        p=lambda b: parse_unified(b.decode("utf-8","replace"))
        c["current_argv_hostile_parsed_differs"]+= p(cur)!=p(iso)
        c["pinned_argv_hostile_raw_differs"]+= pin_h!=pin_i
        c["pinned_vs_unpinned_default_raw_differs"]+= pin_i!=iso
    res[r]=c; print(r,c,flush=True)
