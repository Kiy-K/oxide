//! CandidateCapsule v2 (SPEC § Candidate capsule): the bounded, typed,
//! storage-free evidence a judge sees about one subject for one question.
//! The same canonical rendering ([`Capsule::render`]) is what a remote
//! judge receives as state, what [`Capsule::digest`] hashes, and what a
//! dataset record stores; training wraps it with labels, never a second
//! encoding. A capsule never carries gold labels, policy outcomes,
//! inclusion results or downstream success.
//!
//! Bounds are applied here, deterministically, before any provider sees
//! the capsule: text is cut at a char boundary (snippets at the last
//! newline that fits), lists keep their first items in the order given, and
//! every cut sets a flag. Relation evidence spans are dropped (the relation
//! itself stays), so the struct and its rendering hold the same content.

use crate::digest::digest;
use crate::id::{Digest, EntityId, SnapshotKey};
use crate::knowledge::{
    Basis, Containment, Coverage, Entity, Relation, RelationKind, SourceRef, Target,
    physical_parent,
};
use crate::query::Query;
use crate::retrieve::{Channel, ChannelEvidence, HintKind, Seed};
use crate::route::Origin;
use crate::store::Direction;
use crate::tree::{Region, RegionId};

pub const CAPSULE_VERSION: u32 = 2;
pub const MAX_CAPSULE_QUERY_BYTES: usize = 2048;
pub const MAX_CAPSULE_SIGNATURE_BYTES: usize = 512;
pub const MAX_CAPSULE_SNIPPET_BYTES: usize = 2048;
/// Per text item: kind, name, hints, diagnostics, unresolved names.
/// Ambiguous target lists hold at most [`MAX_CAPSULE_PROVENANCE`] ids.
pub const MAX_CAPSULE_TEXT_BYTES: usize = 256;
pub const MAX_CAPSULE_RELATIONS: usize = 16;
/// Per provenance list: channels, seeds, origins, coverage diagnostics.
pub const MAX_CAPSULE_PROVENANCE: usize = 8;

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Subject {
    Candidate(EntityId),
    Region(RegionId),
}

/// The narrow questions a judge may answer, JEV included. None of them is
/// "is this code correct".
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Question {
    /// Is this candidate useful context for the task?
    Relevance,
    /// Would someone doing the task likely need to read this candidate?
    Necessity,
    /// Would this neighbor add value next to already chosen evidence?
    NeighborValue,
    /// Is this region worth exploring?
    BranchValue,
}

/// Bounded source view of the subject.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Snippet {
    Text {
        text: String,
        truncated: bool,
    },
    /// No source range (repository, module), or not hydrated for this
    /// subject (branch capsules during routing).
    Absent,
    /// The captured bytes are not retained, failed digest verification, or
    /// are not UTF-8. Never a snippet from other bytes.
    Unavailable,
}

/// Evidence about one subject, as the caller found it. [`Capsule::new`]
/// bounds every part.
#[derive(Debug, Clone, Copy)]
pub struct Evidence<'a> {
    pub entity: &'a Entity,
    pub coverage: Option<&'a Coverage>,
    pub snippet: &'a Snippet,
    pub channels: &'a [ChannelEvidence],
    pub seeds: &'a [Seed],
    /// TreeRouter depth; `None` when the subject was not routed (an
    /// expansion neighbor).
    pub depth: Option<u32>,
    pub origins: &'a [Origin],
    pub relations: &'a [Relation],
    pub relations_truncated: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Capsule {
    pub version: u32,
    pub snapshot: SnapshotKey,
    pub subject: Subject,
    pub question: Question,
    pub query: String,
    pub query_truncated: bool,
    /// Digest of the whole query text, truncated or not.
    pub query_digest: Digest,
    pub kind: String,
    pub name: String,
    pub signature: Option<String>,
    /// Kind, name or signature was cut.
    pub entity_truncated: bool,
    pub source: Option<SourceRef>,
    pub test: bool,
    pub coverage: Option<Coverage>,
    pub snippet: Snippet,
    pub channels: Vec<ChannelEvidence>,
    pub seeds: Vec<Seed>,
    pub depth: Option<u32>,
    pub origins: Vec<Origin>,
    /// Channels, seeds, origins or coverage diagnostics were cut.
    pub provenance_truncated: bool,
    /// The physical parent the subject's identity implies.
    pub parent: Option<EntityId>,
    pub relations: Vec<Relation>,
    pub relations_truncated: bool,
}

