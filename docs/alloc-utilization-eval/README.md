# Context allocator utilization (research)

Status: **research complete; disposition INSUFFICIENT EVIDENCE. Production unchanged.** Nothing here changes RRF
(K=60, 0.6/0.4), candidate depth 200, retrieval scoring, the embedding model or
text, the schema, symbol ids, extraction, Git/structural semantics or the
CLI/MCP/JSON contracts. Every challenger is a research-only switch
(`context::alloc_research`) in a detached `/tmp` worktree, recorded
here as `research.patch` rather than merged code. Its default state
reproduces the shipped pack byte-for-byte.

- Baseline SHA: `1abb3d7dd601aa63da06e5336b1ae8e93db75e1d` (origin/main). `src/` is
  identical to #30's `519fb02`.
- Harness: `examples/alloc_dump.rs` (pinned by `results/SHA256SUMS`). It runs the
  real `build_context_with` read-only and emits a per-stage trace.
- Embedder: shipped default `native:arctic-embed-xs-q`. The corpora and indexes
  are #30's, read-only.
- Every number is **measured** unless it is marked *inferred*.

## 1. Answer

OXIDE under-fills the 4096-token budget because **count caps, not tokens,
decide pack size.** Across 462 baseline packs (7 task sets, 3 modes, ± blast
radius), the budget never bound: there were 0 `over token budget` omissions and
utilization peaked at 83 % (median 28–51 %). A pack holds at most 5 primaries, 1
test, 2 structural-expansion neighbors, ≤4 ast-grep callers and ≤2 items per
file, each item ≤350 tokens (+12 tokens of overhead). That typically means about
7 items and about 1.4–2 k tokens.

Most of the unused budget is correct to leave unused:

- Primary-cap drops hit gold *less* often than packed items (5.2 % vs 6.9–12.8 %).
- Test-cap drops never hit gold (0 of 199 items on four sets).
- Filling the budget with every capped candidate (ch1) roughly doubles tokens
  and significantly lowers relevant-token efficiency on dev and BCD.

The one cap that loses useful evidence on the primary sets is the **per-file
diversity cap**. A per-file top-up appended after the baseline pack (ch5) raises
gold coverage on every set, at +230–300 tokens per pack.

The Codex review (§8) showed that ch5's gain depends on also bypassing the
primary cap for same-file siblings. When the role caps are honored (ch7), the
gain nearly vanishes and efficiency drops significantly on the independent sets.
ch5 was also designed on the two closest gold sets, and no efficiency margin was
fixed in advance. The disposition is therefore **INSUFFICIENT EVIDENCE** (§9).

## 2. Allocator pipeline and caps (from `src/context.rs`, `src/evidence/coordinator.rs`)

| stage | cap / guard | kind | balanced, per task (CB / held-out) |
|---|---|---|---|
| hybrid retrieval → seeds | `CONTEXT_MAX_CANDIDATES` = 16 (of fused top-200) | pool depth | 16 / 16 |
| RelationGraph expansion, top-5 seeds | `CONTEXT_EXPANSION_PER_SEED` 2, `_TOTAL` 2, score ×0.4 | budget policy | +2.0 added, **63 / 51 neighbors cap-lost** |
| ast-grep callers (mode-gated) | Balanced (2 seeds, 3 files), Quality (3, 6), Fast off; 2 per seed | budget policy | +0.7 / +1.0 (lost 1.1 / 0.5) |
| blast radius / git (opt-in) | `BLAST_RADIUS_CONTEXT_ITEMS` 4, git caps | budget policy | +0.8 / +1.0 with `--blast-radius`; git not exercised (no working-tree diff in these corpora) |
| completion (`engine.complete`) | none | correctness | — |
| dedup | module subsumed by concrete sibling; >80 % span overlap (of the *smaller* symbol) | correctness/redundancy | −3.2 module, −2.6 overlap / −2.1, −2.2 |
| role order + relevance floor | score < 0.15 × top seed | relevance policy | 0 dropped in every run |
| per-file diversity | ≤2 items per file (top item exempt) | diversity | −1.2 / −3.2 |
| role caps | 5 primaries, 1 test | diversity/budget policy | −3.7 primary, −0.7 test / −3.1, −1.4 |
| token fill | per-item cap 350 (shrink-to-fit), budget 4096 | budget | 4.6 / 2.6 packed items truncated; budget never binds |
| final pack | — | — | 7.0 items, 1.9 k tok / 6.5 items, 1.4 k tok |

