import json, os, sys
R = '/home/khoi/Projects/oxide'
sys.dont_write_bytecode = True
import importlib.util
_spec = importlib.util.spec_from_file_location('run43', f'{R}/docs/cap-removal-eval/scripts/run.py')
r43 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(r43)  # frozen #43 runner (sets binaries)
r42 = r43.r42
r42.J = os.path.expanduser('~/.cache/oxide-capremoval-eval/pilot')
r42.RES = f'{R}/docs/cap-removal-eval/results/phase1-pilot'
r42.parity(); r43.arms()
sys.path.insert(0, f'{R}/docs/treeselect-eval/scripts')
import score as s42
ts = r42.tasks()
packs = {}
for l in open(f'{r42.RES}/packs.jsonl'):
    p = json.loads(l); packs[(p['id'], p['arm'])] = p
pm = s42.score_alloc(ts)['pack_metrics']
M = {a: [pm(packs[(t['id'], a)], {'id': t['id'], 'path': t['root']}, {f: set(v) for f, v in t['g5_gold'].items()}) for t in ts] for a in 'AB'}
c = s42.compare(M['B'], M['A'], [t['repo'].lower() for t in ts])
par = [l.split('\t')[1] for l in list(open(f'{r42.RES}/parity_p1.tsv'))[1:]]
rep = sum(packs[(t['id'], 'B')]['repeat_identical'] for t in ts)
lines = [f"#43 PHASE 1 PILOT (descriptive, NOT the verdict): held-out httpx/requests/flask, n={c['n']}, repos={c['repos']}",
         f"B vs A: cov {c['cov_base']:.4f}->{c['cov_x']:.4f}  Δ {c['d_cov']:+.4f} clustered[{c['d_cov_ci_cluster'][0]:+.4f},{c['d_cov_ci_cluster'][1]:+.4f}] (3 repos: CI not decision-grade) task[{c['d_cov_ci_task'][0]:+.4f},{c['d_cov_ci_task'][1]:+.4f}] up/down {c['improved']}/{c['regressed']}",
         f"rel/used {c['rel_eff']:+.1%} task[{c['rel_eff_ci_task'][0]:+.1%},{c['rel_eff_ci_task'][1]:+.1%}]  tokens {c['tokens_base']:.0f}->{c['tokens_x']:.0f}  items {c['items_base']:.2f}->{c['items_x']:.2f}",
         f"P1 parity {par.count('identical')}/{len(par)}; B repeats {rep}/{len(ts)}"]
open(f'{r42.RES}/pilot.txt', 'w').write('\n'.join(lines) + '\n'); print('\n'.join(lines))
