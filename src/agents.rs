//! Coding-agent MCP wiring for `oxide install` / `oxide uninstall`.
//!
//! Each supported agent is one small adapter answering three questions:
//! where its config file lives, whether an `oxide` MCP entry is already in
//! it, and how to write or remove exactly that entry while leaving every
//! other byte of the file alone. Nothing here knows about the terminal —
//! `cli.rs` owns detection display, selection, confirmation, and printing;
//! this module only computes plans and applies them.
//!
//! Config locations were taken from each agent's own documentation:
//! Claude Code `~/.claude.json` (`mcpServers`), Codex `~/.codex/config.toml`
//! (`[mcp_servers.NAME]`), OpenCode `$XDG_CONFIG_HOME/opencode/opencode.json`
//! (`mcp`), Antigravity CLI `~/.gemini/config/mcp_config.json` (`mcpServers`,
//! shared with the Antigravity IDE).

use serde_json::{Map, Value};
use std::io::Write;
use std::path::{Path, PathBuf};

/// The MCP server name OXIDE owns. Install writes exactly this key and
/// uninstall removes exactly this key — never anything else in the file.
pub const SERVER_NAME: &str = "oxide";

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Agent {
    ClaudeCode,
    Codex,
    OpenCode,
    Antigravity,
}

pub const ALL: [Agent; 4] = [
    Agent::ClaudeCode,
    Agent::Codex,
    Agent::OpenCode,
    Agent::Antigravity,
];

impl Agent {
    pub fn id(self) -> &'static str {
        match self {
            Self::ClaudeCode => "claude",
            Self::Codex => "codex",
            Self::OpenCode => "opencode",
            Self::Antigravity => "antigravity",
        }
    }

    pub fn display(self) -> &'static str {
        match self {
            Self::ClaudeCode => "Claude Code",
            Self::Codex => "Codex",
            Self::OpenCode => "OpenCode",
            Self::Antigravity => "Antigravity CLI",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        match s.trim().to_ascii_lowercase().as_str() {
            "claude" | "claude-code" | "claudecode" => Some(Self::ClaudeCode),
            "codex" => Some(Self::Codex),
            "opencode" => Some(Self::OpenCode),
            "antigravity" | "antigravity-cli" | "agy" => Some(Self::Antigravity),
            _ => None,
        }
    }

    /// Executable names that prove the agent is installed even when it has
    /// never written a config file.
    fn binaries(self) -> &'static [&'static str] {
        match self {
            Self::ClaudeCode => &["claude"],
            Self::Codex => &["codex"],
            Self::OpenCode => &["opencode"],
            Self::Antigravity => &["antigravity", "agy"],
        }
    }

    pub fn config_path(self, paths: &Paths) -> PathBuf {
        match self {
            Self::ClaudeCode => paths.home.join(".claude.json"),
            Self::Codex => paths.home.join(".codex").join("config.toml"),
            Self::OpenCode => {
                // OpenCode accepts either extension. Target whichever the
                // user already has so we never create a second config the
                // agent may or may not merge.
                let dir = paths.xdg_config.join("opencode");
                let jsonc = dir.join("opencode.jsonc");
                let json = dir.join("opencode.json");
                if !json.exists() && jsonc.exists() {
                    jsonc
                } else {
                    json
                }
            }
            Self::Antigravity => paths
                .home
                .join(".gemini")
                .join("config")
                .join("mcp_config.json"),
        }
    }

    /// Directories/files whose presence means the agent has run here.
    fn markers(self, paths: &Paths) -> Vec<PathBuf> {
        match self {
            Self::ClaudeCode => vec![paths.home.join(".claude.json"), paths.home.join(".claude")],
            Self::Codex => vec![paths.home.join(".codex")],
            Self::OpenCode => vec![paths.xdg_config.join("opencode")],
            Self::Antigravity => vec![
                paths.home.join(".gemini").join("config"),
                paths.home.join(".gemini").join("antigravity-cli"),
            ],
        }
    }

    pub fn detected(self, paths: &Paths) -> bool {
        self.markers(paths).iter().any(|p| p.exists()) || self.binaries().iter().any(|b| on_path(b))
    }

    /// Top-level key holding the MCP server map in this agent's JSON config.
    fn json_container(self) -> &'static str {
        match self {
            Self::OpenCode => "mcp",
            _ => "mcpServers",
        }
    }

    /// The fields OXIDE owns in this agent's entry: the ones that name the
    /// executable and the transport. Everything a user has added alongside
    /// them (`env`, a timeout, a custom flag) is theirs, and an update must
    /// leave it in place rather than replace the whole entry.
    fn managed_json(self, bin: &str) -> Vec<(&'static str, Value)> {
        match self {
            Self::ClaudeCode => vec![
                ("type", Value::from("stdio")),
                ("command", Value::from(bin)),
                ("args", serde_json::json!(["mcp"])),
            ],
            // `enabled` is managed, not a create-only default: an entry
            // left at `enabled: false` is one OpenCode will not launch, so
            // reporting it as already configured would be reporting a
            // working integration that does not work.
            Self::OpenCode => vec![
                ("type", Value::from("local")),
                ("command", serde_json::json!([bin, "mcp"])),
                ("enabled", Value::Bool(true)),
            ],
            // Antigravity's schema is the bare MCP shape: no `type` field.
            _ => vec![
                ("command", Value::from(bin)),
                ("args", serde_json::json!(["mcp"])),
            ],
        }
    }

    /// The complete entry for a fresh install, used for the plan preview.
    fn json_entry(self, bin: &str) -> Value {
        let mut entry = Map::new();
        for (key, value) in self.managed_json(bin) {
            entry.insert(key.to_string(), value);
        }
        Value::Object(entry)
    }

    /// True when this agent must be restarted to see the change. Every
    /// supported agent reads MCP config once at startup.
    pub fn needs_restart(self) -> bool {
        true
    }
}

