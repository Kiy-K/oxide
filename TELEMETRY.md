# Telemetry

OXIDE is local-first. It does not send usage data, and it does not report
crashes unless you ask it to. This document is the complete description of
what "unless you ask" means. The implementation it describes is
[`src/telemetry.rs`](src/telemetry.rs) (about eighty lines); nothing else in
the codebase talks to a telemetry service.

## Default behavior

Off. With `OXIDE_TELEMETRY` unset, empty, or set to anything other than an
affirmative value, no crash-reporting client is created at all. There is
nothing initialized, nothing buffered, and no code path that could make a
telemetry network request — not on success, not on a handled error, not on
a panic. `oxide index`, `oxide query`, `oxide search`, `oxide status`,
`oxide install`, and the MCP server all run this way.

The only network access OXIDE makes without telemetry is the one you
configure for embeddings: downloading the default model's weights on first
use, or calling an embedding endpoint you set with `--embedder` or
`$OXIDE_EMBED_URL`. See the README's "Embeddings and offline use" for a
zero-network setup.

## Opting in and out

```bash
# Enable crash reporting for one command
OXIDE_TELEMETRY=1 oxide index

# Enable it for a shell session
export OXIDE_TELEMETRY=1

# Disable it again (or just unset the variable)
unset OXIDE_TELEMETRY
```

Accepted affirmative values are `1`, `true`, `yes`, and `on`
(case-insensitive). Everything else is off. There is no config file
setting, no first-run prompt, and no other way to turn it on.

## What may be transmitted when enabled

With `OXIDE_TELEMETRY` on, OXIDE initializes the Sentry Rust SDK and reports
**only unhandled panics** — internal errors that crash the process. Ordinary
failures that OXIDE handles and prints (a missing index, an unreachable
embedding endpoint, a bad flag) are not reported. Nothing is sent by a run
that does not panic.

A panic report contains:

- the panic message and the location in OXIDE's source where it happened;
- a stack trace of function names, source file *names* (`cli.rs`, never a
  directory), and line numbers;
- the OXIDE version (`oxide@x.y.z`);
- operating system name, version, and kernel version;
- CPU architecture, and on macOS the hardware model string;
- the Rust compiler version the binary was built with;
- a random event id and a timestamp.

Before any report leaves the process, `before_send` in `src/telemetry.rs`
removes the machine hostname, any user field, any request field, all
breadcrumbs and extra data, and every path from every stack frame (the
build directory recorded by the compiler would otherwise name the home
directory of whoever built the binary), whatever an SDK integration may
have added.
`send_default_pii` is `false`, which tells the SDK not to attach the
reporting machine's IP address or username. The `debug-images` integration,
which would list every loaded library with its absolute path, is not
compiled in.

One caveat stated plainly: the panic *message* is sent verbatim. If OXIDE
ever panics on a string that happens to contain a path — an I/O error from
the standard library, for instance — that path would be in the message.
OXIDE never constructs such a message on purpose, but does not scrub free
text either, because a scrubbed message would make most reports useless.

## What OXIDE never intentionally collects

Whether telemetry is on or off, OXIDE never sends:

- the contents of any file in your repository, or any source code;
- repository paths, file paths, or file names from your machine;
- queries, tasks, or search terms you type;
- symbol names, signatures, or snippets from your index;
- embeddings or any other derived index data;
- MCP traffic — requests, responses, or tool arguments from a coding agent;
- environment variables (including `OXIDE_EMBED_URL` and `OXIDE_EMBED_MODEL`);
- coding-agent configuration files that `oxide install` reads or edits;
- usage counts, command frequency, timings, or any analytics.

## Sentry's role

Sentry (sentry.io) is the crash-reporting service the opt-in reports go to.
OXIDE uses the `sentry` Rust crate with the `panic`, `backtrace`, and
`contexts` integrations and the `ureq`/`rustls` transport (no system TLS
library). Sentry receives the report over HTTPS; like any HTTPS server it
observes the IP address of the connection, which is governed by
[Sentry's privacy policy](https://sentry.io/privacy/). OXIDE asks Sentry
not to record that address on the event, and reports carry no identifier
that links two crashes from the same machine.

The project DSN (the write-only address reports are posted to) is embedded
in the binary. A DSN cannot be used to read reports.

## Auditing this yourself

1. **Read the implementation.** `src/telemetry.rs` is the whole thing; the
   only other reference is the one call in `src/main.rs`. `grep -rn sentry
   src/` should show nothing else.
2. **Watch the wire.** `OXIDE_TELEMETRY_DSN` overrides where reports go.
   Point it at a local listener (`nc -l 127.0.0.1 8765` with
   `OXIDE_TELEMETRY_DSN=http://key@127.0.0.1:8765/1`) and run whatever you
   like: nothing connects unless `OXIDE_TELEMETRY` is on *and* the process
   panics. The same override lets you route reports to your own Sentry
   instance instead of the project's.
3. **Run the regression test.** `cargo test --test telemetry` runs a suite
   of ordinary commands against exactly such a listener with telemetry
   unset and explicitly off, and fails if anything connects.
   `cargo test --lib telemetry` includes the positive control: once
   enabled, a real panic does reach the listener, and the report contains
   no hostname, username, or build path.
4. **Build without it.** Telemetry is never on unless the environment says
   so, but if you would rather not ship the client code at all, the
   `sentry` dependency and `src/telemetry.rs` are self-contained enough to
   remove in one commit.
