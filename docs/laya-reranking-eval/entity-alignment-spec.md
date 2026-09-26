# Hypothesis B: labeling spec for an entity-alignment diagnostic (not run)

Status: **specification only. No Laya output exists for B.** Issue #15 §4
requires independently labeled identity / related-but-distinct / nonmatch
pairs before any model is asked about them. None exist in this
repository. This document fixes how they would be produced, so a later,
separately approved pass can run without improvising labels after seeing
model output.

## Why the labels cannot come from OXIDE, Jev or Laya

- OXIDE's `calls`, `bases`, `references` and `related_tests` relations are
  bare-name heuristics (AGENTS.md: "same heuristic tier … no scope
  analysis"). Labeling pairs with them would score Laya against the very
  ambiguity the diagnostic is meant to probe.
- Jev and Laya are the models under comparison.
- Commit co-change is a relevance signal, not an identity one.

## Oracle

Precise, compiler-grade resolution from SCIP (`scip-python` 0.6.6,
`scip-typescript` 0.4.0). `docs/scip-provider-eval` measured it:
deterministic, exact cross-file targets, and method-level
`is_implementation` links. It was rejected there as a *runtime provider*
on incremental cost. That is irrelevant for an offline label source.

SCIP symbol strings carry package version or git revision
(`scip-provider-eval`, "Symbol identity is the integration landmine").
Labels therefore join on the **descriptor path** plus OXIDE's
`(file, qualified_name)`, never on raw SCIP symbol strings.

## What a pair is

Each side of a pair is an **OXIDE symbol row** `(file, qualified_name)` at
the ContextBench base commit. It is mapped to the SCIP definition whose
range it encloses, by descriptor path.

Two cases are dropped and counted, never labeled:

- rows that map to no SCIP definition (the 15% Python / 32% TypeScript
  join misses `scip-provider-eval` measured, mostly module symbols and
  constructor naming);
- rows that map to several SCIP definitions.

## Label classes (kept distinct, never merged)

| class | definition from the oracle | examples to include |
| --- | --- | --- |
| **identity** | Two distinct OXIDE rows that map to the same SCIP definition. Import and re-export sites are ordinary SCIP *references*, not definitions. This class may therefore be small; its achievable size is measured first, and "≥ 60" is not assumed. | Re-export/alias vs. its target definition; declaration vs. the implementation it is the same symbol as, where the language makes them one symbol; the same symbol reached through two import paths |
| **related, distinct** | Different definitions linked by a SCIP relationship (`is_implementation`, `is_type_definition`, reference from one definition's body to the other). | Override vs. overridden method; test function vs. the function it calls; subclass vs. base; documentation symbol vs. definition, where the doc is itself indexed |
| **nonmatch** | A **positive** criterion, not just "no relationship found". Both sides map to resolved in-project definitions; neither side's body contains unresolved or `local`-demoted references to a symbol of the other's name; and neither file lost references to missing dependencies. SCIP is not a superset (calls through parameters become `local`, constructor calls become Type references, and TypeScript tests go unresolved without their dependencies), so "no edge" alone would mislabel real links. | **Hard negatives:** same bare name in different files (`__init__`, `login`, `fetch`), overloads, a renamed symbol vs. an unrelated homonym |

## Oracle limits carried into the labels

- **Determinism** holds per machine only: `scip-python` resolves against
  the ambient Python environment. Record the environment, and label on
  one machine.
- **The `scip-provider-eval` harness needs fixes before reuse.** That
  README lists three: the empty `relative_path` from `--target-only`,
  version-bearing symbol strings, and the TypeScript workspace flags.
- **Kind collapses.** The descriptor suffix merges class with interface
  and function with method, so class-level strata use OXIDE's own kind.

## Sampling

1. Start from repositories already indexed for ContextBench
   (`~/.cache/oxide-contextbench/repos/*@<base>`, Python and TypeScript),
   so OXIDE symbol rows exist at the same commit.
2. Pull candidate pairs from three sources:
   - OXIDE's ambiguous bare-name edges (a `calls`/`bases` name matching
     ≥ 2 definitions; `scip-provider-eval` found 5 Python and 1 TypeScript
     cases on the fixtures alone);
   - the SCIP relationships listed above;
   - random same-name pairs.
3. Stratify to at least 60 pairs per class, with provenance kept:
   `file:line` on both sides, the SCIP descriptor, and the OXIDE symbol id.
4. Hand-audit a 20% sample against the source before any model sees the
   set, and record disagreements with the oracle, not silent fixes.

## Metrics for a later model pass

- Per-class precision and recall.
- **False-merge rate:** nonmatch or related pairs predicted identity.
  This is the metric that matters.
- Missed-link rate, calibration, and abstention.
- Always against a deterministic control: exact `(file, qualified_name)`
  equality plus the existing heuristic relations.

## Guardrails

No predicted label is ever persisted, turned into a graph edge, or used
to deduplicate symbols. `Symbol::id` composition is untouched.

## Cost to build (not incurred in this pass)

- An npm install of the two indexers (about 94 MB, `scip-provider-eval`
  measured it).
- One cold index per repository, 9–13 s each.
- A decoding script (the `scip-provider-eval/harness` code can be reused).
- About 2 h of hand audit.

This is its own budgeted task and needs separate approval.
