fn main() {
    use clap::Parser;

    // Rust ignores SIGPIPE, so `oxide status | head -1` would panic on the
    // first write after `head` exits. Restore the default: quietly die like
    // every other CLI in the pipeline.
    #[cfg(unix)]
    // SAFETY: setting a signal disposition before any thread exists.
    unsafe {
        libc::signal(libc::SIGPIPE, libc::SIG_DFL);
    }

    // `None` unless OXIDE_TELEMETRY opts in — see TELEMETRY.md. Held for the
    // whole process so a report queued by a panic is flushed on exit.
    let _telemetry = oxide::telemetry::init();

    let args = oxide::cli::Args::parse_from(std::env::args_os());
    let color = args.color;
    if let Err(e) = oxide::cli::run(args) {
        if e.json {
            println!(
                "{}",
                serde_json::json!({
                    "error": {
                        "code": e.code,
                        "action": e.action.as_str(),
                        "message": e.message,
                    }
                })
            );
        } else {
            let paint = oxide::term::Paint::for_stderr(color);
            eprintln!(
                "{} {}",
                paint.err("error:"),
                oxide::cli::render_human_error(&e, &paint)
            );
        }
        std::process::exit(1);
    }
}
