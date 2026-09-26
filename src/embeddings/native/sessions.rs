pub const EMBED_SESSIONS_ENV: &str = "OXIDE_EMBED_SESSIONS";
/// Upper bound for an explicit session count.
pub const MAX_EMBED_SESSIONS: usize = 16;
/// Most sessions `auto` ever picks: `update_embeddings` runs at most four
/// workers, so a fifth session could never be busy.
pub const AUTO_MAX_EMBED_SESSIONS: usize = 4;
/// `auto` keeps a model whose per-session footprint exceeds this at one
/// session: pooling a large model multiplies its resident weights.
pub const AUTO_MAX_SESSION_MB: u64 = 400;
/// Documents a native embedder must have embedded before its session pool
/// may grow. Loading three extra Arctic XS Q sessions costs ~0.3 s and
/// ~120 MB; measured on a 30-symbol update, growing made indexing slower
/// (0.54 → 0.78 s), while full indexing is 2.9× faster. Queries never count.
pub const POOL_GROWTH_AFTER_DOCUMENTS: usize = 256;

/// How many ONNX sessions a native embedder may use.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EmbedSessions {
    /// Adaptive (the default): see [`auto_embed_sessions`].
    Auto,
    /// Exactly this many (1 = one session on every core, the lowest-memory
    /// setting and OXIDE's behavior before pooling).
    Fixed(usize),
}

/// Parses an `$OXIDE_EMBED_SESSIONS` value: empty or `auto` → adaptive,
/// `1`..=[`MAX_EMBED_SESSIONS`] → that many. Anything else is an error that
/// says how to fix it — never a silent default.
pub fn parse_embed_sessions(value: &str) -> anyhow::Result<EmbedSessions> {
    let v = value.trim();
    if v.is_empty() || v.eq_ignore_ascii_case("auto") {
        return Ok(EmbedSessions::Auto);
    }
    match v.parse::<usize>() {
        Ok(n @ 1..=MAX_EMBED_SESSIONS) => Ok(EmbedSessions::Fixed(n)),
        _ => anyhow::bail!(
            "{EMBED_SESSIONS_ENV}={value:?} is not valid: use `auto` (the default) or a \
             session count from 1 to {MAX_EMBED_SESSIONS}; `1` restores single-session, \
             lowest-memory embedding"
        ),
    }
}

/// `$OXIDE_EMBED_SESSIONS`, unset meaning [`EmbedSessions::Auto`].
pub fn embed_sessions_from_env() -> anyhow::Result<EmbedSessions> {
    match std::env::var(EMBED_SESSIONS_ENV) {
        Err(std::env::VarError::NotPresent) => Ok(EmbedSessions::Auto),
        Err(e) => anyhow::bail!(
            "{EMBED_SESSIONS_ENV} is not valid ({e}): use `auto` or 1-{MAX_EMBED_SESSIONS}"
        ),
        Ok(v) => parse_embed_sessions(&v),
    }
}

/// The adaptive session count, as a pure function of the machine and the
/// model so it can be tested: one session per four cores (so every session
/// keeps at least four intra-op threads), at most
/// [`AUTO_MAX_EMBED_SESSIONS`]; one session for a model above
/// [`AUTO_MAX_SESSION_MB`]; and the extra sessions together may use at most
/// a quarter of the memory available now (`available_mb`, `None` when the
/// platform does not report it).
pub fn auto_embed_sessions(cores: usize, session_mb: u64, available_mb: Option<u64>) -> usize {
    if session_mb > AUTO_MAX_SESSION_MB {
        return 1;
    }
    let by_cpu = (cores / 4).clamp(1, AUTO_MAX_EMBED_SESSIONS);
    let by_memory = available_mb.map_or(by_cpu, |mb| 1 + (mb / 4 / session_mb.max(1)) as usize);
    by_cpu.min(by_memory).max(1)
}

