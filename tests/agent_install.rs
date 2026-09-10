//! `oxide install` / `oxide uninstall`: agent detection, the confirmation
//! gate, and the config mutations themselves.
//!
//! Every run here gets a throwaway `$HOME` and an emptied `$PATH`, so
//! detection sees only what the test put there — never the agents actually
//! installed on the machine running the suite.

use serde_json::Value;
use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Output, Stdio};

struct Home {
    dir: tempfile::TempDir,
    binary: PathBuf,
}

impl Home {
    fn new() -> Self {
        Self {
            dir: tempfile::tempdir().unwrap(),
            binary: PathBuf::from(env!("CARGO_BIN_EXE_oxide")),
        }
    }

    /// The same throwaway home, but reached through a copy of the binary
    /// living at a path with a space in it.
    fn with_spaced_binary(self) -> Self {
        let dir = self.dir.path().join("bin dir");
        std::fs::create_dir_all(&dir).unwrap();
        let binary = dir.join("oxide");
        std::fs::copy(&self.binary, &binary).unwrap();
        Self { binary, ..self }
    }

    fn config(&self, relative: &str) -> PathBuf {
        self.dir.path().join(relative)
    }

    fn write(&self, relative: &str, contents: &str) {
        let path = self.config(relative);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, contents).unwrap();
    }

    fn read(&self, relative: &str) -> String {
        std::fs::read_to_string(self.config(relative)).unwrap()
    }

    fn json(&self, relative: &str) -> Value {
        serde_json::from_str(&self.read(relative)).unwrap()
    }

    fn mkdir(&self, relative: &str) {
        std::fs::create_dir_all(self.config(relative)).unwrap();
    }

    fn run(&self, args: &[&str]) -> Output {
        self.run_with_stdin(args, None)
    }

    fn run_with_stdin(&self, args: &[&str], answer: Option<&str>) -> Output {
        let mut command = Command::new(&self.binary);
        command
            .args(args)
            .env_clear()
            .env("HOME", self.dir.path())
            .env("XDG_CONFIG_HOME", self.dir.path().join(".config"))
            // An empty PATH is what keeps detection hermetic: without it,
            // a machine with a real `claude` on PATH would flip every
            // "not detected" assertion below.
            .env("PATH", "")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let mut child = command.spawn().unwrap();
        if let Some(answer) = answer {
            child
                .stdin
                .as_mut()
                .unwrap()
                .write_all(answer.as_bytes())
                .unwrap();
        }
        drop(child.stdin.take());
        child.wait_with_output().unwrap()
    }
}

fn out(output: &Output) -> String {
    format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    )
}

const CLAUDE: &str = ".claude.json";
const CODEX: &str = ".codex/config.toml";
const OPENCODE: &str = ".config/opencode/opencode.json";
const ANTIGRAVITY: &str = ".gemini/config/mcp_config.json";

fn oxide_entry(config: &Value, container: &str) -> Option<Value> {
    config.get(container)?.get("oxide").cloned()
}

#[test]
fn nothing_detected_reports_it_and_writes_nothing() {
    let home = Home::new();
    let output = home.run(&["install"]);
    let text = out(&output);

    assert!(output.status.success(), "{text}");
    for agent in ["Claude Code", "Codex", "OpenCode", "Antigravity CLI"] {
        assert!(
            text.contains(agent),
            "{agent} missing from listing:\n{text}"
        );
    }
    assert_eq!(
        text.matches("not detected").count(),
        4,
        "all four should read as not detected:\n{text}"
    );
    assert!(
        !home.config(CLAUDE).exists() && !home.config(CODEX).exists(),
        "install created a config for an agent that is not installed"
    );
}

#[test]
fn detection_distinguishes_present_absent_and_configured() {
    let home = Home::new();
    home.mkdir(".codex");

    let listing = out(&home.run(&["install"]));
    assert!(
        listing.contains("Codex") && listing.contains("✓ detected"),
        "a present agent must read as detected:\n{listing}"
    );
    assert_eq!(
        listing.matches("not detected").count(),
        3,
        "only Codex is installed here:\n{listing}"
    );

    home.run(&["install", "--agent", "codex", "--yes"]);
    let after = out(&home.run(&["install", "--agent", "codex", "--dry-run"]));
    assert!(
        after.contains("already configured"),
        "a configured agent must be reported as such, not re-added:\n{after}"
    );
}

