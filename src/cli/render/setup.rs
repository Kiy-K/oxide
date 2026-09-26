use crate::term::Paint;

pub(in crate::cli) fn print_setup_status(
    cfg: &crate::user_config::UserConfig,
    config_dir: &std::path::Path,
    p: &Paint,
) {
    match &cfg.provider {
        Some(provider) if cfg.remote_consent_ack => {
            println!("{} Remote embedding configured", p.bold("Provider:"));
            println!("  provider: {provider}");
            println!(
                "  model:    {}",
                cfg.model.as_deref().unwrap_or("(default)")
            );
            if let Some(url) = &cfg.base_url {
                println!("  base_url: {url}");
            }
            if let Some(dim) = cfg.vector_dim {
                println!("  vector dim: {dim}");
            }
            let has_key = crate::credentials::has_key(config_dir, provider);
            println!(
                "  api_key:  {}",
                if has_key {
                    crate::credentials::REDACTED
                } else {
                    "not set"
                }
            );
            if let Some(at) = &cfg.remote_consent_at {
                println!("  consent acknowledged: {at}");
            }
        }
        _ => {
            println!(
                "{} local (Arctic XS-Q). Run `oxide setup` to configure a remote provider.",
                p.bold("Provider:")
            );
        }
    }
}

pub(in crate::cli) fn render_setup_warning(provider: &str, p: &Paint) {
    println!("\n{}", p.warn("⚠ Remote embeddings enabled"));
    println!(
        "\nSource-code excerpts and queries will be sent to {}.",
        crate::remote_embed::provider_display(provider)
    );
    println!("Local embedding keeps repository data on this machine.\n");
}
pub(in crate::cli) fn render_setup_cancelled() {
    println!("Cancelled. Nothing was saved.");
}
pub(in crate::cli) fn render_setup_saved(config_dir: &std::path::Path, p: &Paint) {
    println!(
        "{} Saved to {}",
        p.check(),
        config_dir.join("config.toml").display()
    );
}
