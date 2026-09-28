"""Render the failure-taxonomy tables (markdown) from rows.json."""
import json, collections, random, statistics, sys

rows = json.load(open('/tmp/claude-0/work/an/rows.json'))
STAGES = ['EVAL_GOLD_PROBLEM', 'CANDIDATE_GENERATION_LOSS', 'ROUTE_LOSS',
          'STRUCTURAL_EXPANSION_LOSS', 'FUSION_ORDER_LOSS', 'ALLOCATION_LOSS']
SHORT = {'EVAL_GOLD_PROBLEM': 'EVAL', 'CANDIDATE_GENERATION_LOSS': 'CANDGEN', 'ROUTE_LOSS': 'ROUTE',
         'STRUCTURAL_EXPANSION_LOSS': 'STRUCT', 'FUSION_ORDER_LOSS': 'FUSION', 'ALLOCATION_LOSS': 'ALLOC',
         'AMBIGUOUS/MULTI': 'MULTI', 'HIT': 'HIT'}
SETS = ['dev', 'masked', 'heldout', 'cb']
NATURAL = ['dev', 'heldout', 'cb']
KS = [5, 10, 16, 50, 200]
out = []
P = out.append

def mean(x):
    x = list(x); return sum(x) / len(x) if x else float('nan')

def boot_delta(a, b, n=10000, seed=0):
    d = [y - x for x, y in zip(a, b)]; m = len(d); rnd = random.Random(seed)
    v = sorted(sum(d[rnd.randrange(m)] for _ in range(m)) / m for _ in range(n))
    return sum(d) / m, v[int(.025 * n)], v[int(.975 * n)]

def renorm(r, k):
    """coverage excluding EVAL units (known gold/corpus mismatches)."""
    denom = 1 - r['eval_w']
    return r[k] / denom if denom > 1e-9 else float('nan')

# ------------------------------------------------------------ 1 baseline/oracles
P('## Oracle ladder (mean gold coverage; balanced, budget 4096)\n')
P('| set | n | baseline | strict (exact key) | D alloc | B16 | B20 | B50 | B100 | B full | B chan16 | B∘D | C (dep role) | C (primary role) | C (top-seed score, primary) | C∘D | A pool | A+literal | A index | A index, full bodies |')
P('|---|---:|' + '---:|' * 18)
for s in SETS + ['all']:
    R = [r for r in rows if s == 'all' or r['set'] == s]
    ks = ['cov', 'cov_strict', 'D', 'B16', 'B20', 'B50', 'B100', 'B', 'BCHAN16', 'BD', 'C', 'CP', 'CTOP', 'CD', 'A', 'A_route', 'A_index', 'A_index_full']
    P(f'| {s} | {len(R)} | ' + ' | '.join(f"{mean(r[k] for r in R):.3f}" for k in ks) + ' |')
P('')
P('Excluding known gold/corpus-mismatch lines (EVAL units removed, coverage renormalized; affects 4 CB tasks only):\n')
P('| set | n | baseline | D | B full | C primary | A pool | A index |')
P('|---|---:|---:|---:|---:|---:|---:|---:|')
for s in ['cb', 'all']:
    R = [r for r in rows if s == 'all' or r['set'] == s]
    P(f'| {s} | {len(R)} | ' + ' | '.join(f"{mean(renorm(r, k) for r in R):.3f}" for k in ['cov', 'D', 'B', 'CP', 'A', 'A_index']) + ' |')
P('')
P('Paired deltas vs baseline (bootstrap 95 % CI, 10k, seed 0), all 226 instances:\n')
P('| arm | Δ coverage | CI |')
P('|---|---:|---|')
base = [r['cov'] for r in rows]
for k, lab in [('D', 'D allocator oracle'), ('B16', 'B16 reorder within seed top-16'), ('B50', 'B50'), ('B', 'B full'),
               ('BCHAN16', 'B chan16 (gold some channel had ≤16)'), ('C', 'C dependency role'), ('CP', 'C primary role'), ('CTOP', 'C top-seed score, primary role'), ('RT', 'root tests/ as test role (diagnostic)'),
               ('A_route', 'A pool + literal (route)'), ('A', 'A pool'), ('T', '#25 canonical test predicate')]:
    m, lo, hi = boot_delta(base, [r[k] for r in rows])
    P(f'| {lab} | {m:+.3f} | [{lo:+.3f}, {hi:+.3f}] |')