/// Detection is not permission. Listing agents must never be the same act
/// as writing to them.
#[test]
fn a_declined_confirmation_writes_nothing() {
    let home = Home::new();
    home.write(CLAUDE, "{\"numStartups\": 7}\n");
    let before = home.read(CLAUDE);

    let output = home.run_with_stdin(&["install", "--agent", "claude"], Some("n\n"));
    let text = out(&output);
    assert!(output.status.success(), "{text}");
    assert!(text.contains("Cancelled"), "{text}");
    assert_eq!(home.read(CLAUDE), before, "a declined install still wrote");

    // Answering the agent picker with a blank line is also a cancellation.
    home.mkdir(".codex");
    let blank = home.run_with_stdin(&["install"], Some("\n"));
    assert!(blank.status.success());
    assert!(out(&blank).contains("Cancelled"));
    assert_eq!(home.read(CLAUDE), before);
}

/// Silence is not consent: with no answer available at all, install must
/// fail loudly rather than exit 0 having done nothing.
#[test]
fn end_of_input_at_the_prompt_is_an_error_not_a_silent_no_op() {
    let home = Home::new();
    home.write(CLAUDE, "{}\n");

    let output = home.run(&["install", "--agent", "claude"]);
    assert!(!output.status.success(), "{}", out(&output));
    assert!(out(&output).contains("--yes"), "{}", out(&output));
    assert_eq!(home.read(CLAUDE), "{}\n");
}

#[test]
fn dry_run_prints_the_exact_change_and_writes_nothing() {
    let home = Home::new();
    home.mkdir(".codex");
    home.write(CODEX, "model = \"gpt-5\"\n");

    let output = home.run(&["install", "--agent", "codex", "--dry-run"]);
    let text = out(&output);
    assert!(output.status.success(), "{text}");
    assert!(text.contains("[mcp_servers.oxide]"), "{text}");
    assert!(text.contains("args = [\"mcp\"]"), "{text}");
    assert!(text.contains("Dry run"), "{text}");
    assert_eq!(
        home.read(CODEX),
        "model = \"gpt-5\"\n",
        "--dry-run modified the config"
    );
}

#[test]
fn first_install_writes_one_entry_per_selected_agent() {
    let home = Home::new();
    home.write(CLAUDE, "{\"numStartups\": 4}\n");
    home.write(CODEX, "model = \"gpt-5\"\n");
    home.write(OPENCODE, "{\"plugin\": [\"p\"]}\n");
    home.mkdir(".gemini/config");

    let output = home.run(&["install", "--agent", "all", "--yes"]);
    let text = out(&output);
    assert!(output.status.success(), "{text}");
    assert!(text.contains("Restart"), "no restart note:\n{text}");

    let binary = home.binary.display().to_string();

    let claude = oxide_entry(&home.json(CLAUDE), "mcpServers").expect("claude entry");
    assert_eq!(claude["type"], "stdio");
    assert_eq!(claude["command"], binary);
    assert_eq!(claude["args"], serde_json::json!(["mcp"]));

    let opencode = oxide_entry(&home.json(OPENCODE), "mcp").expect("opencode entry");
    assert_eq!(opencode["type"], "local");
    assert_eq!(opencode["command"], serde_json::json!([binary, "mcp"]));
    assert_eq!(opencode["enabled"], true);

    let antigravity = oxide_entry(&home.json(ANTIGRAVITY), "mcpServers").expect("antigravity");
    assert_eq!(antigravity["command"], binary);
    assert!(
        antigravity.get("type").is_none(),
        "Antigravity's schema has no type field"
    );

    let codex = home.read(CODEX);
    assert!(codex.contains("[mcp_servers.oxide]"), "{codex}");
    assert!(
        codex.contains(&format!("command = \"{binary}\"")),
        "{codex}"
    );
}

/// The absolute binary path is what an agent will actually exec, so it has
/// to survive a home directory or install prefix with a space in it.
#[test]
fn a_binary_path_containing_spaces_round_trips_through_both_formats() {
    let home = Home::new().with_spaced_binary();
    home.write(CLAUDE, "{}\n");
    home.write(CODEX, "");

    let output = home.run(&["install", "--agent", "claude", "--agent", "codex", "--yes"]);
    assert!(output.status.success(), "{}", out(&output));

    let binary = home.binary.display().to_string();
    assert!(binary.contains("bin dir"), "test did not use a spaced path");

    let claude = oxide_entry(&home.json(CLAUDE), "mcpServers").unwrap();
    assert_eq!(claude["command"], binary);
    assert!(
        home.read(CODEX)
            .contains(&format!("command = \"{binary}\"")),
        "TOML did not quote the spaced path:\n{}",
        home.read(CODEX)
    );

    // And it still reads back as "already configured", i.e. the quoting is
    // symmetric between what we write and what we parse.
    let again = out(&home.run(&["install", "--agent", "codex", "--dry-run"]));
    assert!(again.contains("already configured"), "{again}");
}

