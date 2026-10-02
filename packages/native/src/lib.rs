//! Node-API binding behind `@oxide/client`'s native backend (#36): the same
//! `RepositoryService` calls the CLI makes, kept warm in the host process.
//!
//! Each call answers exactly what `oxide <command> --json` prints, as JSON
//! text: the result (`ok: true`) or the `{error:{code,action,message}}`
//! envelope (`ok: false`), so `@oxide/protocol` validates both backends the
//! same way. Defaults and flag validation mirror the CLI
//! (`src/cli/args.rs`, `src/cli/commands/mod.rs`); the client's integration
//! suite runs against both backends to hold that parity. The service runs
//! with the process cache, like `oxide mcp`, on libuv's worker pool.

use napi::bindgen_prelude::{AsyncTask, Env, Error, Result, Task};
use napi_derive::napi;
use oxide::index::IndexOptions as IndexFlags;
use oxide::retrieval::{RetrievalMode, SearchMode};
use oxide::service::{ErrorAction, RepositoryService, SearchRequest, ServiceError};
use std::panic::{catch_unwind, AssertUnwindSafe};

const DEFAULT_SEARCH_LIMIT: usize = 10;
const DEFAULT_CONTEXT_BUDGET: usize = 4096;

/// What `oxide <command> --json` would have printed, and whether it exited 0.
#[napi(object)]
pub struct Outcome {
    pub ok: bool,
    pub json: String,
}

#[napi(object)]
pub struct IndexOptions {
    pub rebuild: Option<bool>,
}

#[napi(object)]
pub struct SearchOptions {
    pub limit: Option<f64>,
    pub mode: Option<String>,
    pub profile: Option<String>,
    pub expand: Option<bool>,
    pub blast_radius: Option<bool>,
}

#[napi(object)]
pub struct LiteralOptions {
    pub limit: Option<f64>,
}

#[napi(object)]
pub struct QueryOptions {
    pub budget_tokens: Option<f64>,
    pub profile: Option<String>,
    pub blast_radius: Option<bool>,
    pub git: Option<bool>,
}

/// One repository, addressed explicitly (`oxide <command> <path>`). The path
/// is resolved on every call, so a missing repository is the service's own
/// `repository_not_found`, as with the binary.
#[napi]
pub struct Repository {
    path: String,
}

#[napi]
impl Repository {
    #[napi(constructor)]
    pub fn new(path: String) -> Self {
        Self { path }
    }

    #[napi]
    pub fn status(&self) -> AsyncTask<Call> {
        self.call(|service| answer(service.status()))
    }

    #[napi]
    pub fn index(&self, options: IndexOptions) -> AsyncTask<Call> {
        let flags = if options.rebuild.unwrap_or(false) {
            IndexFlags::all()
        } else {
            IndexFlags::default()
        };
        self.call(move |service| answer(service.index(None, &flags)))
    }

    #[napi]
    pub fn search(&self, query: String, options: SearchOptions) -> Result<AsyncTask<Call>> {
        let limit = count("limit", options.limit, DEFAULT_SEARCH_LIMIT)?;
        let mode = match options.mode.as_deref().unwrap_or("hybrid") {
            "lexical" => SearchMode::LexicalOnly,
            "semantic" | "vector" => SearchMode::VectorOnly,
            "hybrid" => SearchMode::Hybrid,
            other => {
                return Ok(failed(format!(
                    "unknown mode {other}; use lexical|semantic|hybrid|literal"
                )))
            }
        };
        let retrieval_mode = match profile(options.profile.as_deref()) {
            Ok(mode) => mode,
            Err(outcome) => return Ok(done(outcome)),
        };
        let request = SearchRequest {
            limit,
            mode,
            expand: options.expand.unwrap_or(true),
            retrieval_mode,
            blast_radius: options.blast_radius.unwrap_or(false),
        };
        Ok(self.call(move |service| answer(service.search(&query, request))))
    }

    #[napi]
    pub fn search_literal(
        &self,
        pattern: String,
        options: LiteralOptions,
    ) -> Result<AsyncTask<Call>> {
        let limit = count("limit", options.limit, DEFAULT_SEARCH_LIMIT)?;
        if pattern.trim().is_empty() {
            return Ok(failed("literal search pattern must not be empty".into()));
        }
        Ok(self.call(move |service| answer(service.search_literal(&pattern, limit))))
    }

