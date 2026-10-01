#!/usr/bin/env node
// Stand-in `oxide` for unit tests: records its argv and cwd, then replays whatever
// stdout/stderr/exit status (or signal) the test asked for via FAKE_* env.
import { readFileSync, writeFileSync } from "node:fs";

const env = process.env;
if (env.FAKE_ARGV_FILE) {
  writeFileSync(
    env.FAKE_ARGV_FILE,
    JSON.stringify({ argv: process.argv.slice(2), cwd: process.cwd() }),
  );
}
if (env.FAKE_SIGNAL) process.kill(process.pid, env.FAKE_SIGNAL);
process.stdout.write(env.FAKE_STDOUT_FILE ? readFileSync(env.FAKE_STDOUT_FILE, "utf8") : "");
process.stderr.write(env.FAKE_STDERR ?? "");
process.exitCode = Number(env.FAKE_EXIT ?? 0);
