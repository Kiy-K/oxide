//! Optional JEV DecisionProvider adapter (SPEC § Provider candidates and
//! JEV). JEV judges fuzzy value only: is this candidate useful, likely
//! necessary, a worthwhile neighbor or branch; never whether code is
//! correct. Rust keeps every policy decision; this adapter only maps
//! capsules to requests and validated answers back to judgments.
//!
//! Wire contract, read from the live TypeSafe docs on 2026-10-09
//! (`docs.typesafe.ai/api`, `/confidence`, `/models`): `POST
//! https://api.typesafe.ai/v1/systemone` with `{state, model, questions}`;
//! a `score` question with ordered `criteria` levels answers `{type:
//! "score", score, legend, probabilities, confidence}`; the response names
//! the versioned model that answered. Pinned model `jev-1.13.0`; aliases
//! (`jev-latest`, `jev-preview`) move silently and are refused.
//!
//! Mapping: one request per capsule (no batching, so no position bias);
//! `state` is the capsule's canonical rendering, with source text withheld
//! unless [`Disclosure::Source`] was configured; one three-level Score per
//! question; value = `score / 2`, confidence = the answer's `confidence`
//! (provider-reported, uncalibrated; any floor is Rust's
//! `DecisionPolicy::min_confidence`). JEV has no abstention; a malformed,
//! failed, late or unasked answer is simply missing, so the kernel falls
//! back to the heuristic for that subject.
//!
//! Transport is a trait. No HTTP client exists in the runtime yet, so this
//! phase ships [`Replay`] (captured exchanges keyed by the SHA-256 of the
//! exact request body) and [`Recording`] (captures any transport's
//! exchanges into that format). Live use is opt-in and needs an HTTP
//! transport plus credentials, neither of which CI has or needs.

use std::collections::BTreeMap;
use std::time::{Duration, Instant};

use oxide_kernel::capsule::{Capsule, Question};
use oxide_kernel::decision::{
    DecisionProvider, Judgment, ProviderError, ProviderIdentity, Verdict,
};
use serde_json::{Value, json};
use sha2::{Digest as _, Sha256};

pub const MODEL: &str = "jev-1.13.0";
pub const REPLAY_FORMAT: &str = "oxide-jev-replay-v1";
/// Key of the one question in every request. Not sent to the model.
const KEY: &str = "judgment";

/// What may leave the machine. There is no default: configuring JEV means
/// choosing one.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Disclosure {
    /// Query, paths, names, kinds, relations and retrieval/route
    /// provenance; snippet text and signatures are replaced by `"withheld"`.
    Metadata,
    /// Also the bounded source snippet and signature.
    Source,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct JevConfig {
    /// A pinned, versioned model id.
    pub model: String,
    /// Wall-clock budget for one `judge` call, all requests included.
    pub deadline: Duration,
    /// Requests this provider may send over its lifetime.
    pub max_requests: usize,
    pub disclosure: Disclosure,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TransportError {
    Timeout,
    /// Connection refused, DNS, TLS, or nothing recorded for a replay.
    Unavailable(String),
    /// A non-success HTTP status (401, 422, 429, 529, ...).
    Status(u16),
}

pub trait Transport {
    /// Sends one request body, waiting at most `timeout`, and returns the
    /// response body.
    fn post(&mut self, body: &str, timeout: Duration) -> Result<String, TransportError>;
}

/// What the adapter observed, for cost and fallback reporting.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct JevStats {
    pub requests: usize,
    pub answered: usize,
    pub malformed: usize,
    pub timeouts: usize,
    pub unavailable: usize,
    /// Capsules not sent: request cap or deadline reached.
    pub skipped: usize,
    pub input_tokens: u64,
}

pub struct Jev<T> {
    config: JevConfig,
    transport: T,
    pub stats: JevStats,
}

impl<T: Transport> Jev<T> {
    /// Refuses moving aliases: a judgment must name the model that made it.
    pub fn new(config: JevConfig, transport: T) -> Result<Self, String> {
        let m = &config.model;
        if m.is_empty() || m.ends_with("-latest") || m.ends_with("-preview") {
            return Err(format!("JEV model `{m}` is not a pinned version"));
        }
        Ok(Self {
            config,
            transport,
            stats: JevStats::default(),
        })
    }