/// Where an agent's per-user configuration lives. Resolved from the
/// environment once so tests can point a whole run at a temporary home.
pub struct Paths {
    pub home: PathBuf,
    pub xdg_config: PathBuf,
}

impl Paths {
    pub fn from_env() -> Result<Self, String> {
        let home = std::env::var_os("HOME")
            .or_else(|| std::env::var_os("USERPROFILE"))
            .map(PathBuf::from)
            .ok_or_else(|| "cannot locate your home directory ($HOME is unset)".to_string())?;
        let xdg_config = std::env::var_os("XDG_CONFIG_HOME")
            .map(PathBuf::from)
            .filter(|p| p.is_absolute())
            .unwrap_or_else(|| home.join(".config"));
        Ok(Self { home, xdg_config })
    }

    /// `~`-shortened display form, so plans stay readable.
    pub fn shorten(&self, path: &Path) -> String {
        match path.strip_prefix(&self.home) {
            Ok(rest) => format!("~/{}", rest.display()),
            Err(_) => path.display().to_string(),
        }
    }
}

fn on_path(binary: &str) -> bool {
    let Some(path) = std::env::var_os("PATH") else {
        return false;
    };
    std::env::split_paths(&path).any(|dir| dir.join(binary).is_file())
}

/// What `apply` will do to one agent's config.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Action {
    /// No OXIDE entry yet; add one.
    Add,
    /// An OXIDE entry exists but its managed fields are stale. Only those
    /// fields change; anything else in the entry is the user's and stays.
    Update { was: String, now: String },
    /// An identical OXIDE entry is already there; writing would change nothing.
    AlreadyConfigured,
    /// Remove the OXIDE entry.
    Remove,
    /// Uninstall with nothing to remove.
    NothingToRemove,
    /// The config cannot be edited safely; say why and touch nothing.
    Blocked { reason: String },
}

impl Action {
    /// Does applying this plan write to disk?
    pub fn writes(&self) -> bool {
        matches!(self, Self::Add | Self::Update { .. } | Self::Remove)
    }
}

#[derive(Debug, Clone)]
pub struct Plan {
    pub agent: Agent,
    pub detected: bool,
    pub config_path: PathBuf,
    pub action: Action,
    /// Exactly what will land in the file, for the confirmation prompt.
    pub snippet: String,
    binary: String,
    /// The config exactly as it was when this plan was computed (`None` if
    /// the file did not exist). `apply` refuses to write if the file has
    /// changed since — otherwise "here is the exact change, proceed?" would
    /// be a promise about a file that no longer exists in that form.
    reviewed: Option<Vec<u8>>,
}

pub fn plan_install(agent: Agent, paths: &Paths, binary: &Path) -> Plan {
    let bin = binary.display().to_string();
    let config_path = resolve_symlink(agent.config_path(paths));
    // The plan already names the server on its own line, so the snippet is
    // just the value that lands under that key — which makes the
    // replace case's `was:`/`now:` lines directly comparable.
    let snippet = match agent {
        Agent::Codex => codex_entry(&bin),
        _ => serde_json::to_string(&agent.json_entry(&bin)).unwrap_or_else(|_| "{}".to_string()),
    };
    let action = match current_entry(agent, &config_path) {
        Err(reason) => Action::Blocked { reason },
        Ok(None) => Action::Add,
        Ok(Some(current)) if !owned_by_oxide(agent, &current) => Action::Blocked {
            reason: not_ours(agent, &config_path, &current),
        },
        Ok(Some(current)) => {
            if entries_match(agent, &current, &bin) {
                Action::AlreadyConfigured
            } else {
                Action::Update {
                    was: managed_lines(agent, Some(&current), &bin),
                    now: managed_lines(agent, None, &bin),
                }
            }
        }
    };
    Plan {
        detected: agent.detected(paths),
        reviewed: snapshot(&config_path),
        agent,
        config_path,
        action,
        snippet,
        binary: bin,
    }
}

