# Decision Study v1: judge signal and input efficiency (2026-10-24)

Outcome: **REJECT** as preregistered (dev gate fails; held-out stays sealed). A post-hoc
diagnostic, which is not part of the verdict, finds **strong, rank-independent ranking
signal** in JEV: it is lost because the seam feeds JEV's probabilities into a floor tuned
for the heuristic's scale.

| Question | Answer (Python dev, 15 tasks) |
| --- | --- |
| Does JEV judge candidates better than the heuristic? | **Yes.** Within-task AUROC 0.878 (compact) vs 0.716. Pooled 0.905, Brier 0.054, ECE 0.087. Unchanged without rank (0.861) |
| Does the preregistered integration raise packed recall? | **No.** `line_recall@4096` 0.593 vs 0.659. JEV clears the 0.4 floor on 70 of 707 candidates (heuristic: 512), so only half the budget is used |
| Can that signal raise recall at matched budget? | *Post hoc, not preregistered:* production's admitted set in JEV's order reaches **0.783** @4096 (ceiling `oracle_order` 0.792; random order of the same set 0.583) |
| Most efficient representation | **Compact** (header + ≤ 1 KiB excerpt): the only one that fits JEV's default context on every request, and the best AUROC. Canonical fails 8 of 15 requests, shared 6 |
| Does the 1 KiB query clip remove judgment evidence? | No, on dev. The clipped tails are version dumps and a closing remark, and no candidate is named only there |
| Train or deploy a dedicated judge? | **Not yet.** First preregister the order mapping (Decision Study v2) and confirm on held-out. JEV is fast (median 0.9 s) and cheap (~$0.0006 a request), so training a model is not the obvious next step |

Numbers are measured unless marked *inferred*. Nothing in `src/` changed: the seam,
`oxide-capsule-v1`, its digests and `Recorded` replay are used as committed in `fef45e5`.

## Protocol

[preregistration.md](preregistration.md) was frozen before any number. Its model section
was amended the same day to name JEV after the user recommended it, still before any
number. The user authorized sending the 9 dev corpora (8 public upstream repositories at
pinned commits, plus `fixture:py_repo`) to TypeSafe and a spend of $0.15. In total,
**120 requests, 1,521,222 billed input tokens, $0.064.**

| Script | Does |
| --- | --- |
| [scripts/study.py](scripts/study.py) | Runs the unchanged scorer on Python dev and records each `pack` call, then re-packs the same pool at 256/1,024/4,096 with every arm as a `CapsuleJudge` adapter. Writes the requests (capsules only, no gold), results, rendered-token costs and the truncation table |
| [scripts/render.py](scripts/render.py) | Renderers `ds1-{canonical,shared,compact}-v1` (+ `-norank`): pure functions of a request's capsules to a JEV `state`, with one `noul` question per candidate id |
| [scripts/adapter.py](scripts/adapter.py) | The JEV call: pinned `jev-1.14.0` manifest (`sha256:fb74d125…`), one call per request. The network call runs in a child process killed at 60 s, with no retries, and the response's `model` must match. `run` refuses unless every corpus is named in `--authorize` |
| [scripts/gate.py](scripts/gate.py) | The preregistered tables and dev-gate verdict |
| [scripts/posthoc.py](scripts/posthoc.py) | The post-hoc replays (`~order`, `~quantile`, `random~order`), with no new calls |

Rendering is separate from the contract. `adapter.py` rebuilds every `Capsule` from its
canonical JSON and asserts a byte-identical round-trip. Judgments are keyed by capsule
digest and replay through `Recorded`.

## Validity (measured)

- **Heuristic replay is production.** Run as a `CapsuleJudge` adapter, the heuristic packs
  the same spans and units as production on all 15 tasks at all budgets, and its
  `line_recall` matches the scorer's: 0.180 / 0.548 / 0.659 = baseline-v13.
- **The oracles reproduce graph-v3:** `oracle_order` 0.682 / 0.792 and `oracle_pick`
  0.776 / 0.886 @1,024 / 4,096.
- **The deadline is real.** The child process was killed at 1.00 s against a localhost
  server that never answers, and a reply naming another model is rejected
  ([results/selfcheck.txt](results/selfcheck.txt)).
- **Every JEV reply** named `jev-1.14.0`. Every non-200 was `400 max_tokens_exceeded`.