impl Capsule {
    pub fn new(
        snapshot: &SnapshotKey,
        query: &Query,
        subject: Subject,
        question: Question,
        evidence: Evidence<'_>,
    ) -> Self {
        let (query_text, query_truncated) = cut(&query.text, MAX_CAPSULE_QUERY_BYTES);
        let (signature, signature_truncated) = match &evidence.entity.signature {
            Some(s) => {
                let (s, t) = cut(s, MAX_CAPSULE_SIGNATURE_BYTES);
                (Some(s), t)
            }
            None => (None, false),
        };
        let (kind, kind_truncated) = cut(&evidence.entity.kind, MAX_CAPSULE_TEXT_BYTES);
        let (name, name_truncated) = cut(&evidence.entity.name, MAX_CAPSULE_TEXT_BYTES);
        let snippet = match evidence.snippet {
            Snippet::Text { text, truncated } => {
                let (text, cut) = cut_lines(text, MAX_CAPSULE_SNIPPET_BYTES);
                Snippet::Text {
                    text,
                    truncated: *truncated || cut,
                }
            }
            other => other.clone(),
        };
        let mut provenance_truncated = false;
        let mut bound = |n: usize| {
            provenance_truncated |= n > MAX_CAPSULE_PROVENANCE;
            n.min(MAX_CAPSULE_PROVENANCE)
        };
        let channels = evidence.channels[..bound(evidence.channels.len())].to_vec();
        let seeds = evidence.seeds[..bound(evidence.seeds.len())]
            .iter()
            .map(|s| Seed {
                hint: cut(&s.hint, MAX_CAPSULE_TEXT_BYTES).0,
                ..s.clone()
            })
            .collect();
        let origins = evidence.origins[..bound(evidence.origins.len())].to_vec();
        let coverage = evidence.coverage.map(|c| match c {
            Coverage::Partial { diagnostics } => Coverage::Partial {
                diagnostics: diagnostics[..bound(diagnostics.len())]
                    .iter()
                    .map(|d| cut(d, MAX_CAPSULE_TEXT_BYTES).0)
                    .collect(),
            },
            Coverage::Unsupported { reason } => Coverage::Unsupported {
                reason: cut(reason, MAX_CAPSULE_TEXT_BYTES).0,
            },
            Coverage::Complete => Coverage::Complete,
        });
        // Ambiguous targets keep their first candidates (still 2+, still
        // sorted); a cut list is flagged with the relations.
        let mut targets_cut = false;
        let relations = evidence
            .relations
            .iter()
            .take(MAX_CAPSULE_RELATIONS)
            .map(|r| Relation {
                evidence: None,
                to: match &r.to {
                    Target::Unresolved { name } => Target::Unresolved {
                        name: cut(name, MAX_CAPSULE_TEXT_BYTES).0,
                    },
                    Target::Ambiguous(ids) => {
                        targets_cut |= ids.len() > MAX_CAPSULE_PROVENANCE;
                        Target::Ambiguous(
                            ids.iter().take(MAX_CAPSULE_PROVENANCE).cloned().collect(),
                        )
                    }
                    other => other.clone(),
                },
                ..r.clone()
            })
            .collect();
        Self {
            version: CAPSULE_VERSION,
            snapshot: snapshot.clone(),
            subject,
            question,
            query: query_text,
            query_truncated,
            query_digest: digest(query.text.as_bytes()),
            kind,
            name,
            signature,
            entity_truncated: kind_truncated || name_truncated || signature_truncated,
            source: evidence.entity.source.clone(),
            test: evidence.entity.test,
            coverage,
            snippet,
            channels,
            seeds,
            depth: evidence.depth,
            origins,
            provenance_truncated,
            parent: physical_parent(&evidence.entity.id),
            relations,
            relations_truncated: evidence.relations_truncated
                || targets_cut
                || evidence.relations.len() > MAX_CAPSULE_RELATIONS,
        }
    }