pub fn plan_uninstall(agent: Agent, paths: &Paths) -> Plan {
    let config_path = resolve_symlink(agent.config_path(paths));
    let action = match current_entry(agent, &config_path) {
        Err(reason) => Action::Blocked { reason },
        Ok(None) => Action::NothingToRemove,
        Ok(Some(current)) if !owned_by_oxide(agent, &current) => Action::Blocked {
            reason: not_ours(agent, &config_path, &current),
        },
        Ok(Some(_)) => Action::Remove,
    };
    Plan {
        detected: agent.detected(paths),
        reviewed: snapshot(&config_path),
        agent,
        config_path,
        action,
        snippet: String::new(),
        binary: String::new(),
    }
}

/// The config's exact bytes, or `None` if it is absent or unreadable.
fn snapshot(path: &Path) -> Option<Vec<u8>> {
    std::fs::read(path).ok()
}

/// Follow a symlinked config to the file it points at.
///
/// Writes here are a temp file plus a rename, and a rename replaces the
/// symlink itself rather than writing through it — so a config managed by a
/// dotfiles repo (`~/.codex/config.toml -> ~/dotfiles/codex/config.toml`)
/// would be quietly replaced by a regular file, leaving the dotfile source
/// untouched and disconnected. Resolving first means the edit lands in the
/// user's actual file and the symlink survives. A dangling symlink resolves
/// to nothing and is left alone, so it fails as a normal write error rather
/// than being papered over.
fn resolve_symlink(path: PathBuf) -> PathBuf {
    match std::fs::symlink_metadata(&path) {
        Ok(meta) if meta.file_type().is_symlink() => std::fs::canonicalize(&path).unwrap_or(path),
        _ => path,
    }
}

/// Write the plan, then read the file back and confirm the entry is in the
/// state the plan promised. The write itself is a temp-file rename, so a
/// failure at any point leaves the previous config byte-for-byte intact.
pub fn apply(plan: &Plan) -> Result<(), String> {
    if !plan.action.writes() {
        return Ok(());
    }
    let remove = plan.action == Action::Remove;
    let updated = match plan.agent {
        Agent::Codex => codex_apply(&plan.config_path, &plan.binary, remove)?,
        _ => json_apply(plan.agent, &plan.config_path, &plan.binary, remove)?,
    };
    write_atomic(&plan.config_path, &updated, plan.reviewed.as_deref())?;
    match (current_entry(plan.agent, &plan.config_path), remove) {
        (Ok(None), true) => Ok(()),
        (Ok(Some(entry)), false) if entries_match(plan.agent, &entry, &plan.binary) => Ok(()),
        (Err(reason), _) => Err(format!(
            "wrote {} but could not verify it: {reason}",
            plan.config_path.display()
        )),
        _ => Err(format!(
            "wrote {} but the oxide entry did not read back as expected",
            plan.config_path.display()
        )),
    }
}

/// The `oxide` entry currently in this agent's config, rendered canonically
/// for comparison and display. `Ok(None)` means the agent has no OXIDE
/// entry; `Err` means the file exists but cannot be edited safely.
fn current_entry(agent: Agent, path: &Path) -> Result<Option<String>, String> {
    match agent {
        Agent::Codex => codex_current_entry(path),
        _ => {
            reject_jsonc(path)?;
            let Some(root) = read_json(path)? else {
                return Ok(None);
            };
            let Some(obj) = root.as_object() else {
                return Err(format!(
                    "{} does not contain a JSON object at the top level",
                    path.display()
                ));
            };
            match obj.get(agent.json_container()) {
                None => Ok(None),
                Some(Value::Object(servers)) => match servers.get(SERVER_NAME) {
                    None => Ok(None),
                    Some(Value::Object(entry)) => {
                        Ok(Some(serde_json::to_string(entry).unwrap_or_default()))
                    }
                    Some(_) => Err(format!(
                        "{}'s \"{}\".\"{SERVER_NAME}\" is not an object",
                        path.display(),
                        agent.json_container()
                    )),
                },
                Some(_) => Err(format!(
                    "{}'s \"{}\" is not an object",
                    path.display(),
                    agent.json_container()
                )),
            }
        }
    }
}

/// The executable an existing entry launches, if it names one.
fn entry_command(agent: Agent, current: &str) -> Option<String> {
    match agent {
        Agent::Codex => current
            .parse::<toml_edit::DocumentMut>()
            .ok()?
            .get("command")?
            .as_str()
            .map(str::to_string),
        Agent::OpenCode => {
            let entry: Value = serde_json::from_str(current).ok()?;
            match entry.get("command")? {
                Value::Array(argv) => argv.first()?.as_str().map(str::to_string),
                Value::String(command) => Some(command.clone()),
                _ => None,
            }
        }
        _ => serde_json::from_str::<Value>(current)
            .ok()?
            .get("command")?
            .as_str()
            .map(str::to_string),
    }
}

