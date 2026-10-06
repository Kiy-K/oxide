"""Value packer (dev, descriptive) scorer: #42's metrics and repo-clustered bootstrap.

  score.py -> results/result.txt
Parity: arms A and B must reproduce #42's amended-run traces exactly (same binary base and pool).
"""
import json, os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'treeselect-eval', 'scripts'))
sys.dont_write_bytecode = True
import score as s42  # noqa: E402

RES = os.path.join(HERE, '..', 'results')
R42 = os.path.join(HERE, '..', '..', 'treeselect-eval', 'results', 'packs.jsonl')
PAIRS = [('P', 'A'), ('P', 'B'), ('B', 'A'), ('P', 'PND'), ('P', 'PKD'), ('PND', 'B')]


def load(path):
    out = {}
    for l in open(path):
        p = json.loads(l)
        out[(p['id'], p['arm'])] = p
    return out


def main():
    ts = s42.tasks()
    packs, old = load(os.path.join(RES, 'packs.jsonl')), load(R42)
    arms = sorted({a for _, a in packs})
    assert all((t['id'], a) in packs for t in ts for a in arms), 'missing packs'
    same = {a: sum(packs[(t['id'], a)]['trace']['packed'] == old[(t['id'], a)]['trace']['packed'] for t in ts)
            for a in ('A', 'B')}
    rep = {a: sum(packs[(t['id'], a)]['repeat_identical'] for t in ts) for a in arms}
    pm = s42.score_alloc(ts)['pack_metrics']
    M = {a: [pm(packs[(t['id'], a)], {'id': t['id'], 'path': t['root']},
                {f: set(v) for f, v in t['g5_gold'].items()}) for t in ts] for a in arms}
    repo = [t['repo'].lower() for t in ts]
    strata = {'pooled': range(len(ts)), 'heldout': [i for i, t in enumerate(ts) if t['set'] == 'heldout'],
              'cb': [i for i, t in enumerate(ts) if t['set'] == 'cb']}
    lines = [f'value packer (dev, descriptive) n={len(ts)}; parity vs #42 A {same["A"]}/{len(ts)}, '
             f'B {same["B"]}/{len(ts)}; repeats ' + ', '.join(f'{a} {rep[a]}' for a in arms)]
    for x, b in PAIRS:
        lines.append(f'{x} vs {b}')
        for s, ix in strata.items():
            c = s42.compare([M[x][i] for i in ix], [M[b][i] for i in ix], [repo[i] for i in ix])
            lines.append(f"  {s:8} n={c['n']:3} cov {c['cov_base']:.4f}->{c['cov_x']:.4f} Δ {c['d_cov']:+.4f} "
                         f"clu[{c['d_cov_ci_cluster'][0]:+.4f},{c['d_cov_ci_cluster'][1]:+.4f}] "
                         f"up/down {c['improved']}/{c['regressed']} | rel/used {100 * c['rel_eff']:+.1f}% "
                         f"clu[{100 * c['rel_eff_ci_cluster'][0]:+.1f}%,{100 * c['rel_eff_ci_cluster'][1]:+.1f}%] "
                         f"| tok {c['tokens_base']:.0f}->{c['tokens_x']:.0f} items {c['items_base']:.2f}->{c['items_x']:.2f}")
    open(os.path.join(RES, 'result.txt'), 'w').write('\n'.join(lines) + '\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
