//! `oxide lsp install <name>` — a small registry of known LSP-server
//! installers. Only `ty` (Python) has a real entry today, matching that only
//! Python has a working `LspClient` profile in this codebase. Other names
//! return a clear "not supported yet" error rather than installing a binary
//! OXIDE cannot use, or silently no-op-ing.

/// Pinned so `oxide lsp install ty` and CI's `lsp-integration` job install
/// the exact version the real-server tests were written against.
pub const TY_PINNED_VERSION: &str = "0.0.80";

#[derive(Debug)]
pub struct InstallPlan {
    pub program: String,
    pub args: Vec<String>,
}

#[derive(Debug)]
pub struct LspInstallError(String);

impl std::fmt::Display for LspInstallError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}
impl std::error::Error for LspInstallError {}

pub fn plan_install(name: &str) -> Result<InstallPlan, LspInstallError> {
    match name {
        "ty" => Ok(InstallPlan {
            program: "uv".to_string(),
            args: vec![
                "tool".into(),
                "install".into(),
                format!("ty=={TY_PINNED_VERSION}"),
            ],
        }),
        other => Err(LspInstallError(format!(
            "LSP server '{other}' is not supported yet — only 'ty' (Python) has a working OXIDE integration today"
        ))),
    }
}

/// Runs the resolved install plan; separate from `plan_install` so planning
/// logic is testable without `uv` installed.
pub fn install(name: &str) -> Result<(), LspInstallError> {
    let plan = plan_install(name)?;
    let status = std::process::Command::new(&plan.program)
        .args(&plan.args)
        .status()
        .map_err(|e| {
            LspInstallError(format!(
                "failed to run `{} {}`: {e}",
                plan.program,
                plan.args.join(" ")
            ))
        })?;
    if !status.success() {
        return Err(LspInstallError(format!(
            "`{} {}` exited with {status}",
            plan.program,
            plan.args.join(" ")
        )));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unsupported_name_returns_a_clear_error_not_a_silent_noop() {
        let err = plan_install("pyright").unwrap_err();
        assert!(err.to_string().contains("not supported yet"), "{err}");
        assert!(err.to_string().contains("pyright"), "{err}");
    }

    #[test]
    fn ty_resolves_to_the_pinned_uv_tool_install_command() {
        let plan = plan_install("ty").unwrap();
        assert_eq!(plan.program, "uv");
        assert_eq!(
            plan.args,
            vec![
                "tool".to_string(),
                "install".to_string(),
                format!("ty=={TY_PINNED_VERSION}")
            ]
        );
    }
}
