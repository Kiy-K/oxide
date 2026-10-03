"""Issue #39 probe: evaluate X and X+F against fused order on the probe tasks and apply the
frozen PROBE §6 decision rule. Refuses to run if PROBE.md changed since it was hashed.
Writes results/probe-results.json and prints the tables used in the README."""
import gzip, hashlib, json, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
RES = os.path.join(HERE, '..', 'results')
sys.dont_write_bytecode = True
sys.path.insert(0, f'{REPO}/docs/selection-separability-eval/scripts')
from common import boot_idx, delta, eligible, per_task, random_scorer, summarize  # noqa: E402
from probe_tasks import pick  # noqa: E402

for f in ('PROBE',):
    p = os.path.join(HERE, '..', f + '.md')
    assert hashlib.sha256(open(p, 'rb').read()).hexdigest() == \
        open(p.replace('.md', '.sha256')).read().split()[0], f'{f}.md changed since freeze'

GATE = ['X', 'X+F']
NAMES = ['fused_order', 'random'] + GATE



def load():
    sel = set(pick())
    sc, meta = {}, []
    for j in map(json.loads, open(os.path.join(RES, 'scores-probe.jsonl'))):
        sc.update({(j['set'], j['task'], k): s for k, s in zip(j['keys'], j['scores'])})
        meta.append(j)
    assert {(j['set'], j['task']) for j in meta} == sel, 'probe scores incomplete'
    tasks = {}
    for r in map(json.loads, gzip.open(os.path.join(RES, 'cands.jsonl.gz'), 'rt')):
        if (r['set'], r['task']) in sel:
            r['s'] = {'fused_order': -r['fr'], 'X': sc[(r['set'], r['task'], r['key'])]}
            tasks.setdefault((r['set'], r['task']), []).append(r)
    for rs in tasks.values():  # PROBE §5: no fitting
        for i, r in enumerate(sorted(rs, key=lambda r: (-r['s']['X'], r['fr'])), 1):
            r['s']['X+F'] = 1 / (60 + i) + 1 / (60 + r['fr'])
    return tasks, meta


def recall(t, n, k):
    top = sorted(t, key=lambda r: -r['s'][n])[:k]
    return sum(r['y'] for r in top) / sum(r['y'] for r in t)


def evaluate(tl):
    el = [t for t in tl if eligible(t)]
    if not el:
        return {'n': 0}
    idx = boot_idx(len(el), seed=32)
    pts = {n: per_task(el, lambda r, n=n: r['s'][n]) for n in NAMES if n != 'random'}
    runs = [per_task(el, random_scorer(32 + i)) for i in range(200)]
    pts['random'] = {k: np.mean([r[k] for r in runs], 0) for k in runs[0]}
    out = {'n': len(el), 'pos': sum(r['y'] for t in el for r in t),
           'scorers': {n: summarize(pts[n], idx) for n in NAMES},
           'delta': {n: delta(pts[n], pts['fused_order'], idx) for n in NAMES if n != 'fused_order'},
           'per_task_d_auroc': {n: (pts[n]['auc'] - pts['fused_order']['auc']).round(3).tolist() for n in GATE}}
    for n in NAMES:
        if n != 'random':
            for k in (5, 16):
                out['scorers'][n][f'R@{k}'] = float(np.mean([recall(t, n, k) for t in el]))
    return out


def main():
    tasks, meta = load()
    by = lambda s: [v for k, v in sorted(tasks.items()) if k[0] == s]
    R = {s: {'all': evaluate(by(s)),
             'B2': evaluate([[r for r in t if r['band'] == 'B2'] for t in by(s)])} for s in ('cb', 'heldout')}
    pairs = sum(len(j['keys']) for j in meta)
    secs = sum(j['secs'] for j in meta)
    ops = {'pairs': pairs, 'secs': secs, 's_per_pair': secs / pairs, 'peak_rss_mb': max(j['rss_mb'] for j in meta),
           'tokens': sum(j['tokens'] for j in meta), 'load_s': meta[0]['load_s'],
           'median_task_s': float(np.median([j['secs'] for j in meta])),
           'max_task_s': max(j['secs'] for j in meta)}
    R['ops'] = ops
    crit = {}
    for g in GATE:
        cb, ho = R['cb']['all'], R['heldout']['all']
        crit[g] = {
            '1_cb_margin': cb['delta'][g]['d_auroc'] >= 0.05 and cb['delta'][g]['d_auroc_ci'][0] > 0,
            '2_cb_R16': cb['scorers'][g]['R@16'] - cb['scorers']['fused_order']['R@16'] >= 0,
            '3_heldout_positive': ho['delta'][g]['d_auroc'] > 0,
            '4_cost': ops['peak_rss_mb'] <= 1024 and ops['median_task_s'] <= 2}
    R['criteria'] = crit
    R['verdict'] = 'INVESTIGATE' if any(all(c.values()) for c in crit.values()) else 'CLOSE'
    json.dump(R, open(os.path.join(RES, 'probe-results.json'), 'w'), indent=1, sort_keys=True)
    report(R)


def report(R):
    for s in ('cb', 'heldout'):
        for u in ('all', 'B2'):
            c = R[s][u]
            print(f"\n## {s} / {u}: n={c['n']} pos={c.get('pos')}")
            if not c['n']:
                continue
            print('| scorer | AUROC [CI] | Δ AUROC vs fused [CI] | pairwise | R@5 | R@16 |')
            print('|---|---|---|---:|---:|---:|')
            for n in NAMES:
                sc = c['scorers'][n]
                d = c['delta'].get(n)
                ds = f"{d['d_auroc']:+.3f} [{d['d_auroc_ci'][0]:+.3f},{d['d_auroc_ci'][1]:+.3f}]" if d else '—'
                print(f"| {n} | {sc['auroc']:.3f} [{sc['auroc_ci'][0]:.3f},{sc['auroc_ci'][1]:.3f}] | {ds} | "
                      f"{sc['pairwise']:.3f} | {sc.get('R@5', float('nan')):.3f} | {sc.get('R@16', float('nan')):.3f} |")
            for g in GATE:
                print(f'per-task Δ AUROC {g}: {c["per_task_d_auroc"][g]}')
    print('\n## ops', json.dumps({k: round(v, 3) for k, v in R['ops'].items()}))
    print('## criteria', json.dumps(R['criteria']))
    print('verdict', R['verdict'])


if __name__ == '__main__':
    main()
