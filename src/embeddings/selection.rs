#[cfg(feature = "native-embed")]
use super::native::{native_provider_name, native_query_prompt_from_env};
use super::{EmbeddingProvider, HashedEmbedder, HttpEmbedder};
#[cfg(feature = "native-embed")]
use super::{GemmaQueryPrompt, NativeEmbedder};

/// Return the configured provider identity without probing a network endpoint.
///
/// Shares `resolve_native_profile` with `open_embedder` so the two always pick
/// the same profile — see AGENTS.md for what breaks when they drift. They do
/// diverge on *invalid* configuration, deliberately: an unsupported profile
/// name or an unparseable `$OXIDE_EMBED_NATIVE_QUERY_PROMPT` is an error from
/// `open_embedder`, while this function still reports what was configured, so
/// `oxide status` can say the configured provider differs from the stored one
/// rather than refusing to answer.
pub fn configured_provider_name(explicit: Option<&str>) -> String {
    let url = explicit
        .map(str::to_string)
        .or_else(|| std::env::var("OXIDE_EMBED_URL").ok());
    match url {
        Some(u) if !u.is_empty() => {
            let model = std::env::var("OXIDE_EMBED_MODEL").unwrap_or_default();
            format!("http:{model}@{u}")
        }
        _ => {
            // Same remote-resolution step `open_embedder` takes below, so
            // the two can never name a different provider for the same
            // environment — see that function's comment for what breaks
            // otherwise. This function never fails a command by itself, but
            // `Err` (e.g. `$OXIDE_EMBED_PROVIDER` set to an unknown name, or
            // its matching API-key env var missing) must not fall through to
            // the local default's name — `open_embedder` will genuinely
            // fail on the same input, and reporting "local, all good" here
            // (e.g. in `oxide status`) would hide a real misconfiguration
            // instead of surfacing it.
            match super::remote::resolve_configured_remote() {
                Ok(Some(remote)) => {
                    return super::remote::provider_name(
                        &remote.provider,
                        &remote.model,
                        remote.base_url.as_deref(),
                        remote.known_dim.or(remote.dimensions),
                    );
                }
                Ok(None) => {}
                Err(e) => return format!("misconfigured-remote-provider:{e}"),
            }
            // Must resolve through the same `resolve_native_profile` as
            // `open_embedder`, or this name disagrees with the provider that
            // actually embedded: `oxide status` would report
            // `embedder_current: false` against a perfectly current index, and
            // `validate_index` would fire on a space that never changed.
            #[cfg(feature = "native-embed")]
            {
                let configured = std::env::var("OXIDE_EMBED_NATIVE").ok();
                if let Some(profile) = resolve_native_profile(configured.as_deref()) {
                    let query_prompt =
                        native_query_prompt_from_env().unwrap_or(GemmaQueryPrompt::Bare);
                    return native_provider_name(&profile, query_prompt);
                }
            }
            "hashed-bow-256".into()
        }
    }
}

/// The native profile `open_embedder` uses when nothing is configured.
///
/// Chosen over the previous offline-hashed fallback and over the
/// `qwen3-Q8_0` HTTP recommendation on the frozen 21-task ContextBench
/// evidence in `docs/cpu-embedding-survey/` — decisively better vector-only
/// retrieval than Qwen (R@5 0.655 vs 0.536), a gold file in the candidate
/// pool on 21/21 tasks where Qwen manages 19, ~9x faster full-repo indexing
/// (113.6s vs the Jina reference's 1025.5s on the same corpus), and no
/// separate server process to run at all. Qwen remains ahead on budgeted R@5
/// by 0.035 (one to two tasks out of twenty-one); that margin does not pay
/// for a llama.cpp server in the loop.
///
/// Peak RSS is deliberately *not* claimed as a win over Qwen: Arctic's
/// measured 230MB is in-process, while Qwen's ~140-260MB is a separate
/// long-lived server measured with `ps` at varying points. The two are not
/// comparable, and Arctic's 4.3x RSS advantage in the survey is over
/// `jina-code-v2` (996MB), not over Qwen.
pub const DEFAULT_NATIVE_PROFILE: &str = "arctic-embed-xs-q";

/// The `OXIDE_EMBED_NATIVE` value that opts back out to the
/// `HashedEmbedder` — no model, no download. The value OXIDE's own test
/// suite pins, since the default now loads real weights.
///
/// This is *provider selection*, not a network prohibition, and it sits at
/// the bottom of the precedence order in [`open_embedder`]: an explicit
/// `--embedder URL` or a set `$OXIDE_EMBED_URL` still wins over it and still
/// talks to that endpoint. For an actually-offline run, clear the endpoint
/// configuration as well:
///
/// ```sh
/// env -u OXIDE_EMBED_URL OXIDE_EMBED_NATIVE=hashed oxide index .
/// ```
///
/// Building `--no-default-features` drops the `native-embed` feature, so no
/// ONNX model can be loaded at all — but it does not disable the HTTP
/// provider either, for the same reason: the endpoint is still honoured if
/// configured.
pub const OFFLINE_PROFILE: &str = "hashed";

/// Resolves `$OXIDE_EMBED_NATIVE` to the native profile to load, or `None`
/// for the offline hashed embedder.
///
/// Split out from `open_embedder` so the precedence is testable without a
/// model download: unset and empty both mean "use the default", and only the
/// explicit `OFFLINE_PROFILE` opts out.
#[cfg(feature = "native-embed")]
fn resolve_native_profile(configured: Option<&str>) -> Option<String> {
    match configured.map(str::trim) {
        Some(OFFLINE_PROFILE) => None,
        Some(p) if !p.is_empty() => Some(p.to_string()),
        _ => Some(DEFAULT_NATIVE_PROFILE.to_string()),
    }
}

