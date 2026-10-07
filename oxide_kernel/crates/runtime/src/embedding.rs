//! Embedding provider boundary (SPEC § Embedding providers). External runners
//! (Ollama, llama.cpp) execute inference; runtime clients implement
//! [`EmbeddingProvider`] against them in Phase 3. Phase 1 fixes identity,
//! capability, batching and error contracts, and the rule that discovery is
//! advisory: only the configured runner is ever probed or used.

/// Runner API family. Says how to talk to a runner, not which model to use.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Runner {
    Ollama,
    LlamaCpp,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Endpoint {
    pub runner: Runner,
    pub url: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Metric {
    Cosine,
    Dot,
    L2,
}

/// Embedding-space fingerprint. `None` means unknown/unreported and never
/// counts as a match; `Some("")` is a known empty prompt.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct EmbeddingSpace {
    pub runner: Runner,
    /// Configured model name; a mutable alias on most runners.
    pub model: String,
    pub model_digest: Option<String>,
    pub quantization: Option<String>,
    pub dimension: u32,
    pub metric: Metric,
    pub normalized: Option<bool>,
    pub pooling: Option<String>,
    pub tokenizer: Option<String>,
    pub query_prompt: Option<String>,
    pub document_prompt: Option<String>,
    /// How entity text is assembled before embedding; OXIDE-owned.
    pub document_recipe: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Compatibility {
    Same,
    Different,
    /// No known field differs, but some are unknown: unproven, so vectors
    /// from the two spaces are never compared.
    Unverified,
}

impl EmbeddingSpace {
    pub fn compare(&self, other: &Self) -> Compatibility {
        let known = [
            self.runner == other.runner,
            self.model == other.model,
            self.dimension == other.dimension,
            self.metric == other.metric,
            self.document_recipe == other.document_recipe,
        ];
        let optional = [
            field(&self.model_digest, &other.model_digest),
            field(&self.quantization, &other.quantization),
            field(&self.normalized, &other.normalized),
            field(&self.pooling, &other.pooling),
            field(&self.tokenizer, &other.tokenizer),
            field(&self.query_prompt, &other.query_prompt),
            field(&self.document_prompt, &other.document_prompt),
        ];
        if known.contains(&false) || optional.contains(&Compatibility::Different) {
            Compatibility::Different
        } else if optional.contains(&Compatibility::Unverified) {
            Compatibility::Unverified
        } else {
            Compatibility::Same
        }
    }
}

fn field<T: PartialEq>(a: &Option<T>, b: &Option<T>) -> Compatibility {
    match (a, b) {
        (Some(a), Some(b)) if a == b => Compatibility::Same,
        (Some(_), Some(_)) => Compatibility::Different,
        _ => Compatibility::Unverified,
    }
}

/// What a runner reports about the model it serves.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Probe {
    pub space: EmbeddingSpace,
    pub max_batch: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EmbedError {
    /// Runner not reachable.
    Unavailable,
    /// Deadline passed; the caller falls back, it does not wait longer.
    Timeout,
    /// Runner reachable, configured model not present.
    ModelMissing,
    /// Wrong count, dimension, non-finite or unnormalized vectors.
    Malformed(String),
}

/// Runtime client for one runner endpoint.
pub trait EmbeddingProvider {
    fn endpoint(&self) -> &Endpoint;
    fn probe(&self) -> Result<Probe, EmbedError>;
    /// One vector per text, in order; at most `Probe::max_batch` texts.
    fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError>;
}

/// The persisted, explicitly chosen provider. Only explicit reconfiguration
/// replaces it; if the new space does not `compare` as `Same`, vectors built
/// under the old one are invalidated.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Configured {
    pub endpoint: Endpoint,
    pub space: EmbeddingSpace,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Readiness {
    Ready,
    Unavailable(EmbedError),
    /// The runner now serves a different space than configured.
    Mismatch,
    /// The runner cannot prove it serves the configured space.
    Unverified,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SemanticStatus {
    /// Lexical/structural only. `offers` are discovered runners shown as
    /// setup choices; none is enabled.
    Unconfigured { offers: Vec<Endpoint> },
    /// Semantics follow `endpoint` only. `alternatives` are other discovered
    /// runners, reported and never substituted.
    Configured {
        endpoint: Endpoint,
        readiness: Readiness,
        alternatives: Vec<Endpoint>,
    },
}

/// Resolves semantic status. Probes the configured runner only; `discovered`
/// endpoints are listed, never contacted or selected.
pub fn semantic_status(
    configured: Option<(&Configured, &dyn EmbeddingProvider)>,
    discovered: &[Endpoint],
) -> SemanticStatus {
    let Some((config, client)) = configured else {
        return SemanticStatus::Unconfigured {
            offers: discovered.to_vec(),
        };
    };
    assert_eq!(
        client.endpoint(),
        &config.endpoint,
        "client for another runner"
    );
    let readiness = match client.probe() {
        Err(error) => Readiness::Unavailable(error),
        Ok(probe) => match config.space.compare(&probe.space) {
            Compatibility::Same => Readiness::Ready,
            Compatibility::Different => Readiness::Mismatch,
            Compatibility::Unverified => Readiness::Unverified,
        },
    };
    SemanticStatus::Configured {
        endpoint: config.endpoint.clone(),
        readiness,
        alternatives: discovered
            .iter()
            .filter(|e| **e != config.endpoint)
            .cloned()
            .collect(),
    }
}

/// Embeds `texts` in batches of at most `max_batch`, validating every
/// response against `space`.
pub fn embed_all(
    client: &dyn EmbeddingProvider,
    space: &EmbeddingSpace,
    max_batch: usize,
    texts: &[String],
) -> Result<Vec<Vec<f32>>, EmbedError> {
    let mut out = Vec::with_capacity(texts.len());
    for chunk in texts.chunks(max_batch.max(1)) {
        let vectors = client.embed(chunk)?;
        if vectors.len() != chunk.len() {
            return Err(EmbedError::Malformed(format!(
                "{} vectors for {} texts",
                vectors.len(),
                chunk.len()
            )));
        }
        for vector in &vectors {
            check_vector(space, vector)?;
        }
        out.extend(vectors);
    }
    Ok(out)
}

fn check_vector(space: &EmbeddingSpace, vector: &[f32]) -> Result<(), EmbedError> {
    if vector.len() != space.dimension as usize {
        return Err(EmbedError::Malformed(format!(
            "dimension {} != {}",
            vector.len(),
            space.dimension
        )));
    }
    if !vector.iter().all(|x| x.is_finite()) {
        return Err(EmbedError::Malformed("non-finite component".into()));
    }
    let norm = vector.iter().map(|x| x * x).sum::<f32>().sqrt();
    if space.normalized == Some(true) && (norm - 1.0).abs() > 1e-3 {
        return Err(EmbedError::Malformed(format!("norm {norm} != 1")));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    type Answer = fn(&[String], u32) -> Vec<Vec<f32>>;

    /// Fake runner fixture: serves `space` or is down, and answers batches
    /// through `answer`.
    struct FakeRunner {
        endpoint: Endpoint,
        space: EmbeddingSpace,
        up: bool,
        answer: Answer,
    }

    impl EmbeddingProvider for FakeRunner {
        fn endpoint(&self) -> &Endpoint {
            &self.endpoint
        }

        fn probe(&self) -> Result<Probe, EmbedError> {
            match self.up {
                true => Ok(Probe {
                    space: self.space.clone(),
                    max_batch: 2,
                }),
                false => Err(EmbedError::Unavailable),
            }
        }

        fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>, EmbedError> {
            self.probe()?;
            assert!(texts.len() <= 2, "batch over max_batch");
            Ok((self.answer)(texts, self.space.dimension))
        }
    }

    fn unit(texts: &[String], dimension: u32) -> Vec<Vec<f32>> {
        let mut v = vec![0.0; dimension as usize];
        v[0] = 1.0;
        vec![v; texts.len()]
    }

    fn endpoint(runner: Runner, port: u16) -> Endpoint {
        Endpoint {
            runner,
            url: format!("http://127.0.0.1:{port}"),
        }
    }

    fn space() -> EmbeddingSpace {
        EmbeddingSpace {
            runner: Runner::Ollama,
            model: "embed-model".into(),
            model_digest: Some("sha256:aaa".into()),
            quantization: Some("q8_0".into()),
            dimension: 4,
            metric: Metric::Cosine,
            normalized: Some(true),
            pooling: Some("mean".into()),
            tokenizer: Some("tok-1".into()),
            query_prompt: Some(String::new()),
            document_prompt: Some(String::new()),
            document_recipe: "signature+body/1".into(),
        }
    }

    fn runner(up: bool) -> FakeRunner {
        FakeRunner {
            endpoint: endpoint(Runner::Ollama, 11434),
            space: space(),
            up,
            answer: unit,
        }
    }

    fn configured() -> Configured {
        Configured {
            endpoint: endpoint(Runner::Ollama, 11434),
            space: space(),
        }
    }

    #[test]
    fn unconfigured_unavailable_and_discovered_runners_are_distinct_states() {
        let other = endpoint(Runner::LlamaCpp, 8080);
        let discovered = [other.clone()];
        let unconfigured = SemanticStatus::Unconfigured {
            offers: vec![other.clone()],
        };
        assert_eq!(semantic_status(None, &discovered), unconfigured);

        let config = configured();
        let status = |client: &FakeRunner, found: &[Endpoint]| {
            semantic_status(Some((&config, client as &dyn EmbeddingProvider)), found)
        };
        let configured = |readiness, alternatives| SemanticStatus::Configured {
            endpoint: config.endpoint.clone(),
            readiness,
            alternatives,
        };
        assert_eq!(
            status(&runner(true), &[]),
            configured(Readiness::Ready, vec![])
        );

        // A newly discovered runner is reported, not adopted.
        let found = [config.endpoint.clone(), other.clone()];
        assert_eq!(
            status(&runner(true), &found),
            configured(Readiness::Ready, vec![other.clone()])
        );

        // The configured runner going down degrades visibly; the healthy
        // alternative is still only an alternative.
        let down = Readiness::Unavailable(EmbedError::Unavailable);
        assert_eq!(
            status(&runner(false), &discovered),
            configured(down, vec![other])
        );
    }

    #[test]
    fn model_drift_and_unproven_identity_are_not_ready() {
        let config = configured();
        let readiness = |client: &FakeRunner| match semantic_status(Some((&config, client)), &[]) {
            SemanticStatus::Configured { readiness, .. } => readiness,
            other => panic!("{other:?}"),
        };
        let mut drifted = runner(true);
        drifted.space.model_digest = Some("sha256:bbb".into());
        assert_eq!(readiness(&drifted), Readiness::Mismatch);
        let mut opaque = runner(true);
        opaque.space.model_digest = None;
        assert_eq!(readiness(&opaque), Readiness::Unverified);
    }

    #[test]
    fn spaces_compare_on_every_fingerprint_field() {
        let a = space();
        assert_eq!(a.compare(&a), Compatibility::Same);
        // Same alias and dimension, different recipe: a different space.
        let recipe = EmbeddingSpace {
            document_recipe: "body/2".into(),
            ..space()
        };
        assert_eq!(a.compare(&recipe), Compatibility::Different);
        let unknown = EmbeddingSpace {
            pooling: None,
            ..space()
        };
        assert_eq!(a.compare(&unknown), Compatibility::Unverified);
        assert_eq!(unknown.compare(&unknown), Compatibility::Unverified);
    }

    #[test]
    fn batches_respect_max_batch_and_validate_vectors() {
        let texts: Vec<String> = (0..5).map(|i| format!("t{i}")).collect();
        let vectors = embed_all(&runner(true), &space(), 2, &texts).unwrap();
        assert_eq!(vectors.len(), 5);

        let wrong_dimension = |t: &[String], d| unit(t, d + 1);
        let missing_vector = |t: &[String], d| unit(&t[1..], d);
        let non_finite = |t: &[String], d| vec![vec![f32::NAN; d as usize]; t.len()];
        let unnormalized = |t: &[String], d| vec![vec![1.0; d as usize]; t.len()];
        let bad: [Answer; 4] = [wrong_dimension, missing_vector, non_finite, unnormalized];
        for answer in bad {
            let client = FakeRunner {
                answer,
                ..runner(true)
            };
            assert!(matches!(
                embed_all(&client, &space(), 2, &texts),
                Err(EmbedError::Malformed(_))
            ));
        }
        assert_eq!(
            embed_all(&runner(false), &space(), 2, &texts),
            Err(EmbedError::Unavailable)
        );
    }
}
