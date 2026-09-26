use crate::service::{ErrorAction, ServiceError};
use crate::term::Paint;

#[derive(Debug)]
pub struct CliError {
    pub code: String,
    pub action: ErrorAction,
    pub message: String,
    pub json: bool,
}

impl CliError {
    pub(in crate::cli) fn new(
        code: impl Into<String>,
        action: ErrorAction,
        message: impl Into<String>,
        json: bool,
    ) -> Self {
        Self {
            code: code.into(),
            action,
            message: message.into(),
            json,
        }
    }

    pub(in crate::cli) fn service(error: ServiceError, json: bool) -> Self {
        Self::new(error.code(), error.action(), error.message(), json)
    }

    /// Malformed CLI invocation or a wire-serialization failure: neither maps
    /// to an `ErrorCode`, so there is nothing retryable to hint at beyond
    /// fixing the input.
    pub(in crate::cli) fn generic(error: impl std::fmt::Display, json: bool) -> Self {
        Self::new("command_failed", ErrorAction::Stop, error.to_string(), json)
    }
}

impl std::fmt::Display for CliError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for CliError {}

/// The machine-readable CLI error envelope printed by the binary.
pub fn render_json_error(error: &CliError) -> serde_json::Value {
    serde_json::json!({
        "error": {
            "code": error.code,
            "action": error.action.as_str(),
            "message": error.message,
        }
    })
}

/// What a human should see for a failure, and what to run next.
///
/// The machine-readable side is untouched: `code`, `action`, and the
/// service `message` still go out verbatim under `--json`. This only
/// rewrites the terminal rendering, where a sentence plus a command beats a
/// diagnostic string with a path and a backticked hint embedded in it.
pub fn render_human_error(error: &CliError, paint: &Paint) -> String {
    let with_cause: String;
    let (headline, next) = match error.code.as_str() {
        "index_missing" => (
            "No OXIDE index was found for this repository.",
            Some("oxide index"),
        ),
        "index_empty" => (
            "The OXIDE index has no searchable symbols yet.",
            Some("oxide index"),
        ),
        "index_stale" => (
            "The OXIDE index is out of date for this repository.",
            Some("oxide index"),
        ),
        "provider_mismatch" => (
            "The index was built with a different embedding provider, so its \
             vectors cannot be compared with this one's.",
            Some("oxide index"),
        ),
        "index_incompatible" | "index_unreadable" => (
            "The index cannot be used by this build of OXIDE.",
            Some("rm -rf .oxide && oxide index"),
        ),
        // Discovery walks up for `.git`/`.oxide` and finds neither. An
        // explicit path skips discovery entirely, so the way out is to name
        // the directory once — after which `.oxide` makes it discoverable.
        "repository_not_found" if error.message.starts_with("not inside a repository") => (
            "This directory is not inside a git repository or an existing OXIDE index.",
            Some("oxide index ."),
        ),
        "no_source_files" => (
            "No supported source files were found here. OXIDE indexes Python, \
             TypeScript/TSX, Rust, and Go.",
            None,
        ),
        // The cause is the actionable part here — an invalid setting, a
        // model that failed to download, an unreachable endpoint — so it is
        // shown, not replaced.
        "embedder_unavailable" => {
            with_cause = format!(
                "The embedding provider is unavailable, so semantic search cannot run.\n\n\
                 Cause: {}",
                error.message
            );
            (
                with_cause.as_str(),
                Some("oxide search <query> --mode lexical"),
            )
        }
        // Everything else already says the useful thing (which path, which
        // flag) in its own message; inventing a headline would lose that.
        _ => (error.message.as_str(), error.action.next_command()),
    };
    match next {
        Some(command) => format!("{headline}\n\nRun:\n  {}", paint.bold(command)),
        None => headline.to_string(),
    }
}

impl ErrorAction {
    /// The command that resolves this class of failure, when there is one.
    fn next_command(&self) -> Option<&'static str> {
        match self {
            Self::Index => Some("oxide index"),
            Self::Repair => Some("rm -rf .oxide && oxide index"),
            Self::Retry | Self::FallBack | Self::Stop => None,
        }
    }
}

pub(in crate::cli) fn agent_failure(failures: Vec<String>, json: bool) -> CliError {
    CliError::new(
        "agent_config_failed",
        ErrorAction::Stop,
        format!(
            "could not update {} agent configuration(s):\n  {}",
            failures.len(),
            failures.join("\n  ")
        ),
        json,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn actionable_errors_say_what_to_run() {
        let err = CliError::new(
            "index_missing",
            crate::service::ErrorAction::Index,
            "index missing at /x/.oxide/index.db; run `oxide index /x`",
            false,
        );
        let rendered = render_human_error(&err, &crate::term::Paint::plain());
        assert!(rendered.starts_with("No OXIDE index was found"));
        assert!(rendered.ends_with("Run:\n  oxide index"));
    }

    #[test]
    fn embedder_unavailable_shows_its_cause() {
        let err = CliError::new(
            "embedder_unavailable",
            crate::service::ErrorAction::FallBack,
            "OXIDE_EMBED_SESSIONS=\"0\" is not valid: use `auto`",
            false,
        );
        let rendered = render_human_error(&err, &crate::term::Paint::plain());
        assert!(rendered.starts_with("The embedding provider is unavailable"));
        assert!(rendered.contains("Cause: OXIDE_EMBED_SESSIONS=\"0\" is not valid"));
        assert!(rendered.ends_with("Run:\n  oxide search <query> --mode lexical"));
    }

    #[test]
    fn unrecognized_errors_keep_their_own_message() {
        let err = CliError::new(
            "repository_not_found",
            crate::service::ErrorAction::Stop,
            "no such path: /nope",
            false,
        );
        assert_eq!(
            render_human_error(&err, &crate::term::Paint::plain()),
            "no such path: /nope"
        );
    }
}
