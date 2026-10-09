//! ContextPacker (SPEC § Context packing): a [`SelectionPlan`] plus
//! digest-verified captured source becomes a [`ContextBundle`] whose
//! canonical payload never exceeds the declared budget.
//!
//! **Budget unit `oxide-units-v1`.** OXIDE does not claim any downstream
//! model's tokenizer. The canonical count is: each maximal run of
//! alphanumeric characters or `_` costs `ceil(chars / 4)`, each `\n` costs
//! 1, other whitespace costs 0, and every other character costs 1. It is
//! exact and strict for this unit only. Every rendered item ends with `\n`,
//! so the payload's count is the sum of its items' counts.
//!
//! **Payload `oxide-payload-v1`.** Items in plan order (a group's
//! prerequisite headers right before it), each rendered as
//! `### <path>:<first line>-<last line> <names>\n<source bytes>\n`, where a
//! name marked `(header)` is shown only up to its declaration line(s). An
//! empty bundle has an empty payload and costs 0. Everything else in the
//! bundle (omissions, provenance, counts) is diagnostic envelope, outside
//! the budget.
//!
//! **Rules.** Source is hydrated only by digest and verified with the
//! kernel's SHA-256; failing bytes omit the group. Overlapping ranges of
//! one file merge into one item (union range, counted once, every entity
//! and reason kept). A group (an item and its prerequisite headers) is
//! packed whole, reduced to its declared header view, or omitted with a
//! reason; bytes are never cut elsewhere and the budget is never exceeded
//! to force an item in.

use crate::id::{Digest, EntityId, RepoPath, SnapshotKey};
use crate::knowledge::{ByteRange, SourceRef};
use crate::query::{ContextBudget, TokenCounter};
use crate::select::{Omission, OmitReason, PlannedItem, Reason, SelectionPlan, Stage};
use crate::source::{SourceIssue, Sources};
use crate::store::StoreError;

pub const COUNTER: &str = "oxide-units-v1";
pub const PAYLOAD_VERSION: &str = "oxide-payload-v1";
/// A header view spans at most this many lines.
pub const MAX_HEADER_LINES: usize = 8;

/// The canonical budget counter. Strict for `oxide-units-v1` only.
pub fn counter() -> TokenCounter {
    TokenCounter {
        name: COUNTER.into(),
        strict: true,
    }
}

pub fn count(text: &str) -> u32 {
    let mut units = 0u32;
    let mut run = 0u32;
    for c in text.chars() {
        if c.is_alphanumeric() || c == '_' {
            run += 1;
            continue;
        }
        units += run.div_ceil(4);
        run = 0;
        if c == '\n' || !c.is_whitespace() {
            units += 1;
        }
    }
    units + run.div_ceil(4)
}

/// Which source view of an entity an item shows.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum View {
    /// The entity's whole source range.
    Full,
    /// From the range start through the first line naming the entity (at
    /// most [`MAX_HEADER_LINES`]; the first line when none does): the
    /// declaration without its body. Shown as `(header)`, never as complete.
    Header,
}

/// The byte range a view covers, or `None` when a header would not be
/// smaller than the full range (so it is no reduction).
pub fn view_range(bytes: &[u8], range: ByteRange, view: View, name: &str) -> Option<ByteRange> {
    if view == View::Full {
        return Some(range);
    }
    let body = &bytes[range.start as usize..range.end as usize];
    let mut end = None;
    let mut first = None;
    let mut at = 0usize;
    for (i, line) in body.split_inclusive(|b| *b == b'\n').enumerate() {
        if i == MAX_HEADER_LINES {
            break;
        }
        at += line.len();
        first.get_or_insert(at);
        if names(line, name.as_bytes()) {
            end = Some(at);
            break;
        }
    }
    let end = end.or(first)? as u64;
    (end < body.len() as u64).then_some(ByteRange {
        start: range.start,
        end: range.start + end,
    })
}

/// `line` contains `name` as a whole identifier.
fn names(line: &[u8], name: &[u8]) -> bool {
    let ident = |b: u8| b.is_ascii_alphanumeric() || b == b'_' || b >= 0x80;
    !name.is_empty()
        && line.windows(name.len()).enumerate().any(|(i, w)| {
            w == name
                && (i == 0 || !ident(line[i - 1]))
                && line.get(i + name.len()).is_none_or(|b| !ident(*b))
        })
}

