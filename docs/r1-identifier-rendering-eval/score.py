"""R1 quality scoring (ANALYSIS_PLAN §Labels/§Metrics, PROTOCOL §4-5).
usage: score.py dev|heldout|cb   (order enforced: dev before heldout before cb)"""
import glob, json, math, os, random, statistics, sys
from phase0 import exposure, is_ident

HERE = os.path.dirname(os.path.abspath(__file__)); W = os.path.join(HERE, 'work'); RES = os.path.join(HERE, 'results')
SET = sys.argv[1]
PREV = {'heldout': 'score-dev.json', 'cb': 'score-heldout.json'}
if SET in PREV:
    assert os.path.exists(os.path.join(RES, PREV[SET])), f'order: score {PREV[SET]} first'


def symbols(corp):
    out = {}
    for l in open(f'{W}/texts/D0/{corp}.jsonl'):
        r = json.loads(l); out[r['id']] = (r['file'], r['kind'], r['span'])
    return out


def cb_gold(gold_lines, syms):
    by_file = {}
    for k, (f, kind, sp) in syms.items():
        by_file.setdefault(f, []).append((k, kind, sp))
    gold, flags = set(), {'lines': 0, 'unindexed_file': 0, 'outside_symbol': 0}
    for f, ranges in gold_lines.items():
        cands = by_file.get(f)
        for a, b in ranges:
            for line in range(a, b + 1):
                flags['lines'] += 1
                if not cands:
                    flags['unindexed_file'] += 1; continue
                inn = [(kind == 'module', sp[1] - sp[0], k) for k, kind, sp in cands if sp[0] <= line <= sp[1]]
                if not inn:
                    flags['outside_symbol'] += 1; continue
                gold.add(min(inn)[2])
    return gold, flags


def ndcg10(ranked, gold):
    dcg = sum(1 / math.log2(i + 2) for i, s in enumerate(ranked[:10]) if s in gold)
    idcg = sum(1 / math.log2(i + 2) for i in range(min(len(gold), 10)))
    return dcg / idcg if idcg else 0.0


def metrics(rec, gold):
    sem = [s for s, _ in rec['semantic'] if s]; fused = [x[0] for x in rec['fused']]
    pack = {i['id'] for i in rec['pack']['items']}
    m = {f'semR@{k}': len(gold & set(sem[:k])) / len(gold) for k in (16, 50, 200)}
    m['sem_rank'] = min((sem.index(g) + 1 for g in gold if g in sem), default=201)
    m['nDCG@10'] = ndcg10(fused, gold)
    m['fused_rank'] = min((fused.index(g) + 1 for g in gold if g in fused), default=len(fused) + 1)
    m['pack_cov'] = len(gold & pack) / len(gold)
    return m


def boot(d, n=2000):
    rng = random.Random(32)
    bs = sorted(statistics.fmean(rng.choices(d, k=len(d))) for _ in range(n))
    return [bs[int(0.025 * n)], bs[int(0.975 * n) - 1]]


def main():
    rows, flags = [], []
    for tf in sorted(glob.glob(f'{W}/tasks/{SET}__*.jsonl')):
        corp = os.path.basename(tf)[len(SET) + 2:-6]
        syms = symbols(corp)
        d0 = {json.loads(l)['id']: json.loads(l) for l in open(f'{W}/dumps/D0/{corp}.jsonl')}
        r1 = {json.loads(l)['id']: json.loads(l) for l in open(f'{W}/dumps/R1/{corp}.jsonl')}
        for t in map(json.loads, open(tf)):
            if SET == 'cb':
                gold, fl = cb_gold(t['gold_lines'], syms); keys = sorted(gold); missing = []
            else:
                keys = t['gold']; gold = {g for g in keys if g in syms}; missing = [g for g in keys if g not in syms]; fl = {}
            fl.update({'id': t['id'], 'corpus': corp, 'gold_missing': missing, 'n_gold_present': len(gold)})
            flags.append(fl)
            if not gold:
                continue  # invalid at this corpus (listed in flags)
            a, b = metrics(d0[t['id']], gold), metrics(r1[t['id']], gold)
            rows.append({'id': t['id'], 'corpus': corp, 'exposed': bool(exposure(t['query'], keys)),
                         'ident': is_ident(t['query'], keys), 'D0': a, 'R1': b})
    strata = {'all': lambda r: True, 'exposed': lambda r: r['exposed'], 'ident': lambda r: r['ident'],
              'desc': lambda r: not r['ident'], 'exposed_not_ident': lambda r: r['exposed'] and not r['ident']}
    keys = ['semR@16', 'semR@50', 'semR@200', 'sem_rank', 'nDCG@10', 'fused_rank', 'pack_cov']
    out = {'set': SET, 'n_tasks': len(flags), 'n_valid': len(rows),
           'invalid': [f['id'] for f in flags if f['n_gold_present'] == 0], 'flags': flags, 'strata': {}}
    for sname, sel in strata.items():
        rs = [r for r in rows if sel(r)]
        if not rs:
            out['strata'][sname] = {'n': 0}; continue
        st = {'n': len(rs)}
        for k in keys:
            a = [r['D0'][k] for r in rs]; b = [r['R1'][k] for r in rs]; d = [y - x for x, y in zip(a, b)]
            st[k] = {'D0_mean': statistics.fmean(a), 'R1_mean': statistics.fmean(b), 'delta': statistics.fmean(d),
                     'ci': boot(d), 'wins': sum(x > 0 for x in d), 'losses': sum(x < 0 for x in d)}
            if k in ('sem_rank', 'fused_rank'):
                st[k].update({'D0_median': statistics.median(a), 'R1_median': statistics.median(b)})
        out['strata'][sname] = st
    ex, idn = out['strata']['exposed'], out['strata']['ident']
    out['gates'] = {
        'semantic': ex.get('n', 0) > 0 and ex['semR@50']['delta'] >= 0.05 and ex['semR@50']['ci'][0] > 0,
        'fused': ex.get('n', 0) > 0 and ex['nDCG@10']['delta'] >= 0.02,
        'identifier': idn.get('n', 0) > 0 and idn['nDCG@10']['delta'] >= -0.02 and idn['nDCG@10']['ci'][0] >= -0.05,
        'exposed_n': ex.get('n', 0)}
    if SET == 'cb':
        s = ex if ex.get('n', 0) >= 5 else out['strata']['all']
        out['cb_contradicts'] = any(s[k]['delta'] < 0 and s[k]['ci'][1] < 0 for k in ('semR@50', 'nDCG@10'))
        out['cb_contradiction_stratum'] = 'exposed' if s is ex else 'all'
    out['rows'] = rows
    json.dump(out, open(os.path.join(RES, f'score-{SET}.json'), 'w'), indent=1)
    print(f"{SET}: tasks {out['n_tasks']} valid {out['n_valid']} invalid {out['invalid']}")
    for sname, st in out['strata'].items():
        if not st.get('n'):
            print(f'  {sname}: n=0'); continue
        print(f"  {sname} (n={st['n']})")
        for k in keys:
            v = st[k]; extra = f" median {v['D0_median']}->{v['R1_median']}" if 'D0_median' in v else ''
            print(f"    {k:10s} D0 {v['D0_mean']:.3f} R1 {v['R1_mean']:.3f} Δ {v['delta']:+.3f} [{v['ci'][0]:+.3f},{v['ci'][1]:+.3f}] w/l {v['wins']}/{v['losses']}{extra}")
    print('  gates', out['gates'], {k: out[k] for k in ('cb_contradicts', 'cb_contradiction_stratum') if k in out})


if __name__ == '__main__':
    main()
