"""R1 Phase 0 (PROTOCOL.md §1-3): exposure counts + tokenizer mechanism screen.
Reads gold keys + queries only (exposure), the shipped tokenizer, and the
held-out fused top-50 keys (length proxy). No embedding."""
import gzip, json, os, re, statistics
from tokenizers import Tokenizer

HERE = os.path.dirname(os.path.abspath(__file__))
R = '/home/user/oxide/docs/ranking-fusion-eval/results/'
TOK = Tokenizer.from_file(os.path.join(HERE, 'tok', 'tokenizer.json'))
SPLIT = re.compile(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])')


def r1(s):
    return SPLIT.sub(' ', s)


def qname(key):
    return key.split('#', 1)[1]


def is_module(key):
    return key.endswith(':__module__')


def id_tokens(key):
    return [] if is_module(key) else re.findall(r'[A-Za-z0-9_]+', qname(key))


def affected(tok):
    return r1(tok) != tok


def components(tok):
    out = []
    for p in r1(tok).split(' '):
        p = p.strip('_').lower()
        if len(p) >= 3 and p.isalpha():
            out.append(p)
    return out


def query_words(q):
    return set(w.lower() for w in re.findall(r'[A-Za-z]+', q))


def simple_name(key):
    return re.split(r'\.|::', qname(key))[-1]


def is_ident(q, gold):
    for g in gold:
        if is_module(g):
            continue
        n = simple_name(g)
        if len(n) >= 3 and re.search(r'(?<![A-Za-z0-9_])' + re.escape(n) + r'(?![A-Za-z0-9_])', q, re.I):
            return True
    return False


def exposure(q, gold):
    qw = query_words(q); hits = []
    for g in gold:
        for t in id_tokens(g):
            if affected(t):
                m = [c for c in components(t) if c in qw]
                if m:
                    hits.append((g, t, m))
    return hits


def pieces(s):
    return TOK.encode(s, add_special_tokens=False).tokens


def tok_stats(tokens):
    rows = []
    for t in sorted(tokens):
        b = pieces(t); a = pieces(r1(t)); comps = components(t)
        rows.append({'token': t, 'r1': r1(t), 'base': b, 'r1_pieces': a,
                     'base_cont': sum(p.startswith('##') for p in b), 'r1_cont': sum(p.startswith('##') for p in a),
                     'base_n': len(b), 'r1_n': len(a),
                     'comps': comps,
                     'base_recov': sum(c in b for c in comps), 'r1_recov': sum(c in a for c in comps)})
    agg = {'n_tokens': len(rows),
           'base_cont': sum(r['base_cont'] for r in rows), 'r1_cont': sum(r['r1_cont'] for r in rows),
           'base_pieces': sum(r['base_n'] for r in rows), 'r1_pieces': sum(r['r1_n'] for r in rows),
           'components': sum(len(r['comps']) for r in rows),
           'base_recov': sum(r['base_recov'] for r in rows), 'r1_recov': sum(r['r1_recov'] for r in rows)}
    agg['cont_reduction'] = 1 - agg['r1_cont'] / agg['base_cont'] if agg['base_cont'] else None
    agg['pieces_delta_pct'] = 100 * (agg['r1_pieces'] / agg['base_pieces'] - 1) if agg['base_pieces'] else None
    agg['base_recov_rate'] = agg['base_recov'] / agg['components'] if agg['components'] else None
    agg['r1_recov_rate'] = agg['r1_recov'] / agg['components'] if agg['components'] else None
    return agg, rows


def main():
    out = {'tokenizer_version': __import__('tokenizers').__version__}
    sets = [('dev', 'tasks.jsonl'), ('heldout', 'heldout-clean.jsonl'), ('cb', 'cb-tasks.jsonl')]
    exposed_tokens = set(); per = {}
    for s, f in sets:
        T = [json.loads(l) for l in open(R + f)]
        ex, idn = [], []
        for t in T:
            gold = t.get('gold') or []
            h = exposure(t['query'], gold)
            if h:
                ex.append({'id': t['id'], 'hits': [[g, tk, m] for g, tk, m in h]})
                if s in ('dev', 'heldout'):
                    # PROTOCOL §3: all split-affected tokens of gold keys in exposed tasks
                    exposed_tokens.update(tk for g in gold for tk in id_tokens(g) if affected(tk))
            if gold and is_ident(t['query'], gold):
                idn.append(t['id'])
        labeled = sum(1 for t in T if t.get('gold'))
        per[s] = {'tasks': len(T), 'with_gold': labeled,
                  'exposed': len(ex) if labeled else None, 'ident': len(idn) if labeled else None,
                  'exposed_and_ident': len({e['id'] for e in ex} & set(idn)) if labeled else None,
                  'exposed_ids': [e['id'] for e in ex], 'hits': ex}
    out['exposure'] = per
    agg, rows = tok_stats(exposed_tokens)
    out['exposed_sample'] = agg; out['exposed_rows'] = rows
    # descriptive: all split-affected tokens in held-out fused top-50 qualified names
    allt = set(); qn_base = qn_r1 = 0; qn_n = 0; qlens_b = []; qlens_r = []
    for l in gzip.open(R + 'dump-heldout.jsonl.gz', 'rt'):
        d = json.loads(l)
        for k, *_ in d['fused'][:50]:
            if is_module(k):
                continue
            q = qname(k)
            nb = len(pieces(q)); nr = len(pieces(r1(q)))
            qn_base += nb; qn_r1 += nr; qn_n += 1; qlens_b.append(nb); qlens_r.append(nr)
            allt.update(t for t in id_tokens(k) if affected(t))
    agg2, _ = tok_stats(allt)
    out['heldout_top50_affected_tokens'] = agg2
    qlens_b.sort(); qlens_r.sort()
    p95 = lambda v: v[int(0.95 * (len(v) - 1))]
    out['length_proxy_qualified_name'] = {
        'n': qn_n, 'mean_base': qn_base / qn_n, 'mean_r1': qn_r1 / qn_n,
        'delta_pct': 100 * (qn_r1 / qn_base - 1), 'p95_base': p95(qlens_b), 'p95_r1': p95(qlens_r),
        'note': 'qualified_name only (proxy); full symbol_embed_text needs the index'}
    ho = per['heldout']['exposed']
    out['stop_rule_1_cont_reduction'] = agg['cont_reduction']
    out['stop_rule_1_pass'] = agg['cont_reduction'] is not None and agg['cont_reduction'] >= 0.25
    out['stop_rule_2_heldout_exposed'] = ho
    out['stop_rule_2_pass'] = ho is not None and ho >= 15
    out['phase0'] = 'PASS' if out['stop_rule_1_pass'] and out['stop_rule_2_pass'] else 'FAIL'
    json.dump(out, open(os.path.join(HERE, 'results', 'phase0.json'), 'w'), indent=1)
    for s in per:
        print(s, {k: v for k, v in per[s].items() if k not in ('exposed_ids', 'hits')})
    print('exposed sample', agg)
    print('held-out top-50 affected tokens', agg2)
    print('length proxy', out['length_proxy_qualified_name'])
    print('rule1', out['stop_rule_1_cont_reduction'], out['stop_rule_1_pass'], 'rule2', ho, out['stop_rule_2_pass'], '=>', out['phase0'])


if __name__ == '__main__':
    main()