#[test]
fn reinstalling_is_idempotent_and_never_duplicates_the_entry() {
    let home = Home::new();
    home.write(CLAUDE, "{}\n");
    home.write(CODEX, "model = \"gpt-5\"\n");

    home.run(&["install", "--agent", "claude", "--agent", "codex", "--yes"]);
    let claude_once = home.read(CLAUDE);
    let codex_once = home.read(CODEX);

    let second = home.run(&["install", "--agent", "claude", "--agent", "codex", "--yes"]);
    assert!(second.status.success(), "{}", out(&second));
    assert!(out(&second).contains("Nothing to do"), "{}", out(&second));
    assert_eq!(home.read(CLAUDE), claude_once);
    assert_eq!(home.read(CODEX), codex_once);
    assert_eq!(
        home.read(CODEX).matches("[mcp_servers.oxide]").count(),
        1,
        "a second install appended a duplicate table"
    );
}

/// A stale OXIDE registration — say, from a binary that has since moved —
/// is replaced in place, and the plan shows both sides before it happens.
#[test]
fn a_conflicting_oxide_registration_is_shown_and_updated_in_place() {
    let home = Home::new();
    home.write(
        CLAUDE,
        "{\"mcpServers\": {\"oxide\": {\"type\": \"stdio\", \"command\": \"/gone/oxide\", \"args\": [\"mcp\"]}}}\n",
    );
    home.write(
        CODEX,
        "[mcp_servers.oxide]\ncommand = \"/gone/oxide\"\nargs = [\"mcp\"]\n\n[tui]\ntheme = \"x\"\n",
    );

    let preview = out(&home.run(&["install", "--agent", "claude", "--dry-run"]));
    assert!(preview.contains("update"), "{preview}");
    assert!(
        preview.contains("was:") && preview.contains("/gone/oxide"),
        "the preview must show the value being replaced:\n{preview}"
    );

    let output = home.run(&["install", "--agent", "claude", "--agent", "codex", "--yes"]);
    assert!(output.status.success(), "{}", out(&output));

    let binary = home.binary.display().to_string();
    let claude = oxide_entry(&home.json(CLAUDE), "mcpServers").unwrap();
    assert_eq!(claude["command"], binary);

    let codex = home.read(CODEX);
    assert_eq!(codex.matches("[mcp_servers.oxide]").count(), 1);
    assert!(!codex.contains("/gone/oxide"), "{codex}");
    assert!(
        codex.contains("[tui]"),
        "the replace ate a later table:\n{codex}"
    );
}

#[test]
fn a_malformed_agent_config_is_refused_loudly_and_left_untouched() {
    let home = Home::new();
    home.write(CLAUDE, "{ not json at all");
    home.write(CODEX, "model = \n[unclosed\n");
    let claude_before = home.read(CLAUDE);
    let codex_before = home.read(CODEX);

    let output = home.run(&["install", "--agent", "claude", "--agent", "codex", "--yes"]);
    let text = out(&output);
    assert!(
        !output.status.success(),
        "a config OXIDE refuses to edit must not exit 0:\n{text}"
    );
    assert!(text.contains("not valid JSON"), "{text}");
    assert!(text.contains("not valid TOML"), "{text}");
    assert_eq!(home.read(CLAUDE), claude_before);
    assert_eq!(home.read(CODEX), codex_before);
}

/// An update is not a replacement. Anything OXIDE does not manage — an env
/// block, a timeout — is the user's and must survive a binary-path change.
#[test]
fn updating_a_stale_entry_preserves_settings_oxide_does_not_manage() {
    let home = Home::new();
    home.write(
        CLAUDE,
        "{\"mcpServers\": {\"oxide\": {\"type\": \"stdio\", \"command\": \"/gone/oxide\", \"args\": [\"mcp\"], \"env\": {\"RUST_LOG\": \"warn\"}}}}\n",
    );
    home.write(
        CODEX,
        "[mcp_servers.oxide]\ncommand = \"/gone/oxide\"\nargs = [\"mcp\"]\nstartup_timeout_sec = 120.0\n",
    );

    let output = home.run(&["install", "--agent", "claude", "--agent", "codex", "--yes"]);
    assert!(output.status.success(), "{}", out(&output));

    let binary = home.binary.display().to_string();
    let claude = oxide_entry(&home.json(CLAUDE), "mcpServers").unwrap();
    assert_eq!(claude["command"], binary);
    assert_eq!(
        claude["env"]["RUST_LOG"], "warn",
        "the user's env block was dropped by the update"
    );

    let codex = home.read(CODEX);
    assert!(
        codex.contains(&format!("command = \"{binary}\"")),
        "{codex}"
    );
    assert!(
        codex.contains("startup_timeout_sec = 120.0"),
        "the user's timeout was dropped by the update:\n{codex}"
    );
}

