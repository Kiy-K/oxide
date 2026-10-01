import json, os, subprocess, sys
sys.path.insert(0, os.getcwd())
import gitenv
from gitenv import env_for, parse_unified
S=os.getcwd()
HOSTILE=[("diff.algorithm","patience"),("diff.indentHeuristic","false"),("diff.interHunkContext","5"),("diff.renames","false"),
         ("diff.external",f"{S}/hostile_ext.sh"),("core.attributesFile",f"{S}/hostile_attrs"),("diff.hostile.textconv","rev"),
         ("diff.mnemonicPrefix","true"),("diff.noprefix","true"),("diff.suppressBlankEmpty","true"),("color.ui","always"),
         ("diff.relative","true"),("diff.context","9"),("core.quotePath","false")]
gitenv.ALGOS["hostile"]=HOSTILE
BASE=["git","diff","--unified=0","--no-color","--src-prefix=a/","--dst-prefix=b/"]
PIN=["--diff-algorithm=myers","--indent-heuristic","--inter-hunk-context=0","--find-renames","--no-ext-diff","--no-textconv"]
res={}
for r in ["flask","pylint","cobra","zod","oxide"]:
    d=json.load(open(f"stage_a_{r}.json"))[:100]
    c={"current_argv_hostile_differs":0,"pinned_argv_hostile_differs":0,"pinned_vs_isolated_default_raw_differs":0,"n":0}
    for x in d:
        rng=f"{x['commit']}^..{x['commit']}"; c["n"]+=1
        iso=subprocess.run(BASE+[rng],cwd=f"repos/{r}",env=env_for("default"),capture_output=True).stdout
        cur=subprocess.run(BASE+[rng],cwd=f"repos/{r}",env=env_for("hostile"),capture_output=True).stdout
        pin_h=subprocess.run(BASE+PIN+[rng],cwd=f"repos/{r}",env=env_for("hostile"),capture_output=True).stdout
        pin_i=subprocess.run(BASE+PIN+[rng],cwd=f"repos/{r}",env=env_for("default"),capture_output=True).stdout
        p=lambda b: parse_unified(b.decode("utf-8","replace"))
        c["current_argv_hostile_differs"]+= p(cur)!=p(iso)
        c["pinned_argv_hostile_differs"]+= pin_h!=pin_i
        c["pinned_vs_isolated_default_raw_differs"]+= pin_i!=iso
    res[r]=c; print(r,c,flush=True)