The post-dedup pool holds 12.7 / 14.2 candidates. Of these, 5.6 / 7.7 go unused,
worth about 1.5 k tokens at the 350-token cap. Full-body tokens of the pool:
22.8 k / 17.0 k. Full funnels per mode: `results/*.score.txt`.

## 3. Utilization (baseline, requested 4096)

| set | n | p10 | p50 | p90 | max | ≥90 % | pool unused/task |
|---|---:|---:|---:|---:|---:|---:|---:|
| ContextBench (21 pinned) | 21 | 0.35 | 0.48 | 0.60 | 0.62 (quality 0.71) | 0 | 5.6 |
| held-out (#30, parent commits) | 63 | 0.19 | 0.34 | 0.48 | 0.54 | 0 | 7.7 |
| CB + blast / held-out + blast | 21/63 | 0.41/0.20 | 0.51/0.38 | 0.62/0.57 | 0.70/0.83 | 0 | 6.7/9.0 |
| dev / BCD / CA (research screens) | 70/37/19 | 0.11–0.24 | 0.28–0.39 | 0.48–0.51 | ≤0.73 | 0 | 6.9–8.2 |

Fast, balanced and quality are within ±0.01 of each other. Modes only change
the ast-grep stage.

## 4. Under-fill classification (balanced)

Gold is line-level:

- **CB:** human gold lines.
- **Held-out:** an *edit-locus* gold, the parent-side lines the commit touched
  inside its gold symbols (`scripts/heldout_edit_gold.py`, median 4 lines per
  task). #30's whole-symbol spans were dominated by 1,400-line classes.
- **Dev/BCD/CA:** gold-symbol spans.

Each undelivered gold line is attributed to the last stage where a *concrete*
(non-module) symbol covering it was available. Module spans cover whole files,
so they get their own bucket. Labels are per task, with priority B > D > A > C:

| label | CB (21) | held-out (63) | dev (70) | BCD (37) | CA (19) |
|---|---:|---:|---:|---:|---:|
| A candidate starvation | 6 | 19 | 17 | 12 | 4 |
| B allocator starvation (a cap dropped a gold-bearing candidate) | 8 | 25 | 17 | 4 | 4 |
| C gold fully delivered (every gold line already packed; leftover budget could add no gold) | 0 | 13 | 17 | 18 | 8 |
| D accounting/snippet (gold inside a packed item, cut by the 350 cap) | 7 | 6 | 19 | 3 | 3 |

Where gold lines go (balanced, % of all gold lines):

| bucket | CB | held-out |
|---|---:|---:|
| delivered | 11.7 | 27.7 |
| D truncated by per-item cap | 11.9 | 11.7 |
| B per-file diversity cap | 10.7 | 15.1 |
| B primary cap | 1.4 | 9.1 |
| dedup (overlap subsumption) | 3.3 | 5.3 |
| A fused rank 17–50 (beyond seed limit 16) | 17.8 | 7.9 |
| A expansion cap (neighbor covering the line was cap-lost) | 5.1 | 2.6 |
| A not in fused top-50 | 16.9 | 7.2 |
| module-only coverage (no concrete candidate) | 21.2 | 13.4 |

The relevance floor dropped nothing in any run. The test cap never dropped
gold.

## 5. Challengers (all research-only; ch1–5 append after an unchanged baseline prefix)

| arm | rule |
|---|---|
| ch0 | shipped allocator |
| ch1 safe top-up | re-admit every candidate that passed dedup/floor and was dropped by a count cap, in rank order, until the budget |
| ch2 diversity top-up | ch1 without per-file-cap drops, per-file cap still enforced |
| ch3 widen | spend leftover budget growing truncated snippets of packed items (same window, larger cap) |
| ch4 primary-cap relax | ch2 limited to primary-cap drops |
| **ch5 per-file top-up** | re-admit only per-file-cap drops, excluding tests, ≤4 items per file in total |
| ch6 per-file cap 4 in place | reallocation contrast: role caps unchanged, no prefix guarantee |
| ch7 role-capped per-file top-up | ch5, but a re-admitted primary or test must fit under the 5-primary / 1-test caps counted over the whole pack (added after the review) |

In ch5, re-admitted items are appended as a trailing block, so the pack may
exceed 5 primaries (28/63 held-out packs) and a primary may follow dependencies
and tests (32/63). After the review, admitted items are removed from `omitted`
in every top-up arm; the v3 packs are otherwise identical to v2's.

ch5 and ch6 were added **after** seeing CB and held-out per-cap hit rates.
Dev, BCD and CA were run afterwards and are the only data that did not
influence the design.

### Quality, balanced (paired Δ vs ch0, bootstrap 95 % CI, tasks up/down)

| set | arm | gold-line cov | rel tok / used tok | tokens used | other |
|---|---|---|---|---:|---|
| CB | ch0 | 0.208 | 0.074 | 1916 | R@5 0.689, R@10 0.701 |
| CB | ch1 | +0.071 [+0.025,+0.127] 7/0 | −0.003 [−0.031,+0.030] | 3396 | R@10 +0.071 |
| CB | ch3 | +0.085 [+0.019,+0.166] 6/0 | −0.010 [−0.029,+0.010] | 3634 | R@10 ±0 |
| CB | **ch5** | **+0.019 [+0.002,+0.045] 4/0** | +0.002 [−0.012,+0.016] | 2147 | R@5/R@10 ±0 |
| CB | ch6 | +0.016 [0,+0.041] 3/0 | +0.007 [−0.008,+0.023] | 1991 | |
| held-out | ch0 | 0.293 | 0.016 | 1365 | gold-sym cov 0.378 |
| held-out | ch1 | +0.199 [+0.118,+0.283] 22/0 | −0.001 [−0.007,+0.005] | 2830 | sym +0.259 |
| held-out | ch3 | +0.076 [+0.028,+0.137] 10/0 | −0.001 [−0.003,+0.002] | 2838 | sym +0.011 |
| held-out | **ch5** | **+0.053 [+0.019,+0.095] 10/0** | +0.001 [−0.002,+0.004] | 1655 | sym **+0.068 [+0.028,+0.118]** |
| held-out | ch6 | +0.030 [−0.021,+0.079] **10/2** | +0.003 [0,+0.007] | 1466 | sym +0.054, files −0.41 |
| dev | ch1 | +0.137 [+0.073,+0.208] | **−0.044 [−0.073,−0.013]** | 2507 | |
| dev | **ch5** | +0.042 [+0.010,+0.082] 7/0 | −0.010 [−0.024,+0.006] | 1389 | |
| BCD | ch1 | +0.103 [+0.022,+0.211] | **−0.034 [−0.053,−0.016]** | 3314 | |
| BCD | **ch5** | +0.049 [0,+0.130] 2/0 | −0.008 [−0.019,+0.004] | 1815 | |
| CA | **ch5** | +0.105 [0,+0.263] 2/0 | −0.002 [−0.024,+0.023] | 1737 | |

Fast and quality modes and `--blast-radius` show the same ordering
(`results/*.score.txt`). For example, held-out + blast ch5 gives +0.053
[+0.019,+0.095] line coverage and +0.001 efficiency. The ch2/ch4 rows are in the
score files: +0.10 held-out coverage at 2× ch5's added tokens.

**Hit rate of admitted items by the cap that had dropped them** (an item hits
when any delivered line is gold; `results/reason-hits.txt`):

| set | baseline items | per-file cap | primary cap | test cap |
|---|---:|---:|---:|---:|
| CB | 12.8 % | **36.0 %** | 5.2 % | 0 % |
| held-out | 6.9 % | **8.1 %** | 5.2 % | 0 % |
| dev | 11.3 % | 6.0 % | 5.4 % | 0 % |
| BCD | 8.2 % | 1.8 % | 1.5 % | 0 % |

The per-file advantage holds only on the multi-symbol edit sets (CB, held-out).
On dev and BCD, which use symbol-span gold and where BCD has single-symbol
gold, per-file drops hit less often than packed items.

### Relevant-token efficiency

ch1 and ch3 buy coverage with marginal tokens that are less relevant than the
baseline pack's. On dev and BCD, ch1's efficiency loss is significant. ch5's
efficiency change has a CI that includes 0 on every set: slightly positive on
CB and held-out, and −6 % / −12 % relative on dev / BCD (not significant).
Coverage can never regress in ch1–5 by construction, because the baseline pack
is an exact prefix (verified on every record). The regressions are therefore
per-token: 17 of 63 held-out and 4 of 21 CB tasks lose efficiency under ch5.

### Improvements and regressions (ch5; full lists in `results/ex-*-ch5.txt`)

Every admitted item carries `alloc-topup(per-file diversity cap)`. It was
blocked because two same-file items with higher scores had already filled the
file's slots.

- `flask-a411a243` "add back opening session on context push". `AppContext.session`
  (ctx.py:380–397) was blocked because `AppContext.__init__` and `.push` held
  ctx.py's slots. It carries 7 gold lines, and coverage goes 0.18 → 0.82.
- `requests-a044b020` "Move DigestAuth hash algorithms to usedforsecurity=False".
  `HTTPDigestAuth.build_digest_header` (the edited method) goes 0 → 0.60.
- `requests-d2f6bdec` "Clarify decode_unicode … iter_{content,lines}".
  `Response.iter_content` goes 0.50 → 1.00.
- CB `0eecae1e` (pytest `--runxfail` skip location). `evaluate_skip_marks` and
  `pytest_configure` in skipping.py add 43 gold lines, coverage goes
  0.26 → 0.49 and efficiency *rises* 0.215 → 0.257.
- CB `8d780f70`. `parseSitesFixesConfig` adds 21 gold lines (0 → 0.06).

These are useful because the gold edit sits in a sibling of an already-packed
symbol, the typical shape of a multi-method change.

Regressions all follow one pattern: a third same-file symbol that is a
plausible but wrong neighbor. Examples: `requests-bc7dd0fc`
`_validate_header_part` (+203 tokens, 0 gold), CB `36989b6d`
`VectorPlotter.__init__` and `swarmplot` (+582 tokens, 0 gold), and
`flask-dbd4c288` `Scaffold.__init__` (+352 tokens). The cause is candidate
quality inside a hot file, not top-up logic or snippet size. The added item is
always ranked, floor-passing and deduped.

ch6 (in place) regresses 2 held-out tasks. It displaces a lower-ranked
different-file primary and lowers file diversity (−0.41 files per pack). That is
why the append-after shape is preferred.

## 6. Latency and RSS

Across the whole sweep (min of 2 timed calls, balanced), context time was
16.3 → 16.7 ms on held-out (ch5) and 81.3 → 81.0 ms on CB. CB queries are long
problem statements, so embedding dominates there. In separate processes (5 reps
× 2 runs, alternating), the three largest corpora showed median context ms
19.0/19.3, 34.5/34.1 and 372/376 for ch0/ch5. Search alone took 17–368 ms, so
the allocator is under 1 % of a request. Peak RSS was 76–80 MB / 150–154 MB,
with the ch0/ch5 difference within run-to-run noise. ch1 and ch3 cost about
+1.5–3 ms (more snippet renders). The fixture gate on the instrumented build is
unchanged: hybrid R@5 0.909, vector 0.818, matching README.

## 7. Controls

- **Parity:** ch0 reproduces #30's production pack (item ids, `est_tokens`,
  `used_tokens`) on 21/21 CB and 63/63 held-out tasks.
