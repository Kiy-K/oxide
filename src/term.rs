//! Terminal presentation: the one place OXIDE produces ANSI escapes.
//!
//! Every human-facing renderer asks a [`Paint`] for styled text instead of
//! writing escape sequences itself, so "no ANSI in `--json`, pipes, or MCP"
//! is a property of one constructor rather than of every `println!`. The
//! decision is per stream: stdout and stderr are checked separately, since
//! `oxide index 2>log` should still colour stdout and `oxide status | less`
//! should still colour stderr.
//!
//! Color is never the only signal. Markers (`✓ ! ✗ ·`) and words carry the
//! state; color only makes them faster to scan.
//!
//! This module is also the abstraction boundary for the UI crates: cliclack
//! (stage steps, spinners, success/error states) and console (terminal
//! primitives, the global color switch cliclack styles through). No other
//! module names either crate; commands only ever see `Paint` and
//! [`StderrProgress`].

use cliclack::{ProgressBar, Theme, ThemeState};
use std::io::{IsTerminal, Write};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;

/// `--color auto|always|never`.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, clap::ValueEnum)]
pub enum ColorChoice {
    #[default]
    Auto,
    Always,
    Never,
}

/// Whether to emit color on a stream. Pure so the precedence is testable:
/// an explicit `--color` wins; then `NO_COLOR` (any non-empty value, per
/// <https://no-color.org>); then `TERM=dumb`; then whether the stream is a
/// terminal. `--color always` deliberately beats `NO_COLOR` — a flag typed
/// for this one command is a more specific request than a session default.
pub fn color_enabled(
    choice: ColorChoice,
    is_terminal: bool,
    env: impl Fn(&str) -> Option<String>,
) -> bool {
    match choice {
        ColorChoice::Always => true,
        ColorChoice::Never => false,
        ColorChoice::Auto => {
            if env("NO_COLOR").is_some_and(|v| !v.is_empty()) {
                return false;
            }
            if env("TERM").is_some_and(|t| t == "dumb") {
                return false;
            }
            is_terminal
        }
    }
}

fn env(key: &str) -> Option<String> {
    std::env::var(key).ok()
}

const RESET: &str = "\x1b[0m";
const BOLD: &str = "\x1b[1m";
const DIM: &str = "\x1b[2m";
const RED: &str = "\x1b[31m";
const GREEN: &str = "\x1b[32m";
const YELLOW: &str = "\x1b[33m";
const CYAN: &str = "\x1b[36m";

/// Styles text for one output stream. `Paint::plain()` never colors, and is
/// what every non-interactive path (JSON, MCP) gets by construction.
#[derive(Clone, Copy, Debug)]
pub struct Paint {
    on: bool,
}

impl Paint {
    pub fn plain() -> Self {
        Self { on: false }
    }

    pub fn for_stdout(choice: ColorChoice) -> Self {
        Self {
            on: color_enabled(choice, std::io::stdout().is_terminal(), env),
        }
    }

    pub fn for_stderr(choice: ColorChoice) -> Self {
        Self {
            on: color_enabled(choice, std::io::stderr().is_terminal(), env),
        }
    }

    pub fn is_on(&self) -> bool {
        self.on
    }

    fn wrap(&self, code: &str, text: &str) -> String {
        if self.on && !text.is_empty() {
            format!("{code}{text}{RESET}")
        } else {
            text.to_string()
        }
    }

    /// Success / current state.
    pub fn ok(&self, text: &str) -> String {
        self.wrap(GREEN, text)
    }
    /// Warning / stale state.
    pub fn warn(&self, text: &str) -> String {
        self.wrap(YELLOW, text)
    }
    /// Failure.
    pub fn err(&self, text: &str) -> String {
        self.wrap(RED, text)
    }
    /// Metadata: counts, kinds, scores, notes.
    pub fn dim(&self, text: &str) -> String {
        self.wrap(DIM, text)
    }
    /// Headings, paths, symbols, commands.
    pub fn bold(&self, text: &str) -> String {
        self.wrap(BOLD, text)
    }
    /// Symbol names and other things the eye should land on first.
    pub fn accent(&self, text: &str) -> String {
        self.wrap(CYAN, text)
    }

    pub fn check(&self) -> String {
        self.ok("✓")
    }
    pub fn bang(&self) -> String {
        self.warn("!")
    }
    pub fn cross(&self) -> String {
        self.err("✗")
    }
    pub fn dot(&self) -> String {
        self.dim("·")
    }
}

/// `1234` -> `1,234`. Counts are what a human reads off `oxide status`, and
/// unseparated five-digit symbol counts are hard to scan.
pub fn thousands(n: usize) -> String {
    let digits = n.to_string();
    let mut out = String::with_capacity(digits.len() + digits.len() / 3);
    for (i, c) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i).is_multiple_of(3) {
            out.push(',');
        }
        out.push(c);
    }
    out
}

pub fn duration(ms: u128) -> String {
    if ms < 1000 {
        format!("{ms}ms")
    } else {
        format!("{:.1}s", ms as f64 / 1000.0)
    }
}

