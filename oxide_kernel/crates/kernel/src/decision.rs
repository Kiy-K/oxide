//! DecisionProvider seam: bounded capsules out, validated judgments back,
//! deterministic heuristic fallback for anything missing, invalid, uncertain
//! or over the allowance. Models judge; this module's Rust decides what a
//! judgment is worth. The capsule itself is [`crate::capsule`] (v2 in
//! Phase 4).

use std::collections::BTreeMap;

pub use crate::capsule::{
    CAPSULE_VERSION, Capsule, Evidence, MAX_CAPSULE_PROVENANCE, MAX_CAPSULE_QUERY_BYTES,
    MAX_CAPSULE_RELATIONS, MAX_CAPSULE_SIGNATURE_BYTES, MAX_CAPSULE_SNIPPET_BYTES, Question,
    Snippet, Subject,
};
use crate::id::Digest;
use crate::knowledge::RelationKind;
use crate::retrieve::Channel;
use crate::route::Origin;
use crate::store::Direction;

/// A provider's answer for one capsule, correlated by subject, question,
/// capsule version and capsule digest: an answer for other content (a
/// replayed response for an edited capsule, say) never matches.
#[derive(Debug, Clone, PartialEq)]
pub struct Judgment {
    pub subject: Subject,
    pub question: Question,
    pub capsule_version: u32,
    pub capsule_digest: Digest,
    pub verdict: Verdict,
}

impl Judgment {
    /// A judgment correlated to `capsule`.
    pub fn of(capsule: &Capsule, verdict: Verdict) -> Self {
        Self {
            subject: capsule.subject.clone(),
            question: capsule.question,
            capsule_version: capsule.version,
            capsule_digest: capsule.digest(),
            verdict,
        }
    }
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
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
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
/// provider only estimates value and never returns policy. It may answer a
/// subset of the capsules; the rest fall back.
pub trait DecisionProvider {
    fn identity(&self) -> ProviderIdentity;
    fn supports(&self, question: Question) -> bool;
    fn judge(&mut self, capsules: &[Capsule]) -> Result<Vec<Judgment>, ProviderError>;
}

/// Required offline baseline and the fallback for every other provider. It
/// reads only the capsule, so a recorded capsule replays to the same value.
/// Values are priors on one `[0, 1]` scale, not probabilities, and carry no
/// confidence:
///
/// - `BranchValue`: a neutral 0.5 for every region, so heuristic routing is
///   exactly unjudged routing (the Phase 3 baseline asserts this).
/// - `Relevance` and `Necessity` (no separate necessity evidence yet):
///   repositories, modules and files 0.2 (containers; their members compete
///   on their own); a structural seed 0.9; a lexical entry point
///   `0.85 - 0.025 * (rank - 1)`, at least 0.5; a routed-only candidate 0.45
///   at depth 1, else 0.3.
/// - `NeighborValue`, by the relation that reached the subject: a test of the
///   seed 0.6, an implementation link 0.55, a callee 0.55, a referenced
///   definition 0.5, a caller 0.45, anything else 0.35.
#[derive(Debug, Clone, Copy, Default)]
pub struct Heuristic;

impl Heuristic {
    pub fn value(capsule: &Capsule) -> f64 {
        match capsule.question {
            Question::BranchValue => 0.5,
            Question::Relevance | Question::Necessity => {
                if matches!(capsule.kind.as_str(), "repository" | "module" | "file") {
                    0.2
                } else if !capsule.seeds.is_empty() {
                    0.9
                } else if let Some(lexical) = capsule
                    .channels
                    .iter()
                    .find(|c| c.channel == Channel::Lexical)
                {
                    (0.85 - 0.025 * (lexical.rank.saturating_sub(1)) as f64).max(0.5)
                } else if capsule.depth == Some(1) {
                    0.45
                } else {
                    0.3
                }
            }
            Question::NeighborValue => match capsule.origins.first() {
                Some(Origin::Neighbor {
                    relation,
                    direction,
                    ..
                }) => match (relation, direction) {
                    (RelationKind::TestedBy, Direction::Outgoing) => 0.6,
                    (RelationKind::Implements, _) => 0.55,
                    (RelationKind::Calls, Direction::Outgoing) => 0.55,
                    (RelationKind::References, Direction::Outgoing) => 0.5,
                    (RelationKind::Calls, Direction::Incoming) => 0.45,
                    _ => 0.35,
                },
                _ => 0.35,
            },
        }
    }
}

impl DecisionProvider for Heuristic {
    fn identity(&self) -> ProviderIdentity {
        ProviderIdentity {
            name: "heuristic-evidence".into(),
            version: "1".into(),
        }
    }

