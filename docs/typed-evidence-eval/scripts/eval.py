"""Typed-evidence probe: metrics and the PROBE.md §7 decision, per arm (julia, jev).

Refuses to run if PROBE.md changed since it was hashed.
usage: eval.py   -> results/probe-results.json, tables on stdout
"""
import hashlib, json, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
RES = os.path.join(HERE, '..', 'results')
sys.dont_write_bytecode = True
sys.path.insert(0, f'{REPO}/docs/selection-separability-eval/scripts')
from common import boot_idx, delta, eligible, per_task, random_scorer, summarize  # noqa: E402

P = os.path.join(HERE, '..', 'PROBE.md')
assert hashlib.sha256(open(P, 'rb').read()).hexdigest() == \
    open(P.replace('.md', '.sha256')).read().split()[0], 'PROBE.md changed since freeze'

DIMS = ['relevant', 'in_scope', 'primary', 'implementation', 'test', 'concrete_reference']
GATE = ['T-gate', 'T-mean']
SCORERS = ['fused', 'random', 'J1', *DIMS, *GATE, 'J1+F', 'T-gate+F', 'T-mean+F']


def load(arm):
    views = {(v['set'], v['task']): v for v in map(json.loads, open(os.path.join(RES, 'views.jsonl')))}
    sc = {(j['set'], j['task']): j for j in map(json.loads, open(os.path.join(RES, f'scores-{arm}.jsonl')))}
    assert sc.keys() == views.keys(), f'{arm}: scores incomplete'
    tasks = []
    for k, v in views.items():
        p = {f['path']: f['p'] for f in sc[k]['files']}
        rows = []
        for f in v['files']:
            q = p[f['path']]
            s = {'fused': -f['best_fr'], 'J1': q['J1'], **{d: q[d] for d in DIMS},
                 'T-gate': q['relevant'] * q['in_scope'],
                 'T-mean': float(np.mean([q[d] for d in ('relevant', 'in_scope', 'primary', 'implementation',
                                                         'concrete_reference')]))}
            rows.append({'task': k[1], 'key': f['path'], 'y': f['y'], 'fr': f['best_fr'], 'is_test': f['is_test'], 's': s})
        for g in ('J1', *GATE):  # G+F: rank fusion with OXIDE order, no fitting
            for i, r in enumerate(sorted(rows, key=lambda r: (-r['s'][g], r['fr'])), 1):
                r['s'][g + '+F'] = 1 / (60 + i) + 1 / (60 + r['fr'])
        tasks.append({'set': k[0], 'task': k[1], 'repo': v['repo'], 'qclass': v['qclass'], 'rows': rows})
    return tasks, list(sc.values())


def r_at(rows, n, k=3):
    top = sorted(rows, key=lambda r: -r['s'][n])[:k]
    return sum(r['y'] for r in top) / sum(r['y'] for r in rows)


def evaluate(tasks):
    el = [t['rows'] for t in tasks if eligible(t['rows'])]
    idx = boot_idx(len(el), seed=32)
    pts = {n: per_task(el, lambda r, n=n: r['s'][n]) for n in SCORERS if n != 'random'}
    runs = [per_task(el, random_scorer(32 + i)) for i in range(200)]
    pts['random'] = {k: np.mean([r[k] for r in runs], 0) for k in runs[0]}
    out = {'n': len(el), 'scorers': {n: summarize(pts[n], idx) for n in SCORERS},
           'vs_J1': {n: delta(pts[n], pts['J1'], idx) for n in SCORERS if n != 'J1'},
           'vs_fused': {n: delta(pts[n], pts['fused'], idx) for n in SCORERS if n != 'fused'},
           'task_auc': {n: pts[n]['auc'].tolist() for n in SCORERS}}
    for n in SCORERS:
        if n != 'random':
            out['scorers'][n]['R@3'] = float(np.mean([r_at(t, n) for t in el]))
    return out


def junk_profile(tasks):
    """Mean P per dimension for gold files, non-gold test files and other non-gold files."""
    cats = {'gold': [], 'nongold_test': [], 'nongold_other': []}
    for t in tasks:
        for r in t['rows']:
            c = 'gold' if r['y'] else 'nongold_test' if r['is_test'] else 'nongold_other'
            cats[c].append(r['s'])
    return {c: {'n': len(v), **{d: float(np.mean([s[d] for s in v])) for d in ['J1', *DIMS]}}
            for c, v in cats.items() if v}


