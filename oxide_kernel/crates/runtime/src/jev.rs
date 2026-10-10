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
//! Mapping: one request per capsule (no batching, so no position bias; the
//! service documents none across states), sent up to `concurrency` at a
//! time and applied in capsule order;
//! `state` is the capsule's canonical rendering, with source text withheld
//! unless [`Disclosure::Source`] was configured; one three-level Score per
//! question; value = `score / 2`, confidence = the answer's `confidence`
//! (provider-reported, uncalibrated; any floor is Rust's
//! `DecisionPolicy::min_confidence`). JEV has no abstention; a malformed,
//! failed, late or unasked answer is simply missing, so the kernel falls
//! back to the heuristic for that subject.
//!
//! Transport is a trait: [`Http`] (HTTPS, bearer key from
//! [`KEY_VAR`], Phase 5), [`Replay`] (captured exchanges keyed by the
//! SHA-256 of the exact request body) and [`Recording`] (captures any
//! transport's exchanges into that format, with latency). Live use is
//! opt-in and needs credentials, which CI neither has nor needs.
//!
//! Operational rules re-read from the docs on 2026-10-09: 429 and 529 are
//! retried with exponential backoff, here at most `retries` times and never
//! past the call's deadline; input tokens are billed at
//! [`USD_PER_INPUT_TOKEN`] and capped by `max_input_tokens`.

use std::collections::BTreeMap;
use std::sync::OnceLock;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{Duration, Instant};

use oxide_kernel::capsule::{Capsule, Question};
use oxide_kernel::decision::{
    DecisionProvider, Judgment, ProviderError, ProviderIdentity, Verdict,
};
use serde_json::{Value, json};
use sha2::{Digest as _, Sha256};

pub const MODEL: &str = "jev-1.13.0";
pub const ENDPOINT: &str = "https://api.typesafe.ai/v1/systemone";
/// Environment variable holding the API key. The key is sent only as a
/// bearer header: never logged, recorded or rendered by `Debug`.
pub const KEY_VAR: &str = "TYPESAFE_API_KEY";
/// $0.042 per million input tokens; output tokens are free
/// (`docs.typesafe.ai/models`, 2026-10-09).
pub const USD_PER_INPUT_TOKEN: f64 = 0.042e-6;
/// The service rounds score and probabilities to two decimals (observed
/// live, 2026-10-09: `score 1.32`, `probabilities 0.02/0.64/0.34`), so the
/// distribution's sum and mean may each be off by up to 0.015.
const ROUNDING: f64 = 0.02;
/// First retry delay after 429/529; doubles per retry.
const BACKOFF: Duration = Duration::from_millis(250);
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
    /// Requests this provider may send over its lifetime, retries included.
    /// Counted at this adapter, so answers a caching or replaying transport
    /// serves count too; bound paid calls at the transport when it caches.
    pub max_requests: usize,
    /// Input tokens it may spend over its lifetime. A request is sent only
    /// if its body length in bytes, an upper bound on its tokens, still fits.
    pub max_input_tokens: u64,
    /// Retries of one capsule after 429 or 529, within the deadline.
    pub retries: u32,
    pub disclosure: Disclosure,
    /// Requests one `judge` call keeps in flight, 1 to [`MAX_CONCURRENCY`]
    /// (1 is sequential). Answers apply in capsule order, so completion
    /// order never changes a judgment.
    pub concurrency: usize,
}

/// The most requests a [`Jev`] keeps in flight (and [`Http`] keeps
/// connections pooled for).
pub const MAX_CONCURRENCY: usize = 16;

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

    /// Sends every body, at most `concurrency` at a time, none past
    /// `deadline`; answers come back in input order. `None`: never sent,
    /// the deadline passed first. The default sends one at a time.
    fn post_all(
        &mut self,
        bodies: &[&str],
        deadline: Instant,
        concurrency: usize,
    ) -> Vec<Option<Sent>> {
        let _ = concurrency;
        bodies
            .iter()
            .map(|body| timed(body, deadline, |b, left| self.post(b, left)))
            .collect()
    }
}

/// One sent request's outcome.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Sent {
    pub answer: Result<String, TransportError>,
    pub elapsed: Duration,
    /// It finished after the deadline: a timeout, though possibly billed.
    pub late: bool,
}

fn timed(
    body: &str,
    deadline: Instant,
    post: impl FnOnce(&str, Duration) -> Result<String, TransportError>,
) -> Option<Sent> {
    let started = Instant::now();
    let left = deadline.saturating_duration_since(started);
    if left.is_zero() {
        return None;
    }
    let answer = post(body, left);
    Some(Sent {
        answer,
        elapsed: started.elapsed(),
        late: Instant::now() > deadline,
    })
}