/// Those extras must also not make the entry look unconfigured, or every
/// re-install would rewrite a config that already says the right thing.
#[test]
fn user_extras_do_not_break_idempotency() {
    let home = Home::new();
    home.write(CODEX, "");
    home.run(&["install", "--agent", "codex", "--yes"]);

    let with_extra = format!("{}env = {{ RUST_LOG = \"warn\" }}\n", home.read(CODEX));
    home.write(CODEX, &with_extra);

    let again = home.run(&["install", "--agent", "codex", "--dry-run"]);
    assert!(
        out(&again).contains("already configured"),
        "an entry with a user extra was treated as stale:\n{}",
        out(&again)
    );
}

/// A nested `[mcp_servers.oxide.env]` is the shape a real user reaches for,
/// and it must not make every re-install plan a replacement.
#[test]
fn a_nested_env_table_does_not_break_codex_idempotency() {
    let home = Home::new();
    home.write(CODEX, "");
    home.run(&["install", "--agent", "codex", "--yes"]);
    let nested = format!(
        "{}\n[mcp_servers.oxide.env]\nOXIDE_EMBED_NATIVE = \"hashed\"\n",
        home.read(CODEX).trim_end()
    );
    home.write(CODEX, &nested);

    let again = home.run(&["install", "--agent", "codex", "--dry-run"]);
    assert!(
        out(&again).contains("already configured"),
        "an entry with a nested env table read as stale:\n{}",
        out(&again)
    );
}

/// Installing over a disabled OpenCode entry must actually enable it —
/// otherwise install reports success on an integration that will not run.
#[test]
fn installing_over_a_disabled_opencode_entry_re_enables_it() {
    let home = Home::new();
    let binary = home.binary.display().to_string();
    home.write(
        OPENCODE,
        &format!(
            "{{\"mcp\": {{\"oxide\": {{\"type\": \"local\", \"command\": [\"{binary}\", \"mcp\"], \"enabled\": false}}}}}}\n"
        ),
    );

    let preview = out(&home.run(&["install", "--agent", "opencode", "--dry-run"]));
    assert!(
        !preview.contains("already configured"),
        "a disabled entry was reported as configured:\n{preview}"
    );

    let output = home.run(&["install", "--agent", "opencode", "--yes"]);
    assert!(output.status.success(), "{}", out(&output));
    assert_eq!(
        oxide_entry(&home.json(OPENCODE), "mcp").unwrap()["enabled"],
        true
    );
}

/// "Here is the exact change, proceed?" is a promise about the file as it
/// was shown. If it changes in between, the promise no longer holds.
#[test]
fn a_config_edited_after_the_plan_was_shown_is_not_overwritten() {
    let home = Home::new();
    home.write(CODEX, "model = \"gpt-5\"\n");

    // The prompt blocks on stdin, so the edit lands between plan and write.
    let mut child = Command::new(&home.binary)
        .args(["install", "--agent", "codex"])
        .env_clear()
        .env("HOME", home.dir.path())
        .env("XDG_CONFIG_HOME", home.dir.path().join(".config"))
        .env("PATH", "")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    // Wait for the confirmation prompt itself rather than for a fixed
    // duration, so a loaded machine cannot turn this into a flake.
    let mut stdout = child.stdout.take().unwrap();
    let mut seen = Vec::new();
    let mut byte = [0u8; 1];
    while !String::from_utf8_lossy(&seen).contains("Proceed?") {
        assert!(
            std::io::Read::read(&mut stdout, &mut byte).unwrap() == 1,
            "install exited before prompting: {}",
            String::from_utf8_lossy(&seen)
        );
        seen.push(byte[0]);
    }

    let meanwhile = "model = \"gpt-5\"\nweb_search = \"live\"\n";
    home.write(CODEX, meanwhile);
    child.stdin.as_mut().unwrap().write_all(b"y\n").unwrap();
    drop(child.stdin.take());
    std::io::Read::read_to_end(&mut stdout, &mut seen).unwrap();
    let output = child.wait_with_output().unwrap();

    let text = format!("{}{}", String::from_utf8_lossy(&seen), out(&output));
    assert!(!output.status.success(), "{text}");
    assert!(text.contains("changed after the plan was shown"), "{text}");
    assert_eq!(
        home.read(CODEX),
        meanwhile,
        "the concurrent edit was clobbered"
    );
}

