"""Failure-taxonomy analysis over taxonomy_dump traces (research only).

Gold units
  dev / masked / heldout : gold symbols as labeled (commit-derived). A unit is
      delivered if its exact key is packed, or >=50 % of its span lines fall
      inside delivered snippet windows (a packed container). Strict = exact key.
  cb : ContextBench human gold lines (corrected path normalization).

Attribution of an undelivered unit: the LAST pipeline stage where one of its
bearers was present, else the FIRST stage that could have produced it:
  ALLOCATION_LOSS            bearer in the pre-allocation candidate set
                             (seed/expansion/evidence) but not delivered
  FUSION_ORDER_LOSS          bearer in the fused list (lex U sem top-200) at
                             fused rank > 16 (the context seed cut)
  STRUCTURAL_EXPANSION_LOSS  not in the fused list, but one relation hop
                             (neighbors() or callers_of) from a current seed
  ROUTE_LOSS                 none of the above, but the (unrouted) literal
                             channel finds it from query-derived patterns
  CANDIDATE_GENERATION_LOSS  no channel, hop or literal pattern reaches it
  EVAL_GOLD_PROBLEM          gold file not indexed / gold key absent / gold
                             line outside every indexed symbol
"""
import json, glob, os, re, sys, collections, statistics, random

W = '/tmp/claude-0/work'
OUT = f'{W}/out'
STAGES = ['EVAL_GOLD_PROBLEM', 'CANDIDATE_GENERATION_LOSS', 'ROUTE_LOSS',
          'STRUCTURAL_EXPANSION_LOSS', 'FUSION_ORDER_LOSS', 'ALLOCATION_LOSS']
BUDGET = 4096
SEED_DEPTH = 16
KS = [5, 10, 16, 50, 200]

# ---------------------------------------------------------------- loading
def load():
    inputs = {}
    for f in glob.glob(f'{W}/inputs/*__*.jsonl'):
        for l in open(f):
            t = json.loads(l); inputs[(t['set'], t['id'])] = t
    recs = []
    for f in sorted(glob.glob(f'{OUT}/*.jsonl')):
        s, corp = os.path.basename(f)[:-6].split('__')
        for l in open(f):
            r = json.loads(l); t = inputs[(s, r['id'])]
            r['set'] = s; r['corpus'] = corp; r['query'] = t['query']
            r['gold_keys'] = t.get('gold_keys'); r['literal_patterns'] = t['literal_patterns']
            recs.append(r)
    return recs

_linecache = {}
def file_lines(corp, f):
    k = (corp, f)
    if k not in _linecache:
        try:
            with open(f'{W}/corp/{corp}/{f}', encoding='utf8', errors='replace') as fh:
                _linecache[k] = fh.read().split('\n')
        except OSError:
            _linecache[k] = []
    return _linecache[k]

def tok_of_line(corp, f, n):
    ls = file_lines(corp, f)
    return (len(ls[n - 1]) + 1) / 4.0 if 0 < n <= len(ls) else 0.0

# ---------------------------------------------------------------- query taxonomy
IDENT = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+){2,}|[A-Za-z_][A-Za-z0-9_]*(?:(?:\.|::)[A-Za-z_][A-Za-z0-9_]*)*(?:\(\))?")
def identlike(t):
    b = t.rstrip('()')
    if b.count('-') >= 2: return True
    return ('_' in b.strip('_')) or '::' in b or bool(re.search(r"[a-z][A-Z]", b)) \
        or t.endswith('()') or bool(re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)+", b) and any(c.isupper() or c == '_' for c in b))