    #[napi]
    pub fn query(&self, task: String, options: QueryOptions) -> Result<AsyncTask<Call>> {
        let budget = count(
            "budgetTokens",
            options.budget_tokens,
            DEFAULT_CONTEXT_BUDGET,
        )?;
        let retrieval_mode = match profile(options.profile.as_deref()) {
            Ok(mode) => mode,
            Err(outcome) => return Ok(done(outcome)),
        };
        let blast_radius = options.blast_radius.unwrap_or(false);
        let git = options.git.unwrap_or(false);
        Ok(self.call(move |service| {
            answer(service.context(&task, budget, retrieval_mode, blast_radius, git))
        }))
    }

    fn call(
        &self,
        work: impl FnOnce(RepositoryService) -> Outcome + Send + 'static,
    ) -> AsyncTask<Call> {
        let path = self.path.clone();
        AsyncTask::new(Call {
            work: Some(Box::new(move || {
                match RepositoryService::discover(Some(&path)) {
                    Ok(service) => work(service.with_process_cache()),
                    Err(error) => service_error(&error),
                }
            })),
        })
    }
}

/// One request on libuv's worker pool. A panic rejects the promise instead
/// of unwinding into Node.
pub struct Call {
    work: Option<Box<dyn FnOnce() -> Outcome + Send>>,
}

impl Task for Call {
    type Output = Outcome;
    type JsValue = Outcome;

    fn compute(&mut self) -> Result<Outcome> {
        let work = self
            .work
            .take()
            .ok_or_else(|| Error::from_reason("oxide-native: call already ran"))?;
        catch_unwind(AssertUnwindSafe(work))
            .map_err(|_| Error::from_reason("oxide-native: the call panicked"))
    }

    fn resolve(&mut self, _env: Env, output: Outcome) -> Result<Outcome> {
        Ok(output)
    }
}

/// A non-negative integer, as the CLI's `usize` flags accept. JS numbers
/// are checked here because Node-API's own integer conversion wraps or
/// truncates instead of failing. Like a CLI usage error, a bad value has no
/// JSON answer: the call throws.
fn count(name: &str, value: Option<f64>, default: usize) -> Result<usize> {
    match value {
        None => Ok(default),
        Some(n) if n >= 0.0 && n.fract() == 0.0 && n <= usize::MAX as f64 => Ok(n as usize),
        Some(n) => Err(Error::from_reason(format!(
            "{name} must be a non-negative integer, got {n}"
        ))),
    }
}

/// An unset profile falls through to `$OXIDE_RETRIEVAL_MODE`, then balanced;
/// an unknown one fails, as `--profile` does.
fn profile(explicit: Option<&str>) -> std::result::Result<RetrievalMode, Outcome> {
    match explicit {
        Some(s) => RetrievalMode::parse(s).ok_or_else(|| {
            invalid_configuration(format!("unknown profile {s}; use fast|balanced|quality"))
        }),
        None => Ok(RetrievalMode::resolve(None)),
    }
}

fn answer<T: serde::Serialize>(result: std::result::Result<T, ServiceError>) -> Outcome {
    match result {
        Ok(value) => match serde_json::to_string(&value) {
            Ok(json) => Outcome { ok: true, json },
            Err(error) => envelope("command_failed", ErrorAction::Stop, &error.to_string()),
        },
        Err(error) => service_error(&error),
    }
}

fn service_error(error: &ServiceError) -> Outcome {
    envelope(error.code(), error.action(), error.message())
}

fn invalid_configuration(message: String) -> Outcome {
    envelope("invalid_configuration", ErrorAction::Stop, &message)
}

/// `src/cli/error.rs`'s `render_json_error`.
fn envelope(code: &str, action: ErrorAction, message: &str) -> Outcome {
    let json = serde_json::json!({
        "error": { "code": code, "action": action.as_str(), "message": message }
    });
    Outcome {
        ok: false,
        json: json.to_string(),
    }
}

fn done(outcome: Outcome) -> AsyncTask<Call> {
    AsyncTask::new(Call {
        work: Some(Box::new(move || outcome)),
    })
}

fn failed(message: String) -> AsyncTask<Call> {
    done(invalid_configuration(message))
}