## Preregistered results ([results/gate-dev.txt](results/gate-dev.txt))

| Arm | LR@256 | LR@1024 | LR@4096 | AUROC/task | pooled | P@5 | Brier | failed requests |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| heuristic (production) | 0.180 | 0.548 | 0.659 | 0.716 | 0.724 | 0.20 | 0.235 | 0 |
| `oracle_order` (bound) | 0.454 | 0.682 | 0.792 | 0.898 | 0.921 | 0.48 | | |
| `oracle_pick` (bound) | 0.460 | 0.776 | 0.886 | 1.000 | 1.000 | 0.53 | | |
| random, mean of 5 | 0.085 | 0.249 | 0.503 | 0.516 | 0.537 | 0.07 | 0.327 | |
| canonical | 0.222 | 0.348 | 0.459 | 0.695 | 0.633 | 0.23 | 0.201 | 8 |
| canonical-norank | 0.222 | 0.348 | 0.459 | 0.686 | 0.644 | 0.23 | 0.203 | 8 |
| shared | 0.242 | 0.421 | 0.544 | 0.786 | 0.744 | 0.24 | 0.160 | 6 |
| shared-norank | 0.242 | 0.421 | 0.544 | 0.787 | 0.743 | 0.24 | 0.161 | 6 |
| **compact** (gate renderer) | 0.244 | 0.527 | 0.593 | **0.878** | **0.905** | **0.36** | **0.054** | 0 |
| compact-norank | 0.255 | 0.542 | 0.620 | 0.861 | 0.908 | 0.36 | 0.054 | 0 |
| compact-repeat | 0.244 | 0.542 | 0.608 | 0.865 | 0.908 | 0.36 | 0.053 | 0 |
| compact-reversed | 0.255 | 0.512 | 0.605 | 0.895 | 0.923 | 0.36 | 0.053 | 0 |

The failed-request counts go into AUROC through the heuristic fallback. That fallback is
why canonical and shared score below compact.

Dev gate, compact (Δ@4096 −0.066):

1. Recall ≥ +0.030: **FAIL** (−0.066).
2. Above the random and permutation controls by ≥ 0.030: pass.
3. Within-task AUROC ≥ max(0.70, heuristic + 0.05): pass (0.878 ≥ 0.766).
4. The rank-removed variant keeps ≥ 50% of the gain: **FAIL**. This follows from there
   being no gain at all. Rank-removed compact is *better* (0.620), so no rank copying.
5. Operational: no failed requests and latency is far inside the deadline. Repeatability
   (Spearman 0.989) and position bias (0.941) pass ≥ 0.90, but |ΔΔ@4096| is 0.015 and
   0.012 against ≤ 0.010: **FAIL**.

**DEV GATE: FAIL → REJECT.** Held-out was not opened. Production is unchanged.

Cost and latency (measured, billed by JEV). Compact median latency is 0.90 s (max 1.35 s)
and it bills 15,110 input tokens a request ($0.00063). Every arm stays below the 2.0 s
`DEADLINE`. JEV's billed tokens are ~20% below the tiktoken estimate.

## Why the gate failed: scale, not signal (measured)

| Arm | used @4096 | candidates ≥ floor 0.4 | gold holders ≥ floor (of 42) |
| --- | ---: | ---: | ---: |
| heuristic | 2,709 | 512 | 38 |
| compact | 1,348 | 70 | 27 |
| shared | 2,097 | 345 | 32 |

JEV returns calibrated probabilities: most candidates are unlikely to be needed, and it
says so. The seam lets a judgment replace `value`, and `INCLUDE_FLOOR` (0.4) was tuned for
the heuristic's 0.2–0.9 scale. So a calibrated judge drops 11 of 42 gold holders below
the floor and leaves half the budget unused. Its gold share of packed units triples
(0.574 vs 0.189), but recall falls.

## Post-hoc diagnostic: the signal at matched admission ([results/posthoc-dev.txt](results/posthoc-dev.txt))

**Not preregistered, seen after the verdict, never promotable.** It uses the same recorded
judgments, with no new calls. `~order` keeps production's admitted set (heuristic ≥ floor)
and orders it by JEV (0.4 + 0.59 × noul). That is graph-v3's `oracle_order` shape. The
control is the same set in a random order.

