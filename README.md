<p align="center">
  <img src="logo.png" alt="OXIDE" width="140">
</p>

<h1 align="center">OXIDE</h1>
<p align="center"><strong>Local-first Context Engine for Agents</strong></p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#supported-languages">Languages</a> ·
  <a href="#evidence">Evidence</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="LICENSE">License</a>
</p>

---

OXIDE indexes your codebase and gives coding agents a small, task-relevant
set of definitions, callers, implementations, tests, and related code —
instead of making them search the repository blindly. It runs entirely on
your machine, updates incrementally as you edit, and speaks to any agent
over the CLI, JSON, or MCP.

> Maximize coding-agent utility per context token, not retrieval volume.

- **Local-first** — source code and the index never leave your machine.
- **Incremental** — unchanged files are not reparsed; unchanged symbols reuse embeddings.
- **Agent-neutral** — CLI, `--json`, or MCP; use whatever your agent already speaks.
- **Bounded, token-aware context** — `query` returns a working set sized to the budget you choose.
- **Lexical + semantic + structural retrieval** — BM25 and embeddings fused, then expanded along real code relationships.
- **Blast-radius evidence** — an optional, bounded answer to "what else does this touch?"
- **Broad language support** — ten languages today, more on the way.

## Demo

```text
$ oxide index

◇ Scanning files... 547 files
◇ Parsing source... 547/547
◇ Indexing codebase... 547/547
◇ Embedding symbols... 7,155/7,155
◇ Finalizing... done

✓ Indexed 547 files · 7,155 symbols in 5.7s
  Done!
```

