"""Issue #32: evaluate FROZEN scorers on all sets; apply the preregistered gate
(PROTOCOL.md §7-10). Refuses to run if frozen_models.json changed since hashing."""
import hashlib, json, os
import numpy as np
from common import (ALL_FEATURES, FIXED_COMBOS, RES, boot_idx, delta, eligible, fixed_scorers,
                    load_rows, lr_scorer, per_task, random_scorer, summarize)

SETS = ['dev', 'masked', 'heldout', 'cb']
GATE_SETS = ['heldout', 'cb']
BANDS = ['B1', 'B2', 'B3', 'B4']
DUP_LATER = 'httpx-88a81c5d'


def load_frozen():
    p = os.path.join(RES, 'frozen_models.json')
    want = open(p + '.sha256').read().split()[0]
    assert hashlib.sha256(open(p, 'rb').read()).hexdigest() == want, 'frozen models changed'
    return json.load(open(p)), want


def scorers(fr):
    sc = dict(fixed_scorers())
    for name, m in fr['models'].items():
        sc[name] = lr_scorer(m)
    bs = fr['best_single']; sign = fr['single'][bs]['sign']
    sc['best_single'] = lambda r, f=bs, s=sign: s * r['x'][f]
    sc['best_fixed'] = sc[fr['best_fixed']]
    for f in ALL_FEATURES:
        s = fr['single'][f]['sign']
        sc['feat:' + f] = lambda r, f=f, s=s: s * r['x'][f]
    return sc


GATE = ['LR-R', 'LR-S', 'LR-I', 'LR-C', 'LR-RS', 'LR-ALL', 'best_single', 'best_fixed']
CONTROLS = ['random', 'fused_order', 'pool_order', 'lexical_only', 'semantic_only', 'rrf_baseline',
            'rel_only_weight', 'rel_only_links', 'rel_only_seedrank']


def labeled(tasks, s):
    return [v for k, v in sorted(tasks.items()) if k[0] == s and all(r['y'] is not None for r in v)]


def cell(task_lists, names, sc, idx_seed=32):
    el = [t for t in task_lists if eligible(t)]
    if not el:
        return {'n': 0}, None, el
    idx = boot_idx(len(el), seed=idx_seed)
    pts = {}
    for n in names:
        if n == 'random':
            runs = [per_task(el, random_scorer(32 + i)) for i in range(200)]
            pts[n] = {k: np.mean([r[k] for r in runs], 0) for k in runs[0]}
        else:
            pts[n] = per_task(el, sc[n])
    pos = sum(r['y'] for t in el for r in t); tot = sum(len(t) for t in el)
    out = {'n': len(el), 'pos': int(pos), 'neg': int(tot - pos),
           'scorers': {n: summarize(pts[n], idx) for n in names}}
    return out, (pts, idx), el


def cstar(pts):
    return max(['fused_order', 'pool_order'], key=lambda n: pts[n]['auc'].mean())