/// [`Transport::post_all`] on at most `concurrency` scoped threads: a
/// bounded worker pool taking bodies in input order, so queued requests
/// wait (backpressure) and are dropped, never sent, once the deadline
/// passes. Answers are returned by input position.
pub fn fan_out(
    bodies: &[&str],
    deadline: Instant,
    concurrency: usize,
    post: impl Fn(&str, Duration) -> Result<String, TransportError> + Sync,
) -> Vec<Option<Sent>> {
    let next = AtomicUsize::new(0);
    let slots: Vec<OnceLock<Sent>> = bodies.iter().map(|_| OnceLock::new()).collect();
    std::thread::scope(|scope| {
        for _ in 0..concurrency.clamp(1, bodies.len().max(1)) {
            scope.spawn(|| {
                loop {
                    let i = next.fetch_add(1, Ordering::Relaxed);
                    let Some(body) = bodies.get(i) else { break };
                    if let Some(sent) = timed(body, deadline, &post) {
                        let _ = slots[i].set(sent);
                    }
                }
            });
        }
    });
    slots.into_iter().map(OnceLock::into_inner).collect()
}

/// What the adapter observed, for cost and fallback reporting.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct JevStats {
    pub requests: usize,
    pub answered: usize,
    pub malformed: usize,
    pub timeouts: usize,
    pub unavailable: usize,
    /// Capsules not sent: request cap, token cap or deadline reached.
    pub skipped: usize,
    pub retries: usize,
    /// Reported input tokens of answered requests (body bytes when a 2xx
    /// response reports none): what the token cap counts.
    pub input_tokens: u64,
}

