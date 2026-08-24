#!/usr/bin/env python3
"""Per-task attribution: where does the budgeted pack lose gold that hybrid keeps?"""
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
sys.path.insert(0, "/home/khoi/Projects/oxide/scripts/agent_eval")
import contextbench_run as cb  # noqa: E402

ROOT = cb.ROOT
OX = ROOT / "target/release/oxide"
ENV = {"OXIDE_EMBED_URL": os.environ.get("OXIDE_EMBED_URL", "")}

tasks = [t for t in cb.load_tasks() if cb.REPO_CACHE / t["repo"].split("/")[-1] in [
    p for p in cb.REPO_CACHE.iterdir()]]

def f1(g):
    p, c = g["precision"], g["coverage"]
    return 2*p*c/(p+c) if p+c else 0.0

loss_rows = []
cat_counts = defaultdict(int)
hog_stats = []
role_tok = defaultdict(int)
per_file_items = defaultdict(list)

for row in tasks:
    name = row["repo"].split("/")[-1]
    repo = cb.REPO_CACHE / name
    if not repo.exists():
        continue
    repo = cb.ensure_repo_checkout(row["repo_url"], row["base_commit"])
    cb.index_repo(repo, ENV["OXIDE_EMBED_URL"])
    problem = row["problem_statement"]

    hy_items, _ = cb.retrieve(repo, "hybrid", problem)
    bd_items, bd_tok = cb.retrieve(repo, "budgeted", problem)

    # raw pack json for attribution
    r = cb.sh([str(OX), "context", "--task", problem, "--budget-tokens", "4096", "--json"],
              cwd=repo, env=ENV)
    pack = json.loads(r.stdout)

    hy_f = {i["file"] for i in hy_items}
    bd_f = {i["file"] for i in bd_items}
    gold = cb.Gold({"init_ctx": json.loads(row["gold_context"]), "repo_url": row["repo_url"], "commit": row["base_commit"]})
    g_f = set(gold.files())

    hy_m = cb.evaluate_task(repo, row, hy_items)
    bd_m = cb.evaluate_task(repo, row, bd_items)

    kept = g_f & bd_f
    lost_by_pack = (g_f & hy_f) - bd_f          # hybrid had it, pack dropped it
    never_anywhere = g_f - hy_f - bd_f          # retrieval missed entirely
    noise = bd_f - g_f - hy_f                   # pack added non-gold hybrid lacked

    om = {o["id"].split("#")[0]: o["why"] for o in pack["omitted"]}
    for f in lost_by_pack:
        why = om.get(f, "NOT_IN_CANDIDATES")
        cat_counts[why.split(" ")[0:2][0] if why != "NOT_IN_CANDIDATES" else why] += 1
        loss_rows.append((row["instance_id"][:45], f.split("/")[-1], why))

    # token hogs + roles
    its = sorted(pack["items"], key=lambda i: -i["est_tokens"])
    if its:
        hog_stats.append((row["instance_id"][:40], bd_tok,
                          its[0]["est_tokens"], its[0]["file"].split("/")[-1],
                          round(its[0]["est_tokens"] / max(bd_tok, 1), 2)))
    for i in pack["items"]:
        role_tok[i["role"]] += i["est_tokens"]
        per_file_items[i["file"]].append(i["qualified_name"])

    print(f"{row['instance_id'][:44]:<45} hyF1={f1(hy_m['file']):.2f} bdF1={f1(bd_m['file']):.2f} "
          f"hyL={f1(hy_m['line']):.2f} bdL={f1(bd_m['line']):.2f} tok={bd_tok:>4} "
          f"kept={len(kept)} lost={len(lost_by_pack)} never={len(never_anywhere)} noise={len(noise)}",
          flush=True)

print("\n=== LOSS CATEGORIES (gold-in-hybrid-but-not-pack) ===")
for k, v in sorted(cat_counts.items(), key=lambda x: -x[1]):
    print(f"  {v:>3}  {k}")
print(f"\n=== TOKEN HOGS (top item per pack) ===")
for h in hog_stats:
    print(f"  {h[0]:<41} tot={h[1]:>4} top={h[2]:>4} ({h[4]:>4.0%}) {h[3]}")
print("\n=== TOKENS BY ROLE ===")
for r, t in sorted(role_tok.items(), key=lambda x: -x[1]):
    print(f"  {r:<12} {t}")
print("\n=== FILES WITH MULTIPLE ITEMS ===")
multi = {f: q for f, q in per_file_items.items() if len(q) > 2}
for f, q in list(multi.items())[:15]:
    print(f"  {f.split('/')[-1]:<40} x{len(q)}: {[x.split('.')[-1][:18] for x in q]}")
print("\n=== INDIVIDUAL LOSSES ===")
for lid, f, why in loss_rows:
    print(f"  {lid:<46} {f:<38} {why}")