/// Human-facing entity name: `Outer.inner`, `~n` for a nonzero ordinal.
pub fn display(id: &EntityId) -> String {
    match id {
        EntityId::Repository => "repository".into(),
        EntityId::Module(m) => m.as_str().into(),
        EntityId::File(p) => p.as_str().into(),
        EntityId::Symbol(s) => s
            .path()
            .iter()
            .map(|seg| match seg.ordinal {
                0 => seg.name.clone(),
                n => format!("{}~{n}", seg.name),
            })
            .collect::<Vec<_>>()
            .join("."),
    }
}

/// The declared name a header view looks for.
pub fn declared_name(id: &EntityId) -> &str {
    match id {
        EntityId::Symbol(s) => &s.path().last().expect("non-empty").name,
        EntityId::Module(m) => m.as_str(),
        EntityId::File(p) => p.as_str(),
        EntityId::Repository => "",
    }
}

/// 1-based first and last line of `range` (ADR-0010 line view).
pub fn lines(bytes: &[u8], range: ByteRange) -> (u64, u64) {
    let line = |at: u64| 1 + bytes[..at as usize].iter().filter(|b| **b == b'\n').count() as u64;
    let last = range.end.max(range.start + 1) - 1;
    (line(range.start), line(last.min(bytes.len() as u64)))
}

/// One rendered item, or `None` when the bytes are not UTF-8 text.
pub fn render(
    path: &RepoPath,
    bytes: &[u8],
    range: ByteRange,
    labels: &[String],
) -> Option<String> {
    let text = std::str::from_utf8(&bytes[range.start as usize..range.end as usize]).ok()?;
    let (first, last) = lines(bytes, range);
    let mut out = format!(
        "### {}:{first}-{last} {}\n{text}",
        path.as_str(),
        labels.join(", ")
    );
    if !out.ends_with('\n') {
        out.push('\n');
    }
    Some(out)
}

/// One entity's view inside an item.
#[derive(Debug, Clone, PartialEq)]
pub struct ItemEntity {
    pub entity: EntityId,
    /// The item covers the entity's whole source range.
    pub complete: bool,
    pub reason: Reason,
}

/// A source-backed view of one or more entities of one file.
#[derive(Debug, Clone, PartialEq)]
pub struct ContextItem {
    /// File, covered byte range and the digest the bytes were verified against.
    pub source: SourceRef,
    /// 1-based first and last line.
    pub lines: (u64, u64),
    pub entities: Vec<ItemEntity>,
    pub tokens: u32,
    /// The canonical rendering, label included.
    pub text: String,
}

/// Stage-level reasons the bundle may be incomplete.
#[derive(Debug, Clone, PartialEq)]
pub enum Degradation {
    /// A retrieval channel did not run (unconfigured, unavailable, disabled).
    Channel(crate::retrieve::ChannelStatus),
    /// Routing stopped at a bound.
    Route(crate::route::Bound),
    /// Routed candidates beyond the graph's node limit.
    GraphTruncated { omitted: usize },
    /// Judgments that fell back to the heuristic, by reason (provider runs only).
    Fallback {
        reason: crate::decision::Fallback,
        count: usize,
    },
    /// Expansion stopped at a bound.
    Expansion(crate::select::ExpansionBound),
}

/// Versions that make two bundles comparable.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Versions {
    pub retrieval: &'static str,
    pub router: &'static str,
    pub capsule: u32,
    pub selector: &'static str,
    pub payload: &'static str,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ContextBundle {
    pub snapshot: SnapshotKey,
    pub query_digest: Digest,
    pub versions: Versions,
    pub budget: ContextBudget,
    pub used_tokens: u32,
    pub items: Vec<ContextItem>,
    /// Plan exclusions (selection/expansion) and packing omissions.
    pub omitted: Vec<Omission>,
    pub degraded: Vec<Degradation>,
}

impl ContextBundle {
    /// The canonical agent-facing payload; `count(payload) == used_tokens`.
    pub fn payload(&self) -> String {
        self.items.iter().map(|i| i.text.as_str()).collect()
    }

    pub fn remaining_tokens(&self) -> u32 {
        self.budget.tokens - self.used_tokens
    }
}