*(Real numbers from indexing [Tokio](https://github.com/tokio-rs/tokio) with
the offline hashed embedder — see [Evidence](#evidence).)*

```bash
oxide query "Where is authentication handled?"
oxide search AuthService
oxide search AuthService --blast-radius
```

`query` and `search` return real source code for an agent to read — they
never call an LLM or generate an answer themselves:

```text
$ oxide query "Where is retry behavior implemented?" --budget-tokens 800
Relevant code for "Where is retry behavior implemented?"

oxidepy/notifiers.py
  notify_after_final_attempt  31–33  function  ~70 tok
    ↳ lexical, semantic, calls RetryPolicy.should_retry
  Notifier.notify             9–10   method    ~32 tok
    ↳ lexical, semantic

oxidepy/retry.py
  RetryPolicy.should_retry    31–37  method    ~119 tok
    ↳ lexical, semantic
 ...

6 items · 429 / 800 context tokens · embedder hashed-bow-256
```

Output is colored on a terminal and plain everywhere else (`--json`, pipes,
`NO_COLOR`, `--color never`) — every state is readable without color. This
example is reproducible from OXIDE's committed Python fixture with the
offline hashed embedder.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/Kiy-K/oxide/main/install.sh | sh
```

The installer downloads a verified release binary for your platform. See
[Development](#development--contributing) for build-from-source and
version-pinned installs.

## Quick start

```bash
oxide index                               # create or incrementally update the index
oxide query "fix token refresh"           # build a token-budgeted working set
oxide search RetryPolicy                  # find ranked symbol matches
oxide search RetryPolicy --blast-radius   # ... plus who would be affected
oxide status                              # check freshness and provider compatibility
oxide watch                               # update the index as files change
oxide review --diff HEAD~1                # collect context for a git diff
oxide install                             # connect supported coding agents over MCP
oxide query "fix token refresh" --git     # also weigh the current diff's changed symbols
```

`query` is for a question or coding task; `search` is for an identifier or
expression. Reach for `--mode literal` instead of the default hybrid ranking
when you know the *exact* string you're after — a path, an error message, a
config value, a partial identifier — rather than a name or phrase you want
ranked by relevance: it's a deterministic substring scan over all repository
text (not just indexed source files), needs no index, and can't miss a match
the way a ranked top-K can.

```bash
oxide search "TODO: fix flaky_widget" --mode literal   # exact substring, any text file
```

Add `--json` to any command for automation:

```bash
oxide status --json
oxide query "fix refresh-token validation" --budget-tokens 4096 --json
oxide search RefreshToken --json
```

Every evidence item includes its repository-relative path, qualified name,
line range, score, selection reasons, and source snippet. Context packs also
report their estimated token use and why candidates were omitted.

## Why OXIDE

Coding agents do better work when they see the right evidence: the
definition being changed, its callers, the tests that constrain it, and
enough surrounding code to understand the contract. More retrieved code is
often worse — it spends tokens, dilutes the useful evidence, and leaves less
room for reasoning and generation.

Most retrieval systems are evaluated on how much they can find. OXIDE is
designed around how much *useful* code it can fit inside a fixed context
budget. It does not wrap an LLM, edit code, or choose an agent — it prepares
a small, inspectable working set before the agent starts reading and
editing.

### Blast radius: what else does this touch?

`--blast-radius` adds one question to `query` or `search`: if I change this,
what else is involved? It reports a small, bounded neighborhood around the
top matches — direct callers, implementors and subtypes, related tests, and
one tightly capped hop past the direct callers.

```text
src/store.py:1–3  TokenStore  class
  blast radius
    · src/handler1.py:4  handler1 calls this
    · src/cache.py:4     MemoryStore implements this
    · src/router.py:4    route reaches this
```

It is deliberately small rather than complete: at most twelve members across
at most eight files, drawn from at most three seeds. On `search` it is
attached after ranking and never reorders or rescores a result; on `query`
its members compete for the same token budget as everything else in the
pack. With the flag absent, no lookup runs and output is byte-identical to a
build without the feature.

Blast radius is built on precomputed structural relations matched on bare
names, without type resolution. Treat a member as a lead worth reading, not
as proof of impact.

### Git-aware context

`--git` adds evidence from the current diff and recent history: symbols
touched by uncommitted changes, recently-committed symbols, and files that
tend to change together. It's opt-in — with the flag absent, output is
byte-identical to a build without the feature.

```bash
oxide query "fix token refresh" --git
```

Known limitations: untracked files are excluded (git only sees what's been
`git add`ed), a pure rename with no content change produces no diff hunk
and so is invisible to this evidence, and co-change is a heuristic, not a
guarantee of relatedness. Git-enabled retrieval quality has not been
validated by any agent-outcome benchmark — only correctness/plumbing tests
exist today.

Git-aware evidence is evaluated for correctness and determinism; no
agent-level outcome improvement is claimed.

## Supported languages

| Language | Extensions | Indexed definitions |
|---|---|---|
| Python | `.py` | modules, classes, functions, methods, constants |
| TypeScript / TSX | `.ts`, `.tsx` | functions, classes, methods, interfaces, type aliases, enums, exported declarations |
| JavaScript / JSX | `.js`, `.jsx`, `.mjs`, `.cjs` | functions, classes, methods, arrow-function assignments, exported declarations, JSX component usage |
| Rust | `.rs` | modules, structs, enums, traits, impls, functions, methods |
| Go | `.go` | packages, structs, interfaces, functions, methods, constants |
| Java | `.java` | classes, interfaces, enums, records, annotation types, methods, constructors |
| Ruby | `.rb`, `.rake`, `.gemspec`, `Rakefile`, `Gemfile` | classes, modules, methods, functions, constants |
| PHP | `.php`, `.phtml` | classes, interfaces, traits, enums, namespaces, methods, functions, constants |
| C | `.c`, `.h` | functions, structs, unions, enums, typedefs, constants |
| C++ | `.cc`, `.cpp`, `.cxx`, `.hh`, `.hpp`, `.hxx`, `.h` | classes, structs, enums, namespaces, functions |

Every language goes through the same pipeline — a Tree-sitter grammar plus a
`.scm` query file — so adding one is mostly a grammar and a query set, not a
bespoke extractor. Java and C++ method/constructor names carry a normalized
parameter-type signature (e.g. `Store.get(String,String)`), so overloads
stay distinct symbols instead of collapsing into one.

OXIDE also extracts imports, references, calls, inheritance, and containment
where the language grammar exposes them. See the
[language coverage matrix](docs/language-support/README.md) for exact
behavior and known gaps per language — the committed conformance fixtures
are the source of truth.

### Documentation indexing

Markdown (`.md`) is indexed too, but not as an eleventh programming
language: a doc file becomes one whole-file symbol (no heading/section
parsing, no calls or inheritance — there's no AST to extract them from), so
it's reachable by the same lexical, semantic, and incremental-update
machinery every other file already uses — no new indexing code, though it
still costs the same one embedding and one set of lexical postings any
symbol costs. This makes a README's own prose ("where is X handled", "why
does Y work this way")
findable through `oxide query`/`search` alongside the code it describes,
without requiring `--mode literal`. Documentation still respects
`.gitignore` and the same generated/vendor/cache denylist as code, plus one
extra, selective cap: a `.md` file over 64 KB is skipped (`scanner.rs`'s
`MAX_MARKDOWN_BYTES`) — sized from a survey of this repo's own docs, so
ordinary READMEs and design notes are indexed while a sprawling changelog
or an accidentally-vendored doc is not.

## Coding-agent integrations

`oxide install` detects supported agents, shows the exact configuration
change, and asks before writing it. It currently integrates with:

- Claude Code
- Codex
- OpenCode
- Antigravity CLI

```bash
oxide install                              # interactive detection and approval
oxide install --agent codex --dry-run      # preview without writing
oxide install --agent claude --agent codex --yes
oxide uninstall --agent codex
```

The integration runs `oxide mcp` over stdio. Any other tool can speak to the
same MCP server, or call the CLI's JSON interface directly. See
[`docs/agent-usage-policy.md`](docs/agent-usage-policy.md) for the canonical
guidance on how an agent should use OXIDE.

## Evidence

OXIDE keeps performance and retrieval claims reproducible and scoped. A
passing fixture is a regression check, not proof that OXIDE beats another
tool.

### Committed retrieval fixture

Run the same gate used in CI:

```bash
cargo build --release -j 2
env -u OXIDE_EMBED_URL -u OXIDE_EMBED_MODEL \
  OXIDE_EMBED_NATIVE=hashed ./target/release/oxide eval \
  --config fixtures/benchmark.json
```

Current result on the 11-query committed fixture:

| Mode | Recall@5 | Precision@5 |
|---|---:|---:|
| Vector only | 0.818 | 0.182 |
| Hybrid | 0.909 | 0.200 |

`tests/benchmark_gate.rs` fails if hybrid retrieval falls below vector-only
recall on this fixture. It does not establish general retrieval quality.

### Real-repository indexing

Measured per repository at pinned revisions — medians of three release-build
runs on an Intel i7-13620H laptop under `nice -n 10`, using the offline
hashed embedder. These describe indexing behavior, not retrieval quality.

| Repository | Language | Files | Symbols | Cold index | No-change update | One-file edit |
|---|---|---:|---:|---:|---:|---:|
| Flask | Python | 80 | 1,755 | 865 ms | 31 ms | 180 ms |
| Dark Reader | TypeScript / TSX | 197 | 1,356 | 1,021 ms | 29 ms | 190 ms |
| Tokio | Rust | 547 | 7,155 | 5,666 ms | 131 ms | 315 ms |
| Gin | Go | 99 | 2,076 | 1,338 ms | 38 ms | 520 ms |
| Tailwind CSS | JavaScript | 133 | 268 | 210 ms | 10 ms | 100 ms |
| Gson | Java | 264 | 4,388 | 2,010 ms | 80 ms | 270 ms |

Ruby, PHP, C, and C++ support is validated by committed conformance
fixtures but not yet included in this table. The Tailwind CSS and Gson rows
were measured in a later session; compare them against each other, not
against the earlier rows in absolute terms.

Exact revisions, commands, memory use, index sizes, and unsupported
constructs are in the
[language support report](docs/language-support/README.md#performance).

### External-task evidence

The research notes include a pinned 21-task ContextBench retrieval sample
and a four-task same-agent study. The retrieval sample is directional
because it is small and provider-specific. In the same-agent study, no
OXIDE condition beat the stock agent on task outcomes. See
[Context engineering notes](docs/context-engineering-notes.md#evaluation-pivot-contextbench-official)
for the protocol, results, and caveats.

> [!IMPORTANT]
> The controlled, agent-level benchmark is not finalized yet. The question
> it needs to answer: **can the same coding agent solve repository tasks
> with fewer context tokens, fewer searches, and less waiting when using
> OXIDE?** OXIDE does not claim to outperform other developer tools here.
> When the study is finalized, this note will be replaced with pinned tool
> versions, identical corpora, exact commands, hardware, complete results,
> and the cases where OXIDE loses.

## How it works

```text
repository
    ↓
incremental symbol index
    ↓
lexical + semantic retrieval
    ↓
structural relations + optional blast radius
    ↓
bounded context allocator
    ↓
coding agent
```

- [Tree-sitter](https://tree-sitter.github.io/tree-sitter/) extracts symbols
  and syntax-level relationships for each language.
- SQLite stores files, symbols, embeddings, lexical postings, and
  precomputed structural relations in `<repo>/.oxide/index.db`.
- BM25 and embedding similarity run together; reciprocal rank fusion
  combines their ranked results.
- Parent, child, import, reference, caller, implementor, and test
  relationships expand strong direct hits without displacing them.
- The allocator removes overlaps, applies diversity caps, and stops at the
  requested token budget.

Stable symbol IDs and content hashes let OXIDE skip unchanged files and
reuse unchanged embeddings, so a single-file edit re-embeds only what
changed. Read commands use a consistent SQLite snapshot even while another
process is updating the index.

## Privacy and offline use

OXIDE runs locally and phones home to nothing by default. Your code, paths,
queries, index, and MCP traffic never leave the machine. The only optional
telemetry is crash reporting, off unless you set `OXIDE_TELEMETRY=1`, and
even then it sends a panic's stack trace and platform details with no
personal identifiers — see [`TELEMETRY.md`](TELEMETRY.md) for exactly what
is and is not collected.

The default embedding provider, **arctic-embed-xs-q** (a 384-dimensional
int8 ONNX model, ~23 MB), downloads its weights from Hugging Face the first
time a semantic command runs, then caches them under `$HF_HOME`. For a
zero-download, fully air-gapped setup, use the deterministic hashed
embedder instead:

```bash
env -u OXIDE_EMBED_URL OXIDE_EMBED_NATIVE=hashed oxide index
env -u OXIDE_EMBED_URL OXIDE_EMBED_NATIVE=hashed \
  oxide query "Where is authentication handled?"
```

An OpenAI-compatible HTTP embedding endpoint also works, taking precedence
over the built-in native model:

```bash
export OXIDE_EMBED_URL=http://127.0.0.1:8191/v1/embeddings
export OXIDE_EMBED_MODEL=my-model-label
oxide index
```

Changing providers clears and recomputes stored vectors, since embedding
spaces can't be mixed; lexical search stays available throughout
(`oxide search AuthService --mode lexical`).

### Remote embedding providers

OXIDE also supports Voyage AI, Jina AI, and any OpenAI-compatible remote
embedding endpoint as an **explicit, consent-gated opt-in** (`oxide setup`).
Unlike the local/offline paths above, a configured remote provider sends
each symbol's text (never a whole file) to that provider's API over the
network — this is the one way OXIDE's default "your code never leaves the
machine" guarantee changes, and only if you deliberately configure it.

## Limitations

- OXIDE supports ten languages today. C#, Swift, Kotlin, and Assembly are
  not indexed.
- Reference and structural relations use syntax and identifier-name
  matching, not compiler-grade name or type resolution. Ambiguous names can
  produce false positives or miss a cross-file relationship.
- The vector path is a brute-force scan. It is comfortable around 50,000
  symbols; larger repositories need measurement.
- Token counts use a `chars / 4` estimate rather than the target model's
  exact tokenizer.
- `review` assembles relevant context for a diff. It does not produce a
  review verdict.
- v0.1 ships prebuilt artifacts for x86-64 Linux, ARM64 Linux, and Apple
  Silicon macOS. Windows and Intel macOS binaries are not available.

Per-language extraction gaps (unresolved imports, unsupported constructs,
metaprogramming that has no static declaration to point at) are documented
exhaustively in the [language coverage matrix](docs/language-support/README.md#remaining-unsupported-constructs).

## Development & contributing

### Build from source

```bash
git clone https://github.com/Kiy-K/oxide.git
cd oxide
cargo build --release -j 2
./target/release/oxide --version
```

OXIDE pins Rust 1.98.0 in `rust-toolchain.toml`. A default build includes
the native ONNX embedder; `cargo build --release --no-default-features -j 2`
builds without it and uses the hashed provider unless an HTTP endpoint is
configured.

### Installer options

```bash
# Install a specific published version
curl -fsSL https://raw.githubusercontent.com/Kiy-K/oxide/main/install.sh | \
  sh -s -- --version v0.1.0

# Choose another directory
curl -fsSL https://raw.githubusercontent.com/Kiy-K/oxide/main/install.sh | \
  sh -s -- --install-dir "$HOME/bin"
```

The installer verifies the downloaded archive against the release's
`SHA256SUMS`, checks that the binary starts, and replaces an existing
installation atomically. Re-run it to upgrade; `oxide install` is separate
and only wires the already-installed binary into coding-agent MCP configs.

### Running the checks

Prerequisites beyond Rust are pinned in [`mise.toml`](mise.toml) (Python
3.11, uv, shellcheck, jq, cargo-llvm-cov). Install
[mise](https://mise.jdx.dev/installing-mise.html) — a system package
(`pacman -S mise`, `brew install mise`, `apt install mise`) is preferred over
piping an installer script — then:

```bash
mise run bootstrap  # pinned Rust toolchain/components + every mise-managed tool
mise run verify     # everything below, in commit order, default + --no-default-features
```

Each mise task's command matches what CI currently runs step-for-step (see
`.github/workflows/ci.yml`) — CI itself does not yet invoke `mise run`
directly, pending verification of that migration on a real runner. A green
`mise run verify` covers the same checks as CI's `quality`, `test`,
`no-default-features`, and `retrieval-gate` jobs (fmt, clippy, both feature
configurations, the benchmark, and the installer's shellcheck + lifecycle
tests) — it does not run the separate, optional coverage job. Without
mise, run the underlying commands directly:

```bash
cargo fmt --check
cargo clippy -j 2 --all-targets -- -D warnings
cargo test -j 2
cargo build --release -j 2
./target/release/oxide eval --config fixtures/benchmark.json
```

None of the checks above need network access: every test that exercises a
real embedder pins `OXIDE_EMBED_NATIVE=hashed` itself
(`tests/*_e2e.rs` and friends), and `oxide eval` always uses the
deterministic hashed embedder regardless of environment
(`src/eval.rs`). The ~23 MB Arctic model download described in
[Privacy and offline use](#privacy-and-offline-use) only happens the first
time you run a real `oxide index`/`oxide query`/`oxide watch` against a
repository without `OXIDE_EMBED_NATIVE=hashed` set. `mise install` itself
needs network access once, to fetch the pinned tool versions above; after
that, `mise run bootstrap` is offline unless a tool version changes.

Language behavior is pinned by golden files under `fixtures/conformance/`.
Retrieval changes must pass the committed benchmark gate; changing the
expected baseline requires recording both vector-only and hybrid results
honestly.

Bug reports and focused pull requests are welcome. Please include a minimal
reproduction, the command you ran, and the relevant platform or repository
details. Read [`AGENTS.md`](AGENTS.md) and the
[review guide](docs/review/README.md) before changing retrieval, indexing,
storage, or language extraction.

## License

[MIT](LICENSE)
