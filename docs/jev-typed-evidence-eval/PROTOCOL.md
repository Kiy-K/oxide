# Jev typed-evidence evaluation (#40): preregistered protocol

This is the body of issue #40, from *Question* through §7, copied verbatim.
The step-1 constants are filled in below. Nothing below this section's end
changes after the commit that adds this file. Later notes are appended as
clearly marked post-hoc notes.

## Freeze-time constants (step 1)

- **Frozen commit:** `84c4d4a4a2f98a3a8fcd9f4336384b701c0a20b1` (`main`). This
  commit builds every binary, and its committed files define the exclusions.
- **Held-out clones:** cloned from upstream at 2026-10-03T12:54:23Z. HEAD SHAs:
  - `encode/httpx` `b5addb64f0161ff6bfe94c124ef76f6a1fba5254`
  - `psf/requests` `611c6162cbc4ac2020a2f91c7cfa4f3abf9bbb60`
  - `pallets/flask` `d73fa1cdcbd8b1465c151db8924ba58b1dd14e35`
  - `BurntSushi/ripgrep` `3fce3b5bb0236da2df6d99672afb8a719642eca7`
  - `clap-rs/clap` `4be56132cf7a5ef6e237409a13225a5829cb24e4`
  - `rayon-rs/rayon` `ee0a00bdb1ab039e178a215ad5712fb7fa58e58f`
  - `colinhacks/zod` `0b216ef674e297ebe41d8bf902262e56f8755822`
  - `axios/axios` `c4303eeb601d3d5914a0f425cc7539616fdc7ea7`
