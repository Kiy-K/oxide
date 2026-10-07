//! Task inputs: query text, typed hints and the context budget. These are
//! domain values, independent of any integration's request objects.

use crate::id::RepoPath;
use crate::knowledge::ByteRange;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Query {
    pub text: String,
}

/// Explicit hints supplied with the task. Paths are already validated
/// repository paths; symbol hints are names as the user wrote them, resolved
/// (or not) by the kernel against the pinned snapshot.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct QueryContext {
    pub paths: Vec<RepoPath>,
    pub symbols: Vec<String>,
    pub selection: Option<(RepoPath, ByteRange)>,
    pub changed: Vec<RepoPath>,
}

/// Payload-token allowance for the final bundle plus the counting contract it
/// is measured under. Separate from retrieval/traversal limits. The concrete
/// tokenizer is open until Phase 4 packing.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ContextBudget {
    pub tokens: u32,
    pub counter: TokenCounter,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TokenCounter {
    pub name: String,
    /// `true` when `tokens` is a hard bound under this counter; `false` for an
    /// estimate that must be labelled as one.
    pub strict: bool,
}