/// Is the MCP server currently registered under the name `oxide` actually
/// OXIDE?
///
/// The name alone does not prove it: someone else's server could hold that
/// key, and neither overwriting it on install nor deleting it on uninstall
/// would be OXIDE's to do. The proof used is the executable's file name,
/// not its full path — an install that moved to a new prefix, or a config
/// written by an older OXIDE, is still OXIDE's own entry to update or
/// remove, and requiring an exact path match would strand both.
///
/// ponytail: this is forgeable in principle — a different tool registered
/// under the key `oxide` *and* running an executable named `oxide` passes.
/// The alternative, a provenance marker written at install time, is a
/// stronger proof but a worse product: it puts a non-standard field in the
/// user's config, and it cannot recognise an entry written by an older
/// OXIDE or pasted by hand (which is exactly what the JSONC path tells
/// people to do), so every one of those would become an unremovable
/// blocked entry. Revisit if a real collision is ever reported.
fn owned_by_oxide(agent: Agent, current: &str) -> bool {
    entry_command(agent, current).is_some_and(|command| {
        Path::new(&command)
            .file_name()
            .and_then(|n| n.to_str())
            .is_some_and(|name| name == "oxide" || name == "oxide.exe")
    })
}

fn not_ours(agent: Agent, path: &Path, current: &str) -> String {
    format!(
        "the MCP server named \"{SERVER_NAME}\" in {} runs {}, which is not the oxide binary — \
         OXIDE will not touch another tool's configuration; rename or remove that entry by hand",
        path.display(),
        entry_command(agent, current).unwrap_or_else(|| "an unrecognized command".to_string())
    )
}

/// The managed fields alone, in this agent's config syntax — the current
/// values when `current` is given, the ones OXIDE would write otherwise.
///
/// The plan shows this rather than the whole entry, because the whole entry
/// is not what changes: an update rewrites the managed fields and leaves
/// everything else the user put there in place. Previewing a fresh entry
/// would advertise deleting settings that in fact survive.
fn managed_lines(agent: Agent, current: Option<&str>, bin: &str) -> String {
    match agent {
        Agent::Codex => {
            let doc = current.and_then(|c| c.parse::<toml_edit::DocumentMut>().ok());
            codex_managed(bin)
                .iter()
                .map(|(key, fresh)| {
                    let shown = match &doc {
                        Some(doc) => doc
                            .get(key)
                            .and_then(|i| i.as_value())
                            .map(|v| v.to_string().trim().to_string())
                            .unwrap_or_else(|| "(absent)".to_string()),
                        None => fresh.to_string().trim().to_string(),
                    };
                    format!("{key} = {shown}")
                })
                .collect::<Vec<_>>()
                .join("\n")
        }
        _ => {
            let existing: Option<Value> = current.and_then(|c| serde_json::from_str(c).ok());
            agent
                .managed_json(bin)
                .iter()
                .map(|(key, fresh)| {
                    let shown = match existing.as_ref().and_then(|v| v.get(*key)) {
                        Some(value) => value.to_string(),
                        None if current.is_some() => "(absent)".to_string(),
                        None => fresh.to_string(),
                    };
                    format!("\"{key}\": {shown}")
                })
                .collect::<Vec<_>>()
                .join("\n")
        }
    }
}

/// An entry is "already configured" when the fields OXIDE manages already
/// say what OXIDE would write — not when the whole entry is byte-identical.
/// Anything else the user added is invisible to this comparison, which is
/// what lets those extras survive an update.
fn entries_match(agent: Agent, current: &str, bin: &str) -> bool {
    match agent {
        Agent::Codex => {
            let Ok(table) = current.parse::<toml_edit::DocumentMut>() else {
                return false;
            };
            codex_managed(bin)
                .iter()
                .all(|(key, value)| table.get(key).is_some_and(|got| item_eq(got, value)))
        }
        _ => {
            let Ok(Value::Object(entry)) = serde_json::from_str::<Value>(current) else {
                return false;
            };
            agent
                .managed_json(bin)
                .iter()
                .all(|(key, value)| entry.get(*key) == Some(value))
        }
    }
}

// ---------------------------------------------------------------- JSON edits

fn read_json(path: &Path) -> Result<Option<Value>, String> {
    let text = match std::fs::read_to_string(path) {
        Ok(text) => text,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(format!("cannot read {}: {e}", path.display())),
    };
    if text.trim().is_empty() {
        return Ok(Some(Value::Object(Map::new())));
    }
    serde_json::from_str(&text)
        .map(Some)
        .map_err(|e| format!("{} is not valid JSON ({e})", path.display()))
}