PATH = re.compile(r"(?:[\w.-]+/)+[\w.-]+|\b[\w-]+\.(?:py|pyi|ts|tsx|js|jsx|rs|go|java|c|h|cc|cpp|toml|json|ya?ml|cfg|ini)\b")
QUOTED = re.compile(r"`[^`\n]{3,}`|\"[^\"\n]{3,}\"")
ERRTXT = re.compile(r"\b\w*(Error|Exception|Warning)\b|Traceback|raises?\b|crash", re.I)
CALLERS = re.compile(r"\b(callers?|call sites?|who calls|usages?|impact|references to)\b", re.I)
TESTQ = re.compile(r"\b(add(ed|s)? (a )?tests?|tests? for|test coverage|unit tests?)\b", re.I)
IMPLQ = re.compile(r"\b(where is|how does|implement(ed|s|ation)?|support for|add support)\b", re.I)

def classify(q):
    words = q.split()
    idents = [m.group(0) for m in IDENT.finditer(q) if identlike(m.group(0))]
    has_path = bool(PATH.search(q)); has_quote = bool(QUOTED.search(q)); has_err = bool(ERRTXT.search(q))
    if CALLERS.search(q): return 'callers/impact'
    if TESTQ.search(q) and len(words) <= 25: return 'test discovery'
    if has_quote and has_err: return 'quoted literal/error text'
    if has_path and len(words) <= 25: return 'filename/path'
    if idents and len(words) <= 25: return 'exact identifier/symbol'
    if not idents and not has_path and not has_quote:
        return 'implementation discovery (NL)' if IMPLQ.search(q) else 'NL behavioral description'
    return 'mixed/other (long text with identifiers)'

# ---------------------------------------------------------------- per-record analysis
def packed_lines(trace):
    D = set()
    for p in trace['packed']:
        f = p['key'].split('#')[0]; lo, hi = p['span']
        if lo:
            D.update((f, n) for n in range(lo, hi + 1))
    return D

def rank_map(lst):
    m = {}
    for i, e in enumerate(lst):
        k = e[0]
        if k not in m: m[k] = i + 1
    return m

def units_of(r):
    """[(unit_id, weight, bearers(list of bearer dicts), lines(set), eval_reason)]"""
    B = {b['key']: b for b in r['bearers']}
    units = []
    if r['gold_keys'] is not None:
        n = len(r['gold_keys'])
        for k in r['gold_keys']:
            if k in r['gold_keys_missing']:
                units.append((k, 1 / n, [], set(), 'gold key absent from index'))
                continue
            b = B.get(k)
            if b is None:  # file unindexed
                units.append((k, 1 / n, [], set(), 'gold file not indexed'))
                continue
            f = k.split('#')[0]
            units.append((k, 1 / n, [b], {(f, x) for x in range(b['span'][0], b['span'][1] + 1)}, None))
    else:
        GL = set()
        for f, rs in r['gold_lines'].items():
            for a, b in rs:
                GL.update((f, x) for x in range(a, b + 1))
        n = len(GL)
        unidx = set(r['gold_files_unindexed'])
        byfile = collections.defaultdict(list)
        for b in r['bearers']:
            byfile[b['key'].split('#')[0]].append(b)
        # group lines by their bearer signature for speed
        groups = collections.defaultdict(set)
        for (f, x) in GL:
            if f in unidx:
                groups[('EVAL', f, 'gold file not indexed')].add((f, x)); continue
            cov = [b for b in byfile[f] if b['span'][0] <= x <= b['span'][1]]
            conc = [b['key'] for b in cov if b['kind'] != 'Module']
            if conc:
                groups[('B', tuple(sorted(conc)))].add((f, x))
            else:
                mods = [b['key'] for b in cov]
                if mods:
                    groups[('B', tuple(sorted(mods)))].add((f, x))
                else:
                    groups[('EVAL', f, 'gold line outside indexed symbols')].add((f, x))
        for g, lines in groups.items():
            if g[0] == 'EVAL':
                units.append((g, len(lines) / n, [], lines, g[2]))
            else:
                units.append((g, len(lines) / n, [B[k] for k in g[1]], lines, None))
    return units