def main():
    fr, fh = load_frozen()
    sc = scorers(fr)
    tasks = load_rows()
    R = {'frozen_models_sha256': fh, 'sets': {}, 'gate': {}}
    names_all = CONTROLS + FIXED_COMBOS + list(fr['models']) + ['best_single', 'best_fixed'] + \
        ['feat:' + f for f in ALL_FEATURES]
    for s in SETS:
        tl = labeled(tasks, s)
        S = {'tasks_total': sum(1 for k in tasks if k[0] == s), 'tasks_labeled': len(tl)}
        if not tl:
            S['note'] = 'no labels available (ContextBench gold not obtainable in this environment)'
            R['sets'][s] = S; continue
        full, st, _ = cell(tl, names_all, sc)
        S['full'] = full
        if st:
            pts, idx = st
            c = cstar(pts); S['cstar'] = c
            S['delta_vs_fused'] = {n: delta(pts[n], pts['fused_order'], idx) for n in names_all if n != 'fused_order'}
            S['delta_vs_cstar'] = {n: delta(pts[n], pts[c], idx) for n in GATE + list(fr['models']) + FIXED_COMBOS}
            S['structural'] = {
                'LR-RS - LR-R': delta(pts['LR-RS'], pts['LR-R'], idx),
                **{f'{m} - LR-R': delta(pts[m], pts['LR-R'], idx)
                   for m in ['LR-R+reltype', 'LR-R+distance', 'LR-R+seedrank', 'LR-R+degree']},
                'LR-S - fused': delta(pts['LR-S'], pts['fused_order'], idx),
                'LR-ALL - LR-RS': delta(pts['LR-ALL'], pts['LR-RS'], idx),
            }
        # artifact-free universe (no modules, no test files)
        af = [[r for r in t if not r['x']['is_module'] and not r['x']['is_test']] for t in tl]
        afc, ast, _ = cell(af, ['fused_order', 'pool_order'] + GATE, sc)
        S['artifact_free'] = afc
        if ast:
            pts, idx = ast; c = cstar(pts); afc['cstar'] = c
            afc['delta_vs_cstar'] = {n: delta(pts[n], pts[c], idx) for n in GATE}
        # bands
        bn = ['fused_order', 'pool_order', 'lexical_only', 'semantic_only', 'LR-R', 'LR-RS', 'LR-ALL',
              'best_single', 'best_fixed']
        S['bands'] = {}
        for b in BANDS:
            bt = [[r for r in t if r['band'] == b] for t in tl]
            bc, bst, _ = cell(bt, bn, sc)
            if bst:
                pts, idx = bst
                bc['d_LR-RS_vs_fused'] = delta(pts['LR-RS'], pts['fused_order'], idx)
                bc['d_LR-ALL_vs_fused'] = delta(pts['LR-ALL'], pts['fused_order'], idx)
                bc['d_best_single_vs_fused'] = delta(pts['best_single'], pts['fused_order'], idx)
            S['bands'][b] = bc
        # query classes
        S['qclass'] = {}
        for qc in sorted({t[0]['qclass'] for t in tl}):
            qt = [t for t in tl if t[0]['qclass'] == qc]
            qcc, qst, _ = cell(qt, ['fused_order', 'pool_order', 'LR-R', 'LR-RS', 'LR-ALL', 'best_single'], sc)
            if qst:
                pts, idx = qst
                qcc['d_LR-RS_vs_fused'] = delta(pts['LR-RS'], pts['fused_order'], idx)
                qcc['d_LR-ALL_vs_fused'] = delta(pts['LR-ALL'], pts['fused_order'], idx)
            S['qclass'][qc] = qcc
        if s == 'heldout':
            nd = [t for t in tl if t[0]['task'] != DUP_LATER]
            ndc, nst, _ = cell(nd, ['fused_order', 'pool_order'] + GATE, sc)
            if nst:
                pts, idx = nst; c = cstar(pts); ndc['cstar'] = c
                ndc['delta_vs_cstar'] = {n: delta(pts[n], pts[c], idx) for n in GATE}
            S['no_duplicate'] = ndc
        R['sets'][s] = S
    # ---- gate (PROTOCOL §8) ----
    for s in GATE_SETS:
        S = R['sets'][s]
        if 'full' not in S or S['full'].get('n', 0) < 10:
            R['gate'][s] = {'evaluable': False, 'reason': S.get('note', 'fewer than 10 eligible tasks')}
            continue
        g = {'evaluable': True, 'cstar': S['cstar'], 'candidates': {}}
        for n in GATE:
            d = S['delta_vs_cstar'][n]
            c1 = d['d_auroc'] >= 0.05 and d['d_auroc_ci'][0] > 0
            c2 = d['d_pairwise'] >= -0.01
            c3 = True
            if s == 'heldout':
                af = S['artifact_free']
                if af.get('n', 0) < 10:
                    c3 = False
                else:
                    da = af['delta_vs_cstar'][n]
                    c3 = da['d_auroc'] >= 0.05 and da['d_auroc_ci'][0] > 0
            g['candidates'][n] = {'auroc_margin': c1, 'pairwise_ok': c2, 'artifact_free': c3,
                                  'pass': c1 and c2 and c3, **d}
        R['gate'][s] = g
    ho = R['gate']['heldout']; cb = R['gate']['cb']
    if not ho['evaluable']:
        verdict = 'C'
    elif cb['evaluable']:
        both = [n for n in GATE if ho['candidates'][n]['pass'] and cb['candidates'][n]['pass']]
        verdict = 'A' if both else 'B'
    else:
        verdict = 'C' if any(ho['candidates'][n]['pass'] for n in GATE) else 'B'
    R['verdict'] = verdict
    json.dump(R, open(os.path.join(RES, 'results.json'), 'w'), indent=1)
    print('verdict', verdict)
    for s in GATE_SETS:
        g = R['gate'][s]
        if not g['evaluable']:
            print(s, 'NOT evaluable:', g['reason']); continue
        print(s, 'C* =', g['cstar'])
        for n, c in g['candidates'].items():
            print(f"  {n:12s} dAUROC {c['d_auroc']:+.3f} [{c['d_auroc_ci'][0]:+.3f},{c['d_auroc_ci'][1]:+.3f}] "
                  f"dPair {c['d_pairwise']:+.3f} margin={c['auroc_margin']} pair={c['pairwise_ok']} af={c['artifact_free']} PASS={c['pass']}")


if __name__ == '__main__':
    main()
