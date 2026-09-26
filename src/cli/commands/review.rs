use super::super::{
    render::{print_json, render_review},
    CliError,
};
use crate::service::RepositoryService;
use crate::term::Paint;

pub(super) fn cmd_review(
    path: Option<&str>,
    diff: &str,
    json: bool,
    p: Paint,
) -> Result<(), CliError> {
    let service = RepositoryService::discover(path).map_err(|e| CliError::service(e, json))?;
    let ctx = service
        .review(diff)
        .map_err(|e| CliError::service(e, json))?;
    if json {
        print_json(&ctx)?;
    } else {
        render_review(&ctx, service.root(), &p);
    }
    Ok(())
}