/// A commented OpenCode config cannot be rewritten without losing the
/// comments, so OXIDE says so instead of silently reformatting it.
#[test]
fn a_commented_opencode_config_is_refused_with_the_entry_to_paste() {
    let home = Home::new();
    let jsonc = "{\n  // my settings\n  \"mcp\": {}\n}\n";
    home.write(".config/opencode/opencode.jsonc", jsonc);

    let output = home.run(&["install", "--agent", "opencode", "--yes"]);
    let text = out(&output);
    assert!(!output.status.success(), "{text}");
    assert!(text.contains("JSONC"), "{text}");
    assert!(text.contains("by hand"), "{text}");
    assert_eq!(home.read(".config/opencode/opencode.jsonc"), jsonc);
    assert!(
        !home.config(OPENCODE).exists(),
        "a second config file was created alongside the jsonc one"
    );
}

/// A config managed by a dotfiles repo is reached through a symlink. The
/// write is a rename, which would replace the link itself and silently
/// disconnect the dotfile source, so the link has to be resolved first.
#[cfg(unix)]
#[test]
fn a_symlinked_config_is_edited_through_the_link_not_over_it() {
    let home = Home::new();
    home.write("dotfiles/codex.toml", "model = \"gpt-5\"\n");
    home.mkdir(".codex");
    std::os::unix::fs::symlink(home.config("dotfiles/codex.toml"), home.config(CODEX)).unwrap();

    let output = home.run(&["install", "--agent", "codex", "--yes"]);
    assert!(output.status.success(), "{}", out(&output));

    assert!(
        std::fs::symlink_metadata(home.config(CODEX))
            .unwrap()
            .file_type()
            .is_symlink(),
        "the symlink was replaced by a regular file"
    );
    let target = home.read("dotfiles/codex.toml");
    assert!(
        target.contains("[mcp_servers.oxide]"),
        "the edit did not reach the dotfile:\n{target}"
    );
    assert!(target.contains("model = \"gpt-5\""), "{target}");

    let removed = home.run(&["uninstall", "--agent", "codex", "--yes"]);
    assert!(removed.status.success(), "{}", out(&removed));
    assert!(std::fs::symlink_metadata(home.config(CODEX))
        .unwrap()
        .file_type()
        .is_symlink());
    assert_eq!(home.read("dotfiles/codex.toml"), "model = \"gpt-5\"\n");
}

/// The `oxide` key is not proof of ownership: another tool's server could
/// hold that name, and it is not OXIDE's to overwrite or delete.
#[test]
fn a_foreign_server_named_oxide_is_never_touched() {
    let home = Home::new();
    let foreign = "{\"mcpServers\": {\"oxide\": {\"type\": \"stdio\", \"command\": \"/usr/bin/some-other-tool\", \"args\": [\"serve\"]}}}\n";
    home.write(CLAUDE, foreign);

    for command in [
        vec!["install", "--agent", "claude", "--yes"],
        vec!["uninstall", "--agent", "claude", "--yes"],
    ] {
        let output = home.run(&command);
        let text = out(&output);
        assert!(
            !output.status.success(),
            "{command:?} silently took over another tool's entry:\n{text}"
        );
        assert!(text.contains("some-other-tool"), "{text}");
        assert!(text.contains("not the oxide binary"), "{text}");
        assert_eq!(home.read(CLAUDE), foreign, "{command:?} modified it anyway");
    }
}