/// OpenCode accepts a commented `opencode.jsonc`, and serde_json cannot
/// round-trip comments. Rather than parse it strictly and call a perfectly
/// valid config "malformed" — or rewrite it and silently delete the user's
/// comments — say so and hand over the line to paste.
fn reject_jsonc(path: &Path) -> Result<(), String> {
    if path.extension().is_some_and(|e| e == "jsonc") && path.exists() {
        return Err(format!(
            "{} is JSONC; rewriting it would drop its comments. \
             Add this to its \"mcp\" object by hand instead",
            path.display()
        ));
    }
    Ok(())
}

fn json_apply(agent: Agent, path: &Path, bin: &str, remove: bool) -> Result<String, String> {
    let mut root = read_json(path)?.unwrap_or_else(|| Value::Object(Map::new()));
    let obj = root
        .as_object_mut()
        .ok_or_else(|| format!("{} is not a JSON object", path.display()))?;
    let container = agent.json_container();
    if remove {
        if let Some(Value::Object(servers)) = obj.get_mut(container) {
            servers.remove(SERVER_NAME);
        }
    } else {
        let servers = obj
            .entry(container)
            .or_insert_with(|| Value::Object(Map::new()));
        let servers = servers
            .as_object_mut()
            .ok_or_else(|| format!("{}'s \"{container}\" is not an object", path.display()))?;
        match servers.get_mut(SERVER_NAME) {
            // Update in place: only the fields OXIDE manages are touched, so
            // an `env` block or a custom timeout the user added survives.
            Some(Value::Object(entry)) => {
                for (key, value) in agent.managed_json(bin) {
                    entry.insert(key.to_string(), value);
                }
            }
            _ => {
                servers.insert(SERVER_NAME.to_string(), agent.json_entry(bin));
            }
        }
    }
    let mut out = serde_json::to_string_pretty(&root)
        .map_err(|e| format!("cannot serialize {}: {e}", path.display()))?;
    out.push('\n');
    Ok(out)
}

// ---------------------------------------------------------------- TOML edits
//
// Codex's config is TOML and belongs to the user, so the edit goes through
// `toml_edit`: it is lossless, meaning every comment, key order, and blank
// line elsewhere in the file survives byte-for-byte. An earlier line-based
// table editor here was smaller but got two real cases wrong — a root table
// whose quoted key literally *is* `"mcp_servers.oxide"`, and a multiline
// string containing a line that reads like a table header — either of which
// would have rewritten unrelated configuration.

fn codex_managed(bin: &str) -> Vec<(&'static str, toml_edit::Value)> {
    vec![
        ("command", toml_edit::value(bin).into_value().unwrap()),
        ("args", {
            let mut args = toml_edit::Array::new();
            args.push("mcp");
            toml_edit::Value::Array(args)
        }),
    ]
}

fn item_eq(item: &toml_edit::Item, value: &toml_edit::Value) -> bool {
    item.as_value().map(|v| v.to_string().trim().to_string())
        == Some(value.to_string().trim().to_string())
}

/// The `[mcp_servers.oxide]` table as it would be written fresh.
fn codex_entry(bin: &str) -> String {
    let mut doc = toml_edit::DocumentMut::new();
    let mut table = toml_edit::Table::new();
    for (key, value) in codex_managed(bin) {
        table.insert(key, toml_edit::Item::Value(value));
    }
    let mut servers = toml_edit::Table::new();
    servers.set_implicit(true);
    servers.insert(SERVER_NAME, toml_edit::Item::Table(table));
    doc.insert("mcp_servers", toml_edit::Item::Table(servers));
    doc.to_string()
}

fn codex_doc(path: &Path) -> Result<Option<toml_edit::DocumentMut>, String> {
    let text = match std::fs::read_to_string(path) {
        Ok(text) => text,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(format!("cannot read {}: {e}", path.display())),
    };
    text.parse::<toml_edit::DocumentMut>()
        .map(Some)
        .map_err(|e| format!("{} is not valid TOML ({e})", path.display()))
}

/// The `oxide` entry, whether it is written as its own `[mcp_servers.oxide]`
/// table or inline inside `[mcp_servers]` — both are the same key path, and
/// `toml_edit` edits either without changing which form the user chose.
fn codex_current_entry(path: &Path) -> Result<Option<String>, String> {
    let Some(doc) = codex_doc(path)? else {
        return Ok(None);
    };
    let Some(servers) = doc.get("mcp_servers") else {
        return Ok(None);
    };
    if servers.as_table_like().is_none() {
        return Err(format!("{}'s `mcp_servers` is not a table", path.display()));
    }
    match servers.as_table_like().and_then(|t| t.get(SERVER_NAME)) {
        None => Ok(None),
        Some(entry) if entry.as_table_like().is_some() => {
            // Rendered as a standalone TOML document, not as hand-joined
            // `key = value` lines: a nested user table (`[mcp_servers.oxide.env]`)
            // displays as a bare body that way, which then fails to reparse
            // and makes `entries_match` see a correct entry as stale forever.
            let table = entry.as_table_like().expect("checked above");
            let mut rendered = toml_edit::DocumentMut::new();
            for (key, item) in table.iter() {
                rendered.insert(key, item.clone());
            }
            Ok(Some(rendered.to_string().trim_end().to_string()))
        }
        Some(_) => Err(format!(
            "{}'s `mcp_servers.{SERVER_NAME}` is not a table",
            path.display()
        )),
    }
}

