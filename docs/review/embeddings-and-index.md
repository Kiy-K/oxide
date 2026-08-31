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

### EMB-002 — No silent embedding fallback or model download
**Severity:** BLOCKER · **Scope:** `open_embedder`/`configured_provider_name`
and any new provider-construction path.

**Invariant:** provider selection precedence is explicit
`--embedder`/tool-argument > `$OXIDE_EMBED_URL` > (feature-gated)
`$OXIDE_EMBED_NATIVE` > the offline `HashedEmbedder`. An unconfigured
default (no flag, no env var) must always resolve to `HashedEmbedder` — no
network call, no model download, ever. Any path that reaches the network or
downloads model weights requires an explicit, documented user opt-in.

**What constitutes a violation:** a new fallback branch that tries
network/native before `HashedEmbedder`; a new CLI subcommand or MCP path
that constructs a provider without going through `open_embedder`'s
precedence; widening `NativeEmbedder::new`'s reachability so its un-gated
`fastembed` auto-download (an acknowledged, explicitly documented gap — see
the `NativeEmbedder` module doc's "no model-missing/no-silent-download
gating beyond fastembed's own auto-download") becomes reachable without
*both* the `native-embed` compile feature *and* an explicit
`$OXIDE_EMBED_NATIVE` value. That gap existing behind two opt-ins is
accepted; broadening its reach is the violation.

**Evidence required:** cite `open_embedder`'s match arms and its doc
comment ("OXIDE stays fully useful without any server or model download").
For anything touching `native-embed`, cite the module doc's own "unfixed"
note as the accepted baseline, and show specifically how the change
broadens what's reachable without opt-in.

**Exceptions:** `NativeEmbedder::from_local_files` is the one constructor
that provably cannot download (reads local file bytes only; the compiled
feature set has no `hf-hub` capability at all) — expanding its use is fine
and is the documented way to add native models safely.

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