#[cfg(feature = "native-embed")]
/// Memory available to this process in MB: Linux `MemAvailable`, capped by
/// the cgroup v2 limit when one is set (containers). `None` elsewhere.
pub(super) fn available_memory_mb() -> Option<u64> {
    let meminfo = std::fs::read_to_string("/proc/meminfo").ok()?;
    let mut mb = meminfo
        .lines()
        .find_map(|l| l.strip_prefix("MemAvailable:"))?
        .trim()
        .trim_end_matches("kB")
        .trim()
        .parse::<u64>()
        .ok()?
        / 1024;
    let cgroup = std::fs::read_to_string("/proc/self/cgroup").ok();
    if let Some(path) = cgroup
        .as_deref()
        .and_then(|c| c.lines().find_map(|l| l.strip_prefix("0::")))
    {
        let dir = std::path::Path::new("/sys/fs/cgroup").join(path.trim_start_matches('/'));
        let read = |f: &str| {
            std::fs::read_to_string(dir.join(f))
                .ok()
                .and_then(|v| v.trim().parse::<u64>().ok())
        };
        if let (Some(max), Some(cur)) = (read("memory.max"), read("memory.current")) {
            mb = mb.min(max.saturating_sub(cur) / (1024 * 1024));
        }
    }
    Some(mb)
}

#[cfg(feature = "native-embed")]
/// Approximate resident memory of one extra ONNX session per profile, for
/// `auto`. `arctic-embed-xs-q` is measured (≈80 MB); the rest are estimated
/// from model size (weights ×1.5 + 40 MB) and only need to be the right
/// side of [`AUTO_MAX_SESSION_MB`].
pub(super) fn session_mb(profile: &str) -> u64 {
    match profile {
        "arctic-embed-xs-q" | "minilm-l6-v2-q" => 80,
        "bge-small-en-v1.5-q" => 100,
        "arctic-embed-xs" | "minilm-l6-v2" => 180,
        "arctic-embed-s" | "bge-small-en-v1.5" => 250,
        _ => u64::MAX,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn embed_sessions_setting_parses_auto_counts_and_rejects_the_rest() {
        assert_eq!(parse_embed_sessions("").unwrap(), EmbedSessions::Auto);
        assert_eq!(parse_embed_sessions(" AUTO ").unwrap(), EmbedSessions::Auto);
        assert_eq!(parse_embed_sessions("1").unwrap(), EmbedSessions::Fixed(1));
        assert_eq!(
            parse_embed_sessions("16").unwrap(),
            EmbedSessions::Fixed(16)
        );
        for bad in ["0", "17", "-1", "four", "2.5"] {
            let err = parse_embed_sessions(bad).unwrap_err().to_string();
            assert!(
                err.contains("OXIDE_EMBED_SESSIONS") && err.contains("`1` restores"),
                "{bad}: {err}"
            );
        }
    }

    #[test]
    fn auto_embed_sessions_scales_with_cores_memory_and_model_size() {
        // One session per four cores, capped at four.
        assert_eq!(auto_embed_sessions(16, 80, Some(8_000)), 4);
        assert_eq!(auto_embed_sessions(32, 80, Some(8_000)), 4);
        assert_eq!(auto_embed_sessions(8, 80, Some(8_000)), 2);
        assert_eq!(auto_embed_sessions(4, 80, Some(8_000)), 1);
        assert_eq!(auto_embed_sessions(1, 80, None), 1);
        // Extra sessions may use at most a quarter of available memory.
        assert_eq!(auto_embed_sessions(16, 80, Some(400)), 2);
        assert_eq!(auto_embed_sessions(16, 80, Some(300)), 1);
        // Large models never pool automatically.
        assert_eq!(
            auto_embed_sessions(16, AUTO_MAX_SESSION_MB + 1, Some(64_000)),
            1
        );
        // Unknown memory: cores decide.
        assert_eq!(auto_embed_sessions(16, 80, None), 4);
    }
}