/// A hydrated span before merging.
#[derive(Debug, Clone)]
struct Span {
    file: RepoPath,
    digest: Digest,
    bytes: std::sync::Arc<Vec<u8>>,
    range: ByteRange,
    /// (entity, its full range, reason)
    entities: Vec<(EntityId, ByteRange, Reason)>,
}

/// Packs `plan` under `budget`. Store errors from the source provider
/// propagate; missing or mismatched bytes are omissions, not errors.
pub fn pack(
    plan: &SelectionPlan,
    sources: &mut Sources<'_>,
    query_digest: Digest,
    versions: Versions,
    budget: &ContextBudget,
) -> Result<ContextBundle, StoreError> {
    let mut packed: Vec<Span> = Vec::new();
    let mut used = 0u32;
    let mut omitted = plan.excluded.clone();
    for item in &plan.items {
        let omit = |reason| Omission {
            entity: item.entity.clone(),
            stage: Stage::Packing,
            reason,
        };
        let mut views = vec![item.view];
        if item.view == View::Full {
            views.push(View::Header);
        }
        let mut outcome = Err(OmitReason::BudgetExceeded {
            needed: 0,
            remaining: budget.tokens - used,
        });
        for view in views {
            let spans = match group(item, view, sources)? {
                Ok(Some(spans)) => spans,
                Ok(None) => continue, // no smaller header view
                Err(reason) => {
                    outcome = Err(reason);
                    break;
                }
            };
            let mut tentative = packed.clone();
            for span in spans {
                merge(&mut tentative, span);
            }
            let Some(total) = total(&tentative) else {
                outcome = Err(OmitReason::NotText);
                break;
            };
            if total <= budget.tokens {
                packed = tentative;
                used = total;
                outcome = Ok(());
                break;
            }
            outcome = Err(if total - used > budget.tokens {
                OmitReason::TooLarge {
                    needed: total - used,
                }
            } else {
                OmitReason::BudgetExceeded {
                    needed: total - used,
                    remaining: budget.tokens - used,
                }
            });
        }
        if let Err(reason) = outcome {
            omitted.push(omit(reason));
        }
    }
    let items = packed
        .iter()
        .map(|s| {
            let text = span_text(s).expect("checked by total");
            ContextItem {
                source: SourceRef {
                    file: s.file.clone(),
                    range: s.range,
                    digest: s.digest.clone(),
                },
                lines: lines(&s.bytes, s.range),
                entities: s
                    .entities
                    .iter()
                    .map(|(entity, full, reason)| ItemEntity {
                        entity: entity.clone(),
                        complete: s.range.start <= full.start && full.end <= s.range.end,
                        reason: reason.clone(),
                    })
                    .collect(),
                tokens: count(&text),
                text,
            }
        })
        .collect();
    Ok(ContextBundle {
        snapshot: plan.snapshot.clone(),
        query_digest,
        versions,
        budget: budget.clone(),
        used_tokens: used,
        items,
        omitted,
        degraded: Vec::new(),
    })
}

/// The spans of one planned item under `view`: prerequisite headers first,
/// then the item. `Ok(None)`: `view` is a header that would not reduce.
#[allow(clippy::type_complexity)]
fn group(
    item: &PlannedItem,
    view: View,
    sources: &mut Sources<'_>,
) -> Result<Result<Option<Vec<Span>>, OmitReason>, StoreError> {
    let mut spans = Vec::new();
    let parts = item
        .prerequisites
        .iter()
        .map(|p| {
            let reason = Reason::Prerequisite {
                of: item.entity.clone(),
            };
            (&p.entity, &p.source, View::Header, reason)
        })
        .chain([(&item.entity, &item.source, view, item.reason.clone())]);
    for (entity, source, view, reason) in parts {
        let bytes = match sources.file(&source.file, &source.digest)? {
            Ok(bytes) => bytes,
            Err(issue) => {
                return Ok(Err(match issue {
                    SourceIssue::Unavailable => OmitReason::SourceUnavailable,
                    SourceIssue::Mismatch => OmitReason::SourceMismatch,
                }));
            }
        };
        if source.range.end as usize > bytes.len() || source.range.start > source.range.end {
            return Ok(Err(OmitReason::SourceMismatch));
        }
        let Some(range) = view_range(&bytes, source.range, view, declared_name(entity)) else {
            if entity == &item.entity {
                return Ok(Ok(None));
            }
            // A prerequisite whose header is its whole range: show it whole.
            spans.push(Span {
                file: source.file.clone(),
                digest: source.digest.clone(),
                bytes,
                range: source.range,
                entities: vec![(entity.clone(), source.range, reason)],
            });
            continue;
        };
        spans.push(Span {
            file: source.file.clone(),
            digest: source.digest.clone(),
            bytes,
            range,
            entities: vec![(entity.clone(), source.range, reason)],
        });
    }
    Ok(Ok(Some(spans)))
}

