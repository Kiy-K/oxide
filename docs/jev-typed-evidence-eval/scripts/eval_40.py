"""#40 verdict (PROTOCOL §3-§6). Written and hashed before any Jev call; run only after scoring.

Inputs: ~/.cache/oxide-jev-eval/tasks/{heldout,cb}-frozen.jsonl, results/scores.jsonl,
results/canary-{pre,post}.jsonl, results/packs.jsonl (jev_pack arm a and arm b per order
scorer), results/parity_v5.tsv. Output: results/eval.json and results/eval.txt.

Statistics: within-task file AUROC with ties 0.5 (common.task_auc_pairs); repo-clustered
paired bootstrap (repos with replacement, then tasks with replacement within each drawn repo,
keeping its task count; 10,000 resamples; fresh numpy default_rng(40) per analysis, repos in
sorted order; clustering within the analysed subset; < 5 repos -> point estimate decides).
G5 metrics: ch5 score_alloc.pack_metrics, called unchanged through its own module.

usage: eval_40.py
"""
import json, os, runpy, subprocess, sys, tempfile
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
RES = os.path.join(HERE, '..', 'results')
J = os.path.expanduser('~/.cache/oxide-jev-eval')
sys.dont_write_bytecode = True
sys.path.insert(0, f'{REPO}/docs/selection-separability-eval/scripts')
from common import task_auc_pairs, random_scorer  # noqa: E402

B, SEED, MODEL = 10_000, 40, 'jev-1.13.0'
TYPED = ('primary', 'in_scope', 'implementation')
DIMS = ('relevant', 'in_scope', 'primary', 'implementation', 'test', 'concrete_reference')
G3D_APPLICABLE = False  # declared at freeze (PROTOCOL.md step-1 constants: expected N = 2 < 20)


# ---------------------------------------------------------------- data
def load():
    tasks = [json.loads(l) for s in ('heldout', 'cb') for l in open(f'{J}/tasks/{s}-frozen.jsonl')]
    scores = {}
    for l in open(os.path.join(RES, 'scores.jsonl')):
        s = json.loads(l)
        scores[s['task']] = s
    calls = [c for s in scores.values() for c in s['calls']]
    excluded, kept = [], []
    for t in tasks:
        s = scores.get(t['id'])
        p = {} if s is None else {(c['path'], c['arm']): c['p'] for c in s['calls'] if c['ok']}
        if s is None or any((f['path'], a) not in p for f in t['views'] for a in ('typed', 'meta')):
            excluded.append(t['id'])
            continue
        rows = []
        for f in t['views']:
            q, m = p[(f['path'], 'typed')], p[(f['path'], 'meta')]
            sc = {'fused': -f['best_fr'], 'J1': q['J1'], **{d: q[d] for d in DIMS},
                  'S': float(np.mean([q[d] for d in TYPED])), 'S_meta': float(np.mean([m[d] for d in TYPED])),
                  'T-gate': q['relevant'] * q['in_scope'],
                  'T-mean': float(np.mean([q[d] for d in ('relevant', 'in_scope', 'primary', 'implementation',
                                                          'concrete_reference')]))}
            rows.append({'task': t['id'], 'key': f['path'], 'y': f['y'], 'fr': f['best_fr'], 'is_test': f['is_test'],
                         'module_only': all(d.endswith(':__module__') for d in f['decls']), 's': sc})
        for g in ('J1', 'S', 'S_meta', 'T-gate', 'T-mean', *TYPED):  # X+F; rank_X ties broken by best fused rank
            for i, r in enumerate(sorted(rows, key=lambda r: (-r['s'][g], r['fr'])), 1):
                r['s'][g + '+F'] = 1 / (60 + i) + 1 / (60 + r['fr'])
        kept.append({**{k: t[k] for k in ('set', 'id', 'repo', 'stratum', 'qclass', 'root', 'g5_gold')},
                     'commit_date': t.get('commit_date'), 'rows': rows})
    return tasks, kept, excluded, calls


def auc(rows, name):
    return task_auc_pairs([r['s'][name] for r in rows], [r['y'] for r in rows])[0]


def r_at3(rows, name):
    top = sorted(rows, key=lambda r: (-r['s'][name], r['fr']))[:3]
    return sum(r['y'] for r in top) / sum(r['y'] for r in rows)


def random_auc(rows):
    return float(np.mean([task_auc_pairs([random_scorer(SEED + i)(r) for r in rows], [r['y'] for r in rows])[0]
                          for i in range(200)]))


