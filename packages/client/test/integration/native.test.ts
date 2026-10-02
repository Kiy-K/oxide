// The integration suite over the native backend (@oxide/native, built by
// `mise run native:build`). The addon reads this process's environment, so
// the suite's environment is applied here, before the first call.
import assert from "node:assert/strict";
import { test } from "node:test";
import { Oxide } from "@oxide/client";
import { env, suite, tmp } from "./suite.ts";

for (const [key, value] of Object.entries(env)) {
  if (value === undefined) delete process.env[key];
  else process.env[key] = value;
}

suite((cwd) => new Oxide({ cwd, backend: "native" }));

test("auto selects the native backend when the addon loads", () => {
  const oxide = new Oxide({ cwd: tmp });
  assert.equal(oxide.backend, "native");
  assert.equal(oxide.fallbackReason, undefined);
});

test("process-only options are rejected with the native backend", () => {
  assert.throws(() => new Oxide({ cwd: tmp, backend: "native", binary: "oxide" }), {
    name: "TypeError",
    message: "the native backend does not take binary",
  });
  assert.throws(() => new Oxide({ cwd: tmp, backend: "native", env: {}, discover: true }), {
    message: "the native backend does not take env, discover",
  });
});

test("an argument the addon cannot convert is a native client error", async () => {
  const oxide = new Oxide({ cwd: tmp, backend: "native" });
  await assert.rejects(oxide.search("x", { limit: -1 }), {
    name: "OxideClientError",
    reason: "native",
  });
});
