//! CLI: index / query / search / status / watch / install / mcp / review / eval.

mod args;
mod commands;
mod error;
mod prompt;
mod render;
pub use args::{Args, Cmd};
pub use error::{render_human_error, render_json_error, CliError};

pub fn run(args: Args) -> Result<(), CliError> {
    commands::dispatch(args)
}
