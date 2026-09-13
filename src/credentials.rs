//! Encrypted-at-rest storage for remote embedding-provider API keys.
//!
//! Keys never touch `.oxide/index.db` (per-repo) or `config.toml` (global,
//! plaintext, see `user_config.rs`) — only this file's AES-256-GCM-encrypted
//! blob, decrypted in memory for the life of the process that needs it. The
//! symmetric key is a random 32-byte file (`~/.config/oxide/key`, mode 0600
//! on unix), not a passphrase or OS keyring entry, so decryption never
//! blocks on user input or a keyring daemon — the explicit "machine-derived
//! keyfile" choice made for this feature, so it works identically in
//! CI/headless/containers. This is the same trust boundary as `~/.netrc` or
//! `~/.aws/credentials`, except encrypted at rest rather than plaintext.

use aes_gcm::aead::{Aead, AeadCore, KeyInit, OsRng};
use aes_gcm::{Aes256Gcm, Nonce};
use anyhow::{Context, Result};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// What every display path (`oxide setup --show`, error messages) prints
/// instead of a real key. Total redaction, never a prefix/suffix of the key.
pub const REDACTED: &str = "********";

const KEY_LEN: usize = 32;
const NONCE_LEN: usize = 12;

type Store = BTreeMap<String, String>;

fn keyfile_path(config_dir: &Path) -> PathBuf {
    config_dir.join("key")
}

fn credentials_path(config_dir: &Path) -> PathBuf {
    config_dir.join("credentials.enc")
}

#[cfg(unix)]
fn restrict_to_owner(path: &Path) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))
        .with_context(|| format!("setting owner-only permissions on {}", path.display()))
}

#[cfg(not(unix))]
fn restrict_to_owner(_path: &Path) -> Result<()> {
    Ok(())
}

fn parse_key(path: &Path, bytes: Vec<u8>) -> Result<[u8; KEY_LEN]> {
    bytes.try_into().map_err(|bytes: Vec<u8>| {
        anyhow::anyhow!(
            "{} is not a valid OXIDE key file ({} bytes, expected {KEY_LEN})",
            path.display(),
            bytes.len()
        )
    })
}

fn load_or_create_key(config_dir: &Path) -> Result<[u8; KEY_LEN]> {
    let path = keyfile_path(config_dir);
    if let Ok(bytes) = std::fs::read(&path) {
        return parse_key(&path, bytes);
    }
    std::fs::create_dir_all(config_dir)
        .with_context(|| format!("creating {}", config_dir.display()))?;
    let key = Aes256Gcm::generate_key(OsRng);
    // Two callers racing a first-ever `oxide setup`/remote-embed call must
    // never each persist a *different* key: whichever key lost would
    // permanently strand the `credentials.enc` the other caller already
    // encrypted under it. Write the candidate key to a temp file unique to
    // this call, then `hard_link` it into place — unlike `rename`,
    // `hard_link` fails with `AlreadyExists` instead of silently replacing
    // a winner, so the loser can detect the race and read back the key
    // that actually won rather than persisting its own over it. The temp
    // name is keyed off the key's own random bytes (not just the PID)
    // because two threads in the same process racing this same function
    // would otherwise share one PID-only temp path and could each
    // overwrite the other's in-flight write before either's `hard_link`
    // runs.
    let suffix: String = key.iter().take(8).map(|b| format!("{b:02x}")).collect();
    let tmp_path = config_dir.join(format!(".key.tmp.{}.{suffix}", std::process::id()));
    std::fs::write(&tmp_path, key.as_slice())
        .with_context(|| format!("writing {}", tmp_path.display()))?;
    restrict_to_owner(&tmp_path)?;
    let link_result = std::fs::hard_link(&tmp_path, &path);
    let _ = std::fs::remove_file(&tmp_path);
    match link_result {
        Ok(()) => Ok(key.into()),
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
            let bytes = std::fs::read(&path)
                .with_context(|| format!("reading key file {}", path.display()))?;
            parse_key(&path, bytes)
        }
        Err(e) => Err(e).context(format!("linking key file {}", path.display())),
    }
}

fn cipher_for(key: &[u8; KEY_LEN]) -> Aes256Gcm {
    Aes256Gcm::new_from_slice(key).expect("key is exactly 32 bytes, checked above")
}

fn load_store(config_dir: &Path, key: &[u8; KEY_LEN]) -> Result<Store> {
    let path = credentials_path(config_dir);
    let Ok(raw) = std::fs::read(&path) else {
        return Ok(Store::new());
    };
    if raw.len() < NONCE_LEN {
        anyhow::bail!(
            "{} is corrupt (too short to contain a nonce)",
            path.display()
        );
    }
    let (nonce_bytes, ciphertext) = raw.split_at(NONCE_LEN);
    let plaintext = cipher_for(key)
        .decrypt(Nonce::from_slice(nonce_bytes), ciphertext)
        .map_err(|_| {
            anyhow::anyhow!(
                "{} could not be decrypted (wrong or corrupted key file at {})",
                path.display(),
                keyfile_path(config_dir).display()
            )
        })?;
    serde_json::from_slice(&plaintext)
        .with_context(|| format!("{} decrypted but is not valid JSON", path.display()))
}