fn codex_apply(path: &Path, bin: &str, remove: bool) -> Result<String, String> {
    let mut doc = codex_doc(path)?.unwrap_or_default();
    if remove {
        if let Some(servers) = doc
            .get_mut("mcp_servers")
            .and_then(|s| s.as_table_like_mut())
        {
            servers.remove(SERVER_NAME);
            // A `[mcp_servers]` header OXIDE created for itself and then
            // emptied is litter; one the user already had is theirs.
            if servers.is_empty() {
                let user_owned = doc
                    .get("mcp_servers")
                    .and_then(|s| s.as_table())
                    .is_some_and(|t| !t.is_implicit());
                if !user_owned {
                    doc.remove("mcp_servers");
                }
            }
        }
        return Ok(doc.to_string());
    }

    let servers = doc
        .entry("mcp_servers")
        .or_insert_with(|| {
            let mut table = toml_edit::Table::new();
            table.set_implicit(true);
            toml_edit::Item::Table(table)
        })
        .as_table_like_mut()
        .ok_or_else(|| format!("{}'s `mcp_servers` is not a table", path.display()))?;

    match servers.get_mut(SERVER_NAME) {
        // Update in place, managed fields only: an `env` table or a
        // `startup_timeout_sec` the user set stays exactly where it is.
        Some(entry) if entry.as_table_like().is_some() => {
            let table = entry.as_table_like_mut().expect("checked above");
            for (key, value) in codex_managed(bin) {
                table.insert(key, toml_edit::Item::Value(value));
            }
        }
        Some(_) => {
            return Err(format!(
                "{}'s `mcp_servers.{SERVER_NAME}` is not a table",
                path.display()
            ))
        }
        None => {
            let mut table = toml_edit::Table::new();
            for (key, value) in codex_managed(bin) {
                table.insert(key, toml_edit::Item::Value(value));
            }
            servers.insert(SERVER_NAME, toml_edit::Item::Table(table));
        }
    }
    Ok(doc.to_string())
}

// ------------------------------------------------------------- atomic writes

