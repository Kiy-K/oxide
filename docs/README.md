# Documentation

## Current guidance

- [SPEC](spec/SPEC.md): architecture authority; [decisions](decisions.md): standing rules, research verdicts, evaluation protocol and remaining work.
- [ADRs](adr/): decisions and proposals; a Proposed ADR is not an accepted requirement.
- [Public API](public-api.md), [parser limits and language support](parser-limits.md), [platforms](platforms.md).
- [Development workflow](agents/workflow.md) and [review policy](review/README.md).

## Evidence

Frozen gate inputs live in `phase-3/baseline-v1/` and `phase-6/` (listed in [decisions § Evaluation protocol](decisions.md#evaluation-protocol)); the FTS release notices in `fts-mirror/`. Completed phase plans, study reports and the migration audit are retired; their verdicts are in [decisions](decisions.md) and the originals are at [`013e791`](https://github.com/Kiy-K/oxide/tree/013e7910d6fe8b9df9ba753bd68e2b6468bb4e51/docs). Old v1 code is pinned at `ac985b28`, the replaced Rust/Bun runtime at `f79808a`; historical reports describe their own revision, not today's product.