    /// Branch-value capsule for a navigated region. Routing has no source
    /// at hand, so the snippet is absent.
    pub fn branch(snapshot: &SnapshotKey, query: &Query, region: &Region) -> Self {
        Self::new(
            snapshot,
            query,
            Subject::Region(region.id.clone()),
            Question::BranchValue,
            Evidence {
                entity: &region.anchor,
                coverage: region.coverage.as_ref(),
                snippet: &Snippet::Absent,
                channels: &[],
                seeds: &[],
                depth: None,
                origins: &[],
                relations: &region.cross_edges,
                relations_truncated: region.cross_edges_truncated,
            },
        )
    }

    /// Digest of the full canonical rendering: correlates judgments and
    /// replayed responses to this exact content.
    pub fn digest(&self) -> Digest {
        digest(self.render(false).as_bytes())
    }

    /// Canonical JSON rendering (fixed key order, no whitespace). With
    /// `withhold_source`, the snippet text and signature are replaced by
    /// `"withheld"`: the form for a judge not authorized to see source.
    pub fn render(&self, withhold_source: bool) -> String {
        let withheld = || J::S("withheld".into());
        let snippet = match (&self.snippet, withhold_source) {
            (Snippet::Text { .. }, true) => withheld(),
            (Snippet::Text { text, truncated }, false) => J::O(vec![
                ("text", J::S(text.clone())),
                ("truncated", J::B(*truncated)),
            ]),
            (Snippet::Absent, _) => J::S("absent".into()),
            (Snippet::Unavailable, _) => J::S("unavailable".into()),
        };
        let signature = match (&self.signature, withhold_source) {
            (None, _) => J::Null,
            (Some(_), true) => withheld(),
            (Some(s), false) => J::S(s.clone()),
        };
        let subject = match &self.subject {
            Subject::Candidate(id) => J::O(vec![("candidate", entity(id))]),
            Subject::Region(r) => J::O(vec![(
                "region",
                J::O(vec![
                    ("projection", J::N(r.projection.0.to_string())),
                    ("anchor", entity(&r.anchor)),
                ]),
            )]),
        };
        let o = J::O(vec![
            ("capsule_version", J::N(self.version.to_string())),
            (
                "snapshot",
                J::O(vec![
                    ("repo", J::S(self.snapshot.repo.as_str().into())),
                    ("snapshot", J::S(self.snapshot.snapshot.as_str().into())),
                    ("derivation", J::S(self.snapshot.derivation.as_str().into())),
                ]),
            ),
            ("subject", subject),
            ("question", J::S(question(self.question).into())),
            (
                "task",
                J::O(vec![
                    ("query", J::S(self.query.clone())),
                    ("query_truncated", J::B(self.query_truncated)),
                    ("query_digest", J::S(self.query_digest.as_str().into())),
                ]),
            ),
            (
                "entity",
                J::O(vec![
                    ("kind", J::S(self.kind.clone())),
                    ("name", J::S(self.name.clone())),
                    ("signature", signature),
                    ("entity_truncated", J::B(self.entity_truncated)),
                    ("source", self.source.as_ref().map_or(J::Null, source)),
                    ("test", J::B(self.test)),
                    ("coverage", self.coverage.as_ref().map_or(J::Null, coverage)),
                    ("parent", self.parent.as_ref().map_or(J::Null, entity)),
                ]),
            ),
            ("snippet", snippet),
            (
                "retrieval",
                J::O(vec![
                    (
                        "channels",
                        J::A(self.channels.iter().map(channel).collect()),
                    ),
                    ("seeds", J::A(self.seeds.iter().map(seed).collect())),
                ]),
            ),
            (
                "route",
                J::O(vec![
                    ("depth", self.depth.map_or(J::Null, |d| J::N(d.to_string()))),
                    ("origins", J::A(self.origins.iter().map(origin).collect())),
                ]),
            ),
            ("provenance_truncated", J::B(self.provenance_truncated)),
            (
                "relations",
                J::A(self.relations.iter().map(relation).collect()),
            ),
            ("relations_truncated", J::B(self.relations_truncated)),
        ]);
        let mut out = String::new();
        o.write(&mut out);
        out
    }
}