P('')

# ------------------------------------------------------------ 1b clean subset
import re as _re, glob as _glob
_Q = {}
for _f in _glob.glob('/tmp/claude-0/work/inputs/*__*.jsonl'):
    for _l in open(_f):
        _t = json.loads(_l); _Q[(_t['set'], _t['id'])] = _t['query']
SWEEP = _re.compile(r"Enable ruff|\[ruff\]|ruff's")
DUP_LATER = {'pylint-21885140', 'pylint-c1f158c6', 'httpx-88a81c5d'}  # identical gold to an earlier task
def flagged(r):
    base_id = r['id'][:-2] if r['set'] == 'masked' else r['id']
    return bool(SWEEP.search(_Q[(r['set'], r['id'])])) or base_id in DUP_LATER
CLEAN = [r for r in rows if not flagged(r)]
P('## Clean subset (label-uncertain lint-sweep tasks and later identical-gold duplicates removed)\n')
P(f"Flagged: {sum(flagged(r) for r in rows)} instances ({collections.Counter(r['set'] for r in rows if flagged(r))}).\n")
P('| set | n | baseline | D | B16 | B50 | B full | B chan16 | C primary | A pool | A+literal | ' + ' | '.join(SHORT[s] for s in STAGES) + ' |')
P('|---|---:|' + '---:|' * (9 + len(STAGES)))
for s in SETS + ['all']:
    R = [r for r in CLEAN if s == 'all' or r['set'] == s]
    tot = sum(r['lost'] for r in R) or 1e-9
    P(f'| {s} | {len(R)} | ' + ' | '.join(f"{mean(r[k] for r in R):.3f}" for k in ['cov', 'D', 'B16', 'B50', 'B', 'BCHAN16', 'CP', 'A', 'A_route'])
      + ' | ' + ' | '.join(f"{100 * sum(r['stages'][st] for r in R) / tot:.1f} %" for st in STAGES) + ' |')
P('')

# ------------------------------------------------------------ 2 taxonomy
P('## Failure taxonomy\n')
P('Share of **lost gold weight** by stage (unit-weighted; each task sums to its uncovered fraction):\n')
P('| set | n | lost weight | ' + ' | '.join(SHORT[s] for s in STAGES) + ' |')
P('|---|---:|---:|' + '---:|' * len(STAGES))
for s in SETS + ['natural (dev+heldout+cb)']:
    R = [r for r in rows if (r['set'] == s) or (s.startswith('natural') and r['set'] in NATURAL)]
    tot = sum(r['lost'] for r in R)
    P(f'| {s} | {len(R)} | {tot:.1f} | ' + ' | '.join(f"{100 * sum(r['stages'][st] for r in R) / tot:.1f} %" for st in STAGES) + ' |')
P('')
P('Task **primary** stage (≥ ⅔ of the task\'s lost weight in one stage, else MULTI):\n')
cols = ['HIT'] + STAGES + ['AMBIGUOUS/MULTI']
P('| set | n | ' + ' | '.join(SHORT[c] for c in cols) + ' |')
P('|---|---:|' + '---:|' * len(cols))
for s in SETS + ['natural']:
    R = [r for r in rows if r['set'] == s or (s == 'natural' and r['set'] in NATURAL)]
    c = collections.Counter(r['prim'] for r in R)
    P(f'| {s} | {len(R)} | ' + ' | '.join(f"{c[x]} ({100 * c[x] / len(R):.0f} %)" for x in cols) + ' |')
P('')
misses = [r for r in rows if r['prim'] != 'HIT']
P(f'Misses only (coverage < 1): {len(misses)} of {len(rows)}; hard misses (coverage 0): {sum(r["cov"] == 0 for r in rows)}.\n')
P('| set | misses | ' + ' | '.join(SHORT[c] for c in cols[1:]) + ' |')
P('|---|---:|' + '---:|' * (len(cols) - 1))
for s in SETS:
    R = [r for r in misses if r['set'] == s]
    c = collections.Counter(r['prim'] for r in R)
    P(f'| {s} | {len(R)} | ' + ' | '.join(f"{100 * c[x] / len(R):.0f} %" for x in cols[1:]) + ' |')