def unit_delivered(r, unit, D, packed_keys):
    uid, w, bearers, lines, ev = unit
    if ev: return 0.0
    if r['gold_keys'] is not None:
        if uid in packed_keys: return 1.0
        return 1.0 if len(lines & D) >= 0.5 * len(lines) else 0.0
    return len(lines & D) / len(lines)

def unit_delivered_strict(r, unit, D, packed_keys):
    uid, w, bearers, lines, ev = unit
    if ev: return 0.0
    if r['gold_keys'] is not None:
        return 1.0 if uid in packed_keys else 0.0
    return len(lines & D) / len(lines)

def coverage(r, trace, units=None, strict=False):
    units = units or units_of(r)
    D = packed_lines(trace); pk = {p['key'] for p in trace['packed']}
    fn = unit_delivered_strict if strict else unit_delivered
    return sum(u[1] * fn(r, u, D, pk) for u in units)

def efficiency(r, trace):
    D = packed_lines(trace)
    GL = set()
    for u in units_of(r):
        GL |= u[2]  if False else u[3]
    rel = sum(tok_of_line(r['corpus'], f, x) for (f, x) in D & GL)
    used = max(1, sum(p['est'] for p in trace['packed']))
    fp = 0
    for p in trace['packed']:
        f = p['key'].split('#')[0]; lo, hi = p['span']
        if not any((f, x) in GL for x in range(lo, hi + 1)) if lo else True:
            fp += 1
    return rel, used, fp, len(trace['packed'])

def attribute(r, units=None):
    """per-unit stage; returns list of (unit, lost_weight, stage, sub, detail)"""
    units = units or units_of(r)
    T = r['base']
    D = packed_lines(T); pk = {p['key'] for p in T['packed']}
    omitted = {k: why for k, why in T['omitted']}
    pre = {c['key'] for c in T['pre_dedup']}
    kept = {c['key'] for c in T['pool']}
    fused = rank_map(r['fused']); lex = rank_map(r['lexical']); sem = rank_map(r['semantic'])
    lit = rank_map(r['literal'])
    reach = collections.defaultdict(list)
    for e in r['reach']:
        reach[e['key']].append(e)
    exp_lost = {e['key'] for e in T.get('expansion_lost', [])}
    str_lost = {e['key'] for e in T.get('structural_lost', [])}
    out = []
    for u in units:
        uid, w, bearers, lines, ev = u
        got = unit_delivered(r, u, D, pk)
        lost = w * (1 - got)
        if lost <= 1e-12:
            continue
        if ev:
            out.append((u, lost, 'EVAL_GOLD_PROBLEM', ev, {})); continue
        best = None
        for b in bearers:
            k = b['key']
            if k in pk:
                lvl, st, sub = 7, 'ALLOCATION_LOSS', 'snippet window (per-item 350-token cap)'
            elif k in kept:
                lvl, st, sub = 6, 'ALLOCATION_LOSS', omitted.get(k, 'not packed (unknown)')
            elif k in pre:
                lvl, st, sub = 5, 'ALLOCATION_LOSS', omitted.get(k, 'dedup (unknown)')
            elif k in fused:
                fr = fused[k]; ch = min(lex.get(k, 10**6), sem.get(k, 10**6))
                bucket = '17-50' if fr <= 50 else ('51-100' if fr <= 100 else '101+')
                sub = f"fused rank {bucket}; " + ('a channel ranked it <=16 (RRF pushed it down)' if ch <= SEED_DEPTH else 'both channels ranked it >16')
                lvl, st = 4, 'FUSION_ORDER_LOSS'
            elif k in reach:
                e = min(reach[k], key=lambda e: e['seed_rank'])
                in_policy = (e['rel'] != 'caller' and e['seed_rank'] < 5) or (e['rel'] == 'caller' and e['seed_rank'] < 2)
                sub = f"{e['rel']} of seed #{e['seed_rank']}; " + ('within expansion scope, cut by cap' if in_policy else 'outside expansion scope (seed rank/relation)')
                lvl, st = 3, 'STRUCTURAL_EXPANSION_LOSS'
            elif k in lit:
                lvl, st, sub = 2, 'ROUTE_LOSS', 'literal channel (unrouted) finds it'
            else:
                lvl, st, sub = 1, 'CANDIDATE_GENERATION_LOSS', ('module-only bearer' if b['kind'] == 'Module' else 'no channel/hop/literal reaches it')
            det = {'key': k, 'fused': fused.get(k), 'lex': lex.get(k), 'sem': sem.get(k), 'lit': lit.get(k),
                   'reach': bool(reach.get(k))}
            if best is None or lvl > best[0]:
                best = (lvl, st, sub, det)
        if best is None:
            out.append((u, lost, 'EVAL_GOLD_PROBLEM', 'no bearer', {})); continue
        out.append((u, lost, best[1], best[2], best[3]))
    return out