| Arm | LR@256 | LR@1024 | LR@4096 |
| --- | ---: | ---: | ---: |
| heuristic | 0.180 | 0.548 | 0.659 |
| random order of the admitted set, mean of 5 (seeds 0.537–0.684) | 0.124 | 0.416 | 0.583 |
| **compact~order** | 0.262 | 0.609 | **0.783** |
| compact-norank~order | 0.262 | 0.542 | 0.783 |
| compact-repeat~order | 0.262 | 0.609 | 0.783 |
| compact-reversed~order | 0.262 | 0.625 | 0.780 |
| shared~order | 0.249 | 0.498 | 0.677 |
| canonical~order | 0.229 | 0.482 | 0.659 |
| `oracle_order` (bound) | 0.454 | 0.682 | 0.792 |

- At 4,096, JEV's order closes 93% of the order headroom (+0.124 of +0.133). It is the
  same without rank, repeated, or reversed. Every random order of the same set is worse
  than the heuristic's.
- The gain comes from 5 tasks, and none regress: requests-5414 +0.776, -2317 +0.345,
  -1142 +0.273, -6028 +0.244, seaborn-3069 +0.227. These are graph-v3's budget-allocation
  losses.
- `~quantile` (JEV chooses which candidates get the heuristic's values) is less stable:
  0.754–0.836 across repeat/reversed.
- Caveats: these are 15 tasks with 42 positives, and the rule was chosen after seeing the
  failure. Only a preregistered held-out run can turn this into a claim.

## Input efficiency (measured)

| Renderer | tiktoken o200k median | JEV billed, mean per request | Fits JEV default context |
| --- | ---: | ---: | ---: |
| C canonical (existing capsules) | 48,214 | 9,585 (fitting requests only) | 7 / 15 |
| S shared (task once) | 34,849 | 11,368 (fitting only) | 9 / 15 (estimate said 11) |
| K compact (header + ≤ 1 KiB excerpt) | 18,768 | 15,110 | **15 / 15** |

Compact is the most efficient on every axis measured. It is the only renderer without
failures, it scores the best AUROC, and its per-request cost is ~$0.0006. The ~60 `noul`
questions take about 110 tokens each (about a third of a compact request), because every
question repeats the criteria.

## Query truncation (measured, [results/truncation.json](results/truncation.json))

3 of 15 dev queries exceed 1,024 bytes. Their tails (164, 244 and 404 bytes) are two
`requests.help` version dumps and one closing remark. No candidate (holder or not) is
named only beyond the clip. The preregistered full-query diagnostic therefore did not run.
*Inferred:* longer issues, with tracebacks after the prose, could differ. Held-out
queries were not inspected.

## Recommendation

1. **Do not integrate, configure or train** on this evidence. The preregistered rule
   rejected, and the positive result is post hoc on 15 tasks.
2. **Decision Study v2** should preregister the order mapping as *deterministic policy
   interpreting a judgment* (SPEC: models judge, code decides). That means: the heuristic
   decides admission, JEV's probability orders the admitted set, with compact renderer, no
   rank. The held-out confirmation (xarray, pytest) runs once. Report gain against random
   order of the same set.
3. Training a dedicated judge is not justified yet. If v2 confirms, JEV is already under
   the 2 s deadline and costs < $0.001 a request. A trained model would need to beat that,
   and DecisionBench labels would be the bottleneck.

## Reproduce

Corpora as in graph-v3. Run under `systemd-run --user --scope -p MemoryMax=3G -p
MemorySwapMax=0 -p CPUQuota=200%`, with `TIKTOKEN_CACHE_DIR` set and `TMPDIR` on disk.

```sh
S=docs/phase-6/decision-study-v1/scripts
O=~/Projects/oxide-eval-data/oxide-decision-study-v1/dev-controls
uv run --with tiktoken python $S/study.py $O          # requests, controls, costs
uv run python $S/adapter.py selfcheck
uv run python $S/adapter.py run $O compact compact --authorize <corpora>  # TYPESAFE_API_KEY; per arm
uv run --with tiktoken python $S/study.py $O/scored-study NAME=$O/judgments/NAME.json ...
python $S/gate.py $O/scored-study
uv run --with tiktoken python $S/posthoc.py $O/scored-posthoc NAME=$O/judgments/NAME.json ...
python $S/gate.py $O/scored-posthoc
```

The recorded judgments and raw responses are in `$O/judgments` and `$O/raw`, outside the
repository, because they sit next to request files that contain corpus source.
