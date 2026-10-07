# oxide_kernel — OXIDE v2 Rust core

All OXIDE v2 Rust lives here. Architecture: `docs/spec/SPEC.md`; phases:
`docs/BOOTSTRAP.md`; boundary and runtime model:
`docs/adr/0003-kernel-runtime-control-plane-boundary.md` (Proposed). The
Bun/TypeScript control plane is root `src/`. Phase 0 is in place: workspace,
boundaries and a minimal typed service seam; no domain logic yet.

## Layout and dependency map

```text
src/  (Bun / TypeScript)                 CLI, MCP, integrations, orchestration, runtime supervision
   │  service contract v1 (contract/cases.json), provisional transport
   ▼
crates/runtime  (oxide-runtime, lib + bin)  sessions, DB/resource ownership, caches, provider clients
   │  plain Rust calls on kernel types
   ▼
crates/kernel   (oxide-kernel, lib)         repository intelligence, deterministic policy
```

| Concern (SPEC § Boundary ownership) | Home | Phase |
| --- | --- | --- |
| Domain types, TreeIndex, TreeRouter, capsules, inclusion policy, ContextPacker, KnowledgeStore *port* | `crates/kernel` | 1+ |
| Filesystem capture, DB adapter (LadybugDB), sessions, caches, scheduling, embedding/decision provider *clients*, wire types | `crates/runtime` | 1+ (wire types: now) |
| MCP, editor/agent adapters, CLI/config UX, process lifecycle, runtime supervision | root `src/` | later |
| Inference execution, weights, model serving | external runners (Ollama, llama.cpp, judge services) | never in OXIDE |

Two crates, because the kernel/runtime split must be a compilation boundary:
the kernel cannot link what the runtime links. A third crate needs a
demonstrated compilation, reuse, runtime or API reason (SPEC § System
architecture). This directory is its own Cargo workspace; Rust is pinned by
`rust-toolchain.toml` here, so run cargo from this directory (the Mise tasks
do).

### Enforced boundaries

| Check | Where | Fails when |
| --- | --- | --- |
| Kernel links no crates, has no build inputs | `crates/kernel/tests/boundary.rs` | the kernel `Cargo.toml` declares a dependency table (dev-dependencies allowed) or a `build`, `links` or `path` key |
| Kernel does no I/O | same | kernel `src/` names a std module outside a side-effect-free allowlist (grouped imports and `std as` aliases included), uses print/`dbg!`/`include!`/`#[path]`/`extern crate`; a self-test pins the known bypass shapes; `#![forbid(unsafe_code)]` closes the FFI route |
| Wire types stay out of the kernel | structural | serde/JSON live only in `crates/runtime` |
| Control plane is TS only and imports no engine | `src/boundary.test.ts` | `src/` holds anything but regular `.ts` files (symlinks included), or an import (parsed with Bun's transpiler: static, side-effect, re-export, dynamic, `require`) resolves outside `src/` other than the contract cases and isn't `bun:`/`node:`; computed `import()` and `require()` fail too |
| Contract agreement | `crates/runtime/tests/contract.rs` and `src/transport.test.ts` | the runtime's answer to a shared case differs from `contract/cases.json`, or TS cannot decode it through the real binary |

"No TS domain logic" cannot be fully machine-checked; the control plane
today only encodes requests, decodes envelopes and checks correlation.

## Service contract v1

Request: `{"version": 1, "id": "<string>", "op": "status"}`. Unknown fields
are rejected.

Response, always one JSON object:

- success: `{"version": 1, "id": "<id>", "ok": true, "result": {...}}`;
  `status` returns `{"kernel_version": "<semver>"}` from `oxide_kernel::status()`.
- error: `{"version": 1, "id": "<id>" | null, "ok": false, "error": {"code", "message"}}`.

`version` in a response is the protocol the runtime speaks. Error codes are
closed per version: `invalid_request` (not JSON/UTF-8, over 1 MiB, wrong
envelope; `id` is null when it could not be read), `unsupported_version`
(checked before the envelope shape, so a newer envelope gets this code) and
`unknown_operation`. Consumers branch on `ok` and `error.code`; `message` is
for humans. Any incompatible change bumps `PROTOCOL_VERSION` in both
`crates/runtime/src/lib.rs` and `src/contract.ts`, plus the cases.

TS distinguishes a contract error (`ok: false`, returned as data),
`ProtocolError` (reply is not a valid v1 response, or its id does not match)
and `TransportError` (spawn failure, non-zero exit or kill, non-JSON output).
An aborted call rejects with the `AbortSignal`'s reason (an `AbortError`), not
a transport error. A contract error with `id: null` (the runtime could not
read the id, e.g. an oversized request) is returned as that request's answer.

## Transport and lifecycle — provisional

Phase 0 runs **one `oxide-runtime` process per request**: request on stdin,
one response line on stdout, exit 0 for every contract response. The process
owns every resource for the life of one request (at most 1 MiB read, then
exit); an `AbortSignal` passed to `call`/`exchange` kills it. Only
`crates/runtime/src/main.rs` and `src/transport.ts` know this; `handle`/`serve`
and `src/contract.ts` are transport-free.

The intended model is a long-lived runtime supervised by Bun that owns
LadybugDB, sessions, caches and provider clients across requests. See ADR-0003
for that decision and its open questions (framing, multi-client ownership,
discovery, supervision). Do not build integrations on process-per-request.

## Commands

```bash
mise run verify       # what CI runs: rust:lint, rust:test, ts:install, ts:lint, ts:typecheck, ts:test
mise run rust:test    # kernel/runtime tests, kernel boundary checks, Rust side of the contract cases
mise run ts:test      # builds oxide-runtime, then bun test (boundary, decoder, contract cases via the binary)
```

No network or model is needed for any test.

## Open questions (none decided by Phase 0)

| Question | Needed before |
| --- | --- |
| Long-lived runtime framing, multi-client ownership/discovery, supervision | real integrations; ADR-0003 |
| One schema generating both contract type sets | when the contract grows past a few operations |
| Async vs sync kernel ports; whether the runtime needs an async executor | Phase 1 KnowledgeStore port |
| Everything in SPEC § Open questions (IDs, TreeIndex projection, LadybugDB pin, capsule schema, tokenizer, ...) | Phase 1+ as listed there |

## Evidence inventory (historical main `ac985b28`, the parent of this branch's docs)

Reference assets kept on `rewrite/v2`, not code to port and not built:
`fixtures/benchmark.json`, `fixtures/{py_repo,ts_repo}`,
`fixtures/{conformance,protocol,structural_benchmark.json}`, `tests/`
(including `tests/benchmark_gate.rs`), `examples/`, `eval-agent/benchmark/`,
`scripts/agent_eval/`, `scripts/perf.sh`, and the reports SPEC § Historical
evidence cites under `docs/`. The legacy implementation they exercise was
removed from this branch; run them against the legacy binary built from
`main`/`ac985b28` (SPEC: "An unmodified legacy binary at a pinned revision may
serve as an external historical comparator"). Machine-local eval caches are
outside the repository. Port semantic assertions, not storage/API coupling.