/// Provider factory: explicit URL wins, then `OXIDE_EMBED_URL`, then
/// `OXIDE_EMBED_NATIVE` (or, unset, `DEFAULT_NATIVE_PROFILE`), and finally
/// the offline hashed embedder.
///
/// **The default is no longer offline.** Unconfigured, this loads
/// `DEFAULT_NATIVE_PROFILE` through fastembed, which downloads its ONNX
/// weights (~23MB) into `$HF_HOME` (else `~/.cache/huggingface/hub`) the
/// first time the model is loaded, and needs network to do so.
///
/// "First model load" is not "first index": every command that can answer
/// semantically — `search`, `context`, `review`, `watch` — calls this before
/// touching the index, and `RepositoryService::search` in particular
/// constructs the provider *before* `validate_index` runs. So a machine that
/// already has an index but no cached weights will still download on its
/// first semantic query. `--mode lexical` never reaches here.
///
/// A failed download is not sticky: nothing is written to the index, so
/// re-running the same command after restoring network retries cleanly.
/// Switching providers later is safe but not free — the new fingerprint
/// makes `update_embeddings` clear and recompute every vector, so budget a
/// full re-embed for the next `oxide index`.
///
/// That is a deliberate trade for a default that actually retrieves well;
/// the previous zero-download behaviour is still one env var away
/// (`OXIDE_EMBED_NATIVE=hashed`, see [`OFFLINE_PROFILE`] for the endpoint
/// caveat), and is what you want for air-gapped machines and for reproducing
/// the benchmark gate, which constructs `HashedEmbedder` directly and is
/// unaffected by any of this.
///
/// A missing model is an error, never a silent downgrade to the hashed
/// embedder: the two are different embedding spaces, and quietly swapping
/// them would trip `update_index`'s fingerprint check and wipe every stored
/// vector on the next run.
pub fn open_embedder(
    explicit: Option<&str>,
) -> anyhow::Result<Box<dyn EmbeddingProvider + Send + Sync>> {
    let url = explicit
        .map(str::to_string)
        .or_else(|| std::env::var("OXIDE_EMBED_URL").ok());
    match url {
        Some(u) if !u.is_empty() => {
            let model = std::env::var("OXIDE_EMBED_MODEL").unwrap_or_default();
            Ok(Box::new(HttpEmbedder::new(&u, &model)?))
        }
        _ => {
            // Remote is checked before the local default, but only ever
            // resolves to something when the caller explicitly opted in
            // (`$OXIDE_EMBED_PROVIDER` env vars, or `oxide setup`'s saved
            // `remote_consent_ack`ed config) — see `resolve_configured_
            // remote`'s doc comment. An unconfigured environment falls
            // through untouched.
            if let Some(remote) = super::remote::resolve_configured_remote()? {
                return super::remote::build(
                    &remote.provider,
                    &remote.model,
                    remote.base_url.as_deref(),
                    remote.dimensions,
                    remote.known_dim,
                    &remote.api_key,
                );
            }
            #[cfg(feature = "native-embed")]
            {
                let configured = std::env::var("OXIDE_EMBED_NATIVE").ok();
                if let Some(profile) = resolve_native_profile(configured.as_deref()) {
                    let query_prompt = native_query_prompt_from_env()?;
                    return Ok(Box::new(NativeEmbedder::new(&profile, query_prompt)?));
                }
            }
            Ok(Box::new(HashedEmbedder::default()))
        }
    }
}

#[cfg(all(test, feature = "native-embed"))]
mod tests {
    use super::*;
    /// `open_embedder` and `configured_provider_name` must name the same
    /// provider for the same environment — see the comment in the latter for
    /// what breaks otherwise. Read-only on the environment, so it cannot race
    /// with a concurrently running test.
    #[cfg(feature = "native-embed")]
    #[test]
    fn unconfigured_provider_name_is_the_shipped_native_default() {
        if std::env::var_os("OXIDE_EMBED_URL").is_some()
            || std::env::var_os("OXIDE_EMBED_NATIVE").is_some()
        {
            return; // a configured environment is not what this pins
        }
        assert_eq!(
            configured_provider_name(None),
            format!("native:{DEFAULT_NATIVE_PROFILE}")
        );
    }

    /// Pins the precedence `open_embedder` applies without constructing a
    /// provider — so it stays offline and cannot be flaked by a missing
    /// model. The `open_embedder` wiring above is a thin `match` over this.
    #[cfg(feature = "native-embed")]
    #[test]
    fn native_profile_resolution_defaults_to_arctic_and_opts_out_on_hashed() {
        assert_eq!(
            resolve_native_profile(None).as_deref(),
            Some(DEFAULT_NATIVE_PROFILE),
            "unset must load the shipped default, not the hashed embedder"
        );
        assert_eq!(
            resolve_native_profile(Some("")).as_deref(),
            Some(DEFAULT_NATIVE_PROFILE),
            "an empty value is 'unconfigured', same as unset"
        );
        assert_eq!(
            resolve_native_profile(Some("jina-code-v2")).as_deref(),
            Some("jina-code-v2"),
            "an explicit profile wins over the default"
        );
        assert_eq!(
            resolve_native_profile(Some(OFFLINE_PROFILE)),
            None,
            "only the explicit offline profile opts back out to hashed"
        );
        assert_eq!(
            resolve_native_profile(Some("  hashed  ")),
            None,
            "the offline opt-out survives surrounding whitespace"
        );
    }
}
