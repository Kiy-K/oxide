# Independent review (#32)

**Reviewer:** a fresh Claude subagent. This experiment ran in Claude Code
cloud, where Codex was unavailable.

The reviewer:

- was launched only after every result existed;
- took no part in protocol design, feature selection, tuning or result
  generation;
- received only the issue, the frozen protocol, provenance files, scripts,
  raw results, the draft report and the relevant production code
  (`src/retrieval/engine.rs`, `src/context.rs`, `src/relations/mod.rs`);
- worked read-only, with its own recomputation in a separate scratch
  directory;
- was told not to propose retuning to rescue a failed result, and proposed
  none.

## Independent recomputation

- **Held-out macro AUROC**, by brute-force pair counting:
  - fused order 0.8483;
  - LR-ALL 0.8616;
  - Δ +0.0133 [−0.0144, +0.0391] (seed 32; a 20k-resample check gives
    [−0.013, +0.041]).
- **Pairwise accuracy:** fused 0.8716, LR-ALL 0.8750.
- **Artifact-free Δ:** −0.0589 [−0.094, −0.027].
- **Universe, labels and fused ranks** rebuilt from the dumps: 0/65 held-out
  tasks mismatch.
- Every figure matches `results/results.json` and the report.

## Findings

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | OK | **Leakage:** no feature, universe step, orientation or tie-break reads labels, the pack or omissions. The AP tie-break is sha1(key). | — |
| 2 | MINOR | **`span_missing` and imputed `span_lines` are dump artifacts.** Spans were dumped only for fused keys, so `span_missing` equals "not in the fused list". Production has real spans. LR-C and LR-ALL would fail gate condition 4. | Disclosed in README §4 and §15. Moot: both fail the AUROC margin. |
| 3 | OK | **Metrics:** AUROC (ties 0.5), AP, pooled pairwise accuracy and macro averaging are correct. | — |
| 4 | MINOR | **Band evidence (§12).** It used the artifact-free B1 cell (n = 8, below the n ≥ 10 rule) as evidence and omitted the significant full-universe in-band LR-ALL CIs. "Depends on the artifact" was too strong. | Full-band CIs added. B1 marked "not interpreted". Wording changed to "not shown to survive". |
| 5 | OK / NOTE | **Bootstrap and gate.** Paired resampling and percentile CIs are correct, and the gate implements PROTOCOL §8/§9 exactly. Condition 4 is not in code. No multiplicity correction, which is irrelevant for B. Every CI upper bound is below +0.05. | Stated that condition 4 was checked by hand, and that a +0.05 gain is excluded, not merely undetected. |
| 6 | OK / NOTE | **Tuning discipline.** `fit.py` reads only dev and masked. `best_single` was a 3-way tie of rank-equivalent features, broken by name, which the protocol did not specify. | Tie-break disclosed (README §4). |
| 7 | NOTE | **Freeze order.** The file mtimes are consistent. The scripts were not hashed at freeze, `evaluate.py` was edited after fitting (before the held-out run), and there was no run log. Pre-freeze gold-coverage counts are disclosed in PROTOCOL §0. | `results/run.log` added. Two full re-runs are byte-identical (README §18). |
| 8 | MINOR | **Undisclosed definition deviations:** `is_nested` and `name_in_query` exclude modules; `lexical_only`'s tie-break is not strictly lexicographic. The diagnostic is correctly labeled and kept out of the gate. | Deviations disclosed (README §4). |
| 9 | MINOR | **Runtime claims overstated.** Fused ranks 17–50 are truncated before the context seam. The production neighbor walk stops after 2 admissions. `pool_order` only approximates production. The universe is about 98–119 candidates, not about 120. | README §15 corrected, with inferences labeled. |
| 10 | OK, with MINOR reporting items | **Verdict.** B follows from PROTOCOL §9: held-out is evaluable (n = 61), 0/8 pass, and CB can only restrict, never rescue. | See the reporting items below. |

## Reporting items under #10, all fixed

- The partial CB gold in the repo cannot implement the line-level labels
  without bias. `reranker-eval` is file-level only; the taxonomy rows list
  lost units only. → README §11.
- Parity does not cover the neighbor or semantic lists. → README §2.
- The cross-check count was 1,389, not 926, and its script was missing.
  → `scripts/provenance_crosscheck.py` added; count corrected.
- 133 of 174 held-out gold keys lie in the universe; the "stop"
  recommendation is scoped accordingly. → README §17.
- Two unlabeled inferences. → labeled *inferred* (README §7, §14).

## Overall assessment (reviewer's words, condensed)

> The study is sound and the verdict follows from the evidence. No leakage
> from labels, pack or omissions; fitting/orientation/selection touch only
> dev+masked; metrics and paired bootstrap are correct; the gate code
> implements PROTOCOL §8/§9 faithfully; every candidate's CI upper bound is
> below the +0.05 margin, so B is a genuine exclusion, not a missed
> detection. No blockers or majors. The "stop" recommendation should be
> scoped to the tested universe and held-out (CB unevaluated).
