"""#43 scorer: B vs A on the fresh tasks (PROTOCOL gates), with #42's metrics and bootstrap.

  score.py -> results/result.json, results/result.txt, results/per_task.tsv
"""
import json, os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'treeselect-eval', 'scripts'))
sys.dont_write_bytecode = True
import score as s42  # noqa: E402

J = os.path.expanduser('~/.cache/oxide-capremoval-eval')
RES = os.path.join(HERE, '..', 'results')


def main():
    ts = [t for s in ('heldout', 'cb') for t in map(json.loads, open(f'{J}/tasks/{s}-frozen.jsonl'))]
    packs = {}
    for l in open(os.path.join(RES, 'packs.jsonl')):
        p = json.loads(l)
        packs[(p['id'], p['arm'])] = p
    assert all((t['id'], a) in packs for t in ts for a in 'AB'), 'missing packs'
    pm = s42.score_alloc(ts)['pack_metrics']
    M = {a: [pm(packs[(t['id'], a)], {'id': t['id'], 'path': t['root']},
                {f: set(v) for f, v in t['g5_gold'].items()}) for t in ts] for a in 'AB'}
    repo = [t['repo'].lower() for t in ts]
    strata = {'pooled': range(len(ts)), 'heldout': [i for i, t in enumerate(ts) if t['set'] == 'heldout'],
              'cb': [i for i, t in enumerate(ts) if t['set'] == 'cb']}
    c = {s: s42.compare([M['B'][i] for i in ix], [M['A'][i] for i in ix], [repo[i] for i in ix])
         for s, ix in strata.items()}
    ms = {a: np.array([min(packs[(t['id'], a)]['context_ms']) for t in ts]) for a in 'AB'}
    search = np.array([packs[(t['id'], 'A')]['search_ms'] for t in ts])
    lat = {'median_d_ms': float(np.median(ms['B'] - ms['A'])), 'median_search_ms': float(np.median(search))}
    rss = {}
    for l in list(open(os.path.join(RES, 'rss_o1.tsv')))[1:]:
        tid, _, _, arms, hwm = l.rstrip('\n').split('\t')
        rss.setdefault(tid, {}).setdefault(arms, []).append(int(hwm))
    mem = {tid: (max(v['A,B']) - max(v['A'])) / max(v['A']) for tid, v in rss.items()}
    par = [l.split('\t')[1] for l in list(open(os.path.join(RES, 'parity_p1.tsv')))[1:]]
    rep_b = sum(packs[(t['id'], 'B')]['repeat_identical'] for t in ts)
    n_h, n_c = len(strata['heldout']), len(strata['cb'])
    h_repos = len({repo[i] for i in strata['heldout']})
    gates = {
        'Q1': c['pooled']['d_cov_ci_cluster'][0] > 0,
        'Q2': c['heldout']['d_cov'] >= 0 and c['cb']['d_cov'] >= 0,
        'E1': c['pooled']['rel_eff_ci_cluster'][0] >= -0.05,
        'R1_counts': c['pooled']['regressed'] <= c['pooled']['improved'],
        'O1': lat['median_d_ms'] <= 0.05 * lat['median_search_ms'] and len(mem) == 3
        and all(v <= 0.05 for v in mem.values()),
        'P1': len(par) == len(ts) and all(p == 'identical' for p in par) and rep_b == len(ts),
        'validity': n_h >= 50 and n_c >= 50 and h_repos >= 6,
    }
    if not (gates['validity'] and gates['P1']):
        verdict = 'INCONCLUSIVE'
    elif not (gates['Q1'] and gates['Q2'] and gates['E1']):
        verdict = 'REJECT'
    elif gates['R1_counts'] and gates['O1']:
        verdict = 'PASS (pending R1 regression explanations)'
    else:
        verdict = 'NOT PASSED'
    out = {'n': len(ts), 'heldout': n_h, 'cb': n_c, 'heldout_repos': h_repos, 'B_vs_A': c,
           'o1_latency': lat, 'o1_rss_rel': mem, 'p1_parity_identical': par.count('identical'),
           'p1_repeat_identical_B': rep_b, 'gates': gates, 'verdict': verdict}
    json.dump(out, open(os.path.join(RES, 'result.json'), 'w'), indent=1)
    with open(os.path.join(RES, 'per_task.tsv'), 'w') as f:
        f.write('id\tset\trepo\tA_cov\tB_cov\tA_used\tB_used\n')
        for i, t in enumerate(ts):
            f.write(f"{t['id']}\t{t['set']}\t{repo[i]}\t{M['A'][i]['gold_line_cov']:.4f}\t"
                    f"{M['B'][i]['gold_line_cov']:.4f}\t{M['A'][i]['used']}\t{M['B'][i]['used']}\n")
    lines = [f"#43 B vs A — n={len(ts)} (held-out {n_h} in {h_repos} repos, CB {n_c}); verdict: {verdict}"]
    for s in ('pooled', 'heldout', 'cb'):
        r = c[s]
        lines.append(f"  {s:8s} n={r['n']:3d} repos={r['repos']:2d} cov {r['cov_base']:.4f}->{r['cov_x']:.4f} "
                     f"Δ {r['d_cov']:+.4f} clu[{r['d_cov_ci_cluster'][0]:+.4f},{r['d_cov_ci_cluster'][1]:+.4f}] "
                     f"task[{r['d_cov_ci_task'][0]:+.4f},{r['d_cov_ci_task'][1]:+.4f}] up/down {r['improved']}/{r['regressed']} "
                     f"| rel/used {r['rel_eff']:+.1%} clu[{r['rel_eff_ci_cluster'][0]:+.1%},{r['rel_eff_ci_cluster'][1]:+.1%}] "
                     f"| tok {r['tokens_base']:.0f}->{r['tokens_x']:.0f} items {r['items_base']:.2f}->{r['items_x']:.2f}")
    lines += [f"O1 latency median Δ {lat['median_d_ms']:+.2f} ms vs search {lat['median_search_ms']:.1f} ms; RSS Δ "
              + ', '.join(f'{v:+.2%}' for v in mem.values()),
              f"P1 parity {par.count('identical')}/{len(par)}; B repeats {rep_b}/{len(ts)}",
              'gates: ' + ', '.join(f'{k}={v}' for k, v in gates.items())]
    open(os.path.join(RES, 'result.txt'), 'w').write('\n'.join(lines) + '\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