# ---------------------------------------------------------------- repo-clustered bootstrap
def hboot(repos):
    """B resamples of task indices: repos with replacement, then tasks within each drawn repo."""
    rng = np.random.default_rng(SEED)
    names = sorted(set(repos))
    members = {n: np.array([i for i, r in enumerate(repos) if r == n]) for n in names}
    out = []
    for _ in range(B):
        idx = []
        for n in rng.choice(names, size=len(names), replace=True):
            m = members[n]
            idx.append(m[rng.integers(0, len(m), size=len(m))])
        out.append(np.concatenate(idx))
    return out


def ci_of(vals, idx, level=95.0):
    lo = (100 - level) / 2
    bs = np.array([vals[i].mean() for i in idx])
    return [float(np.percentile(bs, lo)), float(np.percentile(bs, 100 - lo))]


def delta(ts, a, b, level=95.0):
    """Paired Δ AUROC(a − b) over tasks `ts`: point, CI, repos, n."""
    d = np.array([auc(t['rows'], a) - auc(t['rows'], b) for t in ts])
    repos = [t['repo'] for t in ts]
    nrep = len(set(repos))
    ci = ci_of(d, hboot(repos), level) if len(ts) else [float('nan')] * 2
    return {'point': float(d.mean()) if len(ts) else float('nan'), 'ci': ci, 'n': len(ts), 'repos': nrep,
            'ci_decides': nrep >= 5, 'per_task': d.tolist()}


def passes(dl, thr=0.0, lb=0.0):
    """Point: > thr when thr == 0, else >= thr. CI (only with >= 5 repos): lower bound > lb."""
    pt_ok = dl['point'] > thr if thr == 0.0 else dl['point'] >= thr
    ci_ok = (not dl['ci_decides']) or dl['ci'][0] > lb
    return pt_ok, ci_ok


# ---------------------------------------------------------------- G5 (ch5 pack metrics)
def pack_metrics_for(ts, label):
    packs = {}
    for l in open(os.path.join(RES, 'packs.jsonl')):
        p = json.loads(l)
        packs[(p['id'], p['label'])] = p
    with tempfile.TemporaryDirectory() as d:
        tp, gp, dp = f'{d}/t.jsonl', f'{d}/g.json', f'{d}/d.jsonl'
        with open(tp, 'w') as f:
            for t in ts:
                f.write(json.dumps({'id': t['id'], 'path': t['root']}) + '\n')
        json.dump({t['id']: {'lines': t['g5_gold'], 'files': sorted(t['g5_gold'])} for t in ts}, open(gp, 'w'))
        open(dp, 'w').close()
        argv = sys.argv
        sys.argv = ['score_alloc.py', tp, dp, '--cb', gp]
        try:
            mod = runpy.run_path(f'{REPO}/docs/alloc-utilization-eval/scripts/score_alloc.py', run_name='score_alloc')
        finally:
            sys.argv = argv
    return [mod['pack_metrics'](packs[(t['id'], label)], {'id': t['id'], 'path': t['root']},
                                {f: set(v) for f, v in t['g5_gold'].items()}) for t in ts]


def g5(ts, label='b:S'):
    a, b = pack_metrics_for(ts, 'a'), pack_metrics_for(ts, label)
    repos = [t['repo'] for t in ts]
    idx = hboot(repos)
    cov = np.array([y['gold_line_cov'] - x['gold_line_cov'] for x, y in zip(a, b)])
    eff_ok = np.array([x['used'] > 0 for x in a])
    ea = np.array([x['rel_per_used'] for x in a])
    eb = np.array([y['rel_per_used'] for y in b])
    ua = np.array([x['used'] for x in a], float)
    ub = np.array([y['used'] for y in b], float)

    def rel_eff(i):  # ch5 E1: (mean_task eff_B − mean_task eff_A) ÷ mean_task eff_A, same resample
        i = i[eff_ok[i]]
        return (eb[i].mean() - ea[i].mean()) / ea[i].mean() if len(i) and ea[i].mean() else float('nan')
    e1 = np.array([rel_eff(i) for i in idx])
    return {'d_cov': float(cov.mean()), 'd_cov_ci': ci_of(cov, idx),
            'rel_eff': float(rel_eff(np.arange(len(ts)))),
            'rel_eff_ci': [float(np.nanpercentile(e1, 2.5)), float(np.nanpercentile(e1, 97.5))],
            'token_rise': float((ub.mean() - ua.mean()) / ua.mean()), 'used0_tasks': int((~eff_ok).sum()),
            'repos': len(set(repos))}


