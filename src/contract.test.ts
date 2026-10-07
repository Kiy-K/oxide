// The decoder accepts only valid version 1 responses.

import { expect, test } from "bun:test";
import { decodeResponse, ProtocolError } from "./contract.ts";

const ok = { version: 1, id: "a", ok: true, result: { kernel_version: "0.0.0" } };

test.each([
  ["non-object", "nope"],
  ["other version", { ...ok, version: 2 }],
  ["extra key", { ...ok, extra: 1 }],
  ["missing result", { version: 1, id: "a", ok: true }],
  ["non-boolean ok", { ...ok, ok: "yes" }],
  ["unknown error code", { version: 1, id: "a", ok: false, error: { code: "boom", message: "" } }],
] as [string, unknown][])("decoder rejects %s", (_, value) => {
  expect(() => decodeResponse(value)).toThrow(ProtocolError);
});
