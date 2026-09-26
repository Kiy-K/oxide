use super::super::CliError;

/// Build a tokio runtime only for the `mcp` subcommand, so every other CLI
/// command stays fully synchronous and pays no async runtime startup cost.
pub(super) fn run_mcp() -> Result<(), CliError> {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|e| CliError::generic(e, false))?;
    runtime
        .block_on(crate::mcp::serve())
        .map_err(|e| CliError::generic(e, false))
}
