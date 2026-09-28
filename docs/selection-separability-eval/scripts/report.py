"""Render results/results.json + frozen_models.json into results/tables.md."""
import json, os
from common import ALL_FEATURES, RES

R = json.load(open(os.path.join(RES, 'results.json')))
F = json.load(open(os.path.join(RES, 'frozen_models.json')))
SETS = ['dev', 'masked', 'heldout']
L = []
P = L.append


def ci(v, k):
    a, b = v[k + '_ci']
    return f"{v[k]:.3f} [{a:.3f}, {b:.3f}]"


def dci(d, k='d_auroc'):
    a, b = d[k + '_ci']
    return f"{d[k]:+.3f} [{a:+.3f}, {b:+.3f}]"


def interp(n):
    return '' if n >= 10 else (' (n<10, not interpreted)' if n >= 5 else ' (n<5, hidden)')


P(f"frozen_models.json sha256 `{R['frozen_models_sha256']}`  \nverdict: **{R['verdict']}**\n")
P('## Eligible tasks (full universe)\n')
P('| set | tasks | labeled | eligible | positives | negatives |\n|---|---:|---:|---:|---:|---:|')
for s in SETS + ['cb']:
    S = R['sets'][s]; f = S.get('full', {})
    P(f"| {s} | {S['tasks_total']} | {S['tasks_labeled']} | {f.get('n', 0)} | {f.get('pos', '—')} | {f.get('neg', '—')} |")
P('\ncb: ' + R['sets']['cb'].get('note', '') + '\n')

P('## Controls (macro within-task AUROC [95 % CI]; AUPRC; pooled pairwise accuracy)\n')
for s in SETS:
    sc = R['sets'][s]['full']['scorers']
    P(f"**{s}** (n={R['sets'][s]['full']['n']}, AP random = {sc['fused_order']['ap_random']:.3f})\n")
    P('| scorer | AUROC | AUPRC | pairwise |\n|---|---|---|---|')
    for n in ['random', 'fused_order', 'pool_order', 'lexical_only', 'semantic_only', 'rrf_baseline',
              'rel_only_weight', 'rel_only_links', 'rel_only_seedrank']:
        v = sc[n]
        P(f"| {n} | {ci(v, 'auroc')} | {v['auprc']:.3f} | {v['pairwise']:.3f} |")
    P('')

P('## Single features (oriented on dev+masked; AUROC; Δ vs fused order on heldout)\n')
P('| family | feature | sign | dev | masked | heldout | Δ heldout vs fused [CI] |\n|---|---|---:|---:|---:|---:|---|')
fam = {f: k for k, v in __import__('features').FEATURES.items() for f in v}
rows = sorted(ALL_FEATURES, key=lambda f: -R['sets']['heldout']['full']['scorers']['feat:' + f]['auroc'])
for f in rows:
    a = [R['sets'][s]['full']['scorers']['feat:' + f]['auroc'] for s in SETS]
    d = R['sets']['heldout']['delta_vs_fused']['feat:' + f]
    P(f"| {fam[f]} | {f} | {F['single'][f]['sign']:+d} | {a[0]:.3f} | {a[1]:.3f} | {a[2]:.3f} | {dci(d)} |")
P(f"\nbest_single (dev+masked) = `{F['best_single']}`; best_fixed = `{F['best_fixed']}`\n")

P('## Fixed combinations and fitted models (AUROC; Δ vs fused order [CI])\n')
P('| scorer | dev | masked | heldout | Δ heldout vs fused | Δ heldout pairwise |\n|---|---:|---:|---:|---|---|')
for n in ['fused_order', 'pool_order', 'FX1_best_channel_rr', 'FX2_lexical_first', 'FX3_agreement_first',
          'FX4_struct_vote', 'LR-R', 'LR-S', 'LR-I', 'LR-C', 'LR-RS', 'LR-ALL', 'LR-R+reltype',
          'LR-R+distance', 'LR-R+seedrank', 'LR-R+degree']:
    a = [R['sets'][s]['full']['scorers'][n]['auroc'] for s in SETS]
    if n == 'fused_order':
        P(f"| {n} | {a[0]:.3f} | {a[1]:.3f} | {a[2]:.3f} | — | — |"); continue
    d = R['sets']['heldout']['delta_vs_fused'][n]
    P(f"| {n} | {a[0]:.3f} | {a[1]:.3f} | {a[2]:.3f} | {dci(d)} | {dci(d, 'd_pairwise')} |")