    pub fn into_transport(self) -> T {
        self.transport
    }
}

/// The exact request body for one capsule. Deterministic: same capsule,
/// model and disclosure give the same bytes (object keys are sorted).
pub fn request(capsule: &Capsule, model: &str, disclosure: Disclosure) -> String {
    let state: Value = serde_json::from_str(&capsule.render(disclosure == Disclosure::Metadata))
        .expect("canonical capsule rendering is JSON");
    let not_correctness =
        "Judge only its value as reading context; do not judge whether any code is correct.";
    let (question, levels) = match capsule.question {
        Question::Relevance => (
            "How useful would the code entity described by `entity` and `snippet` be as context for a developer working on the task in `task.query`?",
            [
                "Not useful for this task",
                "Possibly useful background for this task",
                "Directly useful for this task",
            ],
        ),
        Question::Necessity => (
            "Would a developer working on the task in `task.query` most likely need to read the code entity described by `entity` and `snippet` to complete it?",
            [
                "Not needed for this task",
                "Might be needed for this task",
                "Very likely needed for this task",
            ],
        ),
        Question::NeighborValue => (
            "The code entity in `entity` is a structural neighbor (see `route.origins`) of code already chosen as context for the task in `task.query`. How much would also reading it help?",
            [
                "Adds nothing useful",
                "Adds some useful context",
                "Adds clearly needed context",
            ],
        ),
        Question::BranchValue => (
            "Is the code region anchored at `entity`, with the relations in `relations`, worth exploring further to find context for the task in `task.query`?",
            [
                "Not worth exploring",
                "Possibly worth exploring",
                "Clearly worth exploring",
            ],
        ),
    };
    json!({
        "state": state,
        "model": model,
        "questions": {
            KEY: {
                "type": "score",
                "instructions": {"question": question, "scope": not_correctness},
                "criteria": levels,
            }
        }
    })
    .to_string()
}

/// Strictly validates one response: the pinned model, exactly our one Score
/// answer, finite in-range score, a three-level distribution summing to 1,
/// and a confidence in `[0, 1]`. Returns (value, confidence, input tokens).
pub fn parse(body: &str, model: &str) -> Result<(f64, f64, u64), String> {
    let v: Value = serde_json::from_str(body).map_err(|e| format!("not JSON: {e}"))?;
    if v["model"].as_str() != Some(model) {
        return Err(format!("answered by {}, not {model}", v["model"]));
    }
    let answers = v["answers"].as_object().ok_or("no answers object")?;
    if answers.len() != 1 {
        return Err(format!("{} answers, expected 1", answers.len()));
    }
    let a = answers.get(KEY).ok_or("no answer for the question")?;
    if a["type"] != "score" {
        return Err(format!("answer type {}", a["type"]));
    }
    let unit = |x: Option<f64>, what: &str| match x {
        Some(x) if (0.0..=1.0).contains(&x) => Ok(x),
        _ => Err(format!("{what} missing or outside [0, 1]")),
    };
    let score = a["score"].as_f64().filter(|s| (0.0..=2.0).contains(s));
    let score = score.ok_or("score missing or outside [0, 2]")?;
    let probabilities = a["probabilities"].as_object().ok_or("no probabilities")?;
    let mut sum = 0.0;
    for level in ["0", "1", "2"] {
        sum += unit(
            probabilities.get(level).and_then(Value::as_f64),
            "probability",
        )?;
    }
    if probabilities.len() != 3 || (sum - 1.0).abs() > 1e-3 {
        return Err("probabilities are not one three-level distribution".into());
    }
    let confidence = unit(a["confidence"].as_f64(), "confidence")?;
    let tokens = v["usage"]["input_tokens"].as_u64().unwrap_or(0);
    Ok((score / 2.0, confidence, tokens))
}

impl<T: Transport> DecisionProvider for Jev<T> {
    fn identity(&self) -> ProviderIdentity {
        ProviderIdentity {
            name: "jev".into(),
            version: self.config.model.clone(),
        }
    }