def decide(tasks, R):
    el = [t for t in tasks if eligible(t['rows'])]
    crit = {}
    for g in GATE:
        d = R['all']['vs_J1'][g]
        groups = {}
        for t, auc_g, auc_j in zip(el, R['all']['task_auc'][g], R['all']['task_auc']['J1']):
            for kind in ('set', 'repo', 'qclass'):
                groups.setdefault(kind, {}).setdefault(t[kind], []).append(auc_g - auc_j)
        repos = groups['repo']
        pos = sum(np.mean(v) > 0 for v in repos.values())
        q_bad = [q for q, v in groups['qclass'].items() if len(v) >= 3 and np.mean(v) < 0]
        c = {'A1_vs_J1': d['d_auroc'] >= 0.10 and d['d_auroc_ci'][0] > 0,
             'A2_above_chance': R['all']['scorers'][g]['auroc_ci'][0] > 0.5,
             'A3_sets': all(np.mean(groups['set'].get(s, [-1])) > 0 for s in ('cb', 'heldout')),
             'A3_repos': f'{pos}/{len(repos)}', 'A3_repos_ok': pos >= 2 / 3 * len(repos),
             'A3_qclass_ok': not q_bad, 'A3_qclass_bad': q_bad}
        c['A'] = all(c[k] for k in ('A1_vs_J1', 'A2_above_chance', 'A3_sets', 'A3_repos_ok', 'A3_qclass_ok'))
        f = R['all']['vs_fused'][g + '+F']
        c['B'] = c['A'] and f['d_auroc'] >= 0.05 and f['d_auroc_ci'][0] > 0 and all(
            R[s]['vs_fused'][g + '+F']['d_auroc'] > 0 for s in ('cb', 'heldout'))
        crit[g] = c
    verdict = 'LIFT' if any(c['B'] for c in crit.values()) else \
        'TYPED SIGNAL, NO LIFT' if any(c['A'] for c in crit.values()) else 'NO TYPED SIGNAL'
    return crit, verdict


def cost(meta):
    ms = sorted(f['ms'] for m in meta for f in m['files'])
    return {'files': len(ms), 'median_file_ms': ms[len(ms) // 2], 'p95_file_ms': ms[int(0.95 * (len(ms) - 1))],
            'median_task_s': float(np.median([m['secs'] for m in meta])), 'peak_rss_mb': max(m['rss_mb'] for m in meta),
            'load_s': meta[0]['load_s'], 'backend': meta[0]['backend']}


def main():
    out = {}
    for arm in ('julia', 'jev'):
        if not os.path.exists(os.path.join(RES, f'scores-{arm}.jsonl')):
            print(f'## {arm}: no scores, skipped')
            continue
        tasks, meta = load(arm)
        R = {'all': evaluate(tasks), **{s: evaluate([t for t in tasks if t['set'] == s]) for s in ('cb', 'heldout')}}
        crit, verdict = decide(tasks, R)
        out[arm] = {'results': R, 'criteria': crit, 'verdict': verdict, 'junk': junk_profile(tasks), 'cost': cost(meta)}
        report(out[arm], arm)
    json.dump(out, open(os.path.join(RES, 'probe-results.json'), 'w'), indent=1, sort_keys=True, default=float)


def report(o, arm):
    R = o['results']
    f = lambda d: f"{d['d_auroc']:+.3f} [{d['d_auroc_ci'][0]:+.3f},{d['d_auroc_ci'][1]:+.3f}]" if d else '—'
    print(f"\n# {arm} ({o['cost']['backend']})")
    for part in ('all', 'cb', 'heldout'):
        c = R[part]
        print(f"\n## {part}: n={c['n']}")
        print('| scorer | AUROC [CI] | Δ vs J1 [CI] | Δ vs fused [CI] | pairwise | R@3 |')
        print('|---|---|---|---|---:|---:|')
        for n in SCORERS:
            s = c['scorers'][n]
            print(f"| {n} | {s['auroc']:.3f} [{s['auroc_ci'][0]:.3f},{s['auroc_ci'][1]:.3f}] | {f(c['vs_J1'].get(n))} | "
                  f"{f(c['vs_fused'].get(n))} | {s['pairwise']:.3f} | {s.get('R@3', float('nan')):.3f} |")
    print('\n## mean P by file category (useful vs junk)')
    for cat, v in o['junk'].items():
        print(cat, json.dumps({k: round(x, 3) if isinstance(x, float) else x for k, x in v.items()}))
    print('\n## cost', json.dumps({k: round(v, 3) if isinstance(v, float) else v for k, v in o['cost'].items()}))
    print('## criteria', json.dumps(o['criteria'], default=bool))
    print('verdict', arm, o['verdict'])


if __name__ == '__main__':
    main()