/// Write via a sibling temp file and rename, so a failed or interrupted
/// write can never leave a half-rewritten agent config behind.
///
/// `expect` is the file exactly as the caller last read it. It is checked
/// again immediately before the rename, so an edit made while the user was
/// answering the confirmation prompt aborts the write instead of being
/// silently clobbered.
///
/// ponytail: the check-then-rename pair is not atomic, leaving a
/// microseconds-wide window an edit could still land in. Closing it needs an
/// advisory file lock held across the whole operation (a `flock` dependency,
/// plus a stale-lock story); the multi-second window that actually matters —
/// the confirmation prompt — is closed, and the recovery from losing the
/// race is to re-run install.
fn write_atomic(path: &Path, contents: &str, expect: Option<&[u8]>) -> Result<(), String> {
    let dir = path.parent().unwrap_or_else(|| Path::new("."));
    std::fs::create_dir_all(dir).map_err(|e| format!("cannot create {}: {e}", dir.display()))?;
    let mut tmp = tempfile::NamedTempFile::new_in(dir)
        .map_err(|e| format!("cannot write next to {}: {e}", path.display()))?;
    tmp.write_all(contents.as_bytes())
        .and_then(|()| tmp.flush())
        .map_err(|e| format!("cannot write {}: {e}", path.display()))?;
    // A fresh temp file is 0600; keep whatever mode the config already had
    // rather than silently tightening (or loosening) the user's own file.
    #[cfg(unix)]
    if let Ok(meta) = std::fs::metadata(path) {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(
            tmp.path(),
            std::fs::Permissions::from_mode(meta.permissions().mode()),
        );
    }
    if snapshot(path).as_deref() != expect {
        return Err(format!(
            "{} changed after the plan was shown; nothing was written — \
             re-run to see the current change",
            path.display()
        ));
    }
    tmp.persist(path)
        .map_err(|e| format!("cannot replace {}: {}", path.display(), e.error))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn codex_file(contents: &str) -> (tempfile::TempDir, PathBuf) {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.toml");
        std::fs::write(&path, contents).unwrap();
        (dir, path)
    }

    #[test]
    fn codex_edits_preserve_every_unrelated_line() {
        let original = "model = \"gpt-5\"\n\n# keep me\n[mcp_servers.ghost]\ncommand = \"ghost\"\n\n[tui]\ntheme = \"dark\"\n";
        let (_dir, path) = codex_file(original);

        let added = codex_apply(&path, "/opt/oxide", false).unwrap();
        assert!(added.contains("# keep me"), "{added}");
        assert!(added.contains("[mcp_servers.ghost]"), "{added}");
        assert!(added.contains("[tui]"), "{added}");
        assert!(added.contains("[mcp_servers.oxide]"), "{added}");

        std::fs::write(&path, &added).unwrap();
        assert!(entries_match(
            Agent::Codex,
            &codex_current_entry(&path).unwrap().unwrap(),
            "/opt/oxide"
        ));

        // Re-adding at a new path updates in place; never a second table.
        let moved = codex_apply(&path, "/usr/bin/oxide", false).unwrap();
        assert_eq!(moved.matches("[mcp_servers.oxide]").count(), 1);

        std::fs::write(&path, &moved).unwrap();
        let removed = codex_apply(&path, "", true).unwrap();
        assert_eq!(removed, original, "uninstall did not restore the file");
    }

    /// The reason this module uses a real TOML parser. A line scanner
    /// normalizes this root table's quoted key to `mcp_servers.oxide` and
    /// then rewrites the user's unrelated table.
    #[test]
    fn a_quoted_root_key_that_reads_like_the_oxide_path_is_not_ours() {
        let original = "[\"mcp_servers.oxide\"]\nsomething = \"user data\"\n";
        let (_dir, path) = codex_file(original);

        assert_eq!(codex_current_entry(&path).unwrap(), None);
        let added = codex_apply(&path, "/opt/oxide", false).unwrap();
        assert!(
            added.contains("something = \"user data\""),
            "the user's table was rewritten:\n{added}"
        );
        assert!(added.contains("[mcp_servers.oxide]"), "{added}");
    }

    /// The other one: a multiline string whose body contains a line that
    /// reads like a table header.
    #[test]
    fn a_table_header_inside_a_multiline_string_is_not_a_table_header() {
        let original = "notes = \"\"\"\n[mcp_servers.oxide]\ncommand = \"nope\"\n\"\"\"\n\n[tui]\ntheme = \"dark\"\n";
        let (_dir, path) = codex_file(original);

        assert_eq!(codex_current_entry(&path).unwrap(), None);
        let added = codex_apply(&path, "/opt/oxide", false).unwrap();
        assert!(added.contains("[tui]"), "the file was truncated:\n{added}");
        assert!(
            added.contains("command = \"nope\""),
            "the string literal was rewritten:\n{added}"
        );
    }

    /// An update must not be a replacement: settings OXIDE does not manage
    /// belong to the user and have to survive a binary-path change.
    #[test]
    fn updating_a_stale_entry_keeps_the_users_own_settings() {
        let (_dir, path) = codex_file(
            "[mcp_servers.oxide]\ncommand = \"/gone/oxide\"\nargs = [\"mcp\"]\nstartup_timeout_sec = 120.0\n\n[mcp_servers.oxide.env]\nOXIDE_EMBED_NATIVE = \"hashed\"\n",
        );
        let updated = codex_apply(&path, "/new/oxide", false).unwrap();
        assert!(updated.contains("command = \"/new/oxide\""), "{updated}");
        assert!(updated.contains("startup_timeout_sec = 120.0"), "{updated}");
        assert!(updated.contains("OXIDE_EMBED_NATIVE"), "{updated}");
        assert!(!updated.contains("/gone/oxide"), "{updated}");

        let mut entry = serde_json::json!({
            "type": "stdio",
            "command": "/gone/oxide",
            "args": ["mcp"],
            "env": {"OXIDE_EMBED_NATIVE": "hashed"},
        });
        let object = entry.as_object_mut().unwrap();
        for (key, value) in Agent::ClaudeCode.managed_json("/new/oxide") {
            object.insert(key.to_string(), value);
        }
        assert_eq!(entry["command"], "/new/oxide");
        assert_eq!(entry["env"]["OXIDE_EMBED_NATIVE"], "hashed");
    }

    /// An inline `oxide = { ... }` under `[mcp_servers]` is the same key
    /// path as the table form, and stays in whichever form the user wrote.
    #[test]
    fn an_inline_entry_is_updated_without_being_reshaped() {
        let (_dir, path) = codex_file(
            "[mcp_servers]\noxide = { command = \"/old/oxide\", args = [\"mcp\"] }\nghost = { command = \"ghost\" }\n",
        );
        assert!(codex_current_entry(&path).unwrap().is_some());
        let updated = codex_apply(&path, "/new/oxide", false).unwrap();
        assert!(
            updated.contains("oxide = { command = \"/new/oxide\""),
            "{updated}"
        );
        assert!(
            updated.contains("ghost = { command = \"ghost\" }"),
            "{updated}"
        );
        assert_eq!(
            updated.matches("oxide =").count(),
            1,
            "an inline entry was duplicated:\n{updated}"
        );
    }

    #[test]
    fn malformed_toml_is_refused_rather_than_appended_to() {
        let (_dir, path) = codex_file("model = \n[unclosed\n");
        let err = codex_current_entry(&path).unwrap_err();
        assert!(err.contains("not valid TOML"), "{err}");
    }

    #[test]
    fn paths_with_spaces_survive_both_config_formats() {
        let bin = "/home/a b/bin/oxide";
        assert!(codex_entry(bin).contains("\"/home/a b/bin/oxide\""));
        let (_dir, path) = codex_file("");
        std::fs::write(&path, codex_apply(&path, bin, false).unwrap()).unwrap();
        assert!(entries_match(
            Agent::Codex,
            &codex_current_entry(&path).unwrap().unwrap(),
            bin
        ));
        assert_eq!(Agent::ClaudeCode.json_entry(bin)["command"], bin);
    }

    /// The `oxide` key is not proof of ownership. Someone else's server
    /// under that name is theirs, and neither install nor uninstall may
    /// touch it.
    #[test]
    fn an_entry_named_oxide_that_runs_something_else_is_not_ours() {
        let foreign = serde_json::json!({
            "type": "stdio",
            "command": "/usr/bin/some-other-tool",
            "args": ["serve"],
        });
        assert!(!owned_by_oxide(Agent::ClaudeCode, &foreign.to_string()));

        // A moved binary, an older OXIDE, or a bare PATH lookup is still ours.
        for command in ["/opt/oxide", "/usr/local/bin/oxide", "oxide"] {
            let ours = serde_json::json!({"command": command, "args": ["mcp"]});
            assert!(
                owned_by_oxide(Agent::Antigravity, &ours.to_string()),
                "{command} should read as OXIDE's own entry"
            );
        }

        let opencode = serde_json::json!({
            "type": "local",
            "command": ["/opt/oxide", "mcp"],
            "enabled": true,
        });
        assert!(owned_by_oxide(Agent::OpenCode, &opencode.to_string()));

        let (_dir, path) =
            codex_file("[mcp_servers.oxide]\ncommand = \"/usr/bin/other\"\nargs = [\"serve\"]\n");
        let current = codex_current_entry(&path).unwrap().unwrap();
        assert!(!owned_by_oxide(Agent::Codex, &current));
    }

    /// A nested user table renders as its own `[env]` section. If the
    /// rendering is not valid standalone TOML, `entries_match` can never
    /// parse it and a correct entry looks stale on every re-install.
    #[test]
    fn a_nested_user_table_still_reads_back_as_configured() {
        let (_dir, path) = codex_file(
            "[mcp_servers.oxide]\ncommand = \"/opt/oxide\"\nargs = [\"mcp\"]\n\n[mcp_servers.oxide.env]\nOXIDE_EMBED_NATIVE = \"hashed\"\n",
        );
        let current = codex_current_entry(&path).unwrap().unwrap();
        assert!(
            current.parse::<toml_edit::DocumentMut>().is_ok(),
            "the rendered entry is not valid TOML:\n{current}"
        );
        assert!(
            entries_match(Agent::Codex, &current, "/opt/oxide"),
            "a correct entry with a nested table read as stale:\n{current}"
        );
        assert!(!entries_match(Agent::Codex, &current, "/elsewhere/oxide"));
    }

    /// An OpenCode entry left disabled is one the agent will not launch, so
    /// it is not "already configured".
    #[test]
    fn a_disabled_opencode_entry_is_not_configured() {
        let disabled = serde_json::json!({
            "type": "local",
            "command": ["/opt/oxide", "mcp"],
            "enabled": false,
        });
        assert!(!entries_match(
            Agent::OpenCode,
            &disabled.to_string(),
            "/opt/oxide"
        ));
        assert_eq!(Agent::OpenCode.json_entry("/opt/oxide")["enabled"], true);
    }

    /// A user extra means the entry is not byte-identical to a fresh one —
    /// but it is still configured, and re-running install must be a no-op.
    #[test]
    fn extra_user_fields_do_not_make_an_entry_look_unconfigured() {
        let entry = serde_json::json!({
            "type": "stdio",
            "command": "/opt/oxide",
            "args": ["mcp"],
            "env": {"RUST_LOG": "warn"},
        });
        assert!(entries_match(
            Agent::ClaudeCode,
            &entry.to_string(),
            "/opt/oxide"
        ));
        assert!(!entries_match(
            Agent::ClaudeCode,
            &entry.to_string(),
            "/somewhere/else/oxide"
        ));
    }
}
