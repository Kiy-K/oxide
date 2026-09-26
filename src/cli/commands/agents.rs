use super::super::{
    error::agent_failure,
    prompt::{confirm, select_agents},
    render::{
        print_plans, render_agents_cancelled, render_agents_changed, render_agents_dry_run,
        render_agents_noop,
    },
    CliError,
};
use crate::agents::{self, Action, Paths, Plan};
use crate::term::Paint;

// ------------------------------------------------------- agent integrations

pub(super) fn cmd_agents(
    requested: &[String],
    dry_run: bool,
    yes: bool,
    install: bool,
    p: Paint,
) -> Result<(), CliError> {
    let json = false; // install/uninstall are interactive; no machine surface.
    let paths = Paths::from_env().map_err(|e| CliError::generic(e, json))?;
    let binary = std::env::current_exe().map_err(|e| {
        CliError::generic(format!("cannot resolve the oxide binary path: {e}"), json)
    })?;

    let selected = select_agents(requested, &paths, install, json, &p)?;
    if selected.is_empty() {
        return Ok(());
    }

    let plans: Vec<Plan> = selected
        .iter()
        .map(|agent| {
            if install {
                agents::plan_install(*agent, &paths, &binary)
            } else {
                agents::plan_uninstall(*agent, &paths)
            }
        })
        .collect();

    print_plans(&plans, &paths, install, &p);

    // A config OXIDE refuses to edit is a failure of the user's request,
    // not a quiet no-op: it must not exit 0 as if the agent were wired up.
    let blocked: Vec<String> = plans
        .iter()
        .filter_map(|p| match &p.action {
            Action::Blocked { reason } => Some(format!("{}: {reason}", p.agent.display())),
            _ => None,
        })
        .collect();

    if !plans.iter().any(|p| p.action.writes()) {
        if blocked.is_empty() {
            render_agents_noop(&p);
            return Ok(());
        }
        return Err(agent_failure(blocked, json));
    }
    if dry_run {
        render_agents_dry_run(&p);
        return Ok(());
    }
    if !yes && !confirm("Proceed?")? {
        render_agents_cancelled();
        return Ok(());
    }

    let mut failures = blocked;
    let mut changed = Vec::new();
    for plan in plans.iter().filter(|p| p.action.writes()) {
        match agents::apply(plan) {
            Ok(()) => changed.push(plan),
            Err(e) => failures.push(format!("{}: {e}", plan.agent.display())),
        }
    }

    render_agents_changed(&changed, &paths, install, &p);

    if failures.is_empty() {
        Ok(())
    } else {
        Err(agent_failure(failures, json))
    }
}
