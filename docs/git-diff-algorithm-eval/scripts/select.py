import json, random, sys
random.seed(35)
for r in ["flask","pylint","cobra","zod","oxide"]:
    d = json.load(open(f"stage_a_{r}.json"))
    differ = [x["commit"] for x in d if len({json.dumps(x["algos"][a]["code_parsed"]) for a in ("myers","histogram","patience")}) > 1]
    same = [x["commit"] for x in d if x["commit"] not in differ and x["algos"]["myers"]["code_parsed"]]
    pick = differ[:30] + random.sample(same, 5)
    json.dump(pick, open(f"sel_{r}.json","w")); json.dump({"differ": differ[:30], "control": pick[30 if len(differ)>=30 else len(differ):]}, open(f"sel_{r}_meta.json","w"))
    print(r, len(differ), "differ; running", len(pick))
