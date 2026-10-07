//! `oxide-runtime`: one request per process over stdin/stdout.
//!
//! Provisional Phase 0 transport (docs/adr/0003-kernel-runtime-control-plane-boundary.md),
//! not the intended long-lived runtime, a CLI or a committed wire framing.

use std::process::ExitCode;

fn main() -> ExitCode {
    match oxide_runtime::serve(std::io::stdin().lock(), std::io::stdout().lock()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(err) => {
            eprintln!("oxide-runtime: {err}");
            ExitCode::FAILURE
        }
    }
}
