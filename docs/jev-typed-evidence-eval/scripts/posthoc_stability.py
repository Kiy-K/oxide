"""#40 POST-HOC (not preregistered; written after V3 failed; does not affect the verdict).

Repeatability of Jev on the 5 fixed synthetic canary states: each state is requested
REPS times sequentially and REPS times with all 5 x REPS requests in flight at once
(concurrency 12, the scoring run's setting). Identical input, so any spread is service-side
nondeterminism or a model change. Synthetic states only, as the protocol requires.

usage: posthoc_stability.py  -> results/posthoc-stability.jsonl
"""
import json, os, sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
from score_jev import RES, api_key, ask  # noqa: E402

REPS = 10


def main():
    key = api_key()
    canaries = [json.loads(l) for l in open(os.path.join(HERE, '..', 'canaries.jsonl'))]
    with open(os.path.join(RES, 'posthoc-stability.jsonl'), 'w') as out:
        for c in canaries:
            for i in range(REPS):
                out.write(json.dumps({'id': c['id'], 'mode': 'sequential', 'rep': i, **ask(c['state'], key)}) + '\n')
        jobs = [(c, i) for c in canaries for i in range(REPS)]
        with ThreadPoolExecutor(12) as pool:
            for (c, i), r in zip(jobs, pool.map(lambda j: ask(j[0]['state'], key), jobs)):
                out.write(json.dumps({'id': c['id'], 'mode': 'concurrent', 'rep': i, **r}) + '\n')


if __name__ == '__main__':
    main()
