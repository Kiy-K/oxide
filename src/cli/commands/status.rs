use super::super::{
    render::{print_json, render_status},
    CliError,
};
use crate::service::RepositoryService;
use crate::term::Paint;

pub(super) fn cmd_status(
    path: Option<&str>,
    verbose: bool,
    json: bool,
    p: Paint,
) -> Result<(), CliError> {
    let service = RepositoryService::discover(path).map_err(|e| CliError::service(e, json))?;
    let status = service.status().map_err(|e| CliError::service(e, json))?;
    if json {
        print_json(&status)?;
    } else {
        render_status(&status, verbose, &p);
    }
    Ok(())
}