- **Retrieval locality:** the seed list is identical across every mode and arm
  for every task, so no ranking change occurs.
- **Prefix:** in ch1–5 the baseline items (key, tokens, delivered span) are an
  exact prefix of the challenger pack on every record.
- **Determinism:** every traced call was re-run untraced and produced the same
  `used_tokens` (asserted).
- **Re-run agreement:** the v1 dump (arms 0–4) and the v2 dump (arms 0–6, new
  binary) agree exactly on arms 0–4.

### Post-review re-run (v3, balanced; `results/v3-*.txt`)

| set | ch5 Δ gold-line cov | ch5 Δ rel/used | ch7 Δ gold-line cov | ch7 Δ rel/used |
|---|---|---|---|---|
| CB (21) | +0.019 [+0.002,+0.045] 4/0 | +0.002 [−0.012,+0.016] | +0.027 [0,+0.071] 3/0 | +0.007 [−0.011,+0.027] |
| held-out (63) | +0.053 [+0.019,+0.095] 10/0 | +0.001 [−0.002,+0.004] | +0.012 [0,+0.034] 3/0 | −0.001 [−0.002,+0.000] |
| held-out strict (61, see finding 5) | +0.055 [+0.021,+0.094] 10/0 | +0.001 [−0.002,+0.004] | +0.013 [0,+0.035] 3/0 | −0.001 [−0.002,+0.001] |
| dev (70) | +0.042 [+0.010,+0.082] 7/0 | −0.010 [−0.024,+0.006] | +0.004 [0,+0.010] 2/0 | **−0.010 [−0.020,−0.002]** |
| BCD (37) | +0.049 [0,+0.130] 2/0 | −0.008 [−0.019,+0.004] | 0 | **−0.009 [−0.017,−0.003]** |
| CA (19) | +0.105 [0,+0.263] 2/0 | −0.002 [−0.024,+0.023] | 0 | **−0.009 [−0.018,−0.002]** |