- **Jev 1.13.0 availability date: 2026-09-20.**
  - No training cutoff is published.
  - The earliest documentation of `jev-1.13.0` itself is TypeSafe's docs,
    fetched 2026-09-20, where it is the version behind the `jev-latest` alias
    (recorded in `docs/evals/phase-4.2-typesafe/protocol.md`).
  - TypeSafe's launch post (<https://typesafe.ai/blog/introducing-system-one-models-and-jev>,
    accessed 2026-10-03) dates Jev's first release to 2026-09-15. TypeSafe's
    cookbook still used `jev-1.12` on that date
    (<https://docs.typesafe.ai/cookbooks/sde_cascade>), so 1.13.0 itself is
    later than 2026-09-15.
  - The later of the two dates is used. It is the conservative cutoff for
    "possibly unseen by the model".
- **Expected post-date held-out N = 2**, computed by
  `scripts/excl.py --expected-n 2026-09-20` before any task was generated
  (per repo: axios 1, clap 0, flask 0, httpx 0, rayon 0, requests 0, ripgrep 0, zod 1).
  - Because N < 20, **G3d is declared not applicable**.
  - **Pretraining contamination of the held-out set is untested.** Its commits
    predate Jev 1.13.0's availability.
- **Used SHAs:** 535, from 771 committed files at the frozen commit.

---

## Question

**Does Jev provide genuinely new, transferable semantic evidence about which files matter, beyond what OXIDE's fused order and Jev's own broad judgment (J1) already capture? Or was the probe's gain a small-sample, leakage or reranking artifact?**

## Preregistration requirement

`docs/jev-typed-evidence-eval/PROTOCOL.md` is the full issue body from *Question* through §7 (including *Preregistration requirement*) copied verbatim at freeze time, with the step-1 constants filled in. Freeze steps, in order:

1. **Record the constants marked *recorded at freeze*:**
   - the **frozen commit**: the OXIDE `main` commit that builds the binaries and defines the exclusion files;
   - the 8 held-out clone HEAD SHAs and the clone date;
   - Jev 1.13.0's availability date and its source;
   - the **expected post-date held-out N**, computed before any task is generated: Σ over repos of min(10, number of post-date commits among that repo's first 20 qualifying candidates in `git log` order). Here *qualifying* means passing `make_tasks.py`'s message, skip-word, file-count/extension and exclusion filters, evaluated without worktree, index or review. The date rule is the one in §1 *Contamination strata*.

   If that N is < 20, G3d is declared **not applicable** in the frozen text, and pretraining contamination is stated as untested.
2. Write `PROTOCOL.md` and record its sha256 in `PROTOCOL.sha256`.
3. Commit, before any Jev call on evaluation data.
4. Generate the task and gold files, build the views and run the eligibility check.
5. Hash these into `results/prereg.sha256`, and commit it before the first Jev evaluation call (V5 parity is then run, also before any Jev call):
   - the exclusion-file builder, the generation driver, the ContextBench sampler, the fused dumper invocation, the row/view builder, the scorer, the evaluation script, the pack harness and research patch, and the canary states;
   - the task, gold and eligible-sample files.

After step 3:

- question wording, thresholds, combinations, scorers, file view, sampling, bootstrap and gates are not changed;
- until all scoring is complete, labels are used only by the §1 generation filters (parent gold filter, near-duplicate rule, fallback exclusion), the eligibility check, and the G5 pack-metric inputs, which are computed but not inspected. No per-task score is inspected against labels before the full run.

Any later idea is appended as a clearly marked post-hoc note and does not affect the verdict. Smoke tests and latency runs use synthetic states only, never states built from evaluation tasks.

## 1. Data (fixed)

**OXIDE build.**
- Binaries are built at the frozen commit (*recorded at freeze*), with the default native embedder `arctic-embed-xs-q` and `OXIDE_EMBED_*` unset.
- The **fused top-50** is `fused[:50]` from `examples/fusion_dump.rs` (hybrid, `expand: false`).
- `fr` is the 1-based position in that list.
- For held-out, `fusion_dump` and both pack arms run on the parent (c~1) index from `parent.sh`, never on the post-commit worktree index. For ContextBench they run on the `base_commit` index.

**Repo identity.**
- A repo is its normalized upstream `owner/name`, lowercased.
- The same upstream in held-out and in ContextBench is **one repo**. That applies to bootstrap clusters, the 15 % cap, the ≥ 12-repo count, G3b and leave-one-repo-out.

**Fresh commit-derived held-out set.**
- Unmodified: `docs/ranking-fusion-eval/scripts/make_tasks.py` (`--worktree`), `docs/alloc-utilization-eval/scripts/heldout_edit_gold.py`, and the parent-commit gold filter, near-duplicate rule and fallback exclusion as implemented in `docs/alloc-ch5-validation/scripts/{parent.sh,finalize.sh}`.
- Changed: only the binary path, the clone and worktree paths, `max_tasks` and the exclusion file.
- Repos: encode/httpx, psf/requests, pallets/flask, BurntSushi/ripgrep, clap-rs/clap, rayon-rs/rayon, colinhacks/zod and axios/axios. These are fresh upstream clones; HEAD SHAs and the clone date are *recorded at freeze*.
- Generate with `max_tasks=20` per repo, in `make_tasks.py` output order (git log, newest first). After every filter and the eligibility check, keep the **first 10 eligible tasks per repo** in that order.
- A repo with fewer than 10 contributes all it has. There is no regeneration or top-up.
- V1 is decided only by the size floors below.
- Failures inside `make_tasks.py` (worktree, index, review) are skipped by its own loop, unmodified. They are counted as attempted qualifying commits minus emitted tasks.
- A failure after it (parent index, fused dump, rows) makes the task ineligible. It is reported, with no retry.
- **Labels:** a held-out candidate is positive iff its `file#qualified_name` is in the task's parent-filtered gold symbol set (the #39 rule). Edit-locus gold lines are used only for the fallback exclusion and G5.
- Parent-commit corpora remove post-commit **source** leakage. The query is still a commit message describing the completed change.

**Exclusions** (held-out and ContextBench).
- *Used SHAs* are the union of:
  - string values of fields `commit`, `sha`, `base_commit` and `parent` that match `^[0-9a-f]{8,40}$`;
  - the 8-hex suffix of `id` or `task` values that match `-[0-9a-f]{8}$`.

  Both are taken from every committed `docs/**/*.jsonl`, `docs/**/*.jsonl.gz` and `eval-agent/**/*.jsonl` file at the frozen commit.
- A held-out commit c is excluded if c's or c~1's 8-character prefix equals that of a used SHA or of its parent.
- A used SHA's parent comes from `git rev-parse --verify -q <u>~1` in each clone, only when `<u>` resolves uniquely there. Otherwise it contributes only its own prefix.
- ContextBench excludes:
  - every `instance_id` that appears anywhere in those files (this includes the 21 pinned instances and the 6 probe instances);
  - instances with an empty `problem_statement`.

**ContextBench.**
- `EuniAI/ContextBench` `data/full.parquet`, sha256 `2f56535bdc73eb8a68bf4ebb49789d8e9cd4f219ea60df6290b85278aee61ca8` (the #39 pin).
- The query is `problem_statement`, as in `cb_prepare.py`.
- **Labels:** a candidate is positive iff it is a non-module symbol whose span overlaps a ContextBench gold line (the #32/#39 rule).

**Query classes.**
- Assigned by the frozen `classify()` in `docs/retrieval-failure-taxonomy/scripts/analyze.py`, applied to the query text before sampling.
- Two gated strata:
  - **description** = `NL behavioral description` ∪ `implementation discovery (NL)`;
  - **quoted** = `quoted literal/error text`.
- Everything else is **other**: reported, not gated.

**ContextBench sampling.**
- Strata are processed in the order description, quoted, other. Each stratum gets its own `random.Random(40)`.
- Within a stratum, adapted from `views.build()`. Where they differ, this text governs; in particular, repo lists are shuffled in sorted repo-name order, not dict order:
  - group instances by repo, and sort each repo's list by `instance_id`;
  - shuffle each repo's list, visiting repos in sorted repo-name order;
  - then shuffle the sorted repo-name list;
  - round-robin over repos in that order, taking `pop()` from each.
- Each drawn instance is indexed and checked for eligibility in draw order.
- A drawn instance whose checkout, index or candidate build fails counts as ineligible and is reported. There is no retry. Drawing continues until the stratum reaches these eligible counts: **description 25, quoted 34, other 10**, or until its pool is exhausted.

**Rows and views.**
- Candidate rows (`fr` = best fused rank in the top-50, `fscore`, `is_module`, `is_test`, `span`, `qname`, `path`, `text`, `y`) are built with the field definitions of `docs/joint-interaction-eval/scripts/prep.py`, by a builder script hashed in step 5.
- One exception to `prep.py`: `text` is read at the **indexed revision**, which is c~1 for held-out and `base_commit` for ContextBench. It is never read at the post-commit revision.
- The file view is `docs/typed-evidence-eval/scripts/views.py` `file_view()`, unchanged:
  - the first 12 distinct files by best fused rank;
  - spans in line order, with nested spans dropped;
  - a module span is used only if the file has no other candidate;
  - a file is positive iff any of its candidates is positive.
- `views.build()` sampling is not used.
- Jev sees neither OXIDE ranks nor labels.

**Eligibility.** `common.eligible()` on the file view: ≥ 5 files, ≥ 1 positive and ≥ 1 negative. Ineligible tasks are counted and reported.

**Size floors**, checked by V1 **after** V2 exclusions:
- **≥ 120 tasks** in total;
- **≥ 60 held-out** and **≥ 40 ContextBench**;
- **≥ 12 repos**, and no repo contributes more than 15 % of all tasks;
- **≥ 30 description** and **≥ 30 quoted** tasks, pooled over both sets.

**Contamination strata.**
- Jev 1.13.0's availability date: a documented training cutoff if one is published, otherwise the earliest documented availability date. *Recorded at freeze.*
- Held-out tasks are tagged by **committer date (UTC)**, compared at day granularity. A task is post-date iff its date is strictly after the recorded date.
- ContextBench (public SWE-bench issues) is reported as a separate, possibly-seen stratum.

## 2. Model and questions (fixed)

- `jev-1.13.0`, pinned. If the pin becomes unavailable or changes before scoring is complete, the run stops and the verdict is INCONCLUSIVE. A new model needs a new preregistration.
- **States** are built by `docs/typed-evidence-eval/scripts/score.py` `build_states()`, unchanged:
  - the Julia-1 tokenizer @ `a85b127321d580d65176c89ced8273f305745d85`, used only to measure truncation (no Julia inference);
  - the query cut to ≤ 192 tokens and the source to ≤ 640 tokens, with " …" appended when cut;
  - ≤ 20 declarations.
- The request shape and the wording of all seven questions (`primary`, `in_scope`, `implementation`, `relevant`, `concrete_reference`, `test`, `J1`) are copied verbatim from `score.py`. One file per state, all questions pointwise.
- **No-source control arm (`meta`).**
  - The typed state string truncated at offset `len(prefix)`, where `prefix = f"Coding task:\n{q}\n\nFile: {path}\nDeclarations: {decls}"` from the same `build_states` template.
  - The builder asserts that `state[len(prefix):]` starts with `\n\nSource:\n`. The `Source:` header and body are both removed.
  - All seven questions are asked.
  - It keeps task text, file path and declarations only.
  - It tests whether reading source code adds signal beyond path and declaration metadata. No other arm is added.
- **Single scoring pass.**
  - Each (file, arm) is scored exactly once in the first complete run. Only failed requests are retried; successful responses are never re-requested.
  - A request that still fails after its 3 retries is final. There is no later pass or resume for it.
  - Every raw response is logged.
  - Requests go out per task with concurrency 12, and the run records the per-request and per-task timings used in §7.

## 3. Scorers (fixed, no fitting)

- Controls:
  - `fused`: −(best fused rank of the file);
  - `random`: 200 draws with seeds 40…239;
  - Jev `J1`.
- Individually evaluated: `primary`, `in_scope`, `implementation`.
- **Frozen combined score `S`** = mean of P(primary), P(in_scope) and P(implementation). `S` is chosen from the probe's descriptive results and fixed here before any new data exists.
- `S_meta`: `S` computed on the no-source arm.
- With OXIDE order: `X+F` = 1/(60 + rank_X) + 1/(60 + best fused rank).
  - The AUROC of raw X and of X+F counts ties as 0.5.
  - `rank_X` (inside X+F), R@3 and the arm-B order break ties in X by fused rank.
- Reported for continuity, not gated: the probe's `T-mean`, `T-gate`, `relevant`, `concrete_reference`, `test` and `J1+F`.

## 4. Metrics and statistics

**Ranking metrics.** Within-task file AUROC with ties counted 0.5 (`common.py` `task_auc_pairs`), macro-averaged over tasks; pairwise accuracy; R@3.

**Verdict-bearing CIs: repo-clustered paired bootstrap.**
- Each resample draws repos with replacement, then draws tasks with replacement within each drawn repo, keeping that repo's task count.
- The statistic is the task-weighted mean of the per-task paired Δ over the resampled multiset.
- 10,000 resamples, 95 % percentile intervals.
- Every analysis (pooled, each subset, each leave-one-repo-out fold) uses a fresh `numpy.random.default_rng(40)`, with repos in sorted order.
- For a subset (held-out, ContextBench, a query class, the post-release stratum, the artifact-free subset, a leave-one-repo-out fold), clustering is by repo **within that subset**.
- A subset with fewer than 5 repos reports its CI, but its gate is decided on the point estimate only.
- Every CI in G1–G5 uses this bootstrap.
- An ordinary task-level bootstrap (2000 resamples, seed 32, as in #32/the probe) is also reported for continuity. **It does not decide any gate.**

**Ceiling tasks (G3c only).** A task is a ceiling task iff AUROC(S+F) = AUROC(fused) = 1.0 on it.

**Artifact-free subset.**
- Test files (`is_test`) and module-only files (every candidate is a module span) are removed from each view.
- A task stays only if it still has ≥ 1 positive and ≥ 1 negative file.
- `rank_S`, rank_J1 and `best_fr` are unchanged from the full 12-file view. They are not recomputed.

**Final-pack quality (offline research harness only).**
- The allocator fills by role, then by score descending, then by symbol id (`src/context.rs`), so changing only the order of its input is a no-op.
- **One research binary for both arms:** the frozen commit plus `docs/jev-typed-evidence-eval/research.patch` (hashed in step 5). The patch may do only two things:
  - (a) emit ch5-compatible `trace.packed` records (key, rendered-snippet line span via ch5's `render_snippet_span`, `est`, `added`). This is tracing only and changes no output.
  - (b) when the override is enabled, replace the score used in the `kept` sort key with a strictly decreasing synthetic score.
- It is never merged into production.
- **Arm A:** tracing on, override off.
- **Arm B:** same binary, tracing on, override on. The synthetic score follows this global order:
  - view candidates first: the top-50 rows of the 12 view files, by (file rank under `S+F`, then within-file `fr`, then symbol id), matched to `kept` by `file#qualified_name`. All matches are used, and duplicate matches are ordered by symbol id. View rows absent from `kept` are skipped;
  - then every other `kept` candidate (expansion and evidence candidates included), by original allocator score descending, then symbol id.
- This order applies within each role, because the sort is role-first. Roles, the relevance floor (evaluated on the **original allocator scores**), the caps and the budget (4096) are unchanged.
- Metrics follow `docs/alloc-ch5-validation/PREREGISTRATION.md` §3 exactly, computed by `docs/alloc-utilization-eval/scripts/score_alloc.py` `pack_metrics`:
  - **gold-line coverage** = delivered snippet lines ∩ gold lines ÷ gold lines;
  - **relevant tokens** = characters of delivered gold lines ÷ 4;
  - **efficiency** = relevant tokens ÷ used tokens.
- Gold lines are ContextBench gold lines for ContextBench tasks and edit-locus gold lines for held-out tasks.
- A task whose arm-A used tokens are 0 is reported and excluded from the efficiency ratio only.
- **Relative efficiency change** is the ch5 E1 definition: (mean_task eff_B − mean_task eff_A) ÷ mean_task eff_A, computed within each resample. Coverage uses the same resamples.
- **Token rise** = (mean used_B − mean used_A) ÷ mean used_A, as a point estimate.
- G5 is pooled over all eligible tasks.

## 5. Gates (all fixed before scoring)

**Validity (V).** If any V check (V1–V5) fails, no G gate is read and the verdict is INCONCLUSIVE.

- **V1:** every size floor in §1 is met after V2 exclusions.
- **V2:**
  - Retry only on HTTP 429, 5xx, timeout (60 s) and connection errors: up to 3 retries with backoff of 1 s, 2 s and 4 s, or a larger `Retry-After` when given.
  - A malformed response or a missing question key is a failure.
  - At most 2 % of all requests across both arms still fail after retries.
  - Any task with an unscored file in either arm is excluded, and at most 5 % of eligible tasks are excluded.
- **V3:**
  - Every response reports model `jev-1.13.0`.
  - 5 fixed synthetic canary states (hashed in step 5) are scored immediately before and after the run.
  - V3 fails if any canary probability differs by more than 0.02 between the two passes, or if a canary request still fails after its retries.
- **V4:** the `PROTOCOL.sha256` and `results/prereg.sha256` hashes match the files used in the run.
- **V5 (arm-A parity, run before any Jev call):** on every frozen task, the research binary with override off gives `oxide context --json` byte-identical to the unpatched frozen-commit binary. Any mismatch is INCONCLUSIVE.

**Signal gates (G).** These apply to `S`, and every CI is the repo-clustered bootstrap (§4).

The thresholds differ on purpose:
- **G1** asks whether typed decomposition beats Jev's own broad judgment. It keeps the original probe's practical-significance bar of +0.10.
- **G2** asks whether Jev adds information to OXIDE's fused order. That is an incremental-value question, judged by #32's +0.05 bar.

Gates:

- **G1, beats J1:** pooled Δ AUROC(S − J1) ≥ **+0.10**, CI lower bound > 0.
- **G2, adds to fused order:**
  - pooled Δ AUROC(S+F − fused) ≥ **+0.05**, CI lower bound > 0;
  - on the artifact-free subset, the Δ(S+F − fused) point estimate is > 0 and the CI lower bound is > 0.
- **G3, transfer** (all on Δ(S+F − fused)):
  - **a.** On held-out and on ContextBench separately: point estimate > 0 and CI lower bound > 0.
  - **b.** Among repos with ≥ 3 tasks and ≥ 1 ceiling-free task (not both AUROCs 1.0), the point estimate is > 0 in ≥ ⅔ of them. Also, for every single repo left out in turn, the pooled point estimate is > 0 and the CI lower bound is > 0.
  - **c.** For each gated query class (description, quoted), count its non-ceiling tasks:
    - **≥ 15 non-ceiling tasks:** evaluated on the non-ceiling tasks. It needs a point estimate > 0 and a CI lower bound > −0.02.
    - **< 15 non-ceiling tasks:** the class is labelled **ceiling-limited**, and no positive transfer is claimed for it. The positive-lift check is replaced by a non-harm check over **all** of the class's tasks: pooled Δ point estimate ≥ 0.
    - This rule is final and is not changed after scoring.
  - **d.** Unless declared not applicable at freeze (step 1), and if the post-date held-out stratum has ≥ 20 tasks after V2 exclusions: point estimate > 0. Otherwise it is reported as a limitation.
- **G4, source adds signal beyond metadata:** Δ AUROC(S+F − S_meta+F) ≥ **+0.03**, CI lower bound > 0.
- **G5, final-pack quality** (§4 definitions, pooled):
  - Δ gold-line coverage (B − A) ≥ **+0.03** absolute, CI lower bound > 0;
  - token rise ≤ **5 %**;
  - the CI lower bound of the relative efficiency change is ≥ **−5 %** (ch5 E1).

**Individual dimensions (descriptive; no effect on the verdict).**
- For each of `primary`, `in_scope` and `implementation`, report AUROC, Δ vs J1, Δ(X+F − fused) and the X+F pack arm.
- Their CIs are **Bonferroni-adjusted 98.33 %** intervals (percentiles 0.833 and 99.167) from the same repo-clustered bootstrap.
- A dimension may be called "carrying signal" only if its Δ(X+F − fused) 98.33 % lower bound is > 0.

## 6. Outcomes

The outcome is decided in this order:

1. **INCONCLUSIVE (validity):** any V check fails.
2. **NO TRANSFERABLE SIGNAL:** any of these fails:
   - **point estimates:** G1, G2 (pooled and artifact-free), G3a (each set), G3b (repo share and every leave-one-repo-out fold), G3c (either rule), G3d (when applicable), G4, G5 coverage;
   - **G5 limits:** token rise, or the efficiency lower bound.

   **Close the Jev typed-evidence branch.** If `J1+F` alone would meet both the G2 point and CI conditions, including the artifact-free one, record that as a descriptive broad-judgment observation. It does not reopen typed evidence.
3. **INCONCLUSIVE (power):** all point estimates and G5 limits pass, but any CI lower-bound condition fails. That covers G1, G2, G3a, G3b, G3c, G4 and G5 coverage. No production work. A rerun needs a new preregistration with a larger sample, stated before any new data is generated.
4. **TRANSFERABLE SIGNAL:** everything passes, including V1–V5. The evidence justifies **considering** a separate architecture experiment, such as an offline or distilled semantic-evidence path. That experiment needs its own issue and its own synchronous-budget gate. No production work follows from this issue.

## 7. Operational record (measured, reported; not part of the verdict)

All of this is required in the results, so that a positive verdict can be weighed honestly:

- **Latency:**
  - from the scoring run (§2): per-request p50/p95/p99 and per-task wall time p50/p95 at concurrency 12;
  - from 50 synthetic states of matched size: sequential per-request p50/p95, and the cold first request vs warm.
  - Compared with OXIDE's synchronous budget (median ≤ 250 ms per request).
- **Failure behavior:** timeout, 4xx/5xx and rate-limit rates; retry counts; behavior with the network off, measured on a synthetic state. What a local-first tool would have to do on failure is noted, not designed.
- **Network dependency:** the endpoint, region if documented, and the fact that a hosted call breaks OXIDE's offline guarantee.
- **Privacy and data retention:** TypeSafe's published terms at run time on retention, training use and logging, quoted with URL and access date. Only public open-source task text and source are sent; no private code. The API key comes from the gitignored `.env` and is never logged.
- **Cost:** requests and tokens per file and per task, the published price at run time, and projected cost per 1,000 queries at the 12-file view, for both the typed and the no-source arms.
- **Indexing:** every ContextBench and held-out corpus is indexed under the repo's memory and CPU caps (`systemd-run`). Indexing cost is reported but is not part of the Jev cost.
