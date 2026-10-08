//! DecisionProvider seam: bounded capsules out, validated judgments back,
//! deterministic heuristic fallback for anything missing, invalid, uncertain
//! or over the allowance. Models judge; this module's Rust decides what a
//! judgment is worth. Capsule fields are the Phase 1 minimum (SPEC § Candidate
//! capsule); Phase 4 refines them under a new `CAPSULE_VERSION`.

use crate::id::{EntityId, SnapshotKey};
use crate::knowledge::{Coverage, Entity, Relation, SourceRef};
use crate::query::Query;
use crate::retrieve::ChannelEvidence;
use crate::tree::{Region, RegionId};

pub const CAPSULE_VERSION: u32 = 1;
/// Query bytes carried in a capsule; longer queries are cut at a char
/// boundary and flagged, before any provider sees them.
pub const MAX_CAPSULE_QUERY_BYTES: usize = 2048;
pub const MAX_CAPSULE_RELATIONS: usize = 16;

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Subject {
    Candidate(EntityId),
    Region(RegionId),
}

/// The narrow questions a judge may answer, JEV included. None of them is
/// "is this code correct".
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Question {
    /// Is this candidate relevant to the task?
    Relevance,
    /// Would this neighbor add value next to already chosen evidence?
    NeighborValue,
    /// Is this region worth exploring?
    BranchValue,
}

/// Bounded, storage-free evidence about one subject for one question.
#[derive(Debug, Clone, PartialEq)]
pub struct Capsule {
    pub version: u32,
    pub snapshot: SnapshotKey,
    pub subject: Subject,
    pub question: Question,
    pub query: String,
    pub query_truncated: bool,
    pub kind: String,
    pub name: String,
    pub signature: Option<String>,
    pub source: Option<SourceRef>,
    pub test: bool,
    pub coverage: Option<Coverage>,
    pub channels: Vec<ChannelEvidence>,
    pub relations: Vec<Relation>,
    pub relations_truncated: bool,
}

impl Capsule {
    /// Branch-value capsule for a navigated region.
    pub fn branch(snapshot: &SnapshotKey, query: &Query, region: &Region) -> Self {
        let mut capsule = Self::build(
            snapshot,
            query,
            Subject::Region(region.id.clone()),
            Question::BranchValue,
            &region.anchor,
            &region.cross_edges,
        );
        capsule.coverage = region.coverage.clone();
        capsule.relations_truncated |= region.cross_edges_truncated;
        capsule
    }

    /// Candidate capsule; `question` must be a candidate question.
    pub fn candidate(
        snapshot: &SnapshotKey,
        query: &Query,
        question: Question,
        entity: &Entity,
        channels: &[ChannelEvidence],
        relations: &[Relation],
    ) -> Self {
        assert_ne!(
            question,
            Question::BranchValue,
            "branch questions take a region"
        );
        let mut capsule = Self::build(
            snapshot,
            query,
            Subject::Candidate(entity.id.clone()),
            question,
            entity,
            relations,
        );
        capsule.channels = channels.to_vec();
        capsule
    }

    fn build(
        snapshot: &SnapshotKey,
        query: &Query,
        subject: Subject,
        question: Question,
        entity: &Entity,
        relations: &[Relation],
    ) -> Self {
        let mut end = query.text.len().min(MAX_CAPSULE_QUERY_BYTES);
        while !query.text.is_char_boundary(end) {
            end -= 1;
        }
        Self {
            version: CAPSULE_VERSION,
            snapshot: snapshot.clone(),
            subject,
            question,
            query: query.text[..end].to_owned(),
            query_truncated: end < query.text.len(),
            kind: entity.kind.clone(),
            name: entity.name.clone(),
            signature: entity.signature.clone(),
            source: entity.source.clone(),
            test: entity.test,
            coverage: None,
            channels: Vec::new(),
            relations: relations
                .iter()
                .take(MAX_CAPSULE_RELATIONS)
                .cloned()
                .collect(),
            relations_truncated: relations.len() > MAX_CAPSULE_RELATIONS,
        }
    }
}

/// A provider's answer for one capsule, correlated by subject, question and
/// capsule version.
#[derive(Debug, Clone, PartialEq)]
pub struct Judgment {
    pub subject: Subject,
    pub question: Question,
    pub capsule_version: u32,
    pub verdict: Verdict,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Verdict {
    /// Estimated value in `[0, 1]`. `confidence` is what the provider
    /// reports, in `[0, 1]`; `None` means uncalibrated/unreported and is never
    /// upgraded to certainty.
    Value {
        value: f64,
        confidence: Option<f64>,
    },
    Abstain,
    /// The input is outside what the provider can judge.
    Unsupported,
}

/// Provider/model/heuristic identity recorded on every decision.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct ProviderIdentity {
    pub name: String,
    pub version: String,
}

/// Operational failure of a whole provider call.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProviderError {
    Unavailable,
    Timeout,
    Malformed,
}

/// Implemented by the heuristic judge here and by runtime clients (JEV, local
/// or stronger judges). Deadlines and transport are the client's job; a
/// provider only estimates value and never returns policy.
pub trait DecisionProvider {
    fn identity(&self) -> ProviderIdentity;
    fn supports(&self, question: Question) -> bool;
    fn judge(&mut self, capsules: &[Capsule]) -> Result<Vec<Judgment>, ProviderError>;
}