# ---------------------------------------------------------------- validity
def validity(tasks, kept, excluded, calls):
    v = {}
    fail = sum(not c['ok'] for c in calls)
    v['V2'] = {'requests': len(calls), 'failed': fail, 'failed_frac': fail / max(1, len(calls)),
               'tasks_excluded': len(excluded), 'excluded_frac': len(excluded) / max(1, len(tasks))}
    v['V2']['pass'] = v['V2']['failed_frac'] <= 0.02 and v['V2']['excluded_frac'] <= 0.05
    repos = [t['repo'] for t in kept]
    share = max(repos.count(r) for r in set(repos)) / len(repos) if repos else 1.0
    n = lambda f: sum(1 for t in kept if f(t))
    x = {'total': len(kept), 'heldout': n(lambda t: t['set'] == 'heldout'), 'cb': n(lambda t: t['set'] == 'cb'),
         'repos': len(set(repos)), 'max_repo_share': share,
         'description': n(lambda t: t['stratum'] == 'description'), 'quoted': n(lambda t: t['stratum'] == 'quoted')}
    x['pass'] = (x['total'] >= 120 and x['heldout'] >= 60 and x['cb'] >= 40 and x['repos'] >= 12
                 and x['max_repo_share'] <= 0.15 and x['description'] >= 30 and x['quoted'] >= 30)
    v['V1'] = x
    models = {c['model'] for c in calls if c['ok']}
    pre = {c['id']: c for c in map(json.loads, open(os.path.join(RES, 'canary-pre.jsonl')))}
    post = {c['id']: c for c in map(json.loads, open(os.path.join(RES, 'canary-post.jsonl')))}
    can_ok = all(c['ok'] for c in [*pre.values(), *post.values()]) and pre.keys() == post.keys()
    drift = max((abs(pre[k]['p'][q] - post[k]['p'][q]) for k in pre for q in pre[k]['p']),
                default=float('inf')) if can_ok else float('inf')
    v['V3'] = {'models': sorted(m or '' for m in models), 'canary_ok': can_ok, 'max_canary_drift': drift,
               'pass': models == {MODEL} and can_ok and drift <= 0.02}
    chk = lambda f: subprocess.run(['sha256sum', '-c', os.path.basename(f)], cwd=os.path.dirname(f),
                                   capture_output=True).returncode == 0
    v['V4'] = {'pass': chk(os.path.join(HERE, '..', 'PROTOCOL.sha256')) and chk(os.path.join(RES, 'prereg.sha256'))}
    par = [l.rstrip('\n').split('\t') for l in open(os.path.join(RES, 'parity_v5.tsv'))
           if l.strip() and not l.startswith('id\t')]
    v['V5'] = {'tasks': len(par), 'mismatch': sum(r[1] != 'identical' for r in par)}
    v['V5']['pass'] = v['V5']['tasks'] == len(tasks) and v['V5']['mismatch'] == 0
    return v


# ---------------------------------------------------------------- gates
def gates(kept):
    G, pt_fail, ci_fail = {}, [], []

    def rec(name, dl, thr=0.0, lb=0.0):
        p, c = passes(dl, thr, lb)
        G[name] = {**{k: v for k, v in dl.items() if k != 'per_task'}, 'point_ok': p, 'ci_ok': c}
        if not p:
            pt_fail.append(name)
        elif not c:
            ci_fail.append(name)

    rec('G1', delta(kept, 'S', 'J1'), thr=0.10)
    rec('G2', delta(kept, 'S+F', 'fused'), thr=0.05)
    af = []
    for t in kept:  # ranks and X+F from the full view, not recomputed
        rows = [r for r in t['rows'] if not r['is_test'] and not r['module_only']]
        if 0 < sum(r['y'] for r in rows) < len(rows):
            af.append({**t, 'rows': rows})
    rec('G2_artifact_free', delta(af, 'S+F', 'fused'))
    for s in ('heldout', 'cb'):
        rec(f'G3a_{s}', delta([t for t in kept if t['set'] == s], 'S+F', 'fused'))
    ceil = lambda t: auc(t['rows'], 'S+F') == 1.0 and auc(t['rows'], 'fused') == 1.0
    by = {}
    for t in kept:
        by.setdefault(t['repo'], []).append(t)
    elig = {r: ts for r, ts in by.items() if len(ts) >= 3 and any(not ceil(t) for t in ts)}
    pos = {r: float(np.mean([auc(t['rows'], 'S+F') - auc(t['rows'], 'fused') for t in ts])) > 0
           for r, ts in elig.items()}
    share = sum(pos.values()) / len(pos) if pos else 0.0
    G['G3b_repo_share'] = {'repos': len(pos), 'positive': sum(pos.values()), 'share': share, 'point_ok': share >= 2 / 3}
    if share < 2 / 3:
        pt_fail.append('G3b_repo_share')
    loo_p, loo_c, folds = True, True, {}
    for r in sorted(by):
        dl = delta([t for t in kept if t['repo'] != r], 'S+F', 'fused')
        p, c = passes(dl)
        folds[r] = {'point': dl['point'], 'ci': dl['ci'], 'point_ok': p, 'ci_ok': c}
        loo_p &= p
        loo_c &= c
    G['G3b_loo'] = {'folds': folds, 'point_ok': loo_p, 'ci_ok': loo_c}
    if not loo_p:
        pt_fail.append('G3b_loo')
    elif not loo_c:
        ci_fail.append('G3b_loo')
    for st in ('description', 'quoted'):
        cls = [t for t in kept if t['stratum'] == st]
        nonc = [t for t in cls if not ceil(t)]
        if len(nonc) >= 15:
            rec(f'G3c_{st}', delta(nonc, 'S+F', 'fused'), lb=-0.02)
            G[f'G3c_{st}']['rule'] = f'non-ceiling ({len(nonc)} of {len(cls)})'
        else:
            dl = delta(cls, 'S+F', 'fused')
            ok = dl['point'] >= 0
            G[f'G3c_{st}'] = {'rule': 'ceiling-limited (non-harm; no transfer claimed)', 'non_ceiling': len(nonc),
                              'point': dl['point'], 'n': dl['n'], 'point_ok': ok, 'ci_ok': True}
            if not ok:
                pt_fail.append(f'G3c_{st}')
    G['G3d'] = {'applicable': G3D_APPLICABLE, 'note': 'declared not applicable at freeze (expected N = 2 < 20)'}
    rec('G4', delta(kept, 'S+F', 'S_meta+F'), thr=0.03)
    g = g5(kept)
    cov_p, cov_c = g['d_cov'] >= 0.03, g['d_cov_ci'][0] > 0
    lim_ok = g['token_rise'] <= 0.05 and g['rel_eff_ci'][0] >= -0.05
    G['G5'] = {**g, 'cov_point_ok': cov_p, 'cov_ci_ok': cov_c, 'limits_ok': lim_ok}
    if not cov_p or not lim_ok:
        pt_fail.append('G5')
    elif not cov_c:
        ci_fail.append('G5')
    return G, pt_fail, ci_fail