/// `text` cut to at most `max` bytes at a char boundary.
fn cut(text: &str, max: usize) -> (String, bool) {
    if text.len() <= max {
        return (text.to_owned(), false);
    }
    let mut end = max;
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    (text[..end].to_owned(), true)
}

/// Like [`cut`], but backs off to the last newline that fits, if any.
fn cut_lines(text: &str, max: usize) -> (String, bool) {
    let (mut kept, truncated) = cut(text, max);
    if truncated && let Some(at) = kept.rfind('\n') {
        kept.truncate(at + 1);
    }
    (kept, truncated)
}

/// Minimal JSON tree with a deterministic writer.
enum J {
    S(String),
    /// A number literal, already formatted.
    N(String),
    B(bool),
    Null,
    A(Vec<J>),
    O(Vec<(&'static str, J)>),
}

impl J {
    fn write(&self, out: &mut String) {
        match self {
            J::S(s) => string(s, out),
            J::N(n) => out.push_str(n),
            J::B(b) => out.push_str(if *b { "true" } else { "false" }),
            J::Null => out.push_str("null"),
            J::A(items) => {
                out.push('[');
                for (i, item) in items.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    item.write(out);
                }
                out.push(']');
            }
            J::O(fields) => {
                out.push('{');
                for (i, (key, value)) in fields.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    string(key, out);
                    out.push(':');
                    value.write(out);
                }
                out.push('}');
            }
        }
    }
}

fn string(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

/// Injective entity encoding: symbol paths stay structured.
fn entity(id: &EntityId) -> J {
    match id {
        EntityId::Repository => J::S("repository".into()),
        EntityId::Module(m) => J::O(vec![("module", J::S(m.as_str().into()))]),
        EntityId::File(p) => J::O(vec![("file", J::S(p.as_str().into()))]),
        EntityId::Symbol(s) => J::O(vec![(
            "symbol",
            J::O(vec![
                ("file", J::S(s.file().as_str().into())),
                (
                    "path",
                    J::A(
                        s.path()
                            .iter()
                            .map(|seg| {
                                J::A(vec![J::S(seg.name.clone()), J::N(seg.ordinal.to_string())])
                            })
                            .collect(),
                    ),
                ),
            ]),
        )]),
    }
}

fn source(s: &SourceRef) -> J {
    J::O(vec![
        ("file", J::S(s.file.as_str().into())),
        ("start", J::N(s.range.start.to_string())),
        ("end", J::N(s.range.end.to_string())),
        ("digest", J::S(s.digest.as_str().into())),
    ])
}

fn coverage(c: &Coverage) -> J {
    match c {
        Coverage::Complete => J::S("complete".into()),
        Coverage::Partial { diagnostics } => J::O(vec![(
            "partial",
            J::A(diagnostics.iter().map(|d| J::S(d.clone())).collect()),
        )]),
        Coverage::Unsupported { reason } => J::O(vec![("unsupported", J::S(reason.clone()))]),
    }
}

pub(crate) fn question(q: Question) -> &'static str {
    match q {
        Question::Relevance => "relevance",
        Question::Necessity => "necessity",
        Question::NeighborValue => "neighbor_value",
        Question::BranchValue => "branch_value",
    }
}

