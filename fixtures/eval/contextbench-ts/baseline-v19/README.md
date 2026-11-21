# baseline-v19: the crowding gate counts bodies, not items (TypeScript)

Frozen from `contextbench_ts.py run` on `0c4a649+dirty` (the working-tree change is
this selector amendment). Selector `oxide-select-greedy-v6` (was `greedy-v4` in v18);
derivation `sha256:e71d99ef…` (was `sha256:ff500e5a…`). Retrieval, router, payload and
units versions unchanged.

## What changed

The Python packer's crowding gate (issue #77) also serves TypeScript bundles. The
symptom tally now splits mixed items (test + production complete entities) by per-role
covered-source union instead of assigning the whole item to one role; the keep-check
credits the union of complete symbol source ranges (including a class recovered whole
by its merged methods). `header_end` and `module_regions` moved to `oxide.core.regions`;
`unpacked_bodies` moved to `oxide.core.notices`.

## Metrics vs baseline-v18

**0 gated regressions** (tolerance 0.02). **0 gold span moves** (gains or losses) across
all 89 tasks. 29 tasks have compat-field changes (selector/derivation version strings);
89 tasks differ in bundle content (the gate fires/accepts differently on mixed items).

Verified metric deltas (all within tolerance, most improvements):
- dev: `file_recall@1024` 0.437→0.447, `utilization@1024` 0.881→0.883.
- heldout: `file_recall@4096` 0.404→0.404 (unchanged), `utilization@256` 0.950→0.949.
- all: `file_recall@1024` 0.352→0.355, `file_recall@4096` 0.420→0.420.

Unverified splits show small utilization shifts (≤0.005) and `file_recall@1024`
0.061→0.091 (all unverified) — the gate admits more production bodies on mixed queries.

## Provenance

`oxide_source` is `0c4a649…+dirty` (reachable; the working-tree change is this
amendment). v18 is recoverable at `e883f93:fixtures/eval/contextbench-ts/baseline-v18`.
Oracles (`tasks.json`, `gold-audit.json`) byte-identical.

Command: `uv run --locked python tests/python/contextbench_ts.py run --out fixtures/eval/contextbench-ts/baseline-v19`
(after `fetch`). `manifest.json` `baseline`/`protocol` labels set by hand.