// ------------------------------------------------------------- progress

/// True while a stage line is live on the terminal, so an interrupt handler
/// knows whether the cursor needs rescuing.
static LINE_ACTIVE: AtomicBool = AtomicBool::new(false);

/// cliclack's default theme, minus three things that fight this CLI's own
/// layout: the `│` guide line it prints under every finished step (doubles
/// the height of a six-stage run), the `[00:00:00]` elapsed clock and
/// 30-column bar block on determinate stages (a fake-precision look for a
/// 300ms parse), and the two-space gutter after the marker (every other
/// line OXIDE prints — `✓ Indexed …`, `! Index stale` — uses one). Kept:
/// the `◒◐◓◑` spinner, `◇` for a finished step, `▲`/`■` for cancel/error,
/// and the state colors. Grouped (`MultiProgress`) rendering is not
/// overridden because nothing here groups; stages are strictly sequential.
struct OxideTheme;

impl Theme for OxideTheme {
    fn default_progress_template(&self) -> String {
        "{msg} {bar:20.magenta} {human_pos}/{human_len}".into()
    }

    fn format_progress_start(&self, template: &str, grouped: bool, last: bool) -> String {
        self.format_progress_with_state(
            &format!("{{spinner:.magenta}} {template}"),
            grouped,
            last,
            &ThemeState::Active,
        )
    }

    fn format_progress_with_state(
        &self,
        msg: &str,
        _grouped: bool,
        _last: bool,
        state: &ThemeState,
    ) -> String {
        match state {
            ThemeState::Active => msg.to_string(),
            _ => format!("{} {msg}", self.state_symbol(state)),
        }
    }
}

/// Stage/progress reporting on stderr, so stdout stays pipeable.
///
/// On a terminal each stage is a live cliclack step (`◒ Embedding
/// symbols... 6,204/9,817`) that finishes as one printed `✓`/`✗` line —
/// cliclack owns line placement and redraw (it wraps `indicatif::ProgressBar`
/// on the default stderr draw target, which hides itself off a real terminal
/// or under `TERM=dumb`, the same test this struct's own `interactive` gate
/// makes explicitly). Redirected stderr, CI, and `TERM=dumb` get one plain
/// line per completed stage and no redraws, and never construct a cliclack
/// widget at all. Never constructed for `--json`, which `tests/cli_e2e.rs`
/// requires to leave stderr empty.
pub struct StderrProgress {
    paint: Paint,
    interactive: bool,
    bar: Mutex<Option<ProgressBar>>,
}

impl StderrProgress {
    pub fn new(choice: ColorChoice) -> Self {
        // Same gate indicatif applies before it would hide itself, kept
        // explicit here so the plain-line fallback and the interactive
        // renderer can never both be silent for the same stream.
        let interactive = std::io::stderr().is_terminal()
            && env("TERM").is_some_and(|t| !t.is_empty() && t != "dumb");
        let paint = Paint::for_stderr(choice);
        // cliclack paints through `console::style()`, which is gated by
        // these process-wide flags rather than by our own `Paint` — sync
        // them once so `--color always`/`never` and `NO_COLOR` govern both.
        console::set_colors_enabled(paint.is_on());
        console::set_colors_enabled_stderr(paint.is_on());
        if interactive {
            cliclack::set_theme(OxideTheme);
            install_interrupt_cleanup();
        }
        Self {
            paint,
            interactive,
            bar: Mutex::new(None),
        }
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, Option<ProgressBar>> {
        self.bar.lock().unwrap_or_else(|e| e.into_inner())
    }
}

impl Drop for StderrProgress {
    /// An error or Ctrl-C mid-stage must not leave the failure glued onto a
    /// live line: `.error()` finishes the widget with failure styling and
    /// its own trailing newline (cliclack renders finished steps through
    /// `ProgressBar::println`, the documented way to print alongside a live
    /// bar without racing its redraw thread).
    fn drop(&mut self) {
        if let Some(bar) = self.lock().take() {
            bar.error("interrupted");
        }
        LINE_ACTIVE.store(false, Ordering::Relaxed);
    }
}

impl crate::index::ProgressSink for StderrProgress {
    fn begin(&self, stage: crate::index::Stage, total: Option<usize>) {
        if !self.interactive {
            return;
        }
        // A stage with nothing to do is not worth a line on a terminal (an
        // up-to-date run would otherwise print three "nothing to do" steps);
        // the plain/log path still records it. `end` tolerates the missing
        // bar.
        if total == Some(0) {
            return;
        }
        // A known total gets a determinate bar; an unknown one (Model, Scan,
        // Finalize) gets a spinner — never a fake percentage.
        let bar = match total {
            Some(n) => ProgressBar::new(n as u64),
            None => ProgressBar::new(0).with_spinner_template(),
        };
        bar.start(self.paint.dim(stage.label()));
        LINE_ACTIVE.store(true, Ordering::Relaxed);
        if stage == crate::index::Stage::Model {
            // A cold model load is fastembed downloading ~23MB of ONNX
            // weights; nothing here can cheaply tell "downloading" from
            // "loading from cache" without reaching into the embeddings
            // module, so this triggers on elapsed time instead — if the
            // stage is still running after 1.5s it's slow enough to
            // explain. `is_finished()` guards the common cached (fast)
            // case, where this fires after the bar has already moved on to
            // a later stage: `set_message` on a finished bar is inert.
            let hint = bar.clone();
            std::thread::spawn(move || {
                std::thread::sleep(Duration::from_millis(1500));
                if !hint.is_finished() {
                    hint.set_message(
                        "Preparing semantic search... (first run may take a bit longer)",
                    );
                }
            });
        }
        *self.lock() = Some(bar);
    }