Full-output parity: the pristine `1abb3d7` binary and the instrumented binary
(default arm) produce **byte-identical `oxide query --json`** on all 84 CB and
held-out tasks. `index.db` SHA-256 was unchanged afterwards.

## 8. Codex review (read-only; `results/codex-review.txt`)

No BLOCKER. All findings were confirmed; resolutions are below.

| # | sev | finding | resolution |
|---|---|---|---|
| 1 | MAJOR | ch5 bypasses the primary cap (28/63 held-out packs have >5 primaries) and appends primaries after dependencies/tests (32/63) | Measured the contract-honoring variant ch7: the gain nearly vanishes and efficiency drops significantly on dev, BCD and CA. ch5's gain comes from same-file primaries beyond the primary cap. This is documented in §5 and drives §9. |
| 2 | MAJOR | admitted items still listed in `omitted` (46/63, 9/21) | Fixed: admitted IDs are removed from `omitted`. v3 re-run shows 0 overlaps, with ch0/ch5 packs identical to v2's (168/168). |
| 3 | MAJOR | CIs that include 0 are not a non-inferiority test; the dev/BCD lower bounds allow material loss | Accepted. No margin was pre-registered, so efficiency non-inferiority is **not established**. |
| 4 | MAJOR | ch5 was chosen after seeing CB and held-out per-cap rates | Accepted. The held-out gain is in-sample for the rule choice; dev/BCD/CA use a different gold type. |
| 5 | MINOR | 2 held-out tasks (`httpx-7b19cd5f`, `httpx-83a85189`) use the fallback gold outside gold symbols | Strict re-score excluding them: ch5 +0.055 [+0.021,+0.094], unchanged conclusion. |
| 6 | MINOR | the baseline pack count was wrong (462, not 294); C means "gold fully delivered" | Both corrected. The count-cap diagnosis stands. |
| 7 | NOTE | locality, prefix and span accounting verified; add full-byte parity | Done: 84/84 byte-identical `oxide query --json`. |

