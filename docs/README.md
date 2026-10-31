# Documentation

## Current guidance

- [SPEC](spec/SPEC.md): architecture authority; [BOOTSTRAP](BOOTSTRAP.md): phases and exit gates.
- [ADRs](adr/): decisions and proposals; a Proposed ADR is not an accepted requirement.
- [Public API](public-api.md), [parser limits and language support](parser-limits.md), [platforms](platforms.md).
- [Development workflow](agents/workflow.md) and [review policy](review/README.md).

## Evidence, not current instructions

[Phase 6 research and frozen baselines](phase-6/README.md) separates decision summaries from reproduction inputs and raw results. [Migration audit](migration-audit/README.md) covers the Python rewrite; its [infrastructure summary](migration-audit/legacy-infrastructure.md) explains retained legacy tooling.

Historical reports describe the engine and dependencies at their recorded revision, not necessarily today's product. Old v1 code is pinned at `ac985b28`; the replaced Rust/Bun v2 runtime at `f79808a`. Use the matching tree for reproduction.

Generated standalone architecture viewers were removed as duplicate documentation, not benchmark evidence. The [Rust/SQLite diagram and source](https://github.com/Kiy-K/oxide/tree/eb43ac1070d8724d31a0be49bc7357c7132fb90f/docs/architecture) and [Python architecture viewer](https://github.com/Kiy-K/oxide/blob/eb43ac1070d8724d31a0be49bc7357c7132fb90f/docs/oxide-architecture.html) remain recoverable. Use SPEC for current architecture rather than maintaining a second generated contract.
