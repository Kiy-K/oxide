fn main() {
    use clap::Parser;

    // Guard held for the whole process: dropping it flushes queued events on exit.
    // Captures panics (and, with send_default_pii, the reporting machine's IP) automatically.
    // ClientOptions is #[non_exhaustive], so it must be built via Default + field
    // assignment rather than a `..Default::default()` struct-update literal.
    let mut sentry_options = sentry::ClientOptions::default();
    sentry_options.release = sentry::release_name!();
    sentry_options.send_default_pii = true;
    let _guard = sentry::init((
        "https://220500416743d04ad4597d88eec7cb8e@o4511784788557824.ingest.us.sentry.io/4512021829779456",
        sentry_options,
    ));

    let args = oxide::cli::Args::parse_from(std::env::args_os());
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
            eprintln!("error: {}", e.message);
        }
        std::process::exit(1);
    }
}
