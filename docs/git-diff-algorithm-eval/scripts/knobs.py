import json, subprocess, sys
sys.path.insert(0,'.')
import gitenv
from gitenv import env_for, diff_argv, parse_unified
gitenv.ALGOS.update({"ihc3": [("diff.interHunkContext","3")], "norenames": [("diff.renames","false")], "copies": [("diff.renames","copies")]})
norm = lambda p: json.loads(json.dumps(p))
for r in ["flask","pylint","cobra","zod","oxide"]:
    d = json.load(open(f"stage_a_{r}.json"))
    cnt = {k:0 for k in ("ihc3","norenames","copies")}
    for x in d:
        c = x["commit"]; base = x["algos"]["myers"]["parsed"]
        for k in cnt:
            p = subprocess.run(diff_argv(f"{c}^..{c}"), cwd=f"repos/{r}", env=env_for(k), capture_output=True)
            assert p.returncode == 0, p.stderr.decode()
            t = p.stdout.decode("utf-8","replace")
            cnt[k] += norm(parse_unified(t)) != base
    print(r, len(d), "parsed-differs vs isolated default:", cnt)
