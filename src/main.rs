fn main() {
    use clap::Parser;
    let args = oxide::cli::Args::parse_from(std::env::args_os());
    if let Err(e) = oxide::cli::run(args) {
        if e.json {
            println!(
                "{}",
                serde_json::json!({
                    "error": {
                        "code": e.code,
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