P('')
P('Sub-reasons (lost weight, summed over tasks):\n')
P('| stage | sub-reason | dev | masked | heldout | cb |')
P('|---|---|---:|---:|---:|---:|')
sub = collections.defaultdict(lambda: collections.Counter())
for r in rows:
    for st, sb, w, det in r['subs']:
        key = sb.split(';')[0] if st == 'STRUCTURAL_EXPANSION_LOSS' else sb
        if st == 'STRUCTURAL_EXPANSION_LOSS':
            key = 'within expansion scope, cut by cap' if 'within' in sb else 'outside expansion scope'
        sub[(st, key)][r['set']] += w
for (st, sb), c in sorted(sub.items(), key=lambda kv: -sum(kv[1].values())):
    P(f'| {SHORT[st]} | {sb} | ' + ' | '.join(f"{c[s]:.2f}" for s in SETS) + ' |')
P('')

# ------------------------------------------------------------ 3 query classes
P('## By query class\n')
classes = sorted({r['qclass'] for r in rows})
P('Natural sets (dev + heldout + cb); masked reported separately because it is the dev tasks with gold-name tokens removed.\n')
P('| query class | n | baseline cov | lost wt | ' + ' | '.join(SHORT[s] for s in STAGES) + ' | D−base | B16−base | Bfull−base | CP−base | A+lit−A |')
P('|---|---:|---:|---:|' + '---:|' * (len(STAGES) + 5))
for regime, sets in [('natural', NATURAL), ('masked', ['masked'])]:
    for q in classes:
        R = [r for r in rows if r['set'] in sets and r['qclass'] == q]
        if not R: continue
        tot = sum(r['lost'] for r in R) or 1e-9
        P(f'| {regime}: {q} | {len(R)} | {mean(r["cov"] for r in R):.3f} | {sum(r["lost"] for r in R):.1f} | '
          + ' | '.join(f"{100 * sum(r['stages'][st] for r in R) / tot:.0f} %" for st in STAGES)
          + f' | {mean(r["D"] - r["cov"] for r in R):+.3f} | {mean(r["B16"] - r["cov"] for r in R):+.3f} | {mean(r["B"] - r["cov"] for r in R):+.3f}'
          + f' | {mean(r["CP"] - r["cov"] for r in R):+.3f} | {mean(r["A_route"] - r["A"] for r in R):+.3f} |')
P('')

# ------------------------------------------------------------ 4 channel diagnostics
def recall_table(R, title):
    P(f'**{title}** — target-level recall@K (targets: gold symbols; CB: innermost concrete symbol holding each gold line)\n')
    P('| channel | ' + ' | '.join(f'R@{k}' for k in KS) + ' | absent from channel | median rank when present |')
    P('|---|' + '---:|' * (len(KS) + 2))
    lr = [x for r in R for x in r['ranks']['literal']]
    P(f'| literal (any depth; ranks are discovery order, not relevance) | – | – | – | – | {sum(1 for x in lr if x) / (len(lr) or 1):.3f} | {100 * sum(1 for x in lr if not x) / (len(lr) or 1):.0f} % | – |')
    for ch in ['lexical', 'semantic', 'union', 'fused']:
        ranks = [x for r in R for x in r['ranks'][ch]]
        n = len(ranks) or 1
        pres = [x for x in ranks if x]
        P(f'| {ch} | ' + ' | '.join(f"{sum(1 for x in ranks if x and x <= k) / n:.3f}" for k in KS)
          + f' | {100 * (n - len(pres)) / n:.0f} % | {statistics.median(pres) if pres else "–"} |')
    P('')

P('## Channel recall / rank diagnostics\n')
P('`literal` is a research-only channel (query-derived identifier/quoted patterns through `oxide::literal::search`); it is **not routed** into production fusion. `union` = best of lexical/semantic rank. Depth is each channel\'s own top-200 (literal: up to 200 hits per pattern, ≤ 8 patterns).\n')
for s in SETS:
    recall_table([r for r in rows if r['set'] == s], f'{s} ({sum(len(r["targets"]) for r in rows if r["set"] == s)} targets)')
