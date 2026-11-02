"""#42 TreeSelect v0 scorer (PROTOCOL §Metrics and gates).

  score.py -> results/result.json, results/result.txt, results/per_task.tsv

Metrics: ch5 `score_alloc.pack_metrics`, called unchanged through its own module (as #40's
eval_40.py), with #40's frozen line gold (`g5_gold`: held-out edit-locus lines, CB human lines).
Gating CIs: repo-clustered paired bootstrap (repos with replacement, then tasks within each drawn
repo; 10,000 resamples; fresh numpy default_rng(0) per analysis; repos sorted; repo = lowercased
owner/name). Task-level bootstrap (10,000, seed 0) is descriptive only.
"""
import contextlib, io, json, os, runpy, sys, tempfile
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
RES = os.path.join(HERE, '..', 'results')
J = os.path.expanduser('~/.cache/oxide-jev-eval')
B, SEED = 10_000, 0
ARMS = ('A', 'B', 'TX', 'T', 'TC', 'T4096')
NAMES = {'A': 'A', 'B': 'B', 'TX': 'T=−x', 'T': 'T=', 'TC': 'T=−c', 'T4096': 'T4096'}
# (treatment, baseline, label); the first is the gated hierarchy comparison.
COMPARISONS = [('TX', 'B', 'hierarchy (gated)'), ('T', 'B', 'full TreeSelect v0'),
               ('T', 'TX', 'graph-expansion effect'), ('T', 'A', 'total product effect'),
               ('B', 'A', 'cap-removal effect'), ('TC', 'T', 'collapse ablation (T=−c vs T=)'),
               ('T4096', 'A', 'T4096 vs A (descriptive)')]
sys.dont_write_bytecode = True


def tasks():
    return [t for s in ('heldout', 'cb') for t in map(json.loads, open(f'{J}/tasks/{s}-frozen.jsonl'))]


def score_alloc(ts):
    """score_alloc's module, loaded in --cb mode with every task's gold (as #40's eval_40.py)."""
    with tempfile.TemporaryDirectory() as d:
        tp, gp, dp = f'{d}/t.jsonl', f'{d}/g.json', f'{d}/d.jsonl'
        open(tp, 'w').close()
        json.dump({t['id']: {'lines': t['g5_gold'], 'files': sorted(t['g5_gold'])} for t in ts}, open(gp, 'w'))
        open(dp, 'w').close()
        argv = sys.argv
        sys.argv = ['score_alloc.py', tp, dp, '--cb', gp]
        try:
            with contextlib.redirect_stdout(io.StringIO()):  # its module-level main() prints empty tables
                return runpy.run_path(f'{REPO}/docs/alloc-utilization-eval/scripts/score_alloc.py',
                                      run_name='score_alloc')
        finally:
            sys.argv = argv


def cluster_idx(repos):
    rng = np.random.default_rng(SEED)
    names = sorted(set(repos))
    members = {n: np.array([i for i, r in enumerate(repos) if r == n]) for n in names}
    out = []
    for _ in range(B):
        idx = [members[n][rng.integers(0, len(members[n]), size=len(members[n]))]
               for n in rng.choice(names, size=len(names), replace=True)]
        out.append(np.concatenate(idx))
    return out


def task_idx(n):
    rng = np.random.default_rng(SEED)
    return [rng.integers(0, n, size=n) for _ in range(B)]


