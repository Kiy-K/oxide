"""Independent check of TreeSelect v0 steps 2–4 (PROTOCOL §Pipeline) against the Rust arm.

B's `offers` are exactly the post-floor pool (production order) and T=−x selects over that same
pool, so re-deriving the tree order + collapse here from B's offers and the corpus index must
reproduce T=−x's `offers` key sequence.

  tree_check.py <packs.jsonl> <root-or-roots.json>   (root: one corpus dir for every record;
                                                     roots.json: {task id: corpus dir})
Exits non-zero on any mismatch.
"""
import json, math, os, sqlite3, sys
from collections import defaultdict

CAP, CPT = 350, 4.0


def index(root):
    db = sqlite3.connect(f'file:{root}/.oxide/index.db?mode=ro', uri=True)
    rows = db.execute('select id, file, qualified_name, kind, start_line, end_line from symbols').fetchall()
    return [dict(id=r[0] % 2**64, file=r[1], qn=r[2], kind=r[3], lo=r[4], hi=r[5]) for r in rows]


def body_tokens(root, s):
    try:
        lines = open(os.path.join(root, s['file']), encoding='utf-8', errors='replace').read().splitlines()
    except OSError:
        return math.inf
    lo, hi = s['lo'] - 1, min(s['hi'], len(lines))
    return math.ceil(len('\n'.join(lines[lo:hi])) / CPT) if hi > lo else math.inf


def order(root, syms, pool):
    """pool: [(key, score)] -> [(key, source)] (container key for a collapse)."""
    by_key = {f"{s['file']}#{s['qn']}": s for s in syms}
    by_file = defaultdict(list)
    for s in syms:
        by_file[s['file']].append(s)
    nodes = [dict(kind='root', path='', id=0, ch={}, order=[], sum=0.0, n=0)]
    for i, (k, sc) in enumerate(pool):
        s = by_key[k]
        d = s['file'].rsplit('/', 1)[0] if '/' in s['file'] else ''
        cont = sorted((x for x in by_file[s['file']] if x['kind'] not in ('module', 'import')
                       and x['lo'] <= s['lo'] and s['hi'] <= x['hi'] and (x['lo'], x['hi']) != (s['lo'], s['hi'])),
                      key=lambda x: (-(x['hi'] - x['lo']), x['id']))
        path = [('d:' + d, 'dir', d, 0, None), ('f:' + s['file'], 'file', s['file'], 0, None)]
        path += [(f"s:{x['id']}", 'sym', s['file'], x['id'], x) for x in cont]
        path.append((f'l:{i}', 'leaf', s['file'], s['id'], i))
        cur = 0
        nodes[0]['sum'] += sc
        nodes[0]['n'] += 1
        for key, kind, p, nid, ref in path:
            if key not in nodes[cur]['ch']:
                nodes.append(dict(kind=kind, path=p, id=nid, ref=ref, ch={}, order=[], sum=0.0, n=0))
                nodes[cur]['ch'][key] = len(nodes) - 1
                nodes[cur]['order'].append(len(nodes) - 1)
            cur = nodes[cur]['ch'][key]
            nodes[cur]['sum'] += sc
            nodes[cur]['n'] += 1

    def flat(v):
        while nodes[v]['kind'] != 'leaf' and len(nodes[v]['order']) == 1:
            v = nodes[v]['order'][0]
        return v

    def val(v):
        return nodes[v]['sum'] / math.sqrt(nodes[v]['n'] + 1)

    frontier = [flat(c) for c in nodes[0]['order']]
    out = []
    while frontier:
        v = min(frontier, key=lambda v: (-val(v), nodes[v]['path'], nodes[v]['id']))
        frontier.remove(v)
        nd = nodes[v]
        if nd['kind'] == 'leaf':
            out.append((pool[nd['ref']][0], 'tree'))
            continue
        if nd['n'] >= 2:
            c = None
            if nd['kind'] == 'file':
                mods = sorted((x for x in by_file[nd['path']] if x['kind'] == 'module'
                               and x['qn'] == nd['path'] + ':__module__'), key=lambda x: x['id'])
                c = mods[0] if mods else None
            elif nd['kind'] == 'sym' and nd['ref']['kind'] == 'class':
                c = nd['ref']
            if c and body_tokens(root, c) <= CAP:
                out.append((f"{c['file']}#{c['qn']}", 'collapse'))
                continue
        frontier.extend(flat(c) for c in nd['order'])
    return out


def main():
    recs = [json.loads(l) for l in open(sys.argv[1])]
    roots = json.load(open(sys.argv[2])) if sys.argv[2].endswith('.json') else None
    by = defaultdict(dict)
    for r in recs:
        by[r['id']][r['arm']] = r
    cache, bad, n = {}, 0, 0
    for tid, a in by.items():
        root = roots[tid] if roots else sys.argv[2]
        if root not in cache:
            cache[root] = index(root)
        pool = [(o[0], o[2]) for o in a['B']['trace']['offers']]
        want = [(o[0], o[1]) for o in a['TX']['trace']['offers']]
        got = order(root, cache[root], pool)
        n += 1
        if got != want:
            bad += 1
            print('MISMATCH', tid, '\n  rust  ', want, '\n  python', got)
    print(f'tree_check: {n - bad}/{n} identical')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
