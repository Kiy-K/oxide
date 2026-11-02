"""#42 → TreeSelect v1 DIAGNOSIS scorer (development set; nothing here is a verdict).

  dev_score.py -> docs/treeselect-eval/v1-dev/diagnosis.txt, diagnosis.json

Per arm, paired vs B (16 seeds) and vs B at the same depth: Δ gold-line coverage and ch5
relative efficiency (repo-clustered bootstrap, as #42), plus mechanism diagnostics:
  window_loss  gold lines inside a packed item's full symbol span but outside its delivered window,
               as a share of all gold lines (the packing/snippet-window interaction);
  pool_ceiling gold-line coverage of the full spans of every offered item (B arms: the whole pool at
               that depth), ignoring the budget (how much gold the pool holds at all);
  first5_prec  share of the first 5 offers whose full span touches gold;
  first5_multi share of the first 5 offers from files with >= 3 pooled candidates (count bias);
  first5_test  share of the first 5 offers with role test or a test-like path.
"""
import json, os, sys
from collections import defaultdict
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts'))
sys.dont_write_bytecode = True
import score  # noqa: E402  (#42 scorer: tasks, score_alloc, compare)

DEV = os.path.expanduser('~/.cache/oxide-treeselect-eval/dev/packs.jsonl')
TAG = ''
TESTY = ('test', 'spec')


def main():
    global DEV, TAG
    if sys.argv[1:] == ['round2']:
        DEV, TAG = DEV.replace('packs.jsonl', 'packs-round2.jsonl'), '-round2'
    ts = score.tasks()
    packs = defaultdict(dict)
    for l in open(DEV):
        p = json.loads(l)
        packs[p['id']][p['arm']] = p
    arms = list(packs[ts[0]['id']])
    sa = score.score_alloc(ts)
    pm, db_span = sa['pack_metrics'], sa['db_span']
    repo = [t['repo'].lower() for t in ts]
    gold = [{f: set(v) for f, v in t['g5_gold'].items()} for t in ts]
    tot = [sum(len(v) for v in g.values()) for g in gold]

    def full(t, key):
        sp = db_span(t['root'], key)
        return set(range(sp[0], sp[1] + 1)) if sp else set()

    M, D = {}, {}
    for a in arms:
        M[a] = [pm(packs[t['id']][a], {'id': t['id'], 'path': t['root']}, g) for t, g in zip(ts, gold)]
        wl, pc, p5, m5, t5 = [], [], [], [], []
        for t, g, n in zip(ts, gold, tot):
            r = packs[t['id']][a]
            lost = 0
            for it in r['trace']['packed']:
                f = it['key'].split('#')[0]
                if f in g:
                    lo, hi = it['span']
                    got = set(range(lo, hi + 1)) if lo > 0 else set()
                    lost += len((full(t, it['key']) - got) & g[f])
            wl.append(lost / n)
            offers = r['trace'].get('offers') or [[p['key'], 'packed', 0] for p in r['trace']['packed']]
            cover = defaultdict(set)
            for k, *_ in offers:
                cover[k.split('#')[0]] |= full(t, k)
            pc.append(sum(len(cover[f] & v) for f, v in g.items()) / n)
            per_file = defaultdict(int)
            for k, *_ in offers:
                per_file[k.split('#')[0]] += 1
            top = offers[:5]
            if top:
                p5.append(np.mean([bool(full(t, k) & g.get(k.split('#')[0], set())) for k, *_ in top]))
                m5.append(np.mean([per_file[k.split('#')[0]] >= 3 for k, *_ in top]))
                t5.append(np.mean([any(x in k.split('#')[0].lower() for x in TESTY) for k, *_ in top]))
        D[a] = {'window_loss': float(np.mean(wl)), 'pool_ceiling': float(np.mean(pc)),
                'first5_prec': float(np.mean(p5)), 'first5_multi': float(np.mean(m5)),
                'first5_test': float(np.mean(t5)),
                'ms': float(np.median([min(packs[t['id']][a]['context_ms']) for t in ts]))}
    out, lines = {}, ['arm | cov | Δcov vs B16 [clustered CI] up/down | rel/used vs B16 | Δcov vs B@depth | '
                      'tok | items | files | window_loss | pool_ceiling | first5 prec/multi/test | ctx ms']
    for a in arms:
        if a == 'A':
            continue
        depth = next((o[1:] for o in a.split(':')[1:] if o.startswith('d')), '16')
        base_d = f'B:d{depth}'
        c = score.compare(M[a], M['B'], repo)
        cd = score.compare(M[a], M[base_d], repo) if base_d in M and a != base_d else None
        out[a] = {'vs_B16': c, 'vs_B_depth': cd, **D[a]}
        lines.append(
            f"{a:34s} {c['cov_x']:.4f} | {c['d_cov']:+.4f} [{c['d_cov_ci_cluster'][0]:+.4f},{c['d_cov_ci_cluster'][1]:+.4f}] "
            f"{c['improved']}/{c['regressed']} | {c['rel_eff']:+.1%} [{c['rel_eff_ci_cluster'][0]:+.1%},{c['rel_eff_ci_cluster'][1]:+.1%}] | "
            + (f"{cd['d_cov']:+.4f} [{cd['d_cov_ci_cluster'][0]:+.4f},{cd['d_cov_ci_cluster'][1]:+.4f}]" if cd else '—')
            + f" | {c['tokens_x']:.0f} | {c['items_x']:.2f} | {c['files_x']:.2f} | {D[a]['window_loss']:.4f} | "
              f"{D[a]['pool_ceiling']:.4f} | {D[a]['first5_prec']:.3f}/{D[a]['first5_multi']:.3f}/{D[a]['first5_test']:.3f} | {D[a]['ms']:.1f}")
    lines.append(f"A: cov {np.mean([m['gold_line_cov'] for m in M['A']]):.4f}, window_loss {D['A']['window_loss']:.4f}, ms {D['A']['ms']:.1f}")
    json.dump(out, open(os.path.join(HERE, f'diagnosis{TAG}.json'), 'w'), indent=1)
    open(os.path.join(HERE, f'diagnosis{TAG}.txt'), 'w').write('\n'.join(lines) + '\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
