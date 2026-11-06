# Threat model: Kiy-K/oxide (OXIDE v2)

This model was built from the code at `main` (2605eea) and from the owner's answers (2026-11-06). No infrastructure repository: oxide is a local tool, distributed as the `oxide-agent` PyPI package. Its only "infrastructure" is the GitHub Actions workflows in `.github/workflows/`.

## System overview

OXIDE is a local, single-user Python library, CLI, and MCP server. It captures a repository worktree, derives a typed graph of files, modules, symbols, and relations with tree-sitter parsers, persists it in an embedded LadybugDB store under the user's data dir, and answers queries with ranked locations or budgeted source bundles. These bundles are fed to AI coding agents. The query path is offline: no embeddings, no model, no network. Network access happens in exactly two places: `oxide prepare`, which downloads the pinned FTS native extension, and the optional JEV judge, which uploads source excerpts to TypeSafe.

## Components

| Component | Code | Notes |
|---|---|---|
| **Capture** | `src/oxide/capture.py`, `src/oxide/tracking.py` | Reads the worktree. No symlink following, no special files, gitignore handling, size limits (1 MiB/file, 1 GiB total). Uses a watchfiles freshness thread. |
| **Parsers** | `src/oxide/core/languages/*`, `src/oxide/core/indexing.py`, `structural.py` | Tree-sitter derivation for Python/TS/JS/Go/Rust/Java/C/C++. Also tsconfig and Cargo.toml resolution. Handles fully untrusted repo content. |
| **Retrieval** | `src/oxide/core/retrieval.py`, `routing.py`, `navigation.py`, `packing.py`, `capsule.py`, `ordering.py` | Query terms, TreeRouter, selection and packing of source into bundles. |
| **Storage** | `src/oxide/storage/` (`store.py`, `ladybug.py`, `fts.py`), `src/oxide/_index.py` | LadybugDB generations, store lock, publication, Cypher queries, loading the FTS native extension, digest checks. |
| **API/CLI** | `src/oxide/api.py`, `models.py`, `cli.py` | Public Python API and `oxide` CLI. |
| **MCP server** | `src/oxide/mcp_server.py` | FastMCP `oxide_search` over stdio, or stateless HTTP restricted to loopback. Optional `--repo` allowlist. |
| **JEV** | `src/oxide/jev.py` | Optional, per-repo consent (`judge.json` in the store). POSTs query + ≤64 capsules (≤1 KiB source each) to `api.typesafe.ai` with `TYPESAFE_API_KEY`, from a child process. |
| **CI/Release** | `.github/workflows/*.yml`, `pyproject.toml`, `uv.lock`, `docs/fts-mirror` | CI, platforms matrix, PyPI trusted publishing (OIDC). Includes `claude.yml` (issue/comment `@claude` trigger) and `claude-code-review.yml` (`pull_request_target`, uses `CLAUDE_CODE_OAUTH_TOKEN`). |

## Trust boundaries and entry points

1. **Indexed repository content → Capture/Parsers/Storage.** Every file, `.gitignore`, `tsconfig.json`, `Cargo.toml`, path name and symlink in an indexed repo is attacker-controlled. Reached by `oxide index`, `Repository.index()`, and tracking re-indexes.
2. **Query/hints → Retrieval/Storage.** `query`, `paths`, `symbols`, `limit`, `budget`, and `repository` come from CLI args, API callers or MCP clients. The FTS query is built as a string literal and guarded by the `_TERMS_TEXT` regex (`ladybug.py` `lexical`).
3. **MCP client → MCP server.** stdio: the local agent. HTTP: anything that can reach the loopback port, including other local users/processes and browsers (DNS rebinding/CSRF; `host_origin_protection=True` is set). No authentication. The `repository` argument is checked against the `--repo` allowlist only when `--repo` is given.
4. **Bundles → AI agent.** Source and metadata from an untrusted repo end up in agent context (prompt-injection carrier). Output integrity and provenance matter.
5. **Network → local machine.** `prepare_fts` downloads a native library from GitHub releases and checks its SHA-256 (`fts.py`). It is loaded with `LOAD EXTENSION` on every use, after a re-check (`fts_unavailable`).
6. **Local machine → TypeSafe.** JEV upload only with stored consent bound to `MANIFEST_DIGEST`.
7. **Data dir.** `$OXIDE_DATA_DIR` / XDG holds stores, generations, retained source blobs, `judge.json`, `store.lock` (0600). Contents are trusted only after digest checks (fail-closed on foreign or damaged files).
8. **GitHub events → Actions.** Issue/PR comments, PRs from forks (`pull_request_target`), and tag pushes reach workflows holding `CLAUDE_CODE_OAUTH_TOKEN` and PyPI OIDC.