def dimensions(kept):
    """Descriptive: Bonferroni 98.33 % repo-clustered intervals; no effect on the verdict."""
    out = {}
    for d in TYPED:
        vj = delta(kept, d, 'J1', 98.333)
        vf = delta(kept, d + '+F', 'fused', 98.333)
        out[d] = {'auroc': float(np.mean([auc(t['rows'], d) for t in kept])),
                  'vs_J1': {k: v for k, v in vj.items() if k != 'per_task'},
                  'vs_fused_F': {k: v for k, v in vf.items() if k != 'per_task'},
                  'carrying_signal': vf['ci'][0] > 0, 'pack': g5(kept, f'b:{d}')}
    return out


def continuity(kept):
    """Reported only: probe scorers, R@3, random, ordinary task bootstrap, J1+F observation."""
    names = ['fused', 'J1', 'S', 'S_meta', 'T-mean', 'T-gate', *DIMS, 'J1+F', 'S+F', 'S_meta+F', 'T-mean+F', 'T-gate+F']
    ti = np.random.default_rng(32).integers(0, len(kept), size=(2000, len(kept)))
    out = {}
    for n in names:
        a = np.array([auc(t['rows'], n) for t in kept])
        out[n] = {'auroc': float(a.mean()),
                  'task_boot_ci': [float(np.percentile(a[ti].mean(1), 2.5)), float(np.percentile(a[ti].mean(1), 97.5))],
                  'r_at3': float(np.mean([r_at3(t['rows'], n) for t in kept]))}
    out['random'] = {'auroc': float(np.mean([random_auc(t['rows']) for t in kept]))}
    j1f, j1f_af = delta(kept, 'J1+F', 'fused'), None
    out['J1+F_vs_fused'] = {k: v for k, v in j1f.items() if k != 'per_task'}
    return out


def main():
    tasks, kept, excluded, calls = load()
    v = validity(tasks, kept, excluded, calls)
    res = {'validity': v}
    if not all(x['pass'] for x in v.values()):
        res['verdict'] = 'INCONCLUSIVE (validity)'
    else:
        G, pt_fail, ci_fail = gates(kept)
        res.update(gates=G, point_failures=pt_fail, ci_failures=ci_fail, dimensions=dimensions(kept),
                   continuity=continuity(kept))
        res['verdict'] = ('NO TRANSFERABLE SIGNAL' if pt_fail else
                          'INCONCLUSIVE (power)' if ci_fail else 'TRANSFERABLE SIGNAL')
    json.dump(res, open(os.path.join(RES, 'eval.json'), 'w'), indent=1, default=float)
    with open(os.path.join(RES, 'eval.txt'), 'w') as f:
        f.write(json.dumps(res, indent=1, default=float))
    print('VERDICT:', res['verdict'])


if __name__ == '__main__':
    main()