## 9. Disposition

**INSUFFICIENT EVIDENCE.** The diagnosis is solid: the budget never binds,
count caps decide pack size, and the per-file cap drops useful same-file
siblings on multi-symbol edit tasks. No challenger passes the production gate:

- **ch1 (full top-up) and ch3 (widen): REJECT.** They double token use, and
  ch1's efficiency loss is significant on dev and BCD.
- **ch2, ch4 and ch7 (role caps kept): REJECT.** Small or zero gains, and ch7's
  efficiency loss is significant on the independent sets.
- **ch6 (in-place per-file 4): REJECT.** Regresses 2 held-out tasks and reduces
  file diversity.
- **ch5: INSUFFICIENT EVIDENCE.** Coverage improves on every set with 0
  coverage regressions, and latency/RSS are unchanged. However, its gain requires
  relaxing the primary cap for same-file siblings (it is not a pure per-file
  relaxation), it was selected on CB and held-out, and efficiency
  non-inferiority is not established.

**Smallest justified next action (needs approval):** freeze ch5's exact rule and
decide its role/order contract. For example: "same-file siblings of a packed
primary may exceed the primary cap, appended after the pack, ≤4 per file". Fix
an efficiency margin in advance (for example, ≥ −5 % relative rel/used). Then
evaluate once on fresh edit-locus tasks (new held-out commits) that played no
part in the design. Do not change `CONTEXT_MAX_PRIMARIES` or the test cap: both
were measured low-yield (primary-cap drops 1.5–5.4 % hit rate, test-cap drops
0 %).

## 10. Reproduce

```bash
git worktree add --detach /tmp/oxide-alloc-1abb3d7 1abb3d7
git -C /tmp/oxide-alloc-1abb3d7 apply docs/alloc-utilization-eval/research.patch
# (research-only: src/context.rs alloc_research switches + examples/alloc_dump.rs;
#  default arm is byte-identical to production, never merged as code)
CARGO_TARGET_DIR=<dir> cargo build --release -j 2 --example alloc_dump
S=docs/alloc-utilization-eval/scripts
eval-agent/.venv/bin/python $S/cb_gold.py            # CB human gold → cb_gold.json (main checkout)
python3 $S/heldout_edit_gold.py heldout-parent.jsonl > heldout_gold.json
$S/all2.sh                                            # run.sh per set/mode/arm → dumps/*.jsonl
python3 $S/score_alloc.py <tasks> <dump> [--cb cb_gold.json | --gold heldout_gold.json]
python3 $S/reason_hits.py … ; python3 $S/examples.py … --ch 5
```

Task files and corpora are #30's (`~/.cache/oxide-intent-eval/tasks/`). Dumps
live under `~/.cache/oxide-alloc-eval/` (machine-local).
