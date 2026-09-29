//! The embedding-space authority: what an index's stored metadata says about
//! the vectors it holds, read once ([`EmbeddingSpace::read`]) and
//! interpreted only here. The indexer asks it whether to migrate
//! ([`EmbeddingSpace::plan_write`]), `validate_index` whether a provider may
//! read the vectors ([`EmbeddingSpace::readable_by`]), and `status` the
//! network-free subset ([`EmbeddingSpace::locally_current`]). Side effects
//! (clearing, marking, re-embedding) stay with the callers.

use crate::embeddings::{
    EmbeddingSpaceFingerprint, EMBEDDING_FINGERPRINT_SCHEMA_VERSION, SYMBOL_TEXT_RECIPE,
};
use crate::storage::{IndexRead, EMBEDDING_FINGERPRINT_KEY, EMBEDDING_MIGRATION_KEY};
use anyhow::Result;

/// The stored embedding space, strongest evidence first:
/// 1. [`EMBEDDING_MIGRATION_KEY`] — an unfinished migration. Only ever
///    written in the same transaction that empties the embeddings table, so
///    it, not the published metadata (which the interrupted run never
///    reached), describes the surviving rows.
/// 2. [`EMBEDDING_FINGERPRINT_KEY`] — the published space. A schema-1
///    fingerprint (before #33) parses with an empty text recipe, so it never
///    equals a current one.
/// 3. Neither: the vectors' space was never recorded, not even their text
///    recipe (#33). The legacy `embedder` + `dim` pair cannot vouch for it.
///
/// An empty value counts as absent. A present value that does not parse is
/// kept as unreadable at its own tier and is never compatible: "unreadable"
/// must not fail open into the weaker tier below it.
#[derive(Debug)]
pub struct EmbeddingSpace(State);

#[derive(Debug)]
enum State {
    Migrating(Stored),
    Published(Stored),
    /// New or base-only (no vectors), or unversioned (vectors). Telling them
    /// apart needs a vector scan, which only [`EmbeddingSpace::plan_write`]
    /// pays for.
    Unrecorded,
}

// One short-lived value per request or index run; boxing buys nothing.
#[allow(clippy::large_enum_variant)]
#[derive(Debug)]
enum Stored {
    Parsed(EmbeddingSpaceFingerprint),
    Unreadable,
}

/// May a provider read these vectors? The read side's three outcomes.
#[derive(Debug, PartialEq, Eq)]
pub enum SpaceRead {
    Compatible,
    /// A migration is in flight, even one to this provider: its rows are
    /// incomplete until `oxide index` publishes it.
    Migrating,
    /// Another space, an unreadable fingerprint, or none recorded.
    Mismatch,
}

/// What the indexer must do before writing vectors under a provider.
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum SpaceWrite {
    /// The stored vectors belong to another (or an unknown) space: clear
    /// them and re-embed everything. Carries the reason to report.
    Migrate(String),
    /// An interrupted run of this same provider: its rows are ours to finish.
    Resume,
    /// Nothing published and no vectors: mark the space before the first
    /// write, as a migration does, so an interrupted first run resumes.
    FirstRun,
    /// The published space is this provider's: reuse incrementally.
    Reuse,
}

impl EmbeddingSpace {
    /// Read the stored space. Meta reads only; never scans vectors.
    pub fn read(store: &dyn IndexRead) -> Result<Self> {
        let stored = |key| -> Result<Option<Stored>> {
            Ok(store
                .get_meta(key)?
                .filter(|s| !s.is_empty())
                .map(|raw| serde_json::from_str(&raw).map_or(Stored::Unreadable, Stored::Parsed)))
        };
        Ok(Self(
            if let Some(marker) = stored(EMBEDDING_MIGRATION_KEY)? {
                State::Migrating(marker)
            } else if let Some(published) = stored(EMBEDDING_FINGERPRINT_KEY)? {
                State::Published(published)
            } else {
                State::Unrecorded
            },
        ))
    }

    /// Whether `current`'s queries may be scored against the stored vectors.
    /// Only a published fingerprint equal to `current` in full qualifies.
    pub fn readable_by(&self, current: &EmbeddingSpaceFingerprint) -> SpaceRead {
        match &self.0 {
            State::Migrating(_) => SpaceRead::Migrating,
            State::Published(Stored::Parsed(prev)) if prev == current => SpaceRead::Compatible,
            State::Published(_) | State::Unrecorded => SpaceRead::Mismatch,
        }
    }

    /// The network-free part of the compatibility check (#33), for
    /// `status`: no migration in flight, and a published fingerprint of the
    /// current fingerprint schema and [`SYMBOL_TEXT_RECIPE`]. The rest of the
    /// fingerprint needs a live provider, which `status` never builds.
    pub(crate) fn locally_current(&self) -> bool {
        matches!(
            &self.0,
            State::Published(Stored::Parsed(fp))
                if fp.schema_version == EMBEDDING_FINGERPRINT_SCHEMA_VERSION
                    && fp.document_text_recipe == SYMBOL_TEXT_RECIPE
        )
    }

