<p align="center"><em>Project logo pending for v0.1</em></p>

# OXIDE

**OXIDE — a local Context Engine for coding agents.**

OXIDE indexes a repository once, updates only the code that changed, and gives
coding agents a bounded set of relevant symbols for each task.

> Maximize coding-agent utility per context token, not retrieval volume.

- Local by default: source code and the index stay on your machine.
- Incremental: unchanged files are not reparsed; unchanged symbols reuse embeddings.
- Agent-neutral: use the CLI, JSON, or MCP from the agent you already run.
- Token-budgeted: `query` returns a working set sized for the context window you choose.
- Multi-language: Python, TypeScript/TSX, Rust, and Go.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/Kiy-K/oxide/main/install.sh | sh
```

## Quick start

Run these commands from a repository:

```bash
oxide index
oxide query "Where is authentication handled?"
oxide install
```

`oxide query` returns code for an agent to use. It does not generate an answer.

```text
$ oxide query "Where is retry behavior implemented?" --budget-tokens 800
Relevant code for: Where is retry behavior implemented?

 1. oxidepy/notifiers.py:31-33  notify_after_final_attempt [function]
 2. oxidepy/retry.py:31-37      RetryPolicy.should_retry [method]
 3. oxidepy/notifiers.py:9-10   Notifier.notify [method]
 ...

6 items · 429 of 800 token budget used
```

This example comes from OXIDE's committed Python fixture using the offline
hashed embedder.

## Why OXIDE exists

Coding agents do better work when they see the right evidence: the definition
being changed, its callers, the tests that constrain it, and enough surrounding
code to understand the contract. More retrieved code is often worse. It spends
tokens, dilutes the useful evidence, and leaves less room for reasoning and
generation.

OXIDE grew from that constraint. Most retrieval systems are evaluated on how
much they can find. OXIDE is designed around how much useful code it can fit
inside a fixed context budget.

The result is a context-supply layer. OXIDE does not wrap an LLM, edit code, or
choose an agent. It prepares a small, inspectable working set before the agent
starts reading and editing.

## What OXIDE does

```bash
oxide index                         # create or incrementally update the index
oxide query "fix token refresh"     # build a token-budgeted working set
oxide search RetryPolicy            # find ranked symbol matches
oxide status                        # check freshness and provider compatibility
oxide watch                         # update the index as files change
oxide review --diff HEAD~1          # collect context for a Git diff
oxide install                       # connect supported coding agents over MCP
```

`query` is for a question or coding task. `search` is for an identifier or
expression. Both return source evidence; neither calls an LLM.

Use `--json` for automation:

```bash
oxide status --json
oxide index . --json
oxide query "fix refresh-token validation" --budget-tokens 4096 --json
oxide search RefreshToken --json
```

Every evidence item includes its repository-relative path, qualified name,
line range, score, selection reasons, and source snippet. Context packs also
report their estimated token use and why candidates were omitted.

## Supported languages

| Language | Indexed definitions |
|---|---|
| Python | modules, classes, functions, methods, constants |
| TypeScript / TSX | functions, classes, methods, interfaces, type aliases, enums, exported declarations |
| Rust | modules, structs, enums, traits, impls, functions, methods |
| Go | packages, structs, interfaces, functions, methods, constants |

OXIDE also extracts imports, references, calls, inheritance, and containment
where the language grammar exposes them. See the
[language coverage matrix](docs/language-support/README.md) for exact behavior
and known gaps. The committed conformance fixtures are the source of truth.

## Coding-agent integrations

`oxide install` detects supported agents, shows the exact configuration change,
and asks before writing it. It currently integrates with:

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

The integration runs `oxide mcp` over stdio. Other tools can use the same MCP
server or call the CLI's JSON interface directly.

## Evidence

OXIDE keeps performance and retrieval claims reproducible and scoped. A passing
fixture is a regression check, not proof that OXIDE beats another tool.

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

The current language pipeline was measured on one repository per supported
language at pinned revisions. These are medians of three release-build runs on
an Intel i7-13620H laptop under `nice -n 10`, using the offline hashed embedder.

| Repository | Language | Files | Symbols | Cold index | No-change update | One-file edit |
|---|---|---:|---:|---:|---:|---:|
| Flask | Python | 80 | 1,755 | 865 ms | 31 ms | 180 ms |
| Dark Reader | TypeScript / TSX | 197 | 1,356 | 1,021 ms | 29 ms | 190 ms |
| Tokio | Rust | 547 | 7,155 | 5,666 ms | 131 ms | 315 ms |
| Gin | Go | 99 | 2,076 | 1,338 ms | 38 ms | 520 ms |

The measurements describe indexing behavior, not retrieval quality. Exact
revisions, commands, memory use, index sizes, and unsupported constructs are in
the [language support report](docs/language-support/README.md#performance).

### External-task evidence

The research notes include a pinned 21-task ContextBench retrieval sample and a
four-task same-agent study. The retrieval sample is directional because it is
small and provider-specific. In the same-agent study, no OXIDE condition beat
the stock agent on task outcomes. See
[Context engineering notes](docs/context-engineering-notes.md#evaluation-pivot-contextbench-official)
for the protocol, results, and caveats.

> [!IMPORTANT]
> The v0.1 release comparison suite is not finalized. OXIDE does not claim to
> outperform other developer tools here. When the suite is finalized, this
> note will be replaced with pinned tool versions, identical corpora, exact
> commands, hardware, complete results, and the cases where OXIDE loses.

## Limitations

- OXIDE supports four language families today. Java, C/C++, C#, Ruby, PHP, and
  plain JavaScript are not indexed.
- Reference and structural relations use syntax and identifier-name matching,
  not compiler-grade name or type resolution. Ambiguous names can produce
  false positives or miss a cross-file relationship.
- TypeScript declaration files (`.d.ts`) are skipped. Go imports are recorded
  but do not currently produce imported-definition edges.
- The vector path is a brute-force scan. It is comfortable around 50,000
  symbols; larger repositories need measurement.
- Token counts use a `chars / 4` estimate rather than the target model's exact
  tokenizer.
- The default embedder downloads model weights on first use. Fully offline mode
  trades semantic quality for a deterministic hashed embedding.
- `review` assembles relevant context for a diff. It does not produce a review
  verdict.
- v0.1 ships prebuilt artifacts for x86-64 Linux, ARM64 Linux, and Apple
  Silicon macOS. Windows and Intel macOS binaries are not available.

## How it works

OXIDE keeps implementation choices below the product interface because agents
only need the context pack. The current pipeline is:

```text
repository
    ↓
