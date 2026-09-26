use super::super::{
    render::{print_json, render_literal_search, render_search},
    CliError,
};
use crate::service::{ErrorAction, RepositoryService, SearchRequest};
use crate::term::Paint;

pub(super) fn cmd_search(
    path: Option<&str>,
    query: &str,
    request: SearchRequest,
    json: bool,
    p: Paint,
) -> Result<(), CliError> {
    let service = RepositoryService::discover(path).map_err(|e| CliError::service(e, json))?;
    let hits = service
        .search(query, request)
        .map_err(|e| CliError::service(e, json))?;
    if json {
        print_json(&hits)?;
    } else {
        render_search(&hits, &p);
    }
    Ok(())
}

pub(super) fn cmd_search_literal(
    path: Option<&str>,
    query: &str,
    limit: usize,
    json: bool,
) -> Result<(), CliError> {
    if query.trim().is_empty() {
        return Err(CliError::new(
            "invalid_configuration",
            ErrorAction::Stop,
            "literal search pattern must not be empty",
            json,
        ));
    }
    let service = RepositoryService::discover(path).map_err(|e| CliError::service(e, json))?;
    let result = service
        .search_literal(query, limit)
        .map_err(|e| CliError::service(e, json))?;
    if json {
        print_json(&result)?;
    } else {
        render_literal_search(&result);
    }
    Ok(())
}
