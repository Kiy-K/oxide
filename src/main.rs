fn main() {
    use clap::Parser;
    let args = oxide::cli::Args::parse_from(std::env::args_os());
    if let Err(e) = oxide::cli::run(args) {
        eprintln!("error: {e}");
        std::process::exit(1);
    }
}
