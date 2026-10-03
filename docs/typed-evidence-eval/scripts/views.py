"""Typed-evidence probe: build file-level views and pick the probe sample (PROBE.md §3).

Input: the committed, verified #39 universe docs/joint-interaction-eval/results/cands.jsonl.gz
(fused top-50 symbols per task, source at the indexed revision, labels in `y`).
A file view = the first FILES distinct files by best fused rank, each with its candidate
spans. Labels are used only for eligibility and are kept out of the text Julia sees.

usage: views.py   -> results/views.jsonl (one line per probe task)
"""
import gzip, json, os, random

HERE = os.path.dirname(os.path.abspath(__file__))
CANDS = os.path.join(HERE, '..', '..', 'joint-interaction-eval', 'results', 'cands.jsonl.gz')
OUT = os.path.join(HERE, '..', 'results', 'views.jsonl')
FILES, SEED = 12, 39
N = {'cb': 6, 'heldout': 4}


def file_view(rows):
    files = {}
    for r in sorted(rows, key=lambda r: r['fr']):
        if r['path'] not in files and len(files) == FILES:
            continue
        files.setdefault(r['path'], []).append(r)
    out = []
    for path, rs in files.items():
        syms = [r for r in rs if not r['is_module']] or rs  # a module span is the whole file
        spans, last = [], 0
        for r in sorted(syms, key=lambda r: (r['span'][0], -r['span'][1])):
            if r['span'][1] <= last:
                continue  # nested in a span already shown
            spans.append(r)
            last = r['span'][1]
        out.append({'path': path, 'best_fr': rs[0]['fr'], 'y': int(any(r['y'] for r in rs)),
                    'is_test': any(r['is_test'] for r in rs),
                    'decls': [r['qname'] for r in sorted(syms, key=lambda r: r['span'][0])],
                    'source': '\n...\n'.join(r['text'] for r in spans)})
    return out


def build():
    T = {}
    for r in map(json.loads, gzip.open(CANDS, 'rt')):
        T.setdefault((r['set'], r['task']), []).append(r)
    views = {k: file_view(v) for k, v in sorted(T.items())}
    sample = []
    for s, n in N.items():
        by = {}
        for k, fv in views.items():
            if k[0] == s and 0 < sum(f['y'] for f in fv) < len(fv):
                by.setdefault(T[k][0]['repo'], []).append(k)
        rng = random.Random(SEED)
        for v in by.values():
            rng.shuffle(v)
        repos = sorted(by)
        rng.shuffle(repos)
        got = []
        while len(got) < n:
            for p in repos:
                if by[p] and len(got) < n:
                    got.append(by[p].pop())
        sample += got
    return [{'set': s, 'task': t, 'repo': T[(s, t)][0]['repo'], 'qclass': T[(s, t)][0]['qclass'],
             'query': T[(s, t)][0]['query'], 'files': views[(s, t)]} for s, t in sample]


if __name__ == '__main__':
    with open(OUT, 'w') as f:
        for v in build():
            f.write(json.dumps(v) + '\n')
            print(v['set'], v['task'], v['repo'], v['qclass'], 'files', len(v['files']),
                  'pos', sum(x['y'] for x in v['files']))