    fn advance(&self, _stage: crate::index::Stage, done: usize, _total: usize) {
        if !self.interactive {
            return;
        }
        // Every item reports; indicatif rate-limits the actual redraws.
        if let Some(bar) = self.lock().as_ref() {
            bar.set_position(done as u64);
        }
    }

    fn end(&self, stage: crate::index::Stage, summary: &str) {
        let line = format!("{} {summary}", self.paint.dim(stage.label()));
        if !self.interactive {
            let mut err = std::io::stderr().lock();
            let _ = writeln!(err, "{line}");
            let _ = err.flush();
            return;
        }
        if let Some(bar) = self.lock().take() {
            bar.stop(line);
        }
        LINE_ACTIVE.store(false, Ordering::Relaxed);
    }
}

/// Ctrl-C while a stage line is live would leave the shell prompt on the
/// same line as `◒ Embedding symbols... 3,012/9,817`. The default
/// disposition kills the process without running any `Drop`, so a handler
/// terminates the line first — and shows the cursor (`\x1b[?25h`) in case a
/// future cliclack/indicatif version hides it during animation; showing an
/// already-visible cursor is a no-op. Only `write` and `_exit` — both
/// async-signal-safe — run inside it; the exit status is the conventional
/// 128+SIGINT.
#[cfg(unix)]
fn install_interrupt_cleanup() {
    extern "C" fn on_interrupt(_: libc::c_int) {
        if LINE_ACTIVE.load(Ordering::Relaxed) {
            const RESCUE: &[u8] = b"\n\x1b[?25h";
            // SAFETY: write(2) on fd 2 with a valid buffer of known length.
            unsafe {
                libc::write(2, RESCUE.as_ptr().cast(), RESCUE.len());
            }
        }
        // SAFETY: _exit never returns and touches no process state.
        unsafe { libc::_exit(130) }
    }
    // SAFETY: installing a handler that only calls async-signal-safe
    // functions; the function pointer cast is the documented sighandler_t
    // shape.
    unsafe {
        libc::signal(
            libc::SIGINT,
            on_interrupt as extern "C" fn(libc::c_int) as libc::sighandler_t,
        );
    }
}

#[cfg(not(unix))]
fn install_interrupt_cleanup() {}

#[cfg(test)]
mod tests {
    use super::*;

    fn no_env(_: &str) -> Option<String> {
        None
    }

    #[test]
    fn auto_follows_the_terminal_unless_the_environment_objects() {
        assert!(color_enabled(ColorChoice::Auto, true, no_env));
        assert!(!color_enabled(ColorChoice::Auto, false, no_env));
        let no_color = |k: &str| (k == "NO_COLOR").then(|| "1".to_string());
        assert!(!color_enabled(ColorChoice::Auto, true, no_color));
        // An empty NO_COLOR is "not set" by the spec.
        let empty = |k: &str| (k == "NO_COLOR").then(String::new);
        assert!(color_enabled(ColorChoice::Auto, true, empty));
        let dumb = |k: &str| (k == "TERM").then(|| "dumb".to_string());
        assert!(!color_enabled(ColorChoice::Auto, true, dumb));
    }

    #[test]
    fn explicit_flags_override_everything() {
        let no_color = |k: &str| (k == "NO_COLOR").then(|| "1".to_string());
        assert!(color_enabled(ColorChoice::Always, false, no_color));
        assert!(!color_enabled(ColorChoice::Never, true, no_env));
    }

    #[test]
    fn plain_paint_emits_no_escapes() {
        let p = Paint::plain();
        for s in [
            p.ok("a"),
            p.warn("b"),
            p.err("c"),
            p.dim("d"),
            p.bold("e"),
            p.accent("f"),
        ] {
            assert!(!s.contains('\x1b'), "{s:?}");
        }
        let on = Paint { on: true };
        assert_eq!(on.ok("a"), "\x1b[32ma\x1b[0m");
        assert_eq!(on.ok(""), "", "empty text must not become a bare reset");
    }

    #[test]
    fn thousands_separates_only_where_needed() {
        assert_eq!(thousands(0), "0");
        assert_eq!(thousands(999), "999");
        assert_eq!(thousands(1_000), "1,000");
        assert_eq!(thousands(1_234_567), "1,234,567");
    }
}
