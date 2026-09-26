use super::{render::print_detection, CliError};
use crate::agents::{self, Agent, Paths};
use crate::service::ErrorAction;
use crate::term::Paint;
use std::io::Write;

/// One line from stdin, or `None` at end of input.
///
/// Deliberately not gated on `stdin().is_terminal()`: a piped answer is a
/// real answer, and refusing to read one would make the confirmation step
/// untestable and unscriptable. What is refused is *silence* — reaching end
/// of input without an answer is an error, never an implied yes and never a
/// no-op that exits 0 as though the work had been done.
pub(in crate::cli) fn read_answer(json: bool) -> Result<Option<String>, CliError> {
    let mut line = String::new();
    let read = std::io::stdin()
        .read_line(&mut line)
        .map_err(|e| CliError::generic(e, json))?;
    if read == 0 {
        Ok(None)
    } else {
        Ok(Some(line))
    }
}
pub(in crate::cli) fn confirm(question: &str) -> Result<bool, CliError> {
    print!("{question} [y/N] ");
    let _ = std::io::stdout().flush();
    let Some(line) = read_answer(false)? else {
        return Err(CliError::new(
            "invalid_configuration",
            ErrorAction::Stop,
            "no answer on stdin; re-run with --yes to accept the plan above, \
             or --dry-run to only preview it",
            false,
        ));
    };
    Ok(matches!(
        line.trim().to_ascii_lowercase().as_str(),
        "y" | "yes"
    ))
}

/// Resolve `--agent` flags, or ask. Detection alone never selects an agent:
/// with no flags and a terminal, OXIDE lists what it found and waits.
pub(in crate::cli) fn select_agents(
    requested: &[String],
    paths: &Paths,
    install: bool,
    json: bool,
    p: &Paint,
) -> Result<Vec<Agent>, CliError> {
    let detected: Vec<Agent> = agents::ALL
        .iter()
        .copied()
        .filter(|a| a.detected(paths))
        .collect();

    if !requested.is_empty() {
        if requested
            .iter()
            .any(|r| r.trim().eq_ignore_ascii_case("all"))
        {
            if detected.is_empty() {
                println!("No supported coding agents were detected on this machine.");
                return Ok(Vec::new());
            }
            return Ok(detected);
        }
        let mut out = Vec::new();
        for name in requested {
            let agent = Agent::parse(name).ok_or_else(|| {
                CliError::new(
                    "invalid_configuration",
                    ErrorAction::Stop,
                    format!(
                        "unknown agent {name}; use one of: {}, all",
                        agents::ALL
                            .iter()
                            .map(|a| a.id())
                            .collect::<Vec<_>>()
                            .join(", ")
                    ),
                    json,
                )
            })?;
            if !out.contains(&agent) {
                out.push(agent);
            }
        }
        return Ok(out);
    }

    print_detection(&detected, paths, p);
    if detected.is_empty() {
        println!(
            "\nSupported agents: {}.\nInstall one, or name it explicitly with --agent.",
            agents::ALL
                .iter()
                .map(|a| a.display())
                .collect::<Vec<_>>()
                .join(", ")
        );
        return Ok(Vec::new());
    }
    println!(
        "\nWhich agents should OXIDE {}?",
        if install {
            "integrate with"
        } else {
            "be removed from"
        }
    );
    print!("> ");
    let _ = std::io::stdout().flush();
    let Some(line) = read_answer(json)? else {
        return Err(CliError::new(
            "invalid_configuration",
            ErrorAction::Stop,
            format!(
                "no answer to read from stdin\n\nRun:\n  oxide {} --agent all --yes",
                if install { "install" } else { "uninstall" }
            ),
            json,
        ));
    };
    if line.trim().is_empty() {
        println!("Cancelled. Nothing was written.");
        return Ok(Vec::new());
    }
    parse_selection(&line, &detected)
        .map_err(|e| CliError::new("invalid_configuration", ErrorAction::Stop, e, json))
}

/// Accepts `1,2`, `1 3`, `all`, or agent names — the shapes people actually
/// type at a numbered list.
fn parse_selection(input: &str, offered: &[Agent]) -> Result<Vec<Agent>, String> {
    let mut out = Vec::new();
    for token in input
        .split([',', ' ', '\t', '\n'])
        .map(str::trim)
        .filter(|t| !t.is_empty())
    {
        if token.eq_ignore_ascii_case("all") {
            return Ok(offered.to_vec());
        }
        let agent = match token.parse::<usize>() {
            Ok(n) if n >= 1 && n <= agents::ALL.len() => agents::ALL[n - 1],
            Ok(n) => return Err(format!("{n} is not one of the listed agents")),
            Err(_) => Agent::parse(token).ok_or_else(|| format!("unknown agent {token}"))?,
        };
        if !out.contains(&agent) {
            out.push(agent);
        }
    }
    if out.is_empty() {
        return Err("nothing selected".to_string());
    }
    Ok(out)
}

pub(in crate::cli) fn normalize_provider_arg(
    s: &str,
    json: bool,
) -> Result<&'static str, CliError> {
    crate::remote_embed::normalize_provider(s).ok_or_else(|| {
        CliError::new(
            "invalid_configuration",
            ErrorAction::Stop,
            format!("unknown provider {s:?}; use voyage|jina|openai-compatible"),
            json,
        )
    })
}

pub(in crate::cli) fn prompt_line(label: &str, json: bool) -> Result<String, CliError> {
    print!("{label}: ");
    let _ = std::io::stdout().flush();
    let line = read_answer(json)?.ok_or_else(|| {
        CliError::new(
            "invalid_configuration",
            ErrorAction::Stop,
            "no answer on stdin",
            json,
        )
    })?;
    Ok(line.trim().to_string())
}

pub(in crate::cli) fn prompt_provider(json: bool) -> Result<&'static str, CliError> {
    println!("Choose an embedding provider:");
    println!("  [1] Voyage AI       (voyage-code-3, voyage-3, ...)");
    println!("  [2] Jina AI         (jina-embeddings-v3, ...)");
    println!("  [3] OpenAI-compatible endpoint (OpenAI, self-hosted, ...)");
    let line = prompt_line("> ", json)?;
    match line.as_str() {
        "1" => Ok("voyage"),
        "2" => Ok("jina"),
        "3" => Ok("openai-compatible"),
        other => normalize_provider_arg(other, json),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn selection_accepts_numbers_names_and_all() {
        let offered = vec![
            crate::agents::Agent::ClaudeCode,
            crate::agents::Agent::Codex,
        ];
        assert_eq!(
            parse_selection("1,2", &offered).unwrap(),
            vec![
                crate::agents::Agent::ClaudeCode,
                crate::agents::Agent::Codex
            ]
        );
        assert_eq!(
            parse_selection(" codex ", &offered).unwrap(),
            vec![crate::agents::Agent::Codex]
        );
        assert_eq!(parse_selection("all", &offered).unwrap(), offered);
        assert_eq!(
            parse_selection("2 2", &offered).unwrap(),
            vec![crate::agents::Agent::Codex],
            "a repeated pick must not queue two writes to one config"
        );
        assert!(parse_selection("9", &offered).is_err());
        assert!(parse_selection("nope", &offered).is_err());
    }
}
