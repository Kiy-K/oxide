// The decoders accept only valid version 1 responses and results.

import { expect, test } from "bun:test";
import { decodeCapabilities, decodeResponse, decodeStatus, ProtocolError } from "./contract.ts";

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

const caps = {
  protocol_version: 1,
  operations: ["status"],
  max_request_bytes: 1,
  execution: "serial",
};

test.each([
  ["status: missing field", () => decodeStatus({})],
  ["status: extra field", () => decodeStatus({ kernel_version: "0", extra: 1 })],
  ["capabilities: missing field", () => decodeCapabilities({ ...caps, execution: undefined })],
  [
    "capabilities: operations not an array",
    () => decodeCapabilities({ ...caps, operations: "status" }),
  ],
  ["capabilities: non-string operation", () => decodeCapabilities({ ...caps, operations: [1] })],
  [
    "capabilities: fractional version",
    () => decodeCapabilities({ ...caps, protocol_version: 1.5 }),
  ],
] as [string, () => unknown][])("result decoder rejects %s", (_, decode) => {
  expect(decode).toThrow(ProtocolError);
});

test("result is decoded per operation", () => {
  expect(decodeCapabilities(caps)).toEqual(caps);
  expect(decodeStatus({ kernel_version: "1.2.3" })).toEqual({ kernel_version: "1.2.3" });
});