fn save_store(config_dir: &Path, key: &[u8; KEY_LEN], store: &Store) -> Result<()> {
    std::fs::create_dir_all(config_dir)
        .with_context(|| format!("creating {}", config_dir.display()))?;
    let nonce = Aes256Gcm::generate_nonce(&mut OsRng);
    let plaintext = serde_json::to_vec(store)?;
    let ciphertext = cipher_for(key)
        .encrypt(&nonce, plaintext.as_slice())
        .map_err(|e| anyhow::anyhow!("encrypting credentials: {e}"))?;
    let mut out = Vec::with_capacity(NONCE_LEN + ciphertext.len());
    out.extend_from_slice(&nonce);
    out.extend_from_slice(&ciphertext);
    let path = credentials_path(config_dir);
    std::fs::write(&path, &out).with_context(|| format!("writing {}", path.display()))?;
    restrict_to_owner(&path)
}

/// Encrypts and persists `plaintext` under `provider`, creating the keyfile
/// on first use. Overwrites any previously saved key for the same provider.
pub fn set_key(config_dir: &Path, provider: &str, plaintext: &str) -> Result<()> {
    let key = load_or_create_key(config_dir)?;
    let mut store = load_store(config_dir, &key)?;
    store.insert(provider.to_string(), plaintext.to_string());
    save_store(config_dir, &key, &store)
}

/// `Ok(None)` when nothing has ever been saved for `provider` (including
/// "no credentials file exists yet at all") — never an error for that case.
pub fn get_key(config_dir: &Path, provider: &str) -> Result<Option<String>> {
    if !credentials_path(config_dir).exists() {
        return Ok(None);
    }
    let key = load_or_create_key(config_dir)?;
    Ok(load_store(config_dir, &key)?.get(provider).cloned())
}

pub fn has_key(config_dir: &Path, provider: &str) -> bool {
    get_key(config_dir, provider).ok().flatten().is_some()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trips_a_key_through_a_fresh_load() {
        let dir = tempfile::tempdir().unwrap();
        set_key(dir.path(), "voyage", "voy-secret-abc123").unwrap();
        assert_eq!(
            get_key(dir.path(), "voyage").unwrap().as_deref(),
            Some("voy-secret-abc123")
        );
    }

    #[test]
    fn unknown_provider_is_none_not_an_error() {
        let dir = tempfile::tempdir().unwrap();
        set_key(dir.path(), "voyage", "voy-secret").unwrap();
        assert_eq!(get_key(dir.path(), "jina").unwrap(), None);
    }

    #[test]
    fn missing_credentials_file_is_none_not_an_error() {
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(get_key(dir.path(), "voyage").unwrap(), None);
        assert!(!has_key(dir.path(), "voyage"));
    }

    #[test]
    fn neither_stored_file_ever_contains_the_plaintext_key() {
        let dir = tempfile::tempdir().unwrap();
        let secret = "sk-super-secret-do-not-leak-zzzzz";
        set_key(dir.path(), "openai-compatible", secret).unwrap();
        let creds = std::fs::read(dir.path().join("credentials.enc")).unwrap();
        let key = std::fs::read(dir.path().join("key")).unwrap();
        assert!(!creds.windows(secret.len()).any(|w| w == secret.as_bytes()));
        assert!(!key.windows(secret.len()).any(|w| w == secret.as_bytes()));
    }

    #[test]
    fn saving_a_second_provider_keeps_the_first() {
        let dir = tempfile::tempdir().unwrap();
        set_key(dir.path(), "voyage", "voy-key").unwrap();
        set_key(dir.path(), "jina", "jina-key").unwrap();
        assert_eq!(
            get_key(dir.path(), "voyage").unwrap().as_deref(),
            Some("voy-key")
        );
        assert_eq!(
            get_key(dir.path(), "jina").unwrap().as_deref(),
            Some("jina-key")
        );
    }

    /// Regression for the TOCTOU in `load_or_create_key`: many threads
    /// racing the very first key creation on a fresh config dir must all
    /// converge on the SAME key, never each generate and persist their own
    /// — a loser silently overwriting the winner's key would permanently
    /// strand any `credentials.enc` already encrypted under it.
    #[test]
    fn concurrent_first_time_key_creation_converges_on_one_key() {
        let dir = tempfile::tempdir().unwrap();
        let dir_path = dir.path().to_path_buf();
        let handles: Vec<_> = (0..8)
            .map(|_| {
                let dir_path = dir_path.clone();
                std::thread::spawn(move || load_or_create_key(&dir_path).unwrap())
            })
            .collect();
        let keys: Vec<[u8; KEY_LEN]> = handles.into_iter().map(|h| h.join().unwrap()).collect();
        let first = keys[0];
        assert!(
            keys.iter().all(|k| *k == first),
            "every racing caller must observe the same winning key"
        );
    }

    #[cfg(unix)]
    #[test]
    fn keyfile_is_created_owner_only() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        set_key(dir.path(), "voyage", "voy-key").unwrap();
        let mode = std::fs::metadata(dir.path().join("key"))
            .unwrap()
            .permissions()
            .mode()
            & 0o777;
        assert_eq!(mode, 0o600);
    }
}