    fn supports(&self, _: Question) -> bool {
        true
    }

    fn judge(&mut self, capsules: &[Capsule]) -> Result<Vec<Judgment>, ProviderError> {
        Ok(capsules
            .iter()
            .map(|c| {
                let verdict = Verdict::Value {
                    value: Self::value(c),
                    confidence: None,
                };
                Judgment::of(c, verdict)
            })
            .collect())
    }
}

/// A whole provider call that failed, recorded per capsule it carried.
#[derive(Debug, Clone, PartialEq)]
pub struct Failure {
    pub subject: Subject,
    pub question: Question,
    pub capsule_digest: Digest,
    pub error: ProviderError,
}

type Key = (Subject, Question, Digest);

/// Fake/replay provider: answers from recorded judgments and failures,
/// matched by subject, question and capsule digest, under the recorded
/// provider's identity. Every recorded verdict is returned (duplicates
/// replay as duplicates); a batch containing a capsule whose recorded call
/// failed fails the same way; anything unrecorded is left unanswered
/// (heuristic fallback). An edited capsule cannot reuse a stale answer.
/// `fail` scripts a whole-call failure.
#[derive(Debug, Clone)]
pub struct Replay {
    pub identity: ProviderIdentity,
    pub judgments: BTreeMap<Key, Vec<Verdict>>,
    pub failures: BTreeMap<Key, ProviderError>,
    pub fail: Option<ProviderError>,
}

impl Replay {
    pub fn new(identity: ProviderIdentity, judgments: &[Judgment], failures: &[Failure]) -> Self {
        let mut recorded: BTreeMap<Key, Vec<Verdict>> = BTreeMap::new();
        for j in judgments {
            let key = (j.subject.clone(), j.question, j.capsule_digest.clone());
            recorded.entry(key).or_default().push(j.verdict);
        }
        let failures = failures
            .iter()
            .map(|f| {
                let key = (f.subject.clone(), f.question, f.capsule_digest.clone());
                (key, f.error)
            })
            .collect();
        Self {
            identity,
            judgments: recorded,
            failures,
            fail: None,
        }
    }
}

impl DecisionProvider for Replay {
    fn identity(&self) -> ProviderIdentity {
        self.identity.clone()
    }

    fn supports(&self, _: Question) -> bool {
        true
    }

    fn judge(&mut self, capsules: &[Capsule]) -> Result<Vec<Judgment>, ProviderError> {
        if let Some(error) = self.fail {
            return Err(error);
        }
        let keys: Vec<Key> = capsules
            .iter()
            .map(|c| (c.subject.clone(), c.question, c.digest()))
            .collect();
        if let Some(error) = keys.iter().find_map(|k| self.failures.get(k)) {
            return Err(*error);
        }
        Ok(capsules
            .iter()
            .zip(&keys)
            .flat_map(|(c, k)| {
                let verdicts = self.judgments.get(k).map_or(&[][..], Vec::as_slice);
                verdicts.iter().map(|v| Judgment::of(c, *v))
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
    /// The judged capsule, for replay and trace correlation.
    pub capsule_digest: Digest,
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
                Err(reason) => (
                    Heuristic::value(capsule),
                    None,
                    Heuristic.identity(),
                    Some(reason),
                ),
            };
            Decision {
                subject: capsule.subject.clone(),
                question: capsule.question,
                capsule_digest: capsule.digest(),
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
    if judgment.capsule_version != capsule.version || judgment.capsule_digest != capsule.digest() {
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