    fn supports(&self, _: Question) -> bool {
        true
    }

    fn judge(&mut self, capsules: &[Capsule]) -> Result<Vec<Judgment>, ProviderError> {
        let start = Instant::now();
        let mut out = Vec::new();
        let mut errors = Vec::new();
        for capsule in capsules {
            let left = self.config.deadline.saturating_sub(start.elapsed());
            if left.is_zero() {
                self.stats.skipped += 1;
                errors.push(ProviderError::Timeout);
                continue;
            }
            if self.stats.requests >= self.config.max_requests {
                self.stats.skipped += 1;
                errors.push(ProviderError::Unavailable);
                continue;
            }
            self.stats.requests += 1;
            let body = request(capsule, &self.config.model, self.config.disclosure);
            let answer = self.transport.post(&body, left);
            // A late answer is a timeout, whatever the transport did.
            let answer = match answer {
                Ok(_) if start.elapsed() > self.config.deadline => Err(TransportError::Timeout),
                other => other,
            };
            match answer.map(|b| parse(&b, &self.config.model)) {
                Ok(Ok((value, confidence, tokens))) => {
                    self.stats.answered += 1;
                    self.stats.input_tokens += tokens;
                    let confidence = Some(confidence);
                    out.push(Judgment::of(capsule, Verdict::Value { value, confidence }));
                }
                Ok(Err(_)) => {
                    self.stats.malformed += 1;
                    errors.push(ProviderError::Malformed);
                }
                Err(TransportError::Timeout) => {
                    self.stats.timeouts += 1;
                    errors.push(ProviderError::Timeout);
                }
                Err(_) => {
                    self.stats.unavailable += 1;
                    errors.push(ProviderError::Unavailable);
                }
            }
        }
        // Nothing answered: report the whole call's failure so the kernel
        // records why; otherwise unanswered subjects are simply missing.
        if out.is_empty() && !capsules.is_empty() {
            let first = errors
                .first()
                .copied()
                .unwrap_or(ProviderError::Unavailable);
            if errors.iter().all(|e| *e == first) {
                return Err(first);
            }
            return Err(ProviderError::Unavailable);
        }
        Ok(out)
    }
}

fn sha256_hex(body: &str) -> String {
    Sha256::digest(body.as_bytes())
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// One captured exchange: a response body, or how the call failed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Recorded {
    Response(String),
    Failed(TransportError),
}

/// Offline transport over captured exchanges, keyed by the request body's
/// SHA-256: a capsule that changed in any way misses and falls back.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Replay {
    pub exchanges: BTreeMap<String, Recorded>,
}

impl Replay {
    /// Reads an `oxide-jev-replay-v1` document.
    pub fn from_json(text: &str) -> Result<Self, String> {
        let v: Value = serde_json::from_str(text).map_err(|e| e.to_string())?;
        if v["format"] != REPLAY_FORMAT {
            return Err(format!("not {REPLAY_FORMAT}"));
        }
        let mut exchanges = BTreeMap::new();
        for e in v["exchanges"].as_array().ok_or("no exchanges")? {
            let key = e["request_sha256"].as_str().ok_or("no request_sha256")?;
            let error = &e["error"];
            let recorded = if let Some(text) = e["response_text"].as_str() {
                Recorded::Response(text.to_owned())
            } else if !e["response"].is_null() {
                Recorded::Response(e["response"].to_string())
            } else if error == "timeout" {
                Recorded::Failed(TransportError::Timeout)
            } else if let Some(status) = error["status"].as_u64() {
                let status = u16::try_from(status).map_err(|_| "status out of range")?;
                Recorded::Failed(TransportError::Status(status))
            } else if let Some(why) = error["unavailable"].as_str() {
                Recorded::Failed(TransportError::Unavailable(why.to_owned()))
            } else {
                return Err("an exchange needs a response or an error".into());
            };
            exchanges.insert(key.to_owned(), recorded);
        }
        Ok(Self { exchanges })
    }
}