impl JevStats {
    pub fn cost_usd(&self) -> f64 {
        self.input_tokens as f64 * USD_PER_INPUT_TOKEN
    }
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
        if !(1..=MAX_CONCURRENCY).contains(&config.concurrency) {
            return Err(format!("JEV concurrency must be 1 to {MAX_CONCURRENCY}"));
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

    pub fn transport_mut(&mut self) -> &mut T {
        &mut self.transport
    }
}

/// The exact request body for one capsule. Deterministic: same capsule,
/// model and disclosure give the same bytes (object keys are sorted).
pub fn request(capsule: &Capsule, model: &str, disclosure: Disclosure) -> String {
    let state = capsule.render(disclosure == Disclosure::Metadata);
    request_for(&state, capsule.question, model).expect("canonical capsule rendering is JSON")
}

/// The request body for an already rendered capsule (a DecisionBench
/// record's `capsule`): byte-identical to [`request`] for the same
/// rendering, so recorded exchanges replay for either.
pub fn request_for(state: &str, question: Question, model: &str) -> Result<String, String> {
    let state: Value = serde_json::from_str(state).map_err(|e| e.to_string())?;
    let not_correctness =
        "Judge only its value as reading context; do not judge whether any code is correct.";
    let (question, levels) = match question {
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
    Ok(json!({
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
    .to_string())
}

/// Strictly validates one response: the pinned model, exactly our one Score
/// answer, finite in-range score equal to its distribution's mean (the docs
/// define it as the probability-weighted value), a three-level distribution
/// summing to 1, and a confidence in `[0, 1]`. Returns (value, confidence,
/// reported input tokens).
pub fn parse(body: &str, model: &str) -> Result<(f64, f64, Option<u64>), String> {
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
    let (mut sum, mut mean) = (0.0, 0.0);
    for (i, level) in ["0", "1", "2"].into_iter().enumerate() {
        let p = unit(
            probabilities.get(level).and_then(Value::as_f64),
            "probability",
        )?;
        sum += p;
        mean += i as f64 * p;
    }
    if probabilities.len() != 3 || (sum - 1.0).abs() > ROUNDING {
        return Err("probabilities are not one three-level distribution".into());
    }
    if (score - mean).abs() > ROUNDING {
        return Err(format!(
            "score {score} is not its distribution's mean {mean}"
        ));
    }
    let confidence = unit(a["confidence"].as_f64(), "confidence")?;
    let tokens = v["usage"]["input_tokens"].as_u64();
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
        let deadline = Instant::now() + self.config.deadline;
        let bodies: Vec<String> = capsules
            .iter()
            .map(|c| request(c, &self.config.model, self.config.disclosure))
            .collect();
        let answers = self.send(&bodies, deadline);
        let mut out = Vec::new();
        let mut errors = Vec::new();
        for (capsule, answer) in capsules.iter().zip(answers) {
            let error = match answer.map(|a| a.map(|b| parse(&b, &self.config.model))) {
                Answer::Sent(Ok(Ok((value, confidence, _)))) => {
                    self.stats.answered += 1;
                    let confidence = Some(confidence);
                    out.push(Judgment::of(capsule, Verdict::Value { value, confidence }));
                    continue;
                }
                Answer::Sent(Ok(Err(_))) => {
                    self.stats.malformed += 1;
                    ProviderError::Malformed
                }
                Answer::Sent(Err(TransportError::Timeout)) => {
                    self.stats.timeouts += 1;
                    ProviderError::Timeout
                }
                Answer::Sent(Err(_)) => {
                    self.stats.unavailable += 1;
                    ProviderError::Unavailable
                }
                Answer::OverCap => {
                    self.stats.skipped += 1;
                    ProviderError::Unavailable
                }
                Answer::PastDeadline => {
                    self.stats.skipped += 1;
                    ProviderError::Timeout
                }
            };
            errors.push(error);
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

/// What became of one capsule's request.
enum Answer<A> {
    Sent(A),
    /// Not sent: the request or token cap had no room.
    OverCap,
    /// Not sent: the deadline passed before its turn.
    PastDeadline,
}

impl<A> Answer<A> {
    fn map<B>(self, f: impl FnOnce(A) -> B) -> Answer<B> {
        match self {
            Answer::Sent(a) => Answer::Sent(f(a)),
            Answer::OverCap => Answer::OverCap,
            Answer::PastDeadline => Answer::PastDeadline,
        }
    }
}

impl<T: Transport> Jev<T> {
    /// Sends every body with bounded retries on 429/529, in rounds. Each
    /// round admits bodies in capsule order while the request cap and the
    /// token cap (counting each body's bytes, an upper bound on its tokens)
    /// have room, so what is sent never depends on completion order. Unlike
    /// one-at-a-time sending, a 429 is retried after its whole round.
    fn send(
        &mut self,
        bodies: &[String],
        deadline: Instant,
    ) -> Vec<Answer<Result<String, TransportError>>> {
        let mut answers: Vec<_> = bodies.iter().map(|_| Answer::OverCap).collect();
        let all: Vec<usize> = (0..bodies.len()).collect();
        let mut batch = self.admit(&all, bodies);
        let mut attempt = 0;
        loop {
            let refs: Vec<&str> = batch.iter().map(|&i| bodies[i].as_str()).collect();
            let sent = self
                .transport
                .post_all(&refs, deadline, self.config.concurrency);
            let mut retry = Vec::new();
            for (&i, sent) in batch.iter().zip(sent) {
                let Some(Sent { answer, late, .. }) = sent else {
                    // A retry dropped at the deadline keeps its real 429.
                    if matches!(answers[i], Answer::OverCap) {
                        answers[i] = Answer::PastDeadline;
                    }
                    continue;
                };
                self.stats.requests += 1;
                if let Ok(b) = &answer {
                    // Billed whether or not it parses or is late; unreported
                    // usage counts as the body's length.
                    let reported = serde_json::from_str::<Value>(b)
                        .ok()
                        .and_then(|v| v["usage"]["input_tokens"].as_u64());
                    self.stats.input_tokens += reported.unwrap_or(bodies[i].len() as u64);
                }
                // A late answer is a timeout, whatever the transport did.
                let answer = match answer {
                    Ok(_) if late => Err(TransportError::Timeout),
                    other => other,
                };
                if matches!(answer, Err(TransportError::Status(429 | 529))) {
                    retry.push(i);
                }
                answers[i] = Answer::Sent(answer);
            }
            let backoff = BACKOFF * 2u32.pow(attempt);
            if retry.is_empty()
                || attempt >= self.config.retries
                || backoff >= deadline.saturating_duration_since(Instant::now())
            {
                return answers;
            }
            batch = self.admit(&retry, bodies);
            if batch.is_empty() {
                return answers;
            }
            std::thread::sleep(backoff);
            attempt += 1;
            self.stats.retries += batch.len();
        }
    }

    /// The bodies of `pending` the caps leave room for, in order.
    fn admit(&self, pending: &[usize], bodies: &[String]) -> Vec<usize> {
        let mut batch = Vec::new();
        let mut reserved = 0;
        for &i in pending {
            let len = bodies[i].len() as u64;
            if self.stats.requests + batch.len() < self.config.max_requests
                && self.stats.input_tokens + reserved + len <= self.config.max_input_tokens
            {
                reserved += len;
                batch.push(i);
            }
        }
        batch
    }
}

/// HTTPS transport to the JEV service. Plain `http` is accepted only for a
/// loopback host (local mock servers), so the key never crosses a network
/// in clear text. Redirects are not followed.
pub struct Http {
    endpoint: String,
    key: String,
    agent: ureq::Agent,
}

impl std::fmt::Debug for Http {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Http")
            .field("endpoint", &self.endpoint)
            .field("key", &"<redacted>")
            .finish()
    }
}

impl Http {
    pub fn new(endpoint: &str, key: String) -> Result<Self, String> {
        let loopback = ["http://127.0.0.1:", "http://localhost:", "http://[::1]:"];
        if !endpoint.starts_with("https://") && !loopback.iter().any(|p| endpoint.starts_with(p)) {
            return Err(format!("JEV endpoint `{endpoint}` is not HTTPS"));
        }
        if key.trim().is_empty() {
            return Err("empty JEV API key".into());
        }
        // Every in-flight request keeps its connection for the next one.
        let config = ureq::Agent::config_builder()
            .http_status_as_error(false)
            .max_redirects(0)
            .max_idle_connections(MAX_CONCURRENCY)
            .max_idle_connections_per_host(MAX_CONCURRENCY)
            .build();
        Ok(Self {
            endpoint: endpoint.to_owned(),
            key,
            agent: ureq::Agent::new_with_config(config),
        })
    }

    /// The service endpoint with the key from [`KEY_VAR`].
    pub fn from_env() -> Result<Self, String> {
        let key = std::env::var(KEY_VAR).map_err(|_| format!("{KEY_VAR} is not set"))?;
        Self::new(ENDPOINT, key)
    }
}

impl Transport for Http {
    fn post(&mut self, body: &str, timeout: Duration) -> Result<String, TransportError> {
        self.send(body, timeout)
    }

    fn post_all(
        &mut self,
        bodies: &[&str],
        deadline: Instant,
        concurrency: usize,
    ) -> Vec<Option<Sent>> {
        let this = &*self;
        fan_out(bodies, deadline, concurrency, |body, left| {
            this.send(body, left)
        })
    }
}

impl Http {
    fn send(&self, body: &str, timeout: Duration) -> Result<String, TransportError> {
        if timeout.is_zero() {
            return Err(TransportError::Timeout);
        }
        let map = |e: ureq::Error| match e {
            ureq::Error::Timeout(_) => TransportError::Timeout,
            ureq::Error::StatusCode(code) => TransportError::Status(code),
            other => TransportError::Unavailable(other.to_string()),
        };
        let mut response = self
            .agent
            .post(&self.endpoint)
            .config()
            .timeout_global(Some(timeout))
            .build()
            .header("Authorization", &format!("Bearer {}", self.key))
            .header("Content-Type", "application/json")
            .send(body)
            .map_err(map)?;
        let status = response.status().as_u16();
        if !(200..300).contains(&status) {
            return Err(TransportError::Status(status));
        }
        response
            .body_mut()
            .with_config()
            .limit(1 << 20)
            .read_to_string()
            .map_err(map)
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
            Self::keep(&mut exchanges, key.to_owned(), recorded);
        }
        Ok(Self { exchanges })
    }

    /// A request recorded more than once (a 429 then its retry, validity
    /// repeats) replays its first response, or its first failure if it
    /// never got one: what the live call finally answered.
    pub fn keep(exchanges: &mut BTreeMap<String, Recorded>, key: String, recorded: Recorded) {
        let known = exchanges.get(&key);
        if known.is_none()
            || matches!(
                (known, &recorded),
                (Some(Recorded::Failed(_)), Recorded::Response(_))
            )
        {
            exchanges.insert(key, recorded);
        }
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

/// One captured exchange, in send order.
#[derive(Debug, Clone, PartialEq)]
pub struct Exchange {
    pub body: String,
    pub recorded: Recorded,
    pub elapsed: Duration,
}

/// Captures another transport's exchanges for later [`Replay`].
pub struct Recording<T> {
    pub inner: T,
    pub exchanges: Vec<Exchange>,
}

impl<T: Transport> Transport for Recording<T> {
    fn post_all(
        &mut self,
        bodies: &[&str],
        deadline: Instant,
        concurrency: usize,
    ) -> Vec<Option<Sent>> {
        let sent = self.inner.post_all(bodies, deadline, concurrency);
        // In input order, so a recording never depends on completion order.
        for (body, sent) in bodies.iter().zip(&sent) {
            if let Some(Sent {
                answer, elapsed, ..
            }) = sent
            {
                self.exchanges.push(Exchange {
                    body: (*body).to_owned(),
                    recorded: match answer {
                        Ok(r) => Recorded::Response(r.clone()),
                        Err(e) => Recorded::Failed(e.clone()),
                    },
                    elapsed: *elapsed,
                });
            }
        }
        sent
    }

    fn post(&mut self, body: &str, timeout: Duration) -> Result<String, TransportError> {
        let start = Instant::now();
        let answer = self.inner.post(body, timeout);
        let recorded = match &answer {
            Ok(r) => Recorded::Response(r.clone()),
            Err(e) => Recorded::Failed(e.clone()),
        };
        self.exchanges.push(Exchange {
            body: body.to_owned(),
            recorded,
            elapsed: start.elapsed(),
        });
        answer
    }
}

impl<T> Recording<T> {
    /// The `oxide-jev-replay-v1` document, request bodies included for audit.
    pub fn to_json(&self, model: &str) -> String {
        let exchanges: Vec<Value> = self
            .exchanges
            .iter()
            .map(
                |Exchange {
                     body,
                     recorded,
                     elapsed,
                 }| {
                    let request: Value = serde_json::from_str(body).unwrap_or(Value::Null);
                    // A body that is not JSON (the malformed case worth
                    // replaying) is kept verbatim as text.
                    let mut entry = json!({"request_sha256": sha256_hex(body), "request": request,
                                       "elapsed_ms": elapsed.as_millis() as u64});
                    match recorded {
                        Recorded::Response(r) => match serde_json::from_str::<Value>(r) {
                            Ok(v) => entry["response"] = v,
                            Err(_) => entry["response_text"] = json!(r),
                        },
                        Recorded::Failed(TransportError::Timeout) => {
                            entry["error"] = json!("timeout")
                        }
                        Recorded::Failed(TransportError::Status(code)) => {
                            entry["error"] = json!({"status": code})
                        }
                        Recorded::Failed(TransportError::Unavailable(why)) => {
                            entry["error"] = json!({"unavailable": why})
                        }
                    }
                    entry
                },
            )
            .collect();
        serde_json::to_string_pretty(
            &json!({"format": REPLAY_FORMAT, "model": model, "exchanges": exchanges}),
        )
        .expect("JSON")
            + "\n"
    }
}

/// Validity of a recorded exchange log (SPEC § JEV: repeatability, schema
/// validity, model identity, latency), with requests grouped by the
/// SHA-256 of their body. Descriptive only: no threshold lives here.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Validity {
    pub exchanges: usize,
    pub valid: usize,
    pub malformed: usize,
    /// Transport failures and non-2xx statuses, by kind.
    pub failed: BTreeMap<String, usize>,
    /// Distinct request bodies sent more than once with 2+ valid answers.
    pub repeated: usize,
    /// Of those, how many got bit-identical value and confidence every time.
    pub identical: usize,
    /// Of those, how many kept their value within [`STABLE_SPREAD`].
    pub stable: usize,
    /// Largest and mean spread (max - min) of the value over a repeated group.
    pub max_value_spread: f64,
    pub mean_value_spread: f64,
    pub max_confidence_spread: f64,
    /// Models named by responses (any value but the pinned one is malformed).
    pub models: Vec<String>,
    /// Recorded latency of every exchange, milliseconds, sorted.
    pub latency_ms: Vec<u64>,
}

/// Reads an `oxide-jev-replay-v1` document and measures its validity.
/// The value spread under which a repeated answer counts as stable
/// (preregistration § Gates, validity).
pub const STABLE_SPREAD: f64 = 0.05;

pub fn validity(document: &str, model: &str) -> Result<Validity, String> {
    let v: Value = serde_json::from_str(document).map_err(|e| e.to_string())?;
    if v["format"] != REPLAY_FORMAT {
        return Err(format!("not {REPLAY_FORMAT}"));
    }
    let mut out = Validity::default();
    let mut groups: BTreeMap<&str, Vec<(f64, f64)>> = BTreeMap::new();
    for e in v["exchanges"].as_array().ok_or("no exchanges")? {
        out.exchanges += 1;
        let key = e["request_sha256"].as_str().ok_or("no request_sha256")?;
        out.latency_ms.extend(e["elapsed_ms"].as_u64());
        let body = match (&e["response"], e["response_text"].as_str()) {
            (_, Some(text)) => text.to_owned(),
            (Value::Null, None) => {
                let kind = match &e["error"] {
                    Value::String(s) => s.clone(),
                    err if err["status"].is_u64() => format!("status {}", err["status"]),
                    _ => "unavailable".into(),
                };
                *out.failed.entry(kind).or_default() += 1;
                continue;
            }
            (response, None) => response.to_string(),
        };
        if let Some(m) = serde_json::from_str::<Value>(&body)
            .ok()
            .and_then(|r| r["model"].as_str().map(str::to_owned))
            && !out.models.contains(&m)
        {
            out.models.push(m);
        }
        match parse(&body, model) {
            Ok((value, confidence, _)) => {
                out.valid += 1;
                groups.entry(key).or_default().push((value, confidence));
            }
            Err(_) => out.malformed += 1,
        }
    }
    let spread = |xs: &mut dyn Iterator<Item = f64>| {
        let (lo, hi) = xs.fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), x| {
            (lo.min(x), hi.max(x))
        });
        hi - lo
    };
    let mut total = 0.0;
    for answers in groups.values().filter(|a| a.len() > 1) {
        out.repeated += 1;
        let value = spread(&mut answers.iter().map(|a| a.0));
        let confidence = spread(&mut answers.iter().map(|a| a.1));
        if answers.iter().all(|a| a == &answers[0]) {
            out.identical += 1;
        }
        if value <= STABLE_SPREAD + 1e-12 {
            out.stable += 1;
        }
        total += value;
        out.max_value_spread = out.max_value_spread.max(value);
        out.max_confidence_spread = out.max_confidence_spread.max(confidence);
    }
    if out.repeated > 0 {
        out.mean_value_spread = total / out.repeated as f64;
    }
    out.models.sort();
    out.latency_ms.sort_unstable();
    Ok(out)
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
        named(question, "f")
    }

    fn named(question: Question, name: &str) -> Capsule {
        let file = RepoPath::new("a.py").unwrap();
        let id = EntityId::Symbol(
            SymbolId::new(
                file,
                vec![Segment {
                    name: name.into(),
                    ordinal: 0,
                }],
            )
            .unwrap(),
        );
        let entity = Entity {
            id: id.clone(),
            kind: "function".into(),
            name: name.into(),
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
            max_input_tokens: 1_000_000,
            retries: 2,
            disclosure: Disclosure::Metadata,
            concurrency: 1,
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
        // The live service's two-decimal rounding parses (a captured answer).
        let live = r#"{"model":"jev-1.13.0","answers":{"judgment":{"type":"score","score":1.32,"confidence":0.46,"legend":{"0":"a","1":"b","2":"c"},"probabilities":{"0":0.02,"1":0.64,"2":0.34}}},"usage":{"input_tokens":1430,"output_tokens":19}}"#;
        assert_eq!(parse(live, MODEL), Ok((0.66, 0.46, Some(1430))));
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

        // Rust's policy, not the adapter, handles confidence: JEV's own
        // confidence is uncalibrated, so a floor over it accepts nothing.
        let floor = DecisionPolicy {
            min_confidence: Some(0.3),
            confidence_calibrated: false,
        };
        let d = decide(
            Some(&mut jev),
            std::slice::from_ref(&c),
            &mut allowance,
            floor,
        );
        assert_eq!(d[0].fallback, Some(Fallback::Uncalibrated));
        let calibrated = DecisionPolicy {
            min_confidence: Some(0.5),
            confidence_calibrated: true,
        };
        let d = decide(
            Some(&mut jev),
            std::slice::from_ref(&c),
            &mut allowance,
            calibrated,
        );
        assert_eq!(d[0].fallback, Some(Fallback::LowConfidence));
    }

    #[test]
    fn malformed_answers_fall_back() {
        let c = capsule(Question::NeighborValue);
        let bad = [
            answer(2.5, [0.0, 0.0, 1.0], 0.9),  // score out of range
            answer(1.0, [0.5, 0.5, 0.5], 0.9),  // not a distribution
            answer(1.0, [0.0, 0.97, 0.0], 0.9), // sums to 0.97
            answer(1.0, [0.0, 1.0, 0.0], 1.5),  // confidence out of range
            answer(1.0, [0.0, 1.0, 0.0], 0.9).replace(MODEL, "jev-1.12.0"), // other model
            answer(1.0, [0.0, 1.0, 0.0], 0.9).replace("\"type\":\"score\"", "\"type\":\"noul\""),
            answer(1.05, [0.0, 1.0, 0.0], 0.9), // score is not the distribution's mean
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
        for (body, recorded) in [
            ("not json", Recorded::Response("<html>".into())),
            ("rate", Recorded::Failed(TransportError::Status(429))),
        ] {
            recording.exchanges.push(Exchange {
                body: body.into(),
                recorded,
                elapsed: Duration::from_millis(3),
            });
        }
        let document = recording.to_json(MODEL);
        let replay = Replay::from_json(&document).unwrap();
        let replayed: Vec<&Recorded> = replay.exchanges.values().collect();
        assert!(replayed.contains(&&Recorded::Response("<html>".into())));
        assert!(replayed.contains(&&Recorded::Failed(TransportError::Status(429))));
        let mut again = Jev::new(config(), replay).unwrap();
        assert_eq!(again.judge(std::slice::from_ref(&c)).unwrap(), first);
    }

    /// Answers every body after a delay that makes later bodies finish
    /// first, counting the most requests ever in flight.
    struct Delayed {
        answers: BTreeMap<String, String>,
        /// Delay per rank: the last body waits one unit, the first twelve.
        unit: Duration,
        in_flight: AtomicUsize,
        peak: AtomicUsize,
    }

    impl Transport for Delayed {
        fn post(&mut self, body: &str, _: Duration) -> Result<String, TransportError> {
            Ok(self.answers[body].clone())
        }

        fn post_all(
            &mut self,
            bodies: &[&str],
            deadline: Instant,
            concurrency: usize,
        ) -> Vec<Option<Sent>> {
            let this = &*self;
            fan_out(bodies, deadline, concurrency, |body, _| {
                let now = this.in_flight.fetch_add(1, Ordering::SeqCst) + 1;
                this.peak.fetch_max(now, Ordering::SeqCst);
                let rank = bodies.len() - bodies.iter().position(|b| *b == body).unwrap();
                std::thread::sleep(this.unit * rank as u32);
                this.in_flight.fetch_sub(1, Ordering::SeqCst);
                Ok(this.answers[body].clone())
            })
        }
    }

    #[test]
    fn concurrent_answers_apply_in_capsule_order_within_bounds() {
        let capsules: Vec<Capsule> = (0..12)
            .map(|i| named(Question::Relevance, &format!("f{i}")))
            .collect();
        let answers: BTreeMap<String, String> = capsules
            .iter()
            .enumerate()
            .map(|(i, c)| {
                let mut p = [0.0; 3];
                p[i % 3] = 1.0;
                let body = request(c, MODEL, Disclosure::Metadata);
                (body, answer((i % 3) as f64, p, 0.9))
            })
            .collect();
        let transport = |unit| Delayed {
            answers: answers.clone(),
            unit,
            in_flight: AtomicUsize::new(0),
            peak: AtomicUsize::new(0),
        };
        let run = |config: JevConfig, unit| {
            let mut jev = Jev::new(config, transport(unit)).unwrap();
            let mut allowance = Allowance { remaining: 100 };
            let policy = DecisionPolicy::default();
            let d = decide(Some(&mut jev), &capsules, &mut allowance, policy);
            let stats = jev.stats;
            (d, stats, jev.into_transport().peak.into_inner())
        };
        let ms = Duration::from_millis;
        let (one, stats, peak) = run(config(), ms(2));
        assert_eq!(peak, 1);
        assert!(one.iter().all(|d| d.fallback.is_none()));
        let (eight, stats8, peak8) = run(
            JevConfig {
                concurrency: 8,
                ..config()
            },
            ms(2),
        );
        // Completion order is reversed, yet every decision and count is
        // the sequential run's.
        assert_eq!((&one, stats), (&eight, stats8));
        assert!((2..=8).contains(&peak8), "{peak8}");

        // Past the deadline, queued requests are dropped unsent and the
        // late ones time out (the first two take 330+ ms, the deadline is
        // 100 ms, ample time for both workers to start).
        let (late, stats, _) = run(
            JevConfig {
                concurrency: 2,
                deadline: ms(100),
                ..config()
            },
            ms(30),
        );
        assert_eq!((stats.requests, stats.skipped, stats.answered), (2, 10, 0));
        let timeout = Some(Fallback::ProviderFailed(ProviderError::Timeout));
        assert!(late.iter().all(|d| d.fallback == timeout));
        for concurrency in [0, MAX_CONCURRENCY + 1] {
            let bad = JevConfig {
                concurrency,
                ..config()
            };
            assert!(Jev::new(bad, Replay::default()).is_err());
        }
    }

    /// Scripted transport: answers in order, then refuses.
    struct Script(Vec<Result<String, TransportError>>);

    impl Transport for Script {
        fn post(&mut self, _: &str, _: Duration) -> Result<String, TransportError> {
            if self.0.is_empty() {
                return Err(TransportError::Unavailable("script ended".into()));
            }
            self.0.remove(0)
        }
    }

    fn judge_one(jev: &mut Jev<impl Transport>, c: &Capsule) -> Option<Fallback> {
        let mut allowance = Allowance { remaining: 10 };
        decide(
            Some(jev),
            std::slice::from_ref(c),
            &mut allowance,
            DecisionPolicy::default(),
        )[0]
        .fallback
    }

    #[test]
    fn rate_limits_are_retried_boundedly_and_caps_hold() {
        let c = capsule(Question::Relevance);
        let ok = || Ok(answer(2.0, [0.0, 0.0, 1.0], 1.0));
        let busy = || Err(TransportError::Status(429));
        let overloaded = || Err(TransportError::Status(529));
        let mut jev = Jev::new(config(), Script(vec![busy(), overloaded(), ok()])).unwrap();
        assert_eq!(judge_one(&mut jev, &c), None);
        assert_eq!((jev.stats.requests, jev.stats.retries), (3, 2));

        // Retries are bounded: a third 429 is the answer.
        let mut jev = Jev::new(config(), Script(vec![busy(), busy(), busy(), ok()])).unwrap();
        let unavailable = Some(Fallback::ProviderFailed(ProviderError::Unavailable));
        assert_eq!(judge_one(&mut jev, &c), unavailable);
        assert_eq!((jev.stats.requests, jev.stats.retries), (3, 2));

        // Other statuses are not retried.
        let mut jev = Jev::new(
            config(),
            Script(vec![Err(TransportError::Status(500)), ok()]),
        )
        .unwrap();
        assert_eq!(judge_one(&mut jev, &c), unavailable);
        assert_eq!(jev.stats.requests, 1);

        // A retry never outlives the deadline.
        let tight = JevConfig {
            deadline: Duration::from_millis(100),
            ..config()
        };
        let mut jev = Jev::new(tight, Script(vec![busy(), ok()])).unwrap();
        assert_eq!(judge_one(&mut jev, &c), unavailable);
        assert_eq!(jev.stats.retries, 0);

        // The token cap counts body bytes before sending, usage after.
        let body = request(&c, MODEL, Disclosure::Metadata);
        let capped = JevConfig {
            max_input_tokens: body.len() as u64 - 1,
            ..config()
        };
        let mut jev = Jev::new(capped, Script(vec![ok()])).unwrap();
        assert_eq!(judge_one(&mut jev, &c), unavailable);
        assert_eq!((jev.stats.requests, jev.stats.skipped), (0, 1));
        let one = JevConfig {
            max_input_tokens: body.len() as u64 + 299,
            ..config()
        };
        let mut jev = Jev::new(one, Script(vec![ok(), ok()])).unwrap();
        assert_eq!(judge_one(&mut jev, &c), None);
        assert_eq!(jev.stats.input_tokens, 300);
        assert!((jev.stats.cost_usd() - 300.0 * USD_PER_INPUT_TOKEN).abs() < 1e-15);
        assert_eq!(judge_one(&mut jev, &c), unavailable);
    }

    #[test]
    fn validity_groups_repeats_and_counts_failures() {
        let c = capsule(Question::Relevance);
        let body = request(&c, MODEL, Disclosure::Metadata);
        let exchange = |recorded| Exchange {
            body: body.clone(),
            recorded,
            elapsed: Duration::from_millis(40),
        };
        let recording = Recording {
            inner: Script(vec![]),
            exchanges: vec![
                exchange(Recorded::Response(answer(1.0, [0.1, 0.8, 0.1], 0.7))),
                exchange(Recorded::Response(answer(1.2, [0.0, 0.8, 0.2], 0.6))),
                exchange(Recorded::Response(
                    answer(1.0, [0.0, 1.0, 0.0], 0.9).replace(MODEL, "jev-9"),
                )),
                exchange(Recorded::Failed(TransportError::Status(529))),
            ],
        };
        let v = validity(&recording.to_json(MODEL), MODEL).unwrap();
        assert_eq!((v.exchanges, v.valid, v.malformed), (4, 2, 1));
        assert_eq!(v.failed.get("status 529"), Some(&1));
        assert_eq!((v.repeated, v.identical, v.stable), (1, 0, 0));
        assert!((v.max_value_spread - 0.1).abs() < 1e-9);
        assert_eq!(v.models, ["jev-1.13.0", "jev-9"]);
        assert_eq!(v.latency_ms, [40, 40, 40, 40]);
        // Replay answers a repeated request with its first response, even
        // when a failure was recorded before it.
        let mut recording = recording;
        recording.exchanges.rotate_right(1);
        let replay = Replay::from_json(&recording.to_json(MODEL)).unwrap();
        let first = Recorded::Response(answer(1.0, [0.1, 0.8, 0.1], 0.7));
        assert_eq!(replay.exchanges.values().next(), Some(&first));
    }

    /// A one-connection-per-response HTTP server on loopback: it answers
    /// `(status, body, delay)` in order and returns every request it read.
    fn serve(
        script: Vec<(u16, String, Duration)>,
    ) -> (String, std::thread::JoinHandle<Vec<String>>) {
        use std::io::{Read, Write};
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!(
            "http://127.0.0.1:{}/v1/systemone",
            listener.local_addr().unwrap().port()
        );
        let handle = std::thread::spawn(move || {
            let mut seen = Vec::new();
            for (status, body, delay) in script {
                let (mut stream, _) = listener.accept().unwrap();
                let mut raw = Vec::new();
                let mut buf = [0u8; 4096];
                let header_end = loop {
                    let n = stream.read(&mut buf).unwrap();
                    raw.extend_from_slice(&buf[..n]);
                    if let Some(i) = raw.windows(4).position(|w| w == b"\r\n\r\n") {
                        break i + 4;
                    }
                };
                let head = String::from_utf8_lossy(&raw[..header_end]).to_lowercase();
                let length: usize = head
                    .lines()
                    .find_map(|l| l.strip_prefix("content-length:"))
                    .map_or(0, |v| v.trim().parse().unwrap());
                while raw.len() < header_end + length {
                    let n = stream.read(&mut buf).unwrap();
                    raw.extend_from_slice(&buf[..n]);
                }
                seen.push(String::from_utf8(raw).unwrap());
                std::thread::sleep(delay);
                let reply = format!(
                    "HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = stream.write_all(reply.as_bytes());
            }
            seen
        });
        (url, handle)
    }

    #[test]
    fn http_transport_against_a_local_server() {
        let c = capsule(Question::Relevance);
        let good = answer(1.6, [0.0, 0.4, 0.6], 0.4);
        let now = Duration::ZERO;
        let (url, server) = serve(vec![
            (200, good.clone(), now),
            (429, "{}".into(), now),
            (200, good.clone(), now),
            (500, "oops".into(), now),
            (200, "<html>".into(), now),
            (200, good.clone(), Duration::from_millis(400)),
        ]);
        let key = "sk-test-secret".to_string();
        let http = Http::new(&url, key.clone()).unwrap();
        assert!(!format!("{http:?}").contains(&key));
        let recording = Recording {
            inner: http,
            exchanges: Vec::new(),
        };
        let mut jev = Jev::new(config(), recording).unwrap();
        assert_eq!(judge_one(&mut jev, &c), None);
        assert_eq!(judge_one(&mut jev, &c), None); // 429, retried
        assert_eq!(jev.stats.retries, 1);
        let failed = |e| Some(Fallback::ProviderFailed(e));
        assert_eq!(judge_one(&mut jev, &c), failed(ProviderError::Unavailable));
        assert_eq!(judge_one(&mut jev, &c), failed(ProviderError::Malformed));
        jev.config.deadline = Duration::from_millis(100);
        assert_eq!(judge_one(&mut jev, &c), failed(ProviderError::Timeout));
        let seen = server.join().unwrap();
        assert_eq!(seen.len(), 6);
        let body = request(&c, MODEL, Disclosure::Metadata);
        for raw in &seen {
            assert!(raw.starts_with("POST /v1/systemone HTTP/1.1\r\n"));
            assert!(
                raw.contains(&format!("\r\nauthorization: Bearer {key}\r\n"))
                    || raw.contains(&format!("\r\nAuthorization: Bearer {key}\r\n"))
            );
            assert!(raw.ends_with(&body));
        }
        // The key is a header only: never in a recording.
        let document = jev.into_transport().to_json(MODEL);
        assert!(!document.contains(&key));

        // Nothing listening: unavailable, not a hang.
        let dead = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = dead.local_addr().unwrap().port();
        drop(dead);
        let http = Http::new(
            &format!("http://127.0.0.1:{port}/v1/systemone"),
            key.clone(),
        )
        .unwrap();
        let mut jev = Jev::new(config(), http).unwrap();
        assert_eq!(judge_one(&mut jev, &c), failed(ProviderError::Unavailable));

        for (endpoint, key) in [
            ("http://api.typesafe.ai/v1/systemone", "k"),
            (ENDPOINT, " "),
        ] {
            assert!(Http::new(endpoint, key.into()).is_err());
        }
    }
}
