"""Candidate-level evaluation of Julia scores vs OXIDE score/order (supporting evidence only).

usage: eval_cand.py <set>[,<set>...] <score-suffix> [--json out]
  e.g. eval_cand.py cb,heldout,dev J1     reads scores/<set>.J1.jsonl
Labels (gold only, no Julia): snip = capped snippet ∩ gold lines; sym = full span ∩ gold lines.
"""
import json, os, statistics, sys
from collections import defaultdict

J = os.path.expanduser('~/.cache/oxide-julia-eval')
sets, suf = sys.argv[1].split(','), sys.argv[2]
OUT = sys.argv[sys.argv.index('--json') + 1] if '--json' in sys.argv else None
span = lambda a, b: set(range(a, b + 1)) if a > 0 else set()


def auc(pos, neg):
    if not pos or not neg:
        return None
    w = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return w / (len(pos) * len(neg))


def auprc(xs):  # xs: [(score, label)]
    xs = sorted(xs, key=lambda t: -t[0]); tp = 0; ap = 0; P = sum(l for _, l in xs)
    for i, (_, l) in enumerate(xs, 1):
        if l:
            tp += 1; ap += tp / i
    return ap / P if P else None


C = []  # candidate rows
for s in sets:
    gold = json.load(open(f'{J}/gold/{s}.json'))
    sc = {r['id']: r['scores'] for r in map(json.loads, open(f'{J}/scores/{s}.{suf}.jsonl'))}
    for r in map(json.loads, open(f'{J}/dumps/{s}-ch0.jsonl')):
        gl = {f: set(v) for f, v in gold.get(r['id'], {}).get('lines', {}).items()}
        if not gl or r['id'] not in sc:
            continue
        om = dict(map(tuple, r['trace']['omitted']))
        packed = {p['key'] for p in r['trace']['packed']}
        for i, p in enumerate(r['trace']['pool']):
            f = p['key'].split('#', 1)[0]
            snip = bool(span(*p['cap_span']) & gl.get(f, set()))
            sym = bool(span(*p['span']) & gl.get(f, set()))
            cat = 'gold' if snip else ('same-file' if f in gl else 'other-file')
            C.append(dict(set=s, task=r['id'], key=p['key'], julia=sc[r['id']][p['key']], score=p['score'],
                          pos=-i, snip=snip, sym=sym, cat=cat, role=p['role'], tok=p['cap_tok'],
                          status='packed' if p['key'] in packed else om.get(p['key'], '?')))

res = {'n_cand': len(C), 'n_tasks': len({c['task'] for c in C}), 'gold_rate': sum(c['snip'] for c in C) / len(C)}
by = defaultdict(list)
for c in C:
    by[c['task']].append(c)
for lab in ('snip', 'sym'):
    for sig in ('julia', 'score', 'pos'):
        res[f'auroc_{lab}_{sig}'] = auc([c[sig] for c in C if c[lab]], [c[sig] for c in C if not c[lab]])
        res[f'auprc_{lab}_{sig}'] = auprc([(c[sig], c[lab]) for c in C])
    # within-task pairwise ordering accuracy (micro over gold/non-gold pairs) and macro AUROC
    for sig in ('julia', 'score', 'pos'):
        w = n = 0; macro = []
        for cs in by.values():
            pos = [c[sig] for c in cs if c[lab]]; neg = [c[sig] for c in cs if not c[lab]]
            if pos and neg:
                a = auc(pos, neg); macro.append(a); w += a * len(pos) * len(neg); n += len(pos) * len(neg)
        res[f'pairwise_{lab}_{sig}'] = w / n if n else None
        res[f'macro_task_auroc_{lab}_{sig}'] = sum(macro) / len(macro) if macro else None
        res[f'n_tasks_both_{lab}'] = len(macro)
# precision/recall at 0.5
tp = sum(c['snip'] and c['julia'] >= .5 for c in C); pp = sum(c['julia'] >= .5 for c in C); P = sum(c['snip'] for c in C)
res['prec@0.5'] = tp / pp if pp else None; res['rec@0.5'] = tp / P if P else None; res['frac>=0.5'] = pp / len(C)
# calibration (reliability) in deciles of Julia score
bins = defaultdict(list)
for c in C:
    bins[min(9, int(c['julia'] * 10))].append(c['snip'])
res['calibration'] = {f'{b / 10:.1f}': [len(v), round(sum(v) / len(v), 3)] for b, v in sorted(bins.items())}
# useful-vs-junk separation by category
res['mean_by_cat'] = {k: [sum(1 for c in C if c['cat'] == k),
                          round(sum(c['julia'] for c in C if c['cat'] == k) / max(1, sum(1 for c in C if c['cat'] == k)), 4)]
                      for k in ('gold', 'same-file', 'other-file')}
# allocator case: per-file-cap drops, gold sibling vs token-burning sibling
pf = [c for c in C if c['status'] == 'per-file diversity cap']
for sig in ('julia', 'score'):
    res[f'perfile_auroc_{sig}'] = auc([c[sig] for c in pf if c['snip']], [c[sig] for c in pf if not c['snip']])
res['perfile_n'] = [len(pf), sum(c['snip'] for c in pf)]
res['julia_within_task_sd_median'] = statistics.median(
    statistics.pstdev([c['julia'] for c in cs]) for cs in by.values() if len(cs) > 1)
res['julia_mean'] = sum(c['julia'] for c in C) / len(C)
for k, v in res.items():
    print(f'{k}: {round(v, 4) if isinstance(v, float) else v}')
if OUT:
    json.dump(dict(summary=res, rows=C), open(OUT, 'w'))
