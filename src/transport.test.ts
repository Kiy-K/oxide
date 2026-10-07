// Cross-language contract: every shared case goes through the real Rust
// binary (`mise run ts:test` builds it) and decodes to exactly the recorded
// response; plus the transport's own failure modes.

import { expect, test } from "bun:test";
import { existsSync } from "node:fs";
import cases from "../oxide_kernel/contract/cases.json";
import { decodeResponse } from "./contract.ts";
import { call, exchange, TransportError } from "./transport.ts";

const bin =
  process.env.OXIDE_RUNTIME_BIN ??
  `${process.env.CARGO_TARGET_DIR ?? `${import.meta.dir}/../oxide_kernel/target`}/debug/oxide-runtime`;
if (!existsSync(bin)) {
  throw new Error(`no oxide-runtime at ${bin}: run \`mise run ts:test\` or set OXIDE_RUNTIME_BIN`);
}

test.each(cases.map((c) => [c.name, c] as const))("runtime: %s", async (_, c) => {
  expect(decodeResponse(await exchange(bin, c.request))).toEqual(c.response as never);
});

test("typed call reaches the kernel stub", async () => {
  const response = await call(bin, { version: 1, id: "t", op: "status" });
  expect(response.ok).toBe(true);
});

test("abort rejects with the signal's reason, not a transport error", async () => {
  const request = { version: 1, id: "t", op: "status" } as const;
  const controller = new AbortController();
  const pending = call(bin, request, controller.signal);
  controller.abort();
  await expect(pending).rejects.toHaveProperty("name", "AbortError");
  await expect(call(bin, request, AbortSignal.abort())).rejects.toHaveProperty(
    "name",
    "AbortError",
  );
});

test("a contract error with an unreadable id is returned, not a protocol error", async () => {
  const response = await call(bin, { version: 1, id: "x".repeat(2 << 20), op: "status" });
  expect(response).toMatchObject({ ok: false, id: null, error: { code: "invalid_request" } });
});

test("missing binary is a transport error", async () => {
  await expect(exchange("/nonexistent/oxide-runtime", "{}")).rejects.toThrow(TransportError);
});