## Assets

- The user's source code and other local files: confidentiality (no reads outside the repo root, no exfiltration) and integrity (no writes outside the data dir).
- Code execution on the developer's machine: native extension, parsers, never running repo code or Git hooks.
- Correctness and integrity of context bundles fed to agents: provenance, no cross-repo leakage, no forged content.
- Index store integrity: generations, digests, single-writer lock.
- JEV consent: no source leaves the machine without per-repo consent. The `TYPESAFE_API_KEY` must not leak.
- Supply chain: the `oxide-agent` PyPI release (OIDC publisher, `pypi` environment), the FTS extension mirror, `CLAUDE_CODE_OAUTH_TOKEN`, and repo write access via the workflows.

## Attacker profiles (owner: all in scope)

- **Malicious repository author.** Gets a user or agent to index a crafted repo: path traversal, symlink/TOCTOU races, parser crashes or resource exhaustion, query-literal injection via indexed terms, prompt injection through bundles.
- **Untrusted MCP caller.** A local process, another local user, or a web page that reaches the loopback HTTP server. Also a compromised or prompt-injected agent on stdio requesting arbitrary `repository` paths or crafted hints.
- **Supply-chain / CI attacker.** Fork-PR author or commenter targeting `pull_request_target`/`@claude` workflows. Network attacker on the FTS download. Dependency compromise via `uv.lock`. Tag/release tampering.

## Existing controls

- Capture: `O_NOFOLLOW`, identity re-checks, special files skipped, size caps, fail-closed on unreadable `.gitignore` (`capture.py`). Never runs Git, hooks, or repo code.
- FTS: pinned per-platform SHA-256, verified on download and before every load (`fts.py`). Atomic write via `.partial` + `os.replace`.
- Lexical query literal only accepts the `_TERMS_TEXT` pattern. Other Cypher queries use parameters, except integer `limit` interpolation.
- MCP: HTTP refuses non-loopback hosts. `host_origin_protection`, `mask_error_details`, engine error text logged server-side only, input length caps (query 2000, hints 1024 ×32), `MAX_RESULTS`/`MAX_BUDGET` bounds, optional `--repo` allowlist, never indexes. `create=False` on open.
- Storage: exclusive OS file lock (0600), sealed generations with digests, fail-closed on foreign or damaged files, retained source verified by digest.
- JEV: off by default, not reachable from CLI/MCP, consent per repo bound to manifest digest, child process with minimal env and a 2 s kill deadline, key passed via stdin.
- Release: PyPI trusted publishing (OIDC, no token), `contents: read` default, actions pinned by SHA in `release.yml`.
- `claude-code-review.yml` (`pull_request_target`): the classifier runs `scripts/review_tier.py` from the trusted base. The PR head is fetched as Git data only, never checked out or executed, and SHA/repo inputs are regex-validated. Claude reads the diff artifact with Bash/Edit/Write disallowed, using `pull-requests: write`, `allowed_non_write_users: '*'` and `CLAUDE_CODE_OAUTH_TOKEN`. A fork PR's diff therefore becomes model input: prompt injection could influence inline comments and the `blocking_findings` review gate. Actions there are pinned by tag (`@v7`, `@v4`, `@v1`), not SHA.
- `claude.yml`: triggered by any `@claude` issue/comment, including from non-collaborators, subject to the action's own permission checks. It has read-only repo permissions plus `id-token: write`.

## Out of scope / accepted risk

Owner: nothing is out of scope (2026-11-06).

## Open questions

- Is `oxide mcp --http` expected to be used without `--repo` (any path on disk servable to any loopback caller)?
- Are fork PRs and outside commenters expected to trigger the Claude workflows? `allowed_non_write_users: '*'` in the review workflow suggests yes, for review only.
