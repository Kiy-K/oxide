//! Lexical retrieval contract: OXIDE-owned code-aware terms, and the bounded
//! search request/result every store answers. Stores score with their own
//! named scorer; the kernel fixes what is indexed, how queries are split,
//! the order of hits and what a score means (nothing across scorers).

use crate::id::EntityId;
use crate::knowledge::Entity;

/// Version of [`terms`] and [`document`]. Changing either changes what a
/// generation's lexical index holds, so it is a derivation component.
pub const TERMS_VERSION: &str = "oxide-terms-v1";

/// Splits text into lowercase ASCII-alphanumeric terms at non-alphanumeric
/// characters, lower→upper case changes (`fooBar`), acronym ends
/// (`HTTPClient` → `http`, `client`) and letter/digit changes. An identifier
/// of several parts also yields its parts joined (`retry_policy`,
/// `RetryPolicy` → `retrypolicy`), so exact identifiers stay distinctive.
/// Non-ASCII characters separate terms. One-character terms are dropped
/// (the pinned FTS never matches them, so no store does): a name like `q` is
/// not lexically findable, only through a symbol hint. Stores may drop more
/// (the FTS default stopwords); that is part of their named scorer.
pub fn terms(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    for word in text.split(|c: char| !(c.is_ascii_alphanumeric() || c == '_')) {
        let parts = split_identifier(word);
        let joined = (parts.len() > 1).then(|| parts.concat());
        out.extend(parts.into_iter().chain(joined).filter(|t| t.len() > 1));
    }
    out
}

fn split_identifier(word: &str) -> Vec<String> {
    let chars: Vec<char> = word.chars().collect();
    let mut parts = Vec::new();
    let mut current = String::new();
    for (i, &c) in chars.iter().enumerate() {
        if c == '_' {
            if !current.is_empty() {
                parts.push(std::mem::take(&mut current));
            }
            continue;
        }
        if let Some(prev) = chars[..i].last().filter(|_| !current.is_empty()) {
            let next_lower = chars.get(i + 1).is_some_and(char::is_ascii_lowercase);
            let boundary = (prev.is_ascii_lowercase() && c.is_ascii_uppercase())
                || (prev.is_ascii_uppercase() && c.is_ascii_uppercase() && next_lower)
                || (prev.is_ascii_digit() != c.is_ascii_digit());
            if boundary {
                parts.push(std::mem::take(&mut current));
            }
        }
        current.push(c.to_ascii_lowercase());
    }
    if !current.is_empty() {
        parts.push(current);
    }
    parts
}

/// The lexical document of one entity: terms of its name, signature and
/// file path. The repository entity has none.
pub fn document(entity: &Entity) -> String {
    let path = match &entity.id {
        EntityId::Repository => return String::new(),
        EntityId::Module(_) => None,
        EntityId::File(path) => Some(path),
        EntityId::Symbol(symbol) => Some(symbol.file()),
    };
    let text = [
        Some(entity.name.as_str()),
        entity.signature.as_deref(),
        path.map(|p| p.as_str()),
    ];
    text.into_iter()
        .flatten()
        .flat_map(terms)
        .collect::<Vec<_>>()
        .join(" ")
}

/// Lexical search over one pinned generation. Scope is the view, so the
/// snapshot filter always precedes `limit`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LexicalRequest {
    /// Query terms, from [`terms`].
    pub terms: Vec<String>,
    pub limit: usize,
}

#[derive(Debug, Clone, PartialEq)]
pub struct LexicalHit {
    pub entity: EntityId,
    /// On the scale of [`LexicalResult::scorer`] only; finite and positive.
    pub score: f64,
}

/// Hits in descending score, ties by [`EntityId`]; at most `limit`, with
/// `truncated` when more matched. Entities that match nothing are absent,
/// never a zero score.
#[derive(Debug, Clone, PartialEq)]
pub struct LexicalResult {
    /// Names the scoring function and its parameters. Scores from different
    /// scorers are not comparable.
    pub scorer: String,
    pub hits: Vec<LexicalHit>,
    pub truncated: bool,
}

/// Sorts scored matches into the result order and applies `limit`.
/// Non-finite or non-positive scores are a store fault.
pub fn rank(
    scorer: String,
    mut hits: Vec<LexicalHit>,
    limit: usize,
) -> Result<LexicalResult, String> {
    if let Some(bad) = hits
        .iter()
        .find(|h| !(h.score.is_finite() && h.score > 0.0))
    {
        return Err(format!(
            "invalid lexical score {} for {:?}",
            bad.score, bad.entity
        ));
    }
    hits.sort_by(|a, b| {
        b.score
            .total_cmp(&a.score)
            .then_with(|| a.entity.cmp(&b.entity))
    });
    let truncated = hits.len() > limit;
    hits.truncate(limit);
    Ok(LexicalResult {
        scorer,
        hits,
        truncated,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identifiers_split_into_parts_and_keep_their_joined_form() {
        assert_eq!(terms("RetryPolicy"), ["retry", "policy", "retrypolicy"]);
        assert_eq!(terms("should_retry"), ["should", "retry", "shouldretry"]);
        assert_eq!(terms("HTTPClient"), ["http", "client", "httpclient"]);
        assert_eq!(terms("TTLCache"), ["ttl", "cache", "ttlcache"]);
        assert_eq!(terms("sha256"), ["sha", "256", "sha256"]);
        assert_eq!(
            terms("def fetch(self, url: str)"),
            ["def", "fetch", "self", "url", "str"]
        );
        assert_eq!(terms("oxidepy/auth.py"), ["oxidepy", "auth", "py"]);
        assert_eq!(terms("__init__ é x"), ["init"]);
        assert_eq!(terms("v2 a.rs"), ["v2", "rs"]);
        assert!(terms("q t 9").is_empty());
        assert!(terms("  ,;").is_empty());
    }

    #[test]
    fn rank_orders_by_score_then_identity_and_rejects_bad_scores() {
        let hit = |p: &str, score| LexicalHit {
            entity: EntityId::File(crate::id::RepoPath::new(p).unwrap()),
            score,
        };
        let hits = vec![hit("b", 1.0), hit("c", 2.0), hit("a", 1.0)];
        let ranked = rank("s".into(), hits, 2).unwrap();
        assert_eq!(ranked.hits, [hit("c", 2.0), hit("a", 1.0)]);
        assert!(ranked.truncated);
        assert!(rank("s".into(), vec![hit("a", f64::NAN)], 1).is_err());
        assert!(rank("s".into(), vec![hit("a", 0.0)], 1).is_err());
    }
}