P('### By query class (natural sets pooled; masked separate)\n')
P('| regime / class | targets | lex R@16 | sem R@16 | union R@16 | fused R@16 | lex R@200 | sem R@200 | union R@200 | literal R@any | sem-only in union@200 | lex-only in union@200 |')
P('|---|---:|' + '---:|' * 10)
for regime, sets in [('natural', NATURAL), ('masked', ['masked'])]:
    for q in classes:
        R = [r for r in rows if r['set'] in sets and r['qclass'] == q]
        if not R: continue
        rk = {ch: [x for r in R for x in r['ranks'][ch]] for ch in ['lexical', 'semantic', 'union', 'fused', 'literal']}
        n = len(rk['lexical']) or 1
        at = lambda ch, k: sum(1 for x in rk[ch] if x and x <= k) / n
        semonly = sum(1 for a, b in zip(rk['lexical'], rk['semantic']) if b and b <= 200 and not (a and a <= 200)) / n
        lexonly = sum(1 for a, b in zip(rk['lexical'], rk['semantic']) if a and a <= 200 and not (b and b <= 200)) / n
        P(f'| {regime}: {q} | {n} | {at("lexical", 16):.2f} | {at("semantic", 16):.2f} | {at("union", 16):.2f} | {at("fused", 16):.2f} | '
          f'{at("lexical", 200):.2f} | {at("semantic", 200):.2f} | {at("union", 200):.2f} | {at("literal", 200):.2f} | {semonly:.2f} | {lexonly:.2f} |')
P('')

# ------------------------------------------------------------ 5 fusion-loss anatomy
P('## Anatomy of FUSION_ORDER_LOSS units\n')
fl = [(r, st, sb, w, det) for r in rows for st, sb, w, det in r['subs'] if st == 'FUSION_ORDER_LOSS']
P('| set | units | lost wt | fused rank 17–50 | 51–100 | 101+ | a channel had it ≤16 | lexical ≤16 | semantic ≤16 | lexical rank median | semantic rank median |')
P('|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|')
for s in SETS:
    U = [(r, sb, w, d) for r, st, sb, w, d in fl if r['set'] == s]
    if not U: continue
    tw = sum(w for _, _, w, _ in U)
    f = lambda pred: sum(w for r, sb, w, d in U if pred(sb, d)) / tw
    lx = [d['lex'] for _, _, _, d in U if d.get('lex')]; sm = [d['sem'] for _, _, _, d in U if d.get('sem')]
    P(f'| {s} | {len(U)} | {tw:.1f} | {100 * f(lambda sb, d: "17-50" in sb):.0f} % | {100 * f(lambda sb, d: "51-100" in sb):.0f} % | {100 * f(lambda sb, d: "101+" in sb):.0f} % | '
      f'{100 * f(lambda sb, d: "pushed" in sb):.0f} % | {100 * f(lambda sb, d: (d.get("lex") or 999) <= 16):.0f} % | {100 * f(lambda sb, d: (d.get("sem") or 999) <= 16):.0f} % | '
      f'{statistics.median(lx) if lx else "–"} | {statistics.median(sm) if sm else "–"} |')
P('')

# ------------------------------------------------------------ 6 overlap of recoveries
P('## Which oracle recovers which lost unit (lost weight recovered, by attributed stage)\n')
P('| set | stage | lost wt | B16 | B50 | B full | B chan16 | C dep | C primary | C top |')
P('|---|---|---:|---:|---:|---:|---:|---:|---:|---:|')
for s in SETS:
    agg = collections.defaultdict(lambda: collections.Counter())
    for r in rows:
        if r['set'] != s: continue
        for st, sb, lost, rec, det in r['unit_x']:
            a = agg[st]; a['lost'] += lost
            for k, v in rec.items(): a[k] += v
    for st in STAGES:
        a = agg.get(st)
        if not a or a['lost'] < 1e-9: continue
        P(f"| {s} | {SHORT[st]} | {a['lost']:.2f} | " + ' | '.join(f"{100 * a[k] / a['lost']:.0f} %" for k in ['B16', 'B50', 'B', 'BCHAN16', 'C', 'CP', 'CTOP']) + ' |')
P('')

# ------------------------------------------------------------ 6b seed-band channel patterns
import math, glob as _g2
def wilson(k, n):
    if n == 0: return (float('nan'), float('nan'))
    p = k / n; z = 1.96; d = 1 + z * z / n; c = p + z * z / (2 * n); h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)
