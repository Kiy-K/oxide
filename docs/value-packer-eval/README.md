# Value packer on flat candidates (dev, descriptive)

Status: **descriptive dev result, not a verdict.** The TreePacker's selection rules do not beat the
capless flat selector (B). Doc demotion is the one consistent gain, and the metric favours it by
construction. Production is unchanged. Every number is **measured** unless marked *inferred*.

## Why

TreeRouter Phase 0 found no router beating flat file recall (`feat/treeindex`,
`docs/treeindex-eval/phase0/README.md`). The open lead was the selection seam: TreePacker's rules
applied to production's own candidate pool, with routing left to flat retrieval. The rules came from
#43's B-vs-A analysis on #43's tasks. This set is #42's 149 tasks, which that design never saw.

## Setup

- Binary: frozen `96abae8` + `research.patch` (#42's `research.patch` plus three arms). Built in
  `~/.cache/oxide-valuepack-eval/target`. Nothing in `src/` on any branch changes.
- Tasks, indexes and metrics are #42's: 149 tasks (80 held-out, 69 ContextBench, 51 repos), the
  local arctic-embed-xs indexes, line-level gold coverage, and the repo-clustered bootstrap.
- Arms (B, P, PND, PKD are token-matched to A's `used_tokens`):
  - **A**: shipped selector.
  - **B**: no primary or per-file caps, production role-then-score order.
  - **P**, value packer, all three rules:
    - offer order by value per token: score × 1/√(k+1) per file, divided by rendered tokens at the
      350 cap;
    - dependencies right after the first 5 items;
    - files under `test/`, `tests/`, `__tests__/`, `spec(s)/` count as tests (1-test cap);
    - docs and `examples/`, `docs/`, `vendor/`, `third_party/`, `benches/` code last.
  - **PND**: P without the dependency rule.
  - **PKD**: P without doc demotion.
- Harness check: arms A and B reproduce #42's amended-run traces exactly (149/149 each). Every arm
  is identical across its two repeats.

## Results (`results/result.txt`, pooled, repo-clustered 95 % CI)

| comparison | Δ coverage | up/down | rel/used |
|---|---|---|---|
| B vs A (#42's cap removal, reproduced) | +0.0527 [+0.0137, +0.0975] | 30/7 | +13.5 % |
| P vs A | +0.0456 [−0.0312, +0.1229] | 33/25 | +1.1 % |
| **P vs B** | −0.0071 [−0.0716, +0.0608] | 23/32 | −10.9 % |
| PND vs B | +0.0097 [−0.0452, +0.0705] | 23/25 | −8.1 % |
| P vs PND (dependency rule) | **−0.0167 [−0.0454, −0.0010]** | 3/14 | −3.1 % |
| P vs PKD (doc demotion) | **+0.0214 [+0.0022, +0.0587]** | 16/1 | +11.6 % |

## Reading

- **The value-per-token order loses to B's role-then-score order.** It packs more, smaller items
  (9.4 vs 8.1) and regresses more tasks (P vs B 23/32). *Inferred:* dividing by tokens favours tiny
  symbols, which dilutes the pack.
- **The dependency rule is refuted here.** #43's 8 regressions suggested it, but on this set
  protecting dependencies costs coverage (3 up / 14 down). The #43 pattern did not generalize.
- **Doc demotion helps on this metric** (16/1 vs PKD). Edit-locus gold almost never lands in docs, so
  that gain is partly by construction. Whether docs help an agent needs another judge (an agent run
  or a model).
- Not measured: B with only doc demotion and the wider test dirs. It would be chosen after seeing
  these results, so it belongs on a fresh preregistered set, not here.

## Reproduce

```bash
W=~/.cache/oxide-valuepack-eval/wt
git worktree add --detach $W 96abae8 && git -C $W apply docs/value-packer-eval/research.patch
CARGO_TARGET_DIR=~/.cache/oxide-valuepack-eval/target cargo build --release -j 2 --bin oxide --example treeselect_pack
<venv>/python docs/value-packer-eval/scripts/run.py arms && <venv>/python docs/value-packer-eval/scripts/score.py
```
