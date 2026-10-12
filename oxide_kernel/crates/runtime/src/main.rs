//! `oxide-runtime`: a long-lived service over stdin/stdout, one JSON request
//! per line in and one JSON response per line out, until stdin closes
//! (docs/adr/0003-kernel-runtime-control-plane-boundary.md). Diagnostics go
//! to stderr only; stdout carries nothing but responses.

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