P('')
P('Dev/masked numbers for LR models are in-sample (fitted there).\n')

P('## Gate (PROTOCOL §8)\n')
for s in ['heldout', 'cb']:
    g = R['gate'][s]
    if not g['evaluable']:
        P(f"**{s}: not evaluable** — {g['reason']}\n"); continue
    P(f"**{s}** C* = `{g['cstar']}`\n")
    P('| candidate | ΔAUROC vs C* [CI] | Δpairwise | margin | pairwise ok | artifact-free | PASS |\n|---|---|---:|:-:|:-:|:-:|:-:|')
    for n, c in g['candidates'].items():
        af = R['sets'][s].get('artifact_free', {}).get('delta_vs_cstar', {}).get(n)
        afs = dci(af) if af else ''
        P(f"| {n} | {dci(c)} | {c['d_pairwise']:+.3f} | {'y' if c['auroc_margin'] else 'n'} | "
          f"{'y' if c['pairwise_ok'] else 'n'} | {'y' if c['artifact_free'] else 'n'} {afs} | **{'PASS' if c['pass'] else 'fail'}** |")
    P('')

P('## Rank bands (macro AUROC within band; eligible tasks)\n')
for s in SETS:
    P(f"**{s}**\n")
    P('| band | n | pos/neg | fused | pool | lexical | semantic | LR-R | LR-RS | LR-ALL | Δ LR-RS vs fused | Δ LR-ALL vs fused |\n|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---|---|')
    for b, c in R['sets'][s]['bands'].items():
        if c.get('n', 0) < 5:
            P(f"| {b} | {c.get('n', 0)} | | (n<5, hidden) |||||||||"); continue
        sc = c['scorers']
        P(f"| {b}{interp(c['n'])} | {c['n']} | {c['pos']}/{c['neg']} | " + ' | '.join(
            f"{sc[n]['auroc']:.3f}" for n in ['fused_order', 'pool_order', 'lexical_only', 'semantic_only', 'LR-R', 'LR-RS', 'LR-ALL'])
          + f" | {dci(c['d_LR-RS_vs_fused'])} | {dci(c['d_LR-ALL_vs_fused'])} |")
    P('')
P('Bands: B1 fused 1–5, B2 6–16, B3 17–50, B4 neighbor-only (top-5-seed one-hop, not in fused 1–50).\n')

P('## Query classes (macro AUROC, full universe)\n')
P('| set | class | n | fused | LR-R | LR-RS | LR-ALL | Δ LR-ALL vs fused |\n|---|---|---:|---:|---:|---:|---:|---|')
for s in SETS:
    for qc, c in R['sets'][s]['qclass'].items():
        if c.get('n', 0) < 5:
            P(f"| {s} | {qc} | {c.get('n', 0)} | (n<5, hidden) |||||"); continue
        sc = c['scorers']
        P(f"| {s} | {qc}{interp(c['n'])} | {c['n']} | " + ' | '.join(
            f"{sc[n]['auroc']:.3f}" for n in ['fused_order', 'LR-R', 'LR-RS', 'LR-ALL']) + f" | {dci(c['d_LR-ALL_vs_fused'])} |")
P('')

P('## Structural contribution (Δ macro AUROC [CI])\n')
P('| comparison | dev | masked | heldout |\n|---|---|---|---|')
for k in R['sets']['heldout']['structural']:
    P(f"| {k} | " + ' | '.join(dci(R['sets'][s]['structural'][k]) for s in SETS) + ' |')
P('')

P('## Sensitivity\n')
P('| analysis | set | n | C* | best Δ vs C* (candidate) |\n|---|---|---:|---|---|')
for s in SETS:
    af = R['sets'][s]['artifact_free']
    best = max(af['delta_vs_cstar'].items(), key=lambda kv: kv[1]['d_auroc'])
    P(f"| artifact-free universe | {s} | {af['n']} | {af['cstar']} ({af['scorers'][af['cstar']]['auroc']:.3f}) | {dci(best[1])} ({best[0]}) |")
nd = R['sets']['heldout']['no_duplicate']
best = max(nd['delta_vs_cstar'].items(), key=lambda kv: kv[1]['d_auroc'])
P(f"| heldout without duplicate | heldout | {nd['n']} | {nd['cstar']} | {dci(best[1])} ({best[0]}) |")

open(os.path.join(RES, 'tables.md'), 'w').write('\n'.join(L) + '\n')
print('\n'.join(L))