    /// What indexing under `current` must do first. `has_vectors` is asked
    /// only when nothing is recorded: vectors of unrecorded space are never
    /// reused, while an index without any has nothing to migrate.
    pub(crate) fn plan_write(
        &self,
        current: &EmbeddingSpaceFingerprint,
        has_vectors: impl FnOnce() -> Result<bool>,
    ) -> Result<SpaceWrite> {
        Ok(match &self.0 {
            State::Migrating(Stored::Parsed(prev)) if prev == current => SpaceWrite::Resume,
            State::Migrating(Stored::Parsed(prev)) => SpaceWrite::Migrate(format!(
                "an interrupted migration to {} left this index's vectors mid-flight; re-embedding all symbols under {}",
                prev.model, current.model
            )),
            State::Migrating(Stored::Unreadable) => SpaceWrite::Migrate(
                "an interrupted embedding migration left an unreadable fingerprint; re-embedding all symbols".to_string(),
            ),
            State::Published(Stored::Parsed(prev)) if prev == current => SpaceWrite::Reuse,
            State::Published(Stored::Parsed(prev))
                if prev.document_text_recipe != current.document_text_recipe =>
            {
                SpaceWrite::Migrate(format!(
                    "embedding text recipe changed ({} -> {}); re-embedding all symbols",
                    if prev.document_text_recipe.is_empty() {
                        "unrecorded"
                    } else {
                        &prev.document_text_recipe
                    },
                    current.document_text_recipe
                ))
            }
            State::Published(Stored::Parsed(prev)) => SpaceWrite::Migrate(format!(
                "embedding space changed ({} -> {}); re-embedding all symbols",
                prev.model, current.model
            )),
            State::Published(Stored::Unreadable) => SpaceWrite::Migrate(
                "stored embedding fingerprint is unreadable; re-embedding all symbols".to_string(),
            ),
            State::Unrecorded if has_vectors()? => SpaceWrite::Migrate(
                "stored vectors have no embedding fingerprint; re-embedding all symbols"
                    .to_string(),
            ),
            State::Unrecorded => SpaceWrite::FirstRun,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::embeddings::{EmbeddingProvider, HashedEmbedder};

    fn current() -> EmbeddingSpaceFingerprint {
        HashedEmbedder::default().fingerprint()
    }

    fn no_scan() -> Result<bool> {
        panic!("only an unrecorded space may scan vectors")
    }

    /// The legacy schema-1 fingerprint (#33) is readable but never current:
    /// it migrates on write, is refused on read, and is not locally current.
    #[test]
    fn schema_1_fingerprint_is_legacy_everywhere() {
        let legacy = EmbeddingSpaceFingerprint {
            schema_version: 1,
            document_text_recipe: String::new(),
            ..current()
        };
        let s = EmbeddingSpace(State::Published(Stored::Parsed(legacy)));
        assert_eq!(
            s.plan_write(&current(), no_scan).unwrap(),
            SpaceWrite::Migrate(format!(
                "embedding text recipe changed (unrecorded -> {SYMBOL_TEXT_RECIPE}); re-embedding all symbols"
            ))
        );
        assert_eq!(s.readable_by(&current()), SpaceRead::Mismatch);
        assert!(!s.locally_current());
    }

    /// A migration marker outranks everything, including a marker that
    /// matches the reader: reads refuse, the same provider's write resumes.
    #[test]
    fn marker_outranks_the_published_fingerprint() {
        let s = EmbeddingSpace(State::Migrating(Stored::Parsed(current())));
        assert_eq!(s.readable_by(&current()), SpaceRead::Migrating);
        assert!(!s.locally_current());
        assert_eq!(
            s.plan_write(&current(), no_scan).unwrap(),
            SpaceWrite::Resume
        );
    }

    /// Only an unrecorded space depends on whether vectors exist.
    #[test]
    fn unrecorded_space_is_a_first_run_only_without_vectors() {
        let s = EmbeddingSpace(State::Unrecorded);
        assert_eq!(
            s.plan_write(&current(), || Ok(false)).unwrap(),
            SpaceWrite::FirstRun
        );
        assert!(matches!(
            s.plan_write(&current(), || Ok(true)).unwrap(),
            SpaceWrite::Migrate(_)
        ));
        assert_eq!(s.readable_by(&current()), SpaceRead::Mismatch);
        assert!(!s.locally_current());
    }

    #[test]
    fn unreadable_values_never_fail_open() {
        for s in [
            EmbeddingSpace(State::Migrating(Stored::Unreadable)),
            EmbeddingSpace(State::Published(Stored::Unreadable)),
        ] {
            assert!(matches!(
                s.plan_write(&current(), no_scan).unwrap(),
                SpaceWrite::Migrate(_)
            ));
            assert_ne!(s.readable_by(&current()), SpaceRead::Compatible);
            assert!(!s.locally_current());
        }
    }

    #[test]
    fn the_current_published_space_is_reused_and_readable() {
        let s = EmbeddingSpace(State::Published(Stored::Parsed(current())));
        assert_eq!(
            s.plan_write(&current(), no_scan).unwrap(),
            SpaceWrite::Reuse
        );
        assert_eq!(s.readable_by(&current()), SpaceRead::Compatible);
        assert!(s.locally_current());
    }
}
