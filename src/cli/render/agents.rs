use crate::agents::{self, Action, Agent, Paths, Plan};
use crate::term::Paint;

pub(in crate::cli) fn print_block(label: &str, body: &str, p: &Paint) {
    for (i, line) in body.lines().enumerate() {
        if i == 0 {
            println!("            {}: {}", p.dim(label), p.dim(line));
        } else {
            println!(
                "            {:width$}  {}",
                "",
                p.dim(line),
                width = label.len()
            );
        }
    }
}

pub(in crate::cli) fn join_and(items: &[&str]) -> String {
    match items {
        [] => String::new(),
        [one] => one.to_string(),
        [rest @ .., last] => format!("{} and {last}", rest.join(", ")),
    }
}

pub(in crate::cli) fn print_detection(detected: &[Agent], paths: &Paths, p: &Paint) {
    println!("{}\n", p.bold("Detected coding agents"));
    for (i, agent) in agents::ALL.iter().enumerate() {
        let state = if !detected.contains(agent) {
            p.dim("not detected")
        } else if agent.config_path(paths).exists() {
            format!("{} detected", p.check())
        } else {
            format!("{} detected {}", p.check(), p.dim("(no config file yet)"))
        };
        println!(
            "  {} {:<18}{state}",
            p.dim(&format!("[{}]", i + 1)),
            agent.display()
        );
    }
}

pub(in crate::cli) fn print_plans(plans: &[Plan], paths: &Paths, install: bool, p: &Paint) {
    println!(
        "\n{}\n",
        p.bold(if install {
            "OXIDE will configure:"
        } else {
            "OXIDE will remove its MCP server from:"
        })
    );
    for plan in plans {
        println!("  {}", p.bold(plan.agent.display()));
        println!(
            "    {} {}",
            p.dim("config:"),
            paths.shorten(&plan.config_path)
        );
        if !plan.detected {
            println!(
                "    {}",
                p.warn("note:   this agent was not detected on this machine")
            );
        }
        match &plan.action {
            Action::Add => {
                println!(
                    "    {} MCP server \"{}\"",
                    p.dim("add:   "),
                    agents::SERVER_NAME
                );
                for line in plan.snippet.lines() {
                    println!("            {}", p.dim(line));
                }
            }
            Action::Update { was, now } => {
                println!(
                    "    {} MCP server \"{}\" (anything else in this entry is kept)",
                    p.dim("update:"),
                    agents::SERVER_NAME
                );
                // Either side can be multi-line, so neither is squeezed
                // onto the label's line.
                print_block("was", was, p);
                print_block("now", now, p);
            }
            Action::AlreadyConfigured => {
                println!("    {} already configured — no change", p.check());
            }
            Action::Remove => {
                println!(
                    "    {} MCP server \"{}\"",
                    p.dim("remove:"),
                    agents::SERVER_NAME
                );
            }
            Action::NothingToRemove => {
                println!("    {} no oxide entry present — no change", p.dot());
            }
            Action::Blocked { reason } => {
                println!("    {} {}", p.bang(), p.warn(&format!("skipped: {reason}")));
            }
        }
        println!();
    }
}

pub(in crate::cli) fn render_agents_noop(p: &Paint) {
    println!("\n{} Nothing to do.", p.check());
}
pub(in crate::cli) fn render_agents_dry_run(p: &Paint) {
    println!("\n{} Dry run: nothing was written.", p.dot());
}
pub(in crate::cli) fn render_agents_cancelled() {
    println!("Cancelled. Nothing was written.");
}
pub(in crate::cli) fn render_agents_changed(
    changed: &[&Plan],
    paths: &Paths,
    install: bool,
    p: &Paint,
) {
    if !changed.is_empty() {
        println!(
            "\n{} {}",
            p.check(),
            p.bold(if install {
                "Configured"
            } else {
                "Removed from"
            })
        );
        for plan in changed {
            println!(
                "  {:<18}{}",
                plan.agent.display(),
                p.dim(&paths.shorten(&plan.config_path))
            );
        }
        let restart: Vec<&str> = changed
            .iter()
            .filter(|p| p.agent.needs_restart())
            .map(|p| p.agent.display())
            .collect();
        if !restart.is_empty() {
            println!("\nRestart {} to pick up the change.", join_and(&restart));
        }
        if install {
            println!("\nThen, in this repository:\n  {}", p.bold("oxide index"));
        }
    }
}