/// A binary that has moved is still OXIDE's own entry — ownership is proved
/// by the executable's name, not by an exact path match, or an install
/// prefix change would strand the entry forever.
#[test]
fn a_moved_oxide_binary_is_still_recognised_as_ours() {
    let home = Home::new();
    home.write(
        CLAUDE,
        "{\"mcpServers\": {\"oxide\": {\"type\": \"stdio\", \"command\": \"/an/old/prefix/oxide\", \"args\": [\"mcp\"]}}}\n",
    );

    let output = home.run(&["install", "--agent", "claude", "--yes"]);
    assert!(output.status.success(), "{}", out(&output));
    assert_eq!(
        oxide_entry(&home.json(CLAUDE), "mcpServers").unwrap()["command"],
        home.binary.display().to_string()
    );

    let removed = home.run(&["uninstall", "--agent", "claude", "--yes"]);
    assert!(removed.status.success(), "{}", out(&removed));
    assert!(oxide_entry(&home.json(CLAUDE), "mcpServers").is_none());
}

#[test]
fn uninstall_removes_only_oxides_own_entry() {
    let home = Home::new();
    home.write(
        CLAUDE,
        "{\"numStartups\": 9, \"mcpServers\": {\"context7\": {\"type\": \"http\", \"url\": \"https://x\"}}}\n",
    );
    let codex_original =
        "model = \"gpt-5\"\n\n[mcp_servers.ghost]\ncommand = \"ghost\"\n\n[tui]\ntheme = \"x\"\n";
    home.write(CODEX, codex_original);

    home.run(&["install", "--agent", "claude", "--agent", "codex", "--yes"]);
    assert!(oxide_entry(&home.json(CLAUDE), "mcpServers").is_some());

    let output = home.run(&[
        "uninstall",
        "--agent",
        "claude",
        "--agent",
        "codex",
        "--yes",
    ]);
    assert!(output.status.success(), "{}", out(&output));

    let claude = home.json(CLAUDE);
    assert!(oxide_entry(&claude, "mcpServers").is_none());
    assert_eq!(claude["numStartups"], 9, "uninstall touched unrelated keys");
    assert!(
        claude["mcpServers"]["context7"].is_object(),
        "uninstall removed another tool's MCP server"
    );

    assert_eq!(
        home.read(CODEX),
        codex_original,
        "uninstall did not restore the TOML byte-for-byte"
    );

    let again = home.run(&["uninstall", "--agent", "claude", "--yes"]);
    assert!(again.status.success());
    assert!(out(&again).contains("no oxide entry present"));
}

/// The write goes through a temp file and a rename, so a failure at any
/// point leaves the previous config exactly as it was.
#[cfg(unix)]
#[test]
fn a_failed_write_leaves_the_original_config_intact() {
    use std::os::unix::fs::PermissionsExt;

    let home = Home::new();
    home.mkdir(".codex");
    let original = "model = \"gpt-5\"\n";
    home.write(CODEX, original);

    let dir = home.config(".codex");
    let restore = std::fs::metadata(&dir).unwrap().permissions();
    std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o500)).unwrap();

    let output = home.run(&["install", "--agent", "codex", "--yes"]);
    let text = out(&output);

    std::fs::set_permissions(&dir, restore).unwrap();

    assert!(
        !output.status.success(),
        "a failed write must exit 1:\n{text}"
    );
    assert!(text.contains("Codex"), "{text}");
    assert_eq!(
        home.read(CODEX),
        original,
        "the config was left half-written"
    );
    let strays: Vec<_> = std::fs::read_dir(&dir)
        .unwrap()
        .filter_map(|e| e.ok())
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|name| name != "config.toml")
        .collect();
    assert!(strays.is_empty(), "left temp files behind: {strays:?}");
}

#[test]
fn install_writes_only_the_agents_that_were_selected() {
    let home = Home::new();
    home.write(CLAUDE, "{}\n");
    home.write(CODEX, "");
    home.write(OPENCODE, "{}\n");

    let output = home.run_with_stdin(&["install"], Some("2\n y\n"));
    assert!(output.status.success(), "{}", out(&output));

    assert!(
        home.read(CODEX).contains("[mcp_servers.oxide]"),
        "the selected agent was not configured"
    );
    assert!(
        oxide_entry(&home.json(CLAUDE), "mcpServers").is_none(),
        "an unselected agent was configured anyway"
    );
    assert!(
        oxide_entry(&home.json(OPENCODE), "mcp").is_none(),
        "an unselected agent was configured anyway"
    );
}

#[test]
fn an_unknown_agent_name_lists_the_known_ones() {
    let home = Home::new();
    let output = home.run(&["install", "--agent", "emacs", "--yes"]);
    let text = out(&output);
    assert!(!output.status.success(), "{text}");
    for known in ["claude", "codex", "opencode", "antigravity"] {
        assert!(
            text.contains(known),
            "{known} missing from the hint:\n{text}"
        );
    }
}
