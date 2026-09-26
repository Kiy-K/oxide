use super::super::{
    prompt::{confirm, normalize_provider_arg, prompt_line, prompt_provider},
    render::{
        print_setup_status, render_setup_cancelled, render_setup_saved, render_setup_warning,
    },
    CliError,
};
use crate::service::ErrorAction;
use crate::term::Paint;

/// `oxide setup`: configure a remote embedding provider, or show what's
/// configured. No `--json` mode — like `install`/`uninstall`, this is an
/// interactive/scripted command, not a machine surface.
pub(super) fn cmd_setup(
    provider_arg: Option<&str>,
    api_key_env: Option<&str>,
    base_url_arg: Option<&str>,
    model_arg: Option<&str>,
    yes: bool,
    show: bool,
    p: Paint,
) -> Result<(), CliError> {
    let json = false;
    let config_dir =
        crate::user_config::oxide_config_dir().map_err(|e| CliError::generic(e, json))?;

    if show {
        let cfg = crate::user_config::UserConfig::load(&config_dir)
            .map_err(|e| CliError::generic(e, json))?;
        print_setup_status(&cfg, &config_dir, &p);
        return Ok(());
    }

    let provider = match provider_arg {
        Some(s) => normalize_provider_arg(s, json)?,
        None => prompt_provider(json)?,
    };

    let base_url = if provider == "openai-compatible" {
        Some(match base_url_arg {
            Some(u) => u.to_string(),
            None => prompt_line("Endpoint URL (OpenAI-compatible /v1/embeddings)", json)?,
        })
    } else if base_url_arg.is_some() {
        // Voyage/Jina have one fixed public endpoint each — a configured
        // override would otherwise be saved and then silently ignored by
        // `remote_embed::build`, sending the key/excerpts to the real
        // endpoint anyway with no indication anything was dropped.
        return Err(CliError::new(
            "invalid_configuration",
            ErrorAction::Stop,
            format!(
                "--base-url is only supported for --provider openai-compatible (not {provider})"
            ),
            json,
        ));
    } else {
        None
    };

    let default_model = crate::remote_embed::default_model_hint(provider);
    let model = match model_arg {
        Some(m) => m.to_string(),
        None => {
            let answer = prompt_line(&format!("Model [{default_model}]"), json)?;
            if answer.is_empty() {
                default_model.to_string()
            } else {
                answer
            }
        }
    };

    let api_key = match api_key_env {
        Some(var) => std::env::var(var).map_err(|_| {
            CliError::new(
                "invalid_configuration",
                ErrorAction::Stop,
                format!("${var} is not set"),
                json,
            )
        })?,
        None => crate::term::prompt_password(&format!(
            "{} API key",
            crate::remote_embed::provider_display(provider)
        ))
        .map_err(|e| CliError::generic(e, json))?,
    };
    if api_key.trim().is_empty() {
        return Err(CliError::new(
            "invalid_configuration",
            ErrorAction::Stop,
            "no API key given; nothing was saved",
            json,
        ));
    }

    render_setup_warning(provider, &p);
    if !yes && !confirm("Continue?")? {
        render_setup_cancelled();
        return Ok(());
    }

    let candidate =
        crate::remote_embed::build(provider, &model, base_url.as_deref(), None, None, &api_key)
            .map_err(|e| {
                CliError::new(
                    "invalid_configuration",
                    ErrorAction::Stop,
                    format!(
                        "could not reach {} with the given key/endpoint: {e}\n\nnothing was saved",
                        crate::remote_embed::provider_display(provider)
                    ),
                    json,
                )
            })?;
    let dim = candidate.dim();

    crate::credentials::set_key(&config_dir, provider, &api_key)
        .map_err(|e| CliError::generic(e, json))?;
    let cfg = crate::user_config::UserConfig {
        provider: Some(provider.to_string()),
        model: Some(model),
        base_url,
        vector_dim: Some(dim),
        remote_consent_ack: true,
        remote_consent_at: Some(crate::user_config::now_iso8601()),
    };
    cfg.save(&config_dir)
        .map_err(|e| CliError::generic(e, json))?;

    render_setup_saved(&config_dir, &p);
    Ok(())
}