incremental symbol index
    ↓
lexical + semantic retrieval
    ↓
bounded structural expansion
    ↓
ranking and deduplication
    ↓
token-budgeted context pack
    ↓
coding agent
```

Under the hood:

- Tree-sitter extracts symbols and syntax-level relationships for each language.
- SQLite stores files, symbols, embeddings, lexical postings, and precomputed
  structural relations in `<repo>/.oxide/index.db`.
- BM25 and embedding similarity run together; reciprocal rank fusion combines
  their ranked results.
- Parent, child, import, reference, caller, implementor, and test relationships
  expand strong direct hits without displacing them.
- The allocator removes overlaps, applies diversity caps, and stops at the
  requested token budget.

Stable symbol IDs and content hashes let OXIDE skip unchanged files and reuse
unchanged embeddings. Read commands use a consistent SQLite WAL snapshot while
another process may be updating the index.

## Embeddings and offline use

The default provider is `arctic-embed-xs-q`, a 384-dimensional int8 ONNX model
that runs in the OXIDE process. Its weights are about 23 MB and are downloaded
from Hugging Face the first time a semantic command loads the model. They are
cached under `$HF_HOME`, or `~/.cache/huggingface/hub` when `$HF_HOME` is unset.

For a zero-download, air-gapped setup:

```bash
env -u OXIDE_EMBED_URL OXIDE_EMBED_NATIVE=hashed oxide index
env -u OXIDE_EMBED_URL OXIDE_EMBED_NATIVE=hashed \
  oxide query "Where is authentication handled?"
```

The hashed provider is deterministic and fully local, but its semantic matching
is shallower than the native model.

Provider precedence is:

1. `--embedder URL`
2. `$OXIDE_EMBED_URL`
3. `$OXIDE_EMBED_NATIVE`
4. the default native profile

An OpenAI-compatible HTTP embedding endpoint also works:

```bash
export OXIDE_EMBED_URL=http://127.0.0.1:8191/v1/embeddings
export OXIDE_EMBED_MODEL=my-model-label
oxide index
```

Changing providers clears and recomputes stored vectors because embedding spaces
cannot be mixed. OXIDE records migrations so an interrupted rebuild is detected
instead of serving incompatible vectors. Lexical search remains available:

```bash
oxide search AuthService --mode lexical
```

## Installation details

The release installer downloads the archive for your platform, verifies it
against the release's `SHA256SUMS`, checks that the binary starts, and replaces
an existing installation atomically. It installs to `$HOME/.local/bin/oxide` by
default.

```bash
# Install a specific published version
curl -fsSL https://raw.githubusercontent.com/Kiy-K/oxide/main/install.sh | \
  sh -s -- --version v0.1.0

# Choose another directory
curl -fsSL https://raw.githubusercontent.com/Kiy-K/oxide/main/install.sh | \
  sh -s -- --install-dir "$HOME/bin"
```

Re-run the installer to upgrade. `oxide install` is separate: it adds the
already-installed binary to coding-agent MCP configurations.

### Build from source

```bash
git clone https://github.com/Kiy-K/oxide.git
cd oxide
cargo build --release -j 2
./target/release/oxide --version
```

OXIDE pins Rust 1.98.0 in `rust-toolchain.toml`. A default build includes the
native ONNX embedder. `cargo build --release --no-default-features -j 2` builds
without native model support and uses the hashed provider unless an HTTP
endpoint is configured.

## Development

```bash
cargo fmt --check
cargo clippy -j 2 --all-targets -- -D warnings
cargo test -j 2
cargo build --release -j 2
./target/release/oxide eval --config fixtures/benchmark.json
```

Language behavior is pinned by golden files under `fixtures/conformance/`.
Retrieval changes must pass the committed benchmark gate; changing the expected
baseline requires recording both vector-only and hybrid results honestly.

Bug reports and focused pull requests are welcome. Please include a minimal
reproduction, the command you ran, and the relevant platform or repository
details. Read [`AGENTS.md`](AGENTS.md) and the
[review guide](docs/review/README.md) before changing retrieval, indexing,
storage, or language extraction.

## License

[MIT](LICENSE)
