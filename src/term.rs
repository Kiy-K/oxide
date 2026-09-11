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

use indicatif::{ProgressBar, ProgressStyle};
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

/// Stage/progress reporting on stderr, so stdout stays pipeable.
///
/// On a terminal each stage is a live spinner line (`⠹ Embedding...
/// 6,204/9,817`) animated by indicatif's steady-tick thread, so even a stage
/// with no counter (loading the model) visibly moves; when it completes the
/// line is left in place with the spinner cleared. Redirected stderr, CI,
/// and `TERM=dumb` get one plain line per completed stage and no redraws,
/// so logs stay short and stable. Never constructed for `--json`, which
/// `tests/cli_e2e.rs` requires to leave stderr empty.
///
/// indicatif redraws with `\r` and clear-to-end-of-line — cursor control,
/// not color, so `--color never` still animates; only the spinner glyph's
/// color follows the `Paint` decision.
pub struct StderrProgress {
    paint: Paint,
    interactive: bool,
    bar: Mutex<Option<ProgressBar>>,
}

impl StderrProgress {
    pub fn new(choice: ColorChoice) -> Self {
        // Same gate indicatif applies before it would hide itself, kept
        // explicit here so the plain-line fallback and the spinner can
        // never both be silent for the same stream.
        let interactive = std::io::stderr().is_terminal()
            && env("TERM").is_some_and(|t| !t.is_empty() && t != "dumb");
        if interactive {
            install_interrupt_cleanup();
        }
        Self {
            paint: Paint::for_stderr(choice),
            interactive,
            bar: Mutex::new(None),
        }
    }

    fn style(&self, with_counts: bool) -> ProgressStyle {
        let spinner = if self.paint.is_on() {
            "{spinner:.cyan}"
        } else {
            "{spinner}"
        };
        let counts = if with_counts {
            " {human_pos}/{human_len}"
        } else {
            ""
        };
        ProgressStyle::with_template(&format!("{spinner} {{msg}}{counts}"))
            .expect("static template")
            // Braille frames, then a blank: a finished stage keeps its line
            // but gives up the marker column to the `✓` summary that follows.
            .tick_chars("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏ ")
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, Option<ProgressBar>> {
        self.bar.lock().unwrap_or_else(|e| e.into_inner())
    }
}

impl Drop for StderrProgress {
    /// An error mid-stage must not leave the failure message glued onto a
    /// live spinner line: freeze the last frame and move to a fresh line.
    fn drop(&mut self) {
        if let Some(bar) = self.lock().take() {
            bar.abandon();
            end_line();
        }
        LINE_ACTIVE.store(false, Ordering::Relaxed);
    }
}

impl crate::index::ProgressSink for StderrProgress {
    fn begin(&self, stage: crate::index::Stage) {
        if !self.interactive {
            return;
        }
        let bar = ProgressBar::new_spinner()
            .with_style(self.style(false))
            .with_message(self.paint.dim(stage.label()));
        bar.enable_steady_tick(Duration::from_millis(80));
        LINE_ACTIVE.store(true, Ordering::Relaxed);
        *self.lock() = Some(bar);
    }

    fn advance(&self, _stage: crate::index::Stage, done: usize, total: usize) {
        if !self.interactive {
            return;
        }
        let guard = self.lock();
        let Some(bar) = guard.as_ref() else {
            return;
        };
        // Every symbol reports; indicatif rate-limits the actual redraws.
        if bar.length().is_none() {
            bar.set_style(self.style(true));
            bar.set_length(total as u64);
        }
        bar.set_position(done as u64);
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
            // The summary already carries the counts; drop the `{pos}/{len}`
            // suffix so a finished stage reads as one sentence.
            bar.set_style(self.style(false));
            bar.finish_with_message(line);
            end_line();
        }
        LINE_ACTIVE.store(false, Ordering::Relaxed);
    }
}

/// A finished or abandoned standalone bar leaves the cursor at the end of
/// its line (only a `MultiProgress` moves past finished bars), so the next
/// stage — or an error message — would otherwise start on the same line.
fn end_line() {
    let mut err = std::io::stderr().lock();
    let _ = writeln!(err);
    let _ = err.flush();
}

/// Ctrl-C while a spinner line is live would leave the shell prompt on the
/// same line as `⠹ Embedding... 3,012/9,817`. The default disposition kills
/// the process without running any `Drop`, so a handler terminates the line
/// first. Only `write` and `_exit` — both async-signal-safe — run inside
/// it; the exit status is the conventional 128+SIGINT.
#[cfg(unix)]
fn install_interrupt_cleanup() {
    extern "C" fn on_interrupt(_: libc::c_int) {
        if LINE_ACTIVE.load(Ordering::Relaxed) {
            // SAFETY: write(2) on fd 2 with a valid one-byte buffer.
            unsafe {
                libc::write(2, b"\n".as_ptr().cast(), 1);
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