def task_primary(att, total_lost):
    if total_lost <= 1e-9: return 'HIT'
    by = collections.Counter()
    for _, l, st, _, _ in att: by[st] += l
    st, v = by.most_common(1)[0]
    return st if v >= (2 / 3) * total_lost else 'AMBIGUOUS/MULTI'

def unit_cross(r, units):
    """per lost unit: (stage, sub, lost weight, recovered-weight under B, C, B16, BCHAN16)"""
    att = attribute(r, units)
    arms = {'B': r['arm_b'], 'C': r['arm_c'], 'CP': r['arm_cp'], 'CTOP': r['arm_ctop'], **{k.upper(): v for k, v in r['arm_bn'].items()}}
    out = []
    for u, lost, st, sub, det in att:
        rec = {}
        for a, T in arms.items():
            D = packed_lines(T); pk = {p['key'] for p in T['packed']}
            got = unit_delivered(r, u, D, pk) * u[1]
            base_got = u[1] - lost
            rec[a] = max(0.0, got - base_got)
        out.append((st, sub, lost, rec, det))
    return out

# ---------------------------------------------------------------- oracle optimizer
def oracle_pack(r, keys, units, cap=True):
    """Max gold coverage using only candidates in `keys` (gold-bearing only),
    cap windows (production 350) or full bodies, budget 4096. Greedy by
    marginal delivered-gold-weight per token; exact for these sizes in practice."""
    B = {b['key']: b for b in r['bearers']}
    cands = [B[k] for k in keys if k in B]  # only gold-overlapping symbols can add coverage
    chosen = []; used = 0; D = set(); pk = set()
    unit_list = [u for u in units if not u[4]]
    def cov(D, pk):
        return sum(u[1] * unit_delivered(r, u, D, pk) for u in unit_list)
    base = 0.0
    while True:
        bestv = None
        for c in cands:
            if c['key'] in pk: continue
            span = c['cap_span'] if cap else c['full_span']
            tok = c['cap_tok'] if cap else c['full_tok']
            if used + tok > BUDGET or not span[0]: continue
            f = c['key'].split('#')[0]
            D2 = D | {(f, x) for x in range(span[0], span[1] + 1)}
            gain = cov(D2, pk | {c['key']}) - base
            if gain <= 1e-12: continue
            v = gain / tok
            if bestv is None or v > bestv[0]:
                bestv = (v, c, D2, tok)
        if bestv is None: break
        _, c, D, tok = bestv; pk.add(c['key']); used += tok; base = cov(D, pk)
    return base