/// Required offline baseline. Phase 1 answers a neutral prior for every
/// subject.
// ponytail: neutral constant; Phase 3/4 replace it with structural/lexical
// heuristics under a new version.
#[derive(Debug, Clone, Copy, Default)]
pub struct Heuristic;

const HEURISTIC_VALUE: f64 = 0.5;

impl DecisionProvider for Heuristic {
    fn identity(&self) -> ProviderIdentity {
        ProviderIdentity {
            name: "heuristic-neutral".into(),
            version: "1".into(),
        }
    }

    fn supports(&self, _: Question) -> bool {
        true
    }

    fn judge(&mut self, capsules: &[Capsule]) -> Result<Vec<Judgment>, ProviderError> {
        Ok(capsules
            .iter()
            .map(|c| Judgment {
                subject: c.subject.clone(),
                question: c.question,
                capsule_version: c.version,
                verdict: Verdict::Value {
                    value: HEURISTIC_VALUE,
                    confidence: None,
                },
            })
            .collect())
    }
}

/// Provider requests left for one request, shared by routing and candidate
/// judging. A capsule sent counts even if the call then fails or times out.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Allowance {
    pub remaining: usize,
}

/// Rust-owned acceptance rules. `min_confidence`, when set, also rejects
/// judgments that report no confidence.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct DecisionPolicy {
    pub min_confidence: Option<f64>,
}

/// Why a subject got the heuristic judgment instead of the provider's.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Fallback {
    NoProvider,
    Unsupported,
    AllowanceExhausted,
    ProviderFailed(ProviderError),
    Missing,
    Duplicate,
    Invalid,
    Abstained,
    LowConfidence,
}

/// The value Rust policy will use for one subject, with its provenance.
#[derive(Debug, Clone, PartialEq)]
pub struct Decision {
    pub subject: Subject,
    pub question: Question,
    pub value: f64,
    pub confidence: Option<f64>,
    pub provider: ProviderIdentity,
    pub fallback: Option<Fallback>,
}

/// Judges `capsules` (one decision each, in input order) through `provider`
/// within `allowance`, validating every answer and falling back to the
/// heuristic per subject.
pub fn decide(
    provider: Option<&mut (dyn DecisionProvider + '_)>,
    capsules: &[Capsule],
    allowance: &mut Allowance,
    policy: DecisionPolicy,
) -> Vec<Decision> {
    let mut outcomes = vec![Err(Fallback::NoProvider); capsules.len()];
    let mut identity = Heuristic.identity();
    if let Some(provider) = provider {
        identity = provider.identity();
        let mut sent = Vec::new();
        for (i, capsule) in capsules.iter().enumerate() {
            outcomes[i] = if !provider.supports(capsule.question) {
                Err(Fallback::Unsupported)
            } else if allowance.remaining == 0 {
                Err(Fallback::AllowanceExhausted)
            } else {
                allowance.remaining -= 1;
                sent.push(i);
                continue;
            };
        }
        if !sent.is_empty() {
            let batch: Vec<Capsule> = sent.iter().map(|&i| capsules[i].clone()).collect();
            let answer = provider.judge(&batch);
            for &i in &sent {
                outcomes[i] = match &answer {
                    Ok(judgments) => check(&capsules[i], judgments, policy),
                    Err(error) => Err(Fallback::ProviderFailed(*error)),
                };
            }
        }
    }
    capsules
        .iter()
        .zip(outcomes)
        .map(|(capsule, outcome)| {
            let (value, confidence, provider, fallback) = match outcome {
                Ok((value, confidence)) => (value, confidence, identity.clone(), None),
                Err(reason) => (HEURISTIC_VALUE, None, Heuristic.identity(), Some(reason)),
            };
            Decision {
                subject: capsule.subject.clone(),
                question: capsule.question,
                value,
                confidence,
                provider,
                fallback,
            }
        })
        .collect()
}

fn check(
    capsule: &Capsule,
    judgments: &[Judgment],
    policy: DecisionPolicy,
) -> Result<(f64, Option<f64>), Fallback> {
    let mut matching = judgments
        .iter()
        .filter(|j| j.subject == capsule.subject && j.question == capsule.question);
    let judgment = match (matching.next(), matching.next()) {
        (None, _) => return Err(Fallback::Missing),
        (Some(_), Some(_)) => return Err(Fallback::Duplicate),
        (Some(judgment), None) => judgment,
    };
    if judgment.capsule_version != capsule.version {
        return Err(Fallback::Invalid);
    }
    let unit = |x: f64| (0.0..=1.0).contains(&x); // false for NaN and ±inf
    match judgment.verdict {
        Verdict::Abstain => Err(Fallback::Abstained),
        Verdict::Unsupported => Err(Fallback::Unsupported),
        Verdict::Value { value, confidence } => {
            if !unit(value) || confidence.is_some_and(|c| !unit(c)) {
                return Err(Fallback::Invalid);
            }
            if let Some(min) = policy.min_confidence
                && !confidence.is_some_and(|c| c >= min)
            {
                return Err(Fallback::LowConfidence);
            }
            Ok((value, confidence))
        }
    }
}
