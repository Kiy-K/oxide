//! Non-secret, global OXIDE settings shared across every repository on this
//! machine: which remote embedding provider (if any) `oxide setup`
//! configured, and whether its privacy warning was acknowledged. Secrets
//! never live here — see `credentials.rs`. Read/written via `toml_edit`'s DOM
//! API (not serde), matching `agents.rs`'s existing convention for this
//! dependency: it is lossless, so a user's own comments/formatting in a
//! hand-edited `config.toml` survive a `save()` round trip.

use crate::agents::Paths;
use std::path::{Path, PathBuf};
use toml_edit::{DocumentMut, Item, Table};

/// `~/.config/oxide` (or `$XDG_CONFIG_HOME/oxide`) — the directory
/// `config.toml`, `credentials.enc`, and the encryption keyfile all live in.
pub fn oxide_config_dir() -> anyhow::Result<PathBuf> {
    let paths = Paths::from_env().map_err(|e| anyhow::anyhow!(e))?;
    Ok(paths.xdg_config.join("oxide"))
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct UserConfig {
    pub provider: Option<String>,
    pub model: Option<String>,
    /// Endpoint override — required for `provider = "openai-compatible"`,
    /// optional for the others (self-hosted/regional mirrors).
    pub base_url: Option<String>,
    /// The provider's output dimension as observed by `oxide setup`'s live
    /// test call — purely a local optimization so later constructions can
    /// skip re-probing (and so succeed even while the provider is
    /// unreachable). This is NOT a Matryoshka/output-dimension truncation
    /// request: it must never be resent to the provider as one, or a
    /// dimension that happens not to be in the provider's accepted
    /// truncation set turns every subsequent embed call into a 400.
    pub vector_dim: Option<usize>,
    pub remote_consent_ack: bool,
    /// UTC ISO 8601 (`…Z`) timestamp of the acknowledgement, for `--show`.
    pub remote_consent_at: Option<String>,
}

fn config_path(config_dir: &Path) -> PathBuf {
    config_dir.join("config.toml")
}

impl UserConfig {
    pub fn load(config_dir: &Path) -> anyhow::Result<Self> {
        let Ok(text) = std::fs::read_to_string(config_path(config_dir)) else {
            return Ok(Self::default());
        };
        let doc: DocumentMut = text.parse()?;
        let embedding = doc.get("embedding").and_then(Item::as_table);
        let str_field = |k: &str| -> Option<String> {
            embedding
                .and_then(|t| t.get(k))
                .and_then(Item::as_str)
                .map(str::to_string)
        };
        let bool_field = |k: &str| -> bool {
            embedding
                .and_then(|t| t.get(k))
                .and_then(Item::as_bool)
                .unwrap_or(false)
        };
        let usize_field = |k: &str| -> Option<usize> {
            embedding
                .and_then(|t| t.get(k))
                .and_then(Item::as_integer)
                .and_then(|n| usize::try_from(n).ok())
        };
        Ok(Self {
            provider: str_field("provider"),
            model: str_field("model"),
            base_url: str_field("base_url"),
            vector_dim: usize_field("vector_dim"),
            remote_consent_ack: bool_field("remote_consent_ack"),
            remote_consent_at: str_field("remote_consent_at"),
        })
    }

    pub fn save(&self, config_dir: &Path) -> anyhow::Result<()> {
        std::fs::create_dir_all(config_dir)?;
        // Preserve an existing document's formatting/comments outside the
        // `[embedding]` table when one already exists, rather than always
        // starting from a blank document.
        let mut doc = std::fs::read_to_string(config_path(config_dir))
            .ok()
            .and_then(|text| text.parse::<DocumentMut>().ok())
            .unwrap_or_default();
        let mut table = Table::new();
        if let Some(v) = &self.provider {
            table["provider"] = toml_edit::value(v.as_str());
        }
        if let Some(v) = &self.model {
            table["model"] = toml_edit::value(v.as_str());
        }
        if let Some(v) = &self.base_url {
            table["base_url"] = toml_edit::value(v.as_str());
        }
        if let Some(v) = self.vector_dim {
            table["vector_dim"] = toml_edit::value(v as i64);
        }
        table["remote_consent_ack"] = toml_edit::value(self.remote_consent_ack);
        if let Some(v) = &self.remote_consent_at {
            table["remote_consent_at"] = toml_edit::value(v.as_str());
        }
        doc.insert("embedding", Item::Table(table));
        std::fs::write(config_path(config_dir), doc.to_string())?;
        Ok(())
    }
}

/// UTC ISO 8601 with a `Z` suffix, per this project's standing timezone rule
/// (no offsets, no local time). No `chrono`/`time` dependency: this is one
/// display timestamp for `oxide setup --show`, not anything load-bearing, so
/// a small self-contained conversion (Howard Hinnant's `civil_from_days`)
/// beats adding a dependency for it.
pub fn unix_to_iso8601(secs: u64) -> String {
    let days = (secs / 86_400) as i64;
    let rem = secs % 86_400;
    let (h, m, s) = (rem / 3600, (rem % 3600) / 60, rem % 60);
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = if month <= 2 { y + 1 } else { y };
    format!("{year:04}-{month:02}-{d:02}T{h:02}:{m:02}:{s:02}Z")
}

pub fn now_iso8601() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    unix_to_iso8601(secs)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unix_epoch_formats_as_epoch_start() {
        assert_eq!(unix_to_iso8601(0), "1970-01-01T00:00:00Z");
    }

    #[test]
    fn known_timestamp_round_trips() {
        // 2024-01-15T10:30:00Z
        assert_eq!(unix_to_iso8601(1_705_314_600), "2024-01-15T10:30:00Z");
    }

    #[test]
    fn save_then_load_round_trips_every_field() {
        let dir = tempfile::tempdir().unwrap();
        let cfg = UserConfig {
            provider: Some("voyage".to_string()),
            model: Some("voyage-code-3".to_string()),
            base_url: None,
            vector_dim: Some(1024),
            remote_consent_ack: true,
            remote_consent_at: Some("2024-01-15T10:30:00Z".to_string()),
        };
        cfg.save(dir.path()).unwrap();
        let loaded = UserConfig::load(dir.path()).unwrap();
        assert_eq!(cfg, loaded);
    }

    #[test]
    fn missing_config_loads_as_default() {
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(UserConfig::load(dir.path()).unwrap(), UserConfig::default());
    }
}
