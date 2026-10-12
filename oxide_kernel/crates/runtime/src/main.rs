//! `oxide-runtime`: a long-lived service over stdin/stdout, one JSON request
//! per line in and one JSON response per line out, until stdin closes
//! (docs/adr/0003-kernel-runtime-control-plane-boundary.md). Diagnostics go
//! to stderr only; stdout carries nothing but responses.

use std::process::ExitCode;

use oxide_runtime::Runtime;
use oxide_runtime::repository::DataLocation;

fn main() -> ExitCode {
    // Resolved once; without it `status` still works and opening fails.
    let runtime = Runtime::new(DataLocation::linux().ok());
    match oxide_runtime::serve(runtime, std::io::stdin().lock(), std::io::stdout().lock()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(err) => {
            eprintln!("oxide-runtime: {err}");
            ExitCode::FAILURE
        }
    }
}
