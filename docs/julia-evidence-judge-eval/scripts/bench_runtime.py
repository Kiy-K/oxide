"""Phase 0: Julia-1 CPU runtime characterization (research only).

Usage: bench_runtime.py <checkpoint> <out.json> [--reps N]
Run each configuration in a fresh process (cold numbers are per process).
State text is real Rust source from the OXIDE worktree, cut to a target token
length with Julia's own tokenizer, so lengths are exact model tokens.
"""
import json, os, statistics, sys, time, resource
from pathlib import Path

T0 = time.perf_counter()
import torch  # noqa: E402
from julia import load_model  # noqa: E402
T_IMPORT = time.perf_counter() - T0


def rss_mb():
    for l in open('/proc/self/status'):
        if l.startswith('VmRSS:'):
            return int(l.split()[1]) / 1024


def hwm_mb():
    for l in open('/proc/self/status'):
        if l.startswith('VmHWM:'):
            return int(l.split()[1]) / 1024


def cpu_s():
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


ckpt, out = sys.argv[1], sys.argv[2]
reps = int(sys.argv[sys.argv.index('--reps') + 1]) if '--reps' in sys.argv else 7
rss0 = rss_mb()
t = time.perf_counter()
eng = load_model(ckpt, device='cpu', strict_encoding=True, max_length=8192, head_length=512,
                 compile_model=os.environ.get('JB_COMPILE') == '1')
load_s = time.perf_counter() - t
rss_load = rss_mb()

src = ''.join(p.read_text() for p in sorted(Path('/tmp/oxide-julia-1abb3d7/src').rglob('*.rs')))
ids = eng.tokenizer(src, add_special_tokens=False)['input_ids']


def text_of(n, offset=0):
    return eng.tokenizer.decode(ids[offset:offset + n])


Q = 'Would this evidence materially help solve the task?'
CRIT = {'false': 'The evidence is irrelevant, redundant, or unlikely to help implement/debug the task.',
        'true': 'The evidence directly contributes information useful for implementing/debugging the task.'}


def rows(n_cand, state_tok, salt):
    return [dict(state=text_of(state_tok, (salt * 7919 + i * 613) % (len(ids) - state_tok)),
                 question=Q, type='noul', options=[CRIT['false'], CRIT['true']]) for i in range(n_cand)]


def timed(rs):
    eng.clear_cache()
    c = cpu_s(); t = time.perf_counter()
    z = eng.logits(rs)
    return (time.perf_counter() - t) * 1e3, (cpu_s() - c) * 1e3, z


# cold first judgment (one realistic noul row: 400-token state)
cold_ms, cold_cpu, _ = timed(rows(1, 400, 0))
res = dict(threads=torch.get_num_threads(), env_threads=os.environ.get('JULIA_CPU_THREADS'),
           affinity=sorted(os.sched_getaffinity(0)), import_s=T_IMPORT, load_s=load_s,
           rss_before_load_mb=rss0, rss_after_load_mb=rss_load, cold_first_ms=cold_ms,
           cold_first_cpu_ms=cold_cpu, grid=[])
# warm grid: latency vs state length and vs candidate count (one batched call)
grid = [(1, 512), (1, 1024), (16, 400)] if os.environ.get('JB_QUICK') else [(1, L) for L in (128, 256, 512, 1024, 2048)] + [(n, 400) for n in (4, 8, 16, 32)] + [(16, 200), (16, 800)]
for n, L in grid:
    timed(rows(n, L, 99))  # warm this shape
    ms, cpu = [], []
    for r in range(reps):
        w, c, _ = timed(rows(n, L, r + 1))
        ms.append(w); cpu.append(c)
    ms.sort()
    res['grid'].append(dict(n=n, state_tok=L, median_ms=statistics.median(ms), p95_ms=ms[min(len(ms) - 1, int(0.95 * len(ms)))],
                            min_ms=ms[0], cpu_ms_median=statistics.median(cpu), per_cand_ms=statistics.median(ms) / n))
    print(json.dumps(res['grid'][-1]), flush=True)
# determinism: identical input 5x, and batch-vs-single agreement
rs = rows(8, 400, 5)
runs = [timed(rs)[2] for _ in range(5)]
res['determinism_identical_5x'] = all(r == runs[0] for r in runs)
single = [timed([r])[2][0] for r in rs]
res['max_abs_logit_diff_single_vs_batch'] = max(abs(a - b) for s, bt in zip(single, runs[0]) for a, b in zip(s, bt))
res['rss_steady_mb'] = rss_mb()
res['peak_rss_mb'] = hwm_mb()
res['torch'] = torch.__version__
json.dump(res, open(out, 'w'), indent=1)
print(json.dumps({k: v for k, v in res.items() if k != 'grid'}))
