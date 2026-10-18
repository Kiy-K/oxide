# OXIDE review policy

Reviewer-facing invariants for OXIDE v2. Full policy and rule files live in
`docs/review/`; this file is the entry point and is symlinked from there.

- `docs/review/README.md` — policy, severity, reporting.
- `docs/review/api-surface.md` — public API, integrations, output contracts.
- `docs/review/storage-and-generations.md` — store ownership, generations, capture.
- `docs/review/core-and-retrieval.md` — pure core, retrieval/routing/packing, budgets.
- `docs/review/capture-and-language.md` — capture, parsing, language support, identity.
- `docs/review/evidence-and-benchmarks.md` — benchmark provenance and frozen baselines.

Architecture authority: `docs/spec/SPEC.md` and `docs/BOOTSTRAP.md`. When they
disagree with this policy, they win.
