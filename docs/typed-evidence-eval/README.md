# Typed semantic evidence probe: Julia-1 and Jev: NO TYPED SIGNAL (frozen rule)

Preregistration: `PROBE.md` (sha256 `9fd0546…` in `PROBE.sha256`, frozen
before any probe score). Full tables: `results/eval.txt`; machine-readable
results: `results/probe-results.json`. Production is unchanged; retrieval,
fusion and the allocator stay frozen.

**Setup.** 10 tasks (6 ContextBench, 4 held-out), 110 file views, 18 gold
files. Each file was asked seven independent `noul` questions (six typed ones
plus #31's J1 verbatim). Two arms answered identical states: local Julia-1
and hosted Jev `jev-1.13.0`.

## Verdict (per PROBE §7)

| arm | A1 Δ vs J1 ≥ +0.10, CI > 0 | A2 above chance | A3 sets | A3 repos ≥ ⅔ | A3 query classes | verdict |
|---|---|---|---|---|---|---|
| Julia-1 | ✗ | ✗ | ✗ | ✗ (4/9) | ✗ | **NO TYPED SIGNAL** |
| Jev 1.13 | ✓ | ✓ | ✓ | ✓ (7/9) | ✗ (quoted literal) | **NO TYPED SIGNAL** |

**Frozen verdict: NO TYPED SIGNAL.** Neither arm reaches A, so B (lift over
fused) is not reached either, since it requires A. Per PROBE §7, neither arm
reached A, so the direction (typed decomposition vs #31's broad judgment) is
closed. That is a decision under the frozen rule, not evidence that typed
decomposition cannot work: only one question set, one 12-file view and 10
tasks were tested. A Jev follow-up would be a new hypothesis motivated by
descriptive, post-hoc results from this probe. It needs its own
preregistration and fresh data, and it does not reopen or re-score this
probe.

- **Julia-1:** closed and rejected as a semantic evidence source for OXIDE code-evidence selection.
- **Jev 1.13.0:** not validated for production. Its descriptive gains over
  J1 (mainly held-out) and over fused order (mainly cb) justify a separate
  preregistered research issue (see the caveats in "What this establishes").

## Results (measured; within-task file AUROC, pooled n = 10)

| scorer | Julia-1 | Jev 1.13 |
|---|---:|---:|
| fused (OXIDE order) | 0.805 | 0.805 |
| J1 (#31 broad question) | 0.427 | 0.779 |
| relevant | 0.429 | 0.858 |
| in_scope | 0.542 | 0.916 |
| primary | 0.341 | **0.929** |
| implementation | 0.506 | 0.916 |
| test | 0.404 | 0.489 |
| concrete_reference | 0.465 | 0.885 |
| T-gate (relevant × in_scope) | 0.447 | 0.926 |
| T-mean | 0.400 | **0.940** |
| T-mean − J1 [95 % CI] | −0.028 [−0.227, +0.181] | **+0.161 [+0.050, +0.310]** |
| T-mean − fused [CI] | −0.405 [−0.619, −0.195] | +0.135 [+0.052, +0.220] |
| T-mean+F − fused [CI] | −0.068 [−0.159, +0.021] | +0.089 [+0.032, +0.149] |

### Julia-1: no signal in any dimension

- No typed dimension is meaningfully above chance (0.34–0.54; `primary` is
  below it). Julia's J1 is
  at or below chance on this universe (0.427, CI [0.268, 0.587]), consistent
  with #31.
- Answers are saturated: `primary` is ≈ 0.96–0.99 for gold, non-gold test and
  other files alike. Decomposing the question does not give Julia task
  grounding.
- Adding Julia to fused order never helps pooled (G+F − fused −0.016 to
  −0.068); the best per-set value is +0.003 (T-gate+F, held-out).

### Jev: typed dimensions separate gold from junk (descriptive); the gate fails on one transfer check

- **Dimensions with signal** (descriptive: n = 10, 18 gold files, uncorrected
  across 6 dimensions; mean P for gold / non-gold test / other non-gold
  files):
  - `primary`: 0.757 / 0.208 / 0.379
  - `in_scope`: 0.847 / 0.361 / 0.488
  - `implementation`: 0.671 / 0.161 / 0.332
  - `relevant` and `concrete_reference` are weaker; their junk files sit at
    0.40–0.49.
- **`test` carries no selection signal** (AUROC 0.489). It does identify test
  files (0.40 vs 0.12), but gold here is mostly non-test.
- **Transfer.** T-mean beats J1 on both sets (point estimates; cb +0.041, CI
  includes 0) and in 7/9 repos. flask has one task in each set and counts as
  positive only through its held-out task. A3 fails on one
  query class, "quoted literal/error text" (3 cb tasks), where the Δ values
  are 0.00, 0.00 and −0.05. Jev's broad J1 is already 0.95–1.00 on those
  tasks.
  - *Descriptive, post-hoc observation only:* two of the three tasks
    are ties at J1 = 1.00, and the third is a small loss (code-server: J1 0.95,
    T-mean 0.90). The failure reflects a near-ceiling J1 more than a broad
    loss. The frozen gate is unchanged
    and the verdict stands; a future protocol could state how ceiling ties
    are handled before scoring.
- **Against fused order** (descriptive only; B requires A):
  - on cb, every unfused Jev scorer except `test` beats fused, by +0.165
    (`concrete_reference`) to +0.219 (T-gate, T-mean); the fused combinations
    (G+F) gain +0.12 to +0.15;
  - on held-out, fused is already 0.907, and typed Jev only matches it
    (T-mean+F +0.005).
- Leakage note: held-out text is post-commit (PROBE §3). cb is free of
  post-commit leakage. It carries the gain over fused (+0.219) but not the
  gain over J1: on cb, T-mean − J1 is +0.041 [−0.013, +0.108]. The pooled
  +0.161 over J1 comes mainly from held-out (+0.341), whose post-commit text
  can favor any joint reader.

## Cost (measured)

| arm | per file (7 questions) | per task (8–12 files) | memory |
|---|---|---|---|
| Julia-1, 2 threads, capped | median 3.99 s, p95 5.20 s | median 42.9 s | 1.4 GB RSS (cgroup peak 1.1 GB, `results/julia-run.log`) |
| Jev (network) | median 0.50 s, p95 0.52 s | median 5.6 s, sequential | — |

Both are far over OXIDE's synchronous budget (median ≤ 250 ms per request).
Jev also adds a network dependency to a local-first tool.

## What this establishes

- **Julia-1 is closed and rejected as a semantic evidence source for OXIDE
  code-evidence selection**, in both
  the broad (#31) and the typed form. Typed questions do not fix its lack of
  task grounding.
- **Under the preregistered rule, typed decomposition shows NO TYPED SIGNAL in
  this probe.** This does not show that typed decomposition in general is
  exhausted. Only one question set, one file view, 10 tasks and two models
  were tested, and, descriptively (n = 10, 18 gold files, uncorrected across
  6 dimensions), Jev's typed dimensions separated gold from junk files.
- **Jev 1.13.0 is not validated for production.** n = 10 is a screen, and the
  gate failed. Jev is also 0.5 s per file over the network (5.6 s per task),
  against OXIDE's local, median ≤ 250 ms per-request budget.
- **A separate preregistered research issue is justified for Jev.** The
  reasons, all descriptive:
  - T-mean − J1 = +0.161 [+0.050, +0.310] pooled, driven mainly by held-out
    (cb: +0.041, CI includes 0);
  - T-mean − fused = +0.135 [+0.052, +0.220];
  - on ContextBench (no post-commit text, but public SWE-Bench-Verified
    issues that a hosted model may have seen), Jev beats fused by +0.165 to
    +0.219, of which Jev's broad J1 already gets +0.177.

  That issue should fix in advance: more tasks, a fresh split, how ceiling
  ties are handled, training-contamination controls, and the cost and privacy model of a networked
  dependency.
- No production retrieval, scoring, embedding, allocator or API change
  follows from this probe.

## Safety record

- Julia ran in `systemd-run` with `MemoryMax=2560M`, `MemorySwapMax=0`,
  `CPUQuota=200%`, 2 threads, `nice`, no network, and a host watchdog
  (`scripts/run.sh`).
- Probe run (`results/julia-run.log`): 7 min wall, 14 min CPU, 1.1 GB cgroup
  peak; no watchdog ABORT line was emitted. Host swap use (`free`, all
  processes; console readings, not committed) was 5201 MB before and 5233 MB
  after (+32 MB), under the 200 MB abort threshold.
- Models and the venv live on disk, not in tmpfs `/tmp`.

## Reproduce

```sh
# Julia-1 @ a85b1273… and a venv with torch 2.14.1+cpu, transformers 5.0.0, numpy, scipy
python scripts/views.py                                    # results/views.jsonl
JULIA=… PY=… scripts/run.sh 120 score.py states            # results/states.jsonl
JULIA=… PY=… scripts/run.sh 1200 score.py julia            # capped local arm
python scripts/score.py jev                                # needs TYPESAFE_API_KEY
python scripts/eval.py > results/eval.txt
```