/// Adds `span`, merging it with every overlapping span of the same file
/// bytes into the earliest one's position.
fn merge(spans: &mut Vec<Span>, mut span: Span) {
    let overlaps = |a: &Span, b: &Span| {
        a.file == b.file
            && a.digest == b.digest
            && a.range.start < b.range.end
            && b.range.start < a.range.end
    };
    let mut at = None;
    let mut i = 0;
    while i < spans.len() {
        if overlaps(&spans[i], &span) {
            let other = spans.remove(i);
            span.range = ByteRange {
                start: other.range.start.min(span.range.start),
                end: other.range.end.max(span.range.end),
            };
            let mut entities = other.entities;
            for e in span.entities {
                if !entities.iter().any(|(id, _, r)| *id == e.0 && *r == e.2) {
                    entities.push(e);
                }
            }
            span.entities = entities;
            at = Some(at.map_or(i, |a: usize| a.min(i)));
            // The union may now overlap a span already passed: rescan.
            i = 0;
            continue;
        }
        i += 1;
    }
    match at {
        Some(i) => spans.insert(i.min(spans.len()), span),
        None => spans.push(span),
    }
}

fn span_text(span: &Span) -> Option<String> {
    // Label: each entity once, in first-seen order; `(header)` unless the
    // span covers that entity's whole range.
    let mut labels: Vec<String> = Vec::new();
    let mut seen: Vec<&EntityId> = Vec::new();
    for (entity, full, _) in &span.entities {
        if seen.contains(&entity) {
            continue;
        }
        seen.push(entity);
        let complete = span.range.start <= full.start && full.end <= span.range.end;
        let name = display(entity);
        labels.push(if complete {
            name
        } else {
            format!("{name} (header)")
        });
    }
    render(&span.file, &span.bytes, span.range, &labels)
}

fn total(spans: &[Span]) -> Option<u32> {
    spans.iter().map(|s| span_text(s).map(|t| count(&t))).sum()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn units_follow_the_declared_rule() {
        assert_eq!(count(""), 0);
        assert_eq!(count("abcd"), 1);
        assert_eq!(count("abcde"), 2);
        assert_eq!(count("a b\n"), 3);
        assert_eq!(count("f(x)"), 4);
        assert_eq!(count("snake_case_name"), 4);
        assert_eq!(count("é"), 1);
        // Additive across items that end in a newline.
        assert_eq!(count("ab\ncd\n"), count("ab\n") + count("cd\n"));
    }

    #[test]
    fn header_views_stop_at_the_declaring_line() {
        let src = b"@decorator\ndef f(a,\n      b):\n    return a\n";
        let full = ByteRange {
            start: 0,
            end: src.len() as u64,
        };
        let header = view_range(src, full, View::Header, "f").unwrap();
        assert_eq!(&src[..header.end as usize], b"@decorator\ndef f(a,\n");
        // A one-line entity has no smaller header.
        let one = b"x = 1";
        let r = ByteRange { start: 0, end: 5 };
        assert_eq!(view_range(one, r, View::Header, "x"), None);
        assert!(names(b"def f(", b"f") && !names(b"def ff(", b"f"));
    }

    #[test]
    fn line_view_is_one_based() {
        let src = b"a\nbc\nd";
        assert_eq!(lines(src, ByteRange { start: 2, end: 5 }), (2, 2));
        assert_eq!(lines(src, ByteRange { start: 0, end: 6 }), (1, 3));
        assert_eq!(lines(src, ByteRange { start: 3, end: 3 }), (2, 2));
    }
}