def _rk(l):
    m = {}
    for i, e in enumerate(l): m.setdefault(e[0], i + 1)
    return m
pat = collections.defaultdict(collections.Counter); nb = collections.defaultdict(collections.Counter)
for _f in _g2.glob('/tmp/claude-0/work/out/*.jsonl'):
    _s = _f.split('/')[-1].split('__')[0]
    for _l in open(_f):
        _r = json.loads(_l); lx = _rk(_r['lexical']); sm = _rk(_r['semantic'])
        gold = {b['key'] for b in _r['bearers'] if b['oracle_gold']}
        for i, (k, _) in enumerate(_r['fused'][:16]):
            if i < 5: continue
            L, S = lx.get(k), sm.get(k)
            p = 'lexical ≤5, no semantic' if (L and L <= 5 and not S) else ('in both channels' if (L and S) else 'other')
            pat[(_s, p)]['n'] += 1; pat[(_s, p)]['g'] += k in gold
        T = _r['base']; seeds = {x['key'] for x in T['seeds']}; seen = set()
        for e in T.get('expansion_added', []) + T.get('expansion_lost', []) + T.get('structural_lost', []) + T.get('evidence_added', []):
            k = e['key']
            if k in seeds or k in seen: continue
            seen.add(k); nb[_s]['n'] += 1; nb[_s]['g'] += k in gold
        nb[_s]['tasks'] += 1
P('## Is there a request-time signal at the selection seam? (channel-rank patterns, fused ranks 6–16)\n')
P('| set | pattern | candidates | gold | precision [Wilson 95 %] |')
P('|---|---|---:|---:|---|')
for _s in SETS:
    for p in ['lexical ≤5, no semantic', 'in both channels', 'other']:
        c = pat[(_s, p)]
        if c['n']:
            lo, hi = wilson(c['g'], c['n'])
            P(f"| {_s} | {p} | {c['n']} | {c['g']} | {c['g'] / c['n']:.3f} [{lo:.3f}, {hi:.3f}] |")
P('')
P('One-hop neighbors of the top-5 seeds (expansion admitted + cap-lost, ast-grep callers admitted + cap-lost), excluding seeds:\n')
P('| set | neighbors / task | gold | precision [Wilson 95 %] |')
P('|---|---:|---:|---|')
for _s in SETS:
    c = nb[_s]; lo, hi = wilson(c['g'], c['n'])
    P(f"| {_s} | {c['n'] / c['tasks']:.1f} | {c['g']} | {c['g'] / c['n']:.4f} [{lo:.4f}, {hi:.4f}] |")
P('')

# ------------------------------------------------------------ 7 efficiency, #25
P('## Pack efficiency (baseline)\n')
P('| set | used tok | relevant tok | rel/used | items | false-positive items (no gold line delivered) |')
P('|---|---:|---:|---:|---:|---:|')
for s in SETS:
    R = [r for r in rows if r['set'] == s]
    P(f"| {s} | {mean(r['used'] for r in R):.0f} | {mean(r['rel'] for r in R):.1f} | {sum(r['rel'] for r in R) / sum(r['used'] for r in R):.4f} | "
      f"{mean(r['npk'] for r in R):.2f} | {mean(r['fp'] for r in R):.2f} ({100 * sum(r['fp'] for r in R) / sum(r['npk'] for r in R):.0f} %) |")
P('')
P('## #25 canonical test predicate\n')
dis = sum(r['t25_dis'] for r in rows); disg = sum(r['t25_dis_gold'] for r in rows)
P(f"- Pre-allocation candidates where `context::is_test_symbol` and `symbols::is_test_symbol` disagree: **{dis}** across **{sum(r['t25_dis'] > 0 for r in rows)}** of {len(rows)} task-instances; gold-bearing: **{disg}**.")
P(f"- Research arm (canonical predicate for role assignment): packs changed on **{sum(r['t_changed'] for r in rows)}** of {len(rows)}; mean Δ coverage {mean(r['T'] - r['cov'] for r in rows):+.4f}.")
ex = collections.Counter((k.split('#')[1], a, b) for r in rows for k, a, b, g in r['t25_dis_keys'])
P('- Disagreeing symbols: ' + ', '.join(f'`{k}` ×{v}' for (k, a, b), v in ex.most_common()))
P('')
print('\n'.join(out))