def compare(mx, mb, repos):
    """Paired X vs base: Δ coverage and ch5 relative efficiency, clustered and task-level CIs."""
    cov = np.array([x['gold_line_cov'] - b['gold_line_cov'] for x, b in zip(mx, mb)])
    ex = np.array([x['rel_per_used'] for x in mx])
    eb = np.array([b['rel_per_used'] for b in mb])
    ok = np.array([b['used'] > 0 for b in mb])

    def rel_eff(i):
        i = i[ok[i]]
        return (ex[i].mean() - eb[i].mean()) / eb[i].mean() if len(i) and eb[i].mean() else float('nan')

    res = {'n': len(cov), 'repos': len(set(repos)), 'd_cov': float(cov.mean()),
           'rel_eff': float(rel_eff(np.arange(len(cov)))),
           'improved': int((cov > 1e-12).sum()), 'regressed': int((cov < -1e-12).sum()),
           'tokens_x': float(np.mean([x['used'] for x in mx])), 'tokens_base': float(np.mean([b['used'] for b in mb])),
           'items_x': float(np.mean([x['items'] for x in mx])), 'items_base': float(np.mean([b['items'] for b in mb])),
           'files_x': float(np.mean([x['files'] for x in mx])), 'files_base': float(np.mean([b['files'] for b in mb])),
           'cov_x': float(np.mean([x['gold_line_cov'] for x in mx])),
           'cov_base': float(np.mean([b['gold_line_cov'] for b in mb]))}
    for tag, idx in (('cluster', cluster_idx(repos)), ('task', task_idx(len(cov)))):
        bs = np.array([cov[i].mean() for i in idx])
        es = np.array([rel_eff(i) for i in idx])
        res[f'd_cov_ci_{tag}'] = [float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))]
        res[f'rel_eff_ci_{tag}'] = [float(np.nanpercentile(es, 2.5)), float(np.nanpercentile(es, 97.5))]
    return res