pub(crate) fn relation_kind(k: RelationKind) -> &'static str {
    match k {
        RelationKind::Contains(Containment::Physical) => "contains_physical",
        RelationKind::Contains(Containment::Logical) => "contains_logical",
        RelationKind::Defines => "defines",
        RelationKind::References => "references",
        RelationKind::Calls => "calls",
        RelationKind::Imports => "imports",
        RelationKind::Implements => "implements",
        RelationKind::TestedBy => "tested_by",
    }
}

pub(crate) fn direction(d: Direction) -> &'static str {
    match d {
        Direction::Outgoing => "outgoing",
        Direction::Incoming => "incoming",
        Direction::Both => "both",
    }
}

fn channel(c: &ChannelEvidence) -> J {
    let name = match c.channel {
        Channel::Lexical => "lexical",
        Channel::Semantic => "semantic",
    };
    J::O(vec![
        ("channel", J::S(name.into())),
        ("rank", J::N(c.rank.to_string())),
        // Non-finite scores never reach a channel; if one did, it renders
        // as null rather than invalid JSON.
        (
            "score",
            if c.score.is_finite() {
                J::N(c.score.to_string())
            } else {
                J::Null
            },
        ),
        ("scorer", J::S(c.scorer.clone())),
    ])
}

fn seed(s: &Seed) -> J {
    let kind = match s.kind {
        HintKind::Selection => "selection",
        HintKind::Path => "path",
        HintKind::Symbol => "symbol",
        HintKind::Changed => "changed",
    };
    J::O(vec![
        ("hint_kind", J::S(kind.into())),
        ("hint", J::S(s.hint.clone())),
        ("case_insensitive", J::B(s.case_insensitive)),
    ])
}

fn origin(o: &Origin) -> J {
    match o {
        Origin::EntryPoint => J::S("entry_point".into()),
        Origin::RootFallback => J::S("root_fallback".into()),
        Origin::RegionMember(r) => J::O(vec![("member_of", entity(&r.anchor))]),
        Origin::Scope(r) => J::O(vec![("scope_of", entity(&r.anchor))]),
        Origin::Neighbor {
            via,
            relation,
            direction: d,
        } => J::O(vec![(
            "neighbor",
            J::O(vec![
                ("via", entity(via)),
                ("relation", J::S(relation_kind(*relation).into())),
                ("direction", J::S(direction(*d).into())),
            ]),
        )]),
    }
}

fn relation(r: &Relation) -> J {
    let to = match &r.to {
        Target::Resolved(id) => J::O(vec![("resolved", entity(id))]),
        Target::Ambiguous(ids) => J::O(vec![("ambiguous", J::A(ids.iter().map(entity).collect()))]),
        Target::Unresolved { name } => J::O(vec![("unresolved", J::S(name.clone()))]),
    };
    let basis = match r.basis {
        Basis::Syntactic => "syntactic",
        Basis::Resolved => "resolved",
        Basis::Heuristic => "heuristic",
    };
    J::O(vec![
        ("kind", J::S(relation_kind(r.kind).into())),
        ("from", entity(&r.from)),
        ("to", to),
        ("basis", J::S(basis.into())),
    ])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn json_strings_escape_controls_and_quotes() {
        let mut out = String::new();
        string("a\"b\\c\n\u{1}é", &mut out);
        assert_eq!(out, r#""a\"b\\c\n\u0001é""#);
    }

    #[test]
    fn snippets_cut_at_the_last_fitting_newline() {
        assert_eq!(cut_lines("ab\ncd\nef", 7), ("ab\ncd\n".into(), true));
        assert_eq!(cut_lines("abcdef", 4), ("abcd".into(), true));
        assert_eq!(cut_lines("ab", 4), ("ab".into(), false));
        assert_eq!(cut("éé", 3), ("é".into(), true));
    }
}