impl Transport for Replay {
    fn post(&mut self, body: &str, _: Duration) -> Result<String, TransportError> {
        match self.exchanges.get(&sha256_hex(body)) {
            Some(Recorded::Response(r)) => Ok(r.clone()),
            Some(Recorded::Failed(e)) => Err(e.clone()),
            None => Err(TransportError::Unavailable("not recorded".into())),
        }
    }
}

/// Captures another transport's exchanges for later [`Replay`].
pub struct Recording<T> {
    pub inner: T,
    pub exchanges: Vec<(String, Recorded)>,
}

impl<T: Transport> Transport for Recording<T> {
    fn post(&mut self, body: &str, timeout: Duration) -> Result<String, TransportError> {
        let answer = self.inner.post(body, timeout);
        let recorded = match &answer {
            Ok(r) => Recorded::Response(r.clone()),
            Err(e) => Recorded::Failed(e.clone()),
        };
        self.exchanges.push((body.to_owned(), recorded));
        answer
    }
}

impl<T> Recording<T> {
    /// The `oxide-jev-replay-v1` document, request bodies included for audit.
    pub fn to_json(&self, model: &str) -> String {
        let exchanges: Vec<Value> = self
            .exchanges
            .iter()
            .map(|(body, recorded)| {
                let request: Value = serde_json::from_str(body).unwrap_or(Value::Null);
                // A body that is not JSON (the malformed case worth
                // replaying) is kept verbatim as text.
                let mut entry = json!({"request_sha256": sha256_hex(body), "request": request});
                match recorded {
                    Recorded::Response(r) => match serde_json::from_str::<Value>(r) {
                        Ok(v) => entry["response"] = v,
                        Err(_) => entry["response_text"] = json!(r),
                    },
                    Recorded::Failed(TransportError::Timeout) => entry["error"] = json!("timeout"),
                    Recorded::Failed(TransportError::Status(code)) => {
                        entry["error"] = json!({"status": code})
                    }
                    Recorded::Failed(TransportError::Unavailable(why)) => {
                        entry["error"] = json!({"unavailable": why})
                    }
                }
                entry
            })
            .collect();
        serde_json::to_string_pretty(
            &json!({"format": REPLAY_FORMAT, "model": model, "exchanges": exchanges}),
        )
        .expect("JSON")
            + "\n"
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use oxide_kernel::capsule::{Evidence, Snippet, Subject};
    use oxide_kernel::decision::{Allowance, DecisionPolicy, Fallback, decide};
    use oxide_kernel::id::*;
    use oxide_kernel::knowledge::Entity;
    use oxide_kernel::query::Query;

    fn capsule(question: Question) -> Capsule {
        let file = RepoPath::new("a.py").unwrap();
        let id = EntityId::Symbol(
            SymbolId::new(
                file,
                vec![Segment {
                    name: "f".into(),
                    ordinal: 0,
                }],
            )
            .unwrap(),
        );
        let entity = Entity {
            id: id.clone(),
            kind: "function".into(),
            name: "f".into(),
            signature: Some("def f(secret)".into()),
            source: None,
            test: false,
        };
        let snapshot = SnapshotKey {
            repo: RepoId::new("r").unwrap(),
            snapshot: SnapshotId::new("s").unwrap(),
            derivation: DerivationId::new("d").unwrap(),
        };
        let snippet = Snippet::Text {
            text: "def f(secret): pass\n".into(),
            truncated: false,
        };
        Capsule::new(
            &snapshot,
            &Query {
                text: "where is f".into(),
            },
            Subject::Candidate(id),
            question,
            Evidence {
                entity: &entity,
                coverage: None,
                snippet: &snippet,
                channels: &[],
                seeds: &[],
                depth: Some(0),
                origins: &[],
                relations: &[],
                relations_truncated: false,
            },
        )
    }

    const GOLDEN_REQUEST_SHA256: &str =
        "3c48d812bfb022d6b92a44daf9d41ee6cb4bce16dffdcefb6086d266fa973e0c";

    fn config() -> JevConfig {
        JevConfig {
            model: MODEL.into(),
            deadline: Duration::from_secs(5),
            max_requests: 100,
            disclosure: Disclosure::Metadata,
        }
    }

    fn answer(score: f64, probabilities: [f64; 3], confidence: f64) -> String {
        json!({
            "model": MODEL,
            "answers": {KEY: {
                "type": "score", "score": score,
                "legend": {"0": "a", "1": "b", "2": "c"},
                "probabilities": {"0": probabilities[0], "1": probabilities[1], "2": probabilities[2]},
                "confidence": confidence,
            }},
            "usage": {"input_tokens": 300, "output_tokens": 18},
        })
        .to_string()
    }

    fn replay(capsule: &Capsule, response: Recorded) -> Replay {
        let body = request(capsule, MODEL, Disclosure::Metadata);
        Replay {
            exchanges: BTreeMap::from([(sha256_hex(&body), response)]),
        }
    }

    #[test]
    fn requests_are_deterministic_and_withhold_source_unless_disclosed() {
        let c = capsule(Question::Relevance);
        let metadata = request(&c, MODEL, Disclosure::Metadata);
        assert_eq!(metadata, request(&c.clone(), MODEL, Disclosure::Metadata));
        assert!(!metadata.contains("secret") && metadata.contains("withheld"));
        assert!(request(&c, MODEL, Disclosure::Source).contains("def f(secret)"));
        let v: Value = serde_json::from_str(&metadata).unwrap();
        assert_eq!(v["questions"][KEY]["type"], "score");
        assert_eq!(v["questions"][KEY]["criteria"].as_array().unwrap().len(), 3);
        assert_eq!(v["state"]["question"], "relevance");
    }

    #[test]
    fn aliases_are_refused() {
        for model in ["jev-latest", "jev-preview", ""] {
            let config = JevConfig {
                model: model.into(),
                ..config()
            };
            assert!(Jev::new(config, Replay::default()).is_err());
        }
    }

    #[test]
    fn replayed_answers_become_validated_judgments() {
        let c = capsule(Question::Relevance);
        let transport = replay(&c, Recorded::Response(answer(1.6, [0.0, 0.4, 0.6], 0.4)));
        let mut jev = Jev::new(config(), transport).unwrap();
        let mut allowance = Allowance { remaining: 10 };
        let d = decide(
            Some(&mut jev),
            std::slice::from_ref(&c),
            &mut allowance,
            DecisionPolicy::default(),
        );
        assert_eq!(
            (d[0].value, d[0].confidence, d[0].fallback),
            (0.8, Some(0.4), None)
        );
        assert_eq!(d[0].provider.version, MODEL);
        assert_eq!((jev.stats.answered, jev.stats.input_tokens), (1, 300));

        // Rust's confidence floor, not the adapter, rejects an uncertain answer.
        let floor = DecisionPolicy {
            min_confidence: Some(0.5),
        };
        let d = decide(
            Some(&mut jev),
            std::slice::from_ref(&c),
            &mut allowance,
            floor,
        );
        assert_eq!(d[0].fallback, Some(Fallback::LowConfidence));
    }

    #[test]
    fn malformed_answers_fall_back() {
        let c = capsule(Question::NeighborValue);
        let bad = [
            answer(2.5, [0.0, 0.0, 1.0], 0.9), // score out of range
            answer(1.0, [0.5, 0.5, 0.5], 0.9), // not a distribution
            answer(1.0, [0.0, 1.0, 0.0], 1.5), // confidence out of range
            answer(1.0, [0.0, 1.0, 0.0], 0.9).replace(MODEL, "jev-1.12.0"), // other model
            answer(1.0, [0.0, 1.0, 0.0], 0.9).replace("\"type\":\"score\"", "\"type\":\"noul\""),
            "not json".into(),
        ];
        for body in bad {
            let mut jev = Jev::new(config(), replay(&c, Recorded::Response(body.clone()))).unwrap();
            let mut allowance = Allowance { remaining: 10 };
            let d = decide(
                Some(&mut jev),
                std::slice::from_ref(&c),
                &mut allowance,
                DecisionPolicy::default(),
            );
            assert_eq!(
                d[0].fallback,
                Some(Fallback::ProviderFailed(ProviderError::Malformed)),
                "{body}"
            );
        }
    }

    #[test]
    fn timeouts_caps_and_misses_fall_back() {
        let c = capsule(Question::BranchValue);
        let policy = DecisionPolicy::default();
        let mut allowance = Allowance { remaining: 10 };
        let mut timed_out = Jev::new(
            config(),
            replay(&c, Recorded::Failed(TransportError::Timeout)),
        )
        .unwrap();
        let d = decide(
            Some(&mut timed_out),
            std::slice::from_ref(&c),
            &mut allowance,
            policy,
        );
        assert_eq!(
            d[0].fallback,
            Some(Fallback::ProviderFailed(ProviderError::Timeout))
        );

        // A slow transport past the deadline counts as a timeout.
        struct Slow;
        impl Transport for Slow {
            fn post(&mut self, _: &str, _: Duration) -> Result<String, TransportError> {
                std::thread::sleep(Duration::from_millis(30));
                Ok(answer(2.0, [0.0, 0.0, 1.0], 1.0))
            }
        }
        let tight = JevConfig {
            deadline: Duration::from_millis(5),
            ..config()
        };
        let mut slow = Jev::new(tight, Slow).unwrap();
        let two = [c.clone(), capsule(Question::Relevance)];
        let d = decide(Some(&mut slow), &two, &mut allowance, policy);
        assert!(
            d.iter()
                .all(|d| d.fallback == Some(Fallback::ProviderFailed(ProviderError::Timeout)))
        );
        assert_eq!((slow.stats.requests, slow.stats.skipped), (1, 1));

        let capped = JevConfig {
            max_requests: 0,
            ..config()
        };
        let mut capped = Jev::new(capped, Replay::default()).unwrap();
        let d = decide(
            Some(&mut capped),
            std::slice::from_ref(&c),
            &mut allowance,
            policy,
        );
        assert!(d[0].fallback.is_some() && capped.stats.requests == 0);

        let mut unrecorded = Jev::new(config(), Replay::default()).unwrap();
        let d = decide(
            Some(&mut unrecorded),
            std::slice::from_ref(&c),
            &mut allowance,
            policy,
        );
        assert_eq!(
            d[0].fallback,
            Some(Fallback::ProviderFailed(ProviderError::Unavailable))
        );
    }

    /// The replay key is the SHA-256 of the exact request body, so its
    /// bytes are pinned: a serializer or mapping change must show up here,
    /// not as every captured exchange silently missing.
    #[test]
    fn request_bytes_are_pinned() {
        let body = request(&capsule(Question::Relevance), MODEL, Disclosure::Metadata);
        assert!(body.starts_with(r#"{"model":"jev-1.13.0","questions":{"judgment":{"criteria":["#));
        assert_eq!(sha256_hex(&body), GOLDEN_REQUEST_SHA256, "{body}");
    }

    #[test]
    fn recordings_replay_exactly() {
        let c = capsule(Question::Relevance);
        let live = replay(&c, Recorded::Response(answer(1.0, [0.1, 0.8, 0.1], 0.7)));
        let recording = Recording {
            inner: live,
            exchanges: Vec::new(),
        };
        let mut jev = Jev::new(config(), recording).unwrap();
        let first = jev.judge(std::slice::from_ref(&c)).unwrap();
        let mut recording = jev.into_transport();
        recording
            .exchanges
            .push(("not json".into(), Recorded::Response("<html>".into())));
        recording
            .exchanges
            .push(("rate".into(), Recorded::Failed(TransportError::Status(429))));
        let document = recording.to_json(MODEL);
        let replay = Replay::from_json(&document).unwrap();
        let replayed: Vec<&Recorded> = replay.exchanges.values().collect();
        assert!(replayed.contains(&&Recorded::Response("<html>".into())));
        assert!(replayed.contains(&&Recorded::Failed(TransportError::Status(429))));
        let mut again = Jev::new(config(), replay).unwrap();
        assert_eq!(again.judge(std::slice::from_ref(&c)).unwrap(), first);
    }
}
