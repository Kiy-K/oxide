# Decision Study v3: independent quality confirmation (2026-10-24)

Status: **PASS** (2026-10-24, run once at 18:16–18:28 UTC+7 after the user authorized
the 52 corpora and the $0.20 cap). Frozen in `bf86d3a`; the preregistration digest the
report prints is `sha256:cf4707aa…`. Full output:
[results/gate-v3.txt](results/gate-v3.txt). The v3 set is now consumed.

The policy under test is frozen and is exactly Decision Study v2's: production's
heuristic admits, JEV (`jev-1.14.0`, `ds1-compact-v1`) orders the admitted set as
`0.4 + 0.59 p`, the packer is unchanged, and any failure falls back to production for the
whole request. The one change is operational: production's 2.0 s `DEADLINE` is now
**enforced** on every call (v2 only reported latency against it). Nothing in `src/`
changes and no judge is integrated, whatever the verdict.

- Protocol: [preregistration.md](preregistration.md).
- Evaluation set: [tasks.json](tasks.json), [gold-audit.json](gold-audit.json),
  [separation.json](separation.json).

| Script | Does |
| --- | --- |
| [scripts/taskset.py](scripts/taskset.py) | Builds the set from ContextBench `full.parquet`: non-baseline repositories, task ids absent from every prior artifact, seeded order, cost rule, baseline gold audit. Runs no OXIDE code |
| [scripts/score.py](scripts/score.py) | v2's harness and arms, unchanged, over `--split dev` or the sealed `--split v3` (refused until the freeze is committed). `--requests-only` writes requests without computing any arm. Writes request digests and the gold-leak check |
| [scripts/call.py](scripts/call.py) | JEV calls over v1's adapter with the 2.0 s deadline enforced, the spending cap per request, and corpus authorization. `estimate` sends nothing; `selfcheck` is localhost only |
| [scripts/report.py](scripts/report.py) | Validity assertions, end-to-end and conditional tables, operations, and the preregistered gates |

## Evaluation set (measured)

52 tasks, one corpus each, from 8 repositories never used by the Python engine or any
decision study: ansible 8, openlibrary 8, qutebrowser 8 (SWE-bench Pro), transformers 8,
keras 8, langchain 8, yt-dlp 3, AutoGPT 1 (SWE-PolyBench).

- **Separation.** Repositories, task ids and corpus names are disjoint from the baseline
  (asserted). No task id appears in any prior artifact: a scan of every
  JSON/Markdown/text/log/Python file under `oxide-eval-data`, `docs`, `tests`, `scripts`
  and `eval-agent` excluded 303 of 475 candidate rows, including every
  SWE-bench-Verified repository. No `*.py` file of 512 bytes or more in a v3 corpus is
  byte-identical to one in a baseline corpus.
- **Limit.** Each v3 repository had other tasks in legacy Rust-era studies. The tasks
  are new everywhere; the repositories are new to this engine and to every decision study.
- **Gold isolation.** Capsules carry no gold or outcome field. `score.py` checks every
  request's keys, capsule fields and query, and that no gold-content digest, instance
  id or task id appears in any body that would be sent.
- **Cost rule.** At most 3,000 `*.py` files; 4 walked corpora exceeded it. Feasibility:
  a 1,550-file ansible corpus (not in the set) indexed in 4.8 s at 281 MB RSS
  (index only, no query).
- **Query clip.** 40 of 52 problem statements exceed the capsule's 1,024-byte query
  clip (median 1,509 bytes). That is production's behaviour, so it stays. *Inferred*:
  long SWE-bench Pro statements put requirements after the clip, which may weaken the
  judge's evidence on those tasks.

## Validity before the freeze (measured)

- **This code reproduces v2's dev gate.** `score.py --split dev` with v2's recorded dev
  judgments, then v2's `verdict.py`, gives output identical to
  `decision-study-v2/results/gate-dev.txt` (`compact~order` 0.7829 @4096,
  `DEV GATE: PASS`). The only difference is v2's extra `v1-compact` replay arm, which was
  not passed ([results/validity-dev.txt](results/validity-dev.txt)).
- **The report and its fallback assertions run.** `report.py` on the same spent dev
  data, with two primary requests replaced by synthetic 529s, asserts that both packed
  production exactly. It prints both quality views and fails O1 (2/15) as designed
  ([results/report-dryrun-dev.txt](results/report-dryrun-dev.txt); not a verdict).

## Deadline (measured, [results/selfcheck.txt](results/selfcheck.txt))

On localhost, `call.py selfcheck` checks the following:

- A server that sends a valid answer 2.5 s after the request: the child is killed at
  2.00 s, the reply is never read, the request records no judgments, and v2's hybrid
  returns the heuristic for the whole request. The server's late write happened (it was
  observed) and changed nothing.
- Production's seam (`CapsuleJudge` and `packing.judged`) with a live adapter against
  that server: it decides the heuristic in 2.00 s, and that decision is unchanged after
  the late answer arrives.
- A hung server is killed at 2.0 s. A 529, a wrong model and a partial answer each
  judge nothing. An on-time answer judges, with values `0.4 + 0.59 p`.
- Spending: an arm whose estimate exceeds the remaining cap is refused before it sends
  anything. A request whose estimate would cross the cap is not sent. A request that
  reports no billed tokens is charged its estimate.