def main():
    ts = tasks()
    packs = {}
    for l in open(os.path.join(RES, 'packs.jsonl')):
        p = json.loads(l)
        packs[(p['id'], p['arm'])] = p
    missing = [t['id'] for t in ts for a in ARMS if (t['id'], a) not in packs]
    assert not missing, f'missing packs: {missing[:5]} ({len(missing)})'
    pm = score_alloc(ts)['pack_metrics']
    M = {a: [pm(packs[(t['id'], a)], {'id': t['id'], 'path': t['root']},
                {f: set(v) for f, v in t['g5_gold'].items()}) for t in ts] for a in ARMS}
    repo = [t['repo'].lower() for t in ts]
    strata = {'pooled': list(range(len(ts))),
              'heldout': [i for i, t in enumerate(ts) if t['set'] == 'heldout'],
              'cb': [i for i, t in enumerate(ts) if t['set'] == 'cb']}
    out = {'n': len(ts), 'comparisons': {}}
    for x, b, label in COMPARISONS:
        out['comparisons'][f'{x} vs {b}'] = {'label': label, **{
            s: compare([M[x][i] for i in ix], [M[b][i] for i in ix], [repo[i] for i in ix])
            for s, ix in strata.items()}}
    g = out['comparisons']['TX vs B']
    # O1: latency (min of 2 runs) and memory.
    ms = {a: np.array([min(packs[(t['id'], a)]['context_ms']) for t in ts]) for a in ('A', 'T')}
    search = np.array([packs[(t['id'], 'A')]['search_ms'] for t in ts])
    o1_lat = {'median_d_ms': float(np.median(ms['T'] - ms['A'])), 'median_search_ms': float(np.median(search))}
    o1_lat['pass'] = o1_lat['median_d_ms'] <= 0.05 * o1_lat['median_search_ms']
    rss = {}
    for l in list(open(os.path.join(RES, 'rss_o1.tsv')))[1:]:
        tid, _, _, arms, hwm = l.rstrip('\n').split('\t')
        rss.setdefault(tid, {}).setdefault(arms, []).append(int(hwm))
    o1_mem = {tid: (max(v['A,T']) - max(v['A'])) / max(v['A']) for tid, v in rss.items()}
    # P1: parity and deterministic repeats.
    par = [l.split('\t')[1] for l in list(open(os.path.join(RES, 'parity_p1.tsv')))[1:]]
    rep = {a: sum(packs[(t['id'], a)]['repeat_identical'] for t in ts) for a in ARMS}
    gates = {
        'Q1': g['pooled']['d_cov_ci_cluster'][0] > 0,
        'Q2': g['heldout']['d_cov'] >= 0 and g['cb']['d_cov'] >= 0,
        'E1': g['pooled']['rel_eff_ci_cluster'][0] >= -0.05,
        'R1_counts': g['pooled']['regressed'] <= g['pooled']['improved'],
        'O1': o1_lat['pass'] and all(d <= 0.05 for d in o1_mem.values()) and len(o1_mem) == 3,
        'P1': len(par) == len(ts) and all(p == 'identical' for p in par)
        and all(rep[a] == len(ts) for a in ('B', 'TX', 'T')),
    }
    out.update({'o1_latency': o1_lat, 'o1_rss_rel': o1_mem, 'p1_parity_identical': par.count('identical'),
                'p1_repeat_identical': rep, 'gates': gates})
    if not gates['P1']:
        verdict = 'INCONCLUSIVE'
    elif gates['Q1'] and gates['Q2'] and gates['E1'] and gates['R1_counts'] and gates['O1']:
        verdict = 'PROMISING (pending R1 regression explanations)'
    elif not (gates['Q1'] and gates['Q2'] and gates['E1']):
        verdict = 'REJECT (TreeSelect v0)'
    else:
        verdict = 'NOT PROMISING (R1 or O1 failed; Q1/Q2/E1 passed)'
    out['verdict'] = verdict
    json.dump(out, open(os.path.join(RES, 'result.json'), 'w'), indent=1)
    with open(os.path.join(RES, 'per_task.tsv'), 'w') as f:
        f.write('id\tset\trepo\t' + '\t'.join(f'{a}_cov\t{a}_used\t{a}_rel' for a in ARMS) + '\n')
        for i, t in enumerate(ts):
            f.write(f"{t['id']}\t{t['set']}\t{repo[i]}\t" + '\t'.join(
                f"{M[a][i]['gold_line_cov']:.4f}\t{M[a][i]['used']}\t{M[a][i]['rel_tok']:.1f}" for a in ARMS) + '\n')
    lines = [f'#42 TreeSelect v0 — n={len(ts)}, verdict: {verdict}', '']
    for k, c in out['comparisons'].items():
        x, b = k.split(' vs ')
        lines.append(f"{NAMES[x]} vs {NAMES[b]} — {c['label']}")
        for s in ('pooled', 'heldout', 'cb'):
            r = c[s]
            lines.append(
                f"  {s:8s} n={r['n']:3d} repos={r['repos']:2d} cov {r['cov_base']:.3f}->{r['cov_x']:.3f} "
                f"Δ {r['d_cov']:+.4f} clu[{r['d_cov_ci_cluster'][0]:+.4f},{r['d_cov_ci_cluster'][1]:+.4f}] "
                f"task[{r['d_cov_ci_task'][0]:+.4f},{r['d_cov_ci_task'][1]:+.4f}] "
                f"up/down {r['improved']}/{r['regressed']} | rel/used {r['rel_eff']:+.1%} "
                f"clu[{r['rel_eff_ci_cluster'][0]:+.1%},{r['rel_eff_ci_cluster'][1]:+.1%}] "
                f"task[{r['rel_eff_ci_task'][0]:+.1%},{r['rel_eff_ci_task'][1]:+.1%}] | "
                f"tok {r['tokens_base']:.0f}->{r['tokens_x']:.0f} items {r['items_base']:.2f}->{r['items_x']:.2f} "
                f"files {r['files_base']:.2f}->{r['files_x']:.2f}")
        lines.append('')
    lines += [f"O1 latency: median Δ(T=−A) {o1_lat['median_d_ms']:+.2f} ms vs median search "
              f"{o1_lat['median_search_ms']:.1f} ms (limit {0.05 * o1_lat['median_search_ms']:.2f}) -> {o1_lat['pass']}",
              'O1 RSS Δ (A,T vs A): ' + ', '.join(f'{k} {v:+.2%}' for k, v in o1_mem.items()),
              f"P1 parity identical {par.count('identical')}/{len(par)}; repeats identical " +
              ', '.join(f'{NAMES[a]} {n}/{len(ts)}' for a, n in rep.items()),
              'gates: ' + ', '.join(f'{k}={v}' for k, v in gates.items())]
    open(os.path.join(RES, 'result.txt'), 'w').write('\n'.join(lines) + '\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
