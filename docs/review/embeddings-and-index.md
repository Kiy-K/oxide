# Embeddings and index review rules

Scope: `src/embeddings.rs` (`EmbeddingProvider`, `EmbeddingSpaceFingerprint`,
`open_embedder`), `src/index.rs`'s `update_index` staleness check.

---

### EMB-001 — A provider's fingerprint must track its real vector-space semantics
**Severity:** BLOCKER · **Scope:** `EmbeddingProvider::fingerprint`/`name`,
`update_index`'s `current_fp`/`stored_fp` comparison.

**Invariant:** any change to what a provider actually produces — model or
checkpoint, quantization, dimension, query/document prompt formatting,
pooling, normalization, similarity function — must change that provider's
`fingerprint()` (or, for providers relying on the default trait impl that
don't override it, `name()`), so `update_index`'s compatibility check
detects the change and wipes stale vectors instead of silently comparing
old and new vectors as if they lived in the same space. A field added to
`EmbeddingSpaceFingerprint` that would make an old stored value's meaning
ambiguous must bump `EMBEDDING_FINGERPRINT_SCHEMA_VERSION`.

**What constitutes a violation:** a provider whose `embed`/`embed_query`/
`embed_document` behavior changes (new prefix, different pooling, swapped
model file) while `fingerprint()`/`name()` output stays byte-identical to
before. `NativeEmbedder::new` already encodes this correctly for the Gemma
query-prompt variants (the name string includes the variant) — new
providers or new variants must follow the same pattern, not skip it.

**Evidence required:** the behavior-changing diff, plus the (unchanged)
`fingerprint()`/`name()` output for the same provider. Cite `update_index`'s
match over `stored_fp`/`current_fp` (`index.rs`) to show what compatibility
signal the reviewer expects to change and doesn't.

**Exceptions:** a change provably incapable of affecting the vector space
(e.g. renaming a private field, adding a cache with identical output) needs
no fingerprint change — but the reviewer must confirm this by reading the
actual embedding output path, not assume it from the diff's stated intent.

---

### EMB-002 — No silent embedding fallback; downloads only at the documented default
**Severity:** BLOCKER · **Scope:** `open_embedder`/`configured_provider_name`
and any new provider-construction path.

**History:** this rule previously read "an unconfigured default must always
resolve to `HashedEmbedder` — no network call, no model download, ever". That
invariant was **deliberately retired** when `arctic-embed-xs-q` became the
shipped default (`DEFAULT_NATIVE_PROFILE`) and `native-embed` became a default
Cargo feature; see `docs/cpu-embedding-survey/phase3-minilm-rerun.md` §7 for
the decision and its cost. Do not flag the default's download as a violation
of the old rule. What survives is everything below.

**Invariant:** provider selection precedence is explicit
`--embedder`/tool-argument > `$OXIDE_EMBED_URL` > `$OXIDE_EMBED_NATIVE` >
`DEFAULT_NATIVE_PROFILE`, with `OXIDE_EMBED_NATIVE=hashed` (`OFFLINE_PROFILE`)
and `--no-default-features` as the two ways back to `HashedEmbedder`. Three
things must hold:

1. **`open_embedder` and `configured_provider_name` select the same
   *profile*** for any given environment — both go through
   `resolve_native_profile`. Divergence there makes `oxide status` report
   `embedder_current: false` against a current index and makes `validate_index`
   fire on an embedding space that never changed.

   They deliberately differ on *invalid* configuration, and that is not a
   violation: for an unsupported profile name, or an unparseable
   `$OXIDE_EMBED_NATIVE_QUERY_PROMPT`, `open_embedder` returns an error while
   `configured_provider_name` still names what was configured. That is its
   contract — it reports the configured identity without constructing or
   probing anything, so `oxide status` can tell a user their typo'd setting
   differs from the embedder that actually built the index, instead of failing
   to answer. Both outcomes are loud; neither silently mislabels a vector.
2. **No silent cross-space fallback.** A model that cannot be loaded is an
   error. Falling back to `HashedEmbedder` (or any other provider) on failure
   would change the embedding space without changing the recorded identity,
   and the next `update_index` would wipe and recompute every stored vector.
3. **The offline path stays reachable and documented.** Air-gapped use must
   remain possible without editing code.

**What constitutes a violation:** changing the precedence in one of the two
functions but not the other; a `unwrap_or_else`/`ok()` that swallows a provider
construction failure into a different provider; a new CLI subcommand or MCP
path that constructs a provider without going through `open_embedder`; removing
or undocumenting `OFFLINE_PROFILE`; **widening what gets downloaded** — a new
default-reachable path that fetches weights for anything other than
`DEFAULT_NATIVE_PROFILE`, or that downloads without recording a distinct
`EmbeddingSpaceFingerprint`.

**Evidence required:** cite both functions' match arms and show they agree.
For a change to the default profile itself, cite same-corpus benchmark evidence
on the frozen 21-task pin (the standard `docs/cpu-embedding-survey/` runs meet
this) — a default swap silently invalidates every existing user's index, so it
needs the same paired, manifest-verified comparison Phase 2 and Phase 3 used.

**Exceptions:** `NativeEmbedder::from_local_files` is the one constructor
that provably cannot download (reads local file bytes only) — expanding its use
is fine and is the documented way to add native models safely.

---

### EMB-003 — Provider failure must degrade explicitly, not silently
**Severity:** MAJOR · **Scope:** `HttpEmbedder`/`NativeEmbedder` failure
paths, `RetrievalEngine::search`'s provider join, `store.all_embeddings()`
failure handling.

**Invariant:** an embedding provider failure (HTTP error, empty/mismatched
vector, a panicking provider thread, an unreadable embeddings table) must
degrade to a well-defined, documented state — lexical-only influence on the
fused score — and must never crash the whole request because one provider
failed, and must never be indistinguishable from a genuine zero-similarity
result. See `docs/retrieval-coordinator/README.md`'s "Failure / degradation
behavior" section for the accepted baseline contract.

**What constitutes a violation:** a change that turns a provider error into
an empty `Vec` without that `Vec` still being caught by the existing
length/emptiness check downstream; reverting
`lex_handle.join()`/`vec_handle.join()` from `.unwrap_or_default()` to
`.unwrap()`; a `store.all_embeddings()` failure that once again propagates
via `?` and fails the whole `search()` call instead of degrading to
"no semantic evidence for this query."

**Evidence required:** cite the retrieval-coordinator doc's degradation
table, then point to the specific line where the new code diverges from it.

**Exceptions:** `is_available()` reporting unhealthy is a separate,
existing signal (provider health, not a single-call failure) — surfacing it
more prominently elsewhere (e.g. a CLI status field) is not itself a
violation of this rule.