# ---------------------------------------------------------------- main
def main():
    recs = load()
    rows = []
    for r in recs:
        units = units_of(r)
        eval_w = sum(u[1] for u in units if u[4])
        cov_b = coverage(r, r['base'], units)
        cov_bs = coverage(r, r['base'], units, strict=True)
        att = attribute(r, units)
        lost = sum(a[1] for a in att)
        prim = task_primary(att, lost)
        rel, used, fp, npk = efficiency(r, r['base'])
        pool_A = {e[0] for e in r['fused']} | {c['key'] for c in r['base']['pre_dedup']}
        pool_R = pool_A | {e[0] for e in r['literal']}
        all_b = {b['key'] for b in r['bearers']}
        pre = {c['key'] for c in r['base']['pre_dedup']}
        row = dict(id=r['id'], set=r['set'], corpus=r['corpus'], qclass=classify(r['query']),
                   nwords=len(r['query'].split()), n_units=len(units), eval_w=eval_w,
                   cov=cov_b, cov_strict=cov_bs, prim=prim, lost=lost,
                   stages={st: sum(a[1] for a in att if a[2] == st) for st in STAGES},
                   subs=[(a[2], a[3], a[1], a[4]) for a in att],
                   rel=rel, used=used, fp=fp, npk=npk,
                   A=oracle_pack(r, pool_A, units), A_full=oracle_pack(r, pool_A, units, cap=False),
                   A_route=oracle_pack(r, pool_R, units), A_index=oracle_pack(r, all_b, units),
                   A_index_full=oracle_pack(r, all_b, units, cap=False),
                   D=oracle_pack(r, pre, units),
                   B=coverage(r, r['arm_b'], units),
                   BD=oracle_pack(r, {c['key'] for c in r['arm_b']['pre_dedup']}, units),
                   C=coverage(r, r['arm_c'], units),
                   CD=oracle_pack(r, {c['key'] for c in r['arm_c']['pre_dedup']}, units),
                   T=coverage(r, r['arm_t'], units),
                   CP=coverage(r, r['arm_cp'], units),
                   RT=coverage(r, r['arm_rt'], units),
                   CTOP=coverage(r, r['arm_ctop'], units),
                   relRT=efficiency(r, r['arm_rt'])[0], usedRT=efficiency(r, r['arm_rt'])[1],
                   **{k.upper(): coverage(r, v, units) for k, v in r['arm_bn'].items()},
                   unit_x=unit_cross(r, units),
                   base_strict_for_oracle=cov_bs)
        # efficiency of arm T
        relT, usedT, fpT, npkT = efficiency(r, r['arm_t'])
        row.update(relT=relT, usedT=usedT,
                   t_changed=[p['key'] for p in r['arm_t']['packed']] != [p['key'] for p in r['base']['packed']]
                   or [p['role'] for p in r['arm_t']['packed']] != [p['role'] for p in r['base']['packed']])
        # #25 disagreements in pre-allocation set
        gold_keys = {b['key'] for b in r['bearers']}
        dis = [c for c in r['base']['pre_dedup'] if c['test_local'] != c['test_canon']]
        row['t25_dis'] = len(dis); row['t25_dis_gold'] = sum(c['key'] in gold_keys for c in dis)
        row['t25_dis_keys'] = [(c['key'], c['test_local'], c['test_canon'], c['key'] in gold_keys) for c in dis]
        # channel ranks of gold targets
        targets = [u[0] for u in units if not u[4]] if r['gold_keys'] is not None else \
            [b['key'] for b in r['bearers'] if b['oracle_gold'] and b['kind'] != 'Module'] or \
            [b['key'] for b in r['bearers'] if b['oracle_gold']]
        ranks = {}
        for ch in ['literal', 'lexical', 'semantic', 'fused']:
            m = rank_map(r[ch]); ranks[ch] = [m.get(k) for k in targets]
        ranks['union'] = [min([x for x in (a, b) if x] or [None]) if (a or b) else None
                          for a, b in zip(ranks['lexical'], ranks['semantic'])]
        row['ranks'] = ranks; row['targets'] = targets
        row['sem_missing'] = len(r['sem_missing']); row['gold_files_unindexed'] = r['gold_files_unindexed']
        row['gold_keys_missing'] = r['gold_keys_missing']
        row['literal_n'] = len(r['literal']); row['has_patterns'] = bool(r['literal_patterns'])
        rows.append(row)
    json.dump(rows, open(f'{W}/an/rows.json', 'w'))
    print(len(rows), 'rows')

if __name__ == '__main__':
    main()