## Result (measured, [results/gate-v3.txt](results/gate-v3.txt))

Validity checks passed before any number was printed: the bytes sent match the scored
requests in both arms, the leak check holds, and every fallback packs production exactly.

| Arm (end to end, 52 tasks) | LR@256 | LR@1024 | LR@4096 |
| --- | --- | --- | --- |
| `heuristic` (production) | 0.0201 | 0.1779 | 0.3161 |
| `random~order` (mean of 5) | 0.0058 | 0.0505 | 0.1405 |
| `compact~order` (primary) | 0.0334 | 0.3026 | **0.4755** |
| `compact-repeat~order` | 0.0318 | 0.2977 | 0.4686 |
| `oracle_order` (ceiling, not gated) | 0.0458 | 0.3064 | 0.5022 |

- **Quality, end to end.** Primary Δ@4096 **+0.1594**: task bootstrap 95% CI +0.103 to
  +0.224, repository-cluster CI +0.075 to +0.250. At 4,096: 33 tasks win, 0 lose, 19
  tie. Δ is positive at every budget (+0.013, +0.125). Leave-one-repository-out stays
  between +0.117 (without keras) and +0.186 (without langchain). By repository: keras
  +0.391; transformers, qutebrowser and openlibrary +0.16 to +0.19; ansible +0.102;
  langchain +0.011; AutoGPT (1 task) +0.014; yt-dlp (3 tasks) 0.
- **Per-task regressions** happen only at smaller budgets: 9 task-budget pairs for the
  primary and 8 for the repeat. The worst is `transformers-28071` at 256 (−0.375); it
  gains +0.562 at 4,096.
- **Quality on answered requests only.** The primary answered 52/52, so this view is the
  same as end to end. The repeat answered 50/52: Δ@4096 +0.1586 (CI +0.100 to +0.225),
  against a heuristic of 0.3226 on those tasks.
- **Reproducibility.** The repeat's end-to-end Δ@4096 is +0.1525. Median Spearman of
  the two arms' admitted orders is 0.989, and the difference at 4,096 is +0.0002.
- **Operations.**
  - Primary: 52/52 answered with status 200; p50 1.01 s, p95 1.19 s, max 1.42 s.
  - Repeat: 50 answered with 200; 2 openlibrary requests were killed at the 2.0 s
    deadline and fell back. That is a 3.8% failure rate (Wilson 95% interval 1.1% to
    13.0%); p95 1.45 s over sent requests.
  - Neither arm returned a 529, a wrong model or a malformed reply.
  - About 24.3k billed input tokens per request.
- **Cost.** $0.1060 in total ($0.0530 per arm), against the $0.20 cap.
- **Gates.** Q1–Q5 and O1–O3 all pass. **VERDICT: PASS.**

Limitations (operational and evidential):

- **The failure rate is small-sample.** No primary request failed, but the upper Wilson
  bound is 6.9%, and the repeat's bound reaches 13%. The deadline has little headroom
  at this request size: the repeat's two kills came at 2.01 s, and its p95 is 1.45 s.
  v2's 529 outages did not recur in this one ~2-minute window. The reliability study's
  retry and breaker policies remain untested.
- **The gain is uneven across repositories.** Langchain and yt-dlp show almost none,
  and keras drives part of the mean (the gain is +0.117 without it).
- **The judge sees clipped queries.** 40 of 52 issue texts are cut at 1,024 bytes.
- **The repositories are not new to the project.** Legacy Rust-era studies used other
  tasks from them.
- **One model and one snapshot** (`jev-1.14.0`), on Python only.

Nothing in `src/` changed. Per the preregistration, a PASS allows proposing an ADR for
an optional judge adapter, as a separate step; none is drafted here.

## Reproduce

Run under `systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0 -p
CPUQuota=200%`, with `TIKTOKEN_CACHE_DIR` set and `TMPDIR` on disk. Requests, raw
responses and the ledger stay in `$D`, outside the repository, because they contain
corpus source.

```sh
S=docs/phase-6/decision-study-v3/scripts D=~/Projects/oxide-eval-data/oxide-decision-study-v3
P=~/Projects/oxide-eval-data/oxide-jev-eval/contextbench-full.parquet
uv run --with pyarrow python $S/taskset.py $P                         # the set (pre-freeze)
uv run --with tiktoken python $S/call.py selfcheck
uv run --with tiktoken python $S/score.py $D/v3 --split v3 --requests-only
uv run --with tiktoken python $S/call.py estimate $D/v3
# after authorization (TYPESAFE_API_KEY); run once 2026-10-24:
uv run --with tiktoken python $S/call.py run $D/v3 compact --authorize <corpora> --ledger $D/ledger.json
uv run --with tiktoken python $S/call.py run $D/v3 compact-repeat --authorize <corpora> \
  --ledger $D/ledger.json --sent $D/v3/judgments/compact.json
uv run --with tiktoken python $S/score.py $D/v3-scored compact=$D/v3/judgments/compact.json \
  compact-repeat=$D/v3/judgments/compact-repeat.json --split v3
python $S/report.py $D/v3-scored --sent $D/v3 --ledger $D/ledger.json > docs/phase-6/decision-study-v3/results/gate-v3.txt
```
