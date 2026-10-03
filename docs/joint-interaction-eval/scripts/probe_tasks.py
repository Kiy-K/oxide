"""Issue #39 probe: pick the probe tasks (PROBE.md §3). Seeded, round-robin over repos,
eligibility is the only label use. Prints set<TAB>task lines."""
import gzip, json, os, random

RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'results')
N = {'cb': 6, 'heldout': 4}


def pick():
    T = {}
    for r in map(json.loads, gzip.open(os.path.join(RES, 'cands.jsonl.gz'), 'rt')):
        T.setdefault((r['set'], r['task']), []).append(r)
    out = []
    for s, n in N.items():
        by = {}
        for k, v in sorted(T.items()):
            if k[0] == s and 0 < sum(r['y'] for r in v) < len(v):
                by.setdefault(v[0]['repo'], []).append(k)
        rng = random.Random(39)
        for v in by.values():
            rng.shuffle(v)
        repos = sorted(by)
        rng.shuffle(repos)
        got = []
        while len(got) < n:
            for p in repos:
                if by[p] and len(got) < n:
                    got.append(by[p].pop())
        out += got
    return out


if __name__ == '__main__':
    for s, t in pick():
        print(f'{s}\t{t}')
