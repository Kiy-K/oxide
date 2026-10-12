// Public client: behavior over fake transports, error mapping, cancellation,
// unsupported operations, lifecycle, the public export surface, and getStatus
// through the real oxide-runtime (`mise run ts:test` builds it).

import { expect, test } from "bun:test";
import { existsSync } from "node:fs";
import { clientOver, createClient, type OxideClient, OxideError } from "./client.ts";
import { decodeResponse, type Request, type Response } from "./contract.ts";
import { type Transport, TransportError } from "./transport.ts";

const bin =
  process.env.OXIDE_RUNTIME_BIN ??
  `${process.env.CARGO_TARGET_DIR ?? `${import.meta.dir}/../oxide_kernel/target`}/debug/oxide-runtime`;
if (!existsSync(bin)) {
  throw new Error(`no oxide-runtime at ${bin}: run \`mise run ts:test\` or set OXIDE_RUNTIME_BIN`);
}

const ok = (id: string): Response => ({
  version: 1,
  id,
  ok: true,
  result: { kernel_version: "9.9.9" },
});

/** A transport answering with `reply`, recording requests and closes. */
function fake(reply: (request: Request, signal?: AbortSignal) => Promise<Response> | Response) {
  const sent: Request[] = [];
  let closes = 0;
  const transport: Transport = {
    send: async (request, signal) => {
      sent.push(request);
      return reply(request, signal);
    },
    close: async () => {
      closes++;
    },
  };
  return { transport, sent, closes: () => closes };
}

/** Never answers; rejects with the signal's reason when it aborts. */
const hang = (_: Request, signal?: AbortSignal) =>
  new Promise<Response>((_, reject) =>
    signal?.addEventListener("abort", () => reject(signal.reason), { once: true }),
  );

async function failure(promise: Promise<unknown>): Promise<OxideError> {
  const err = await promise.then(
    () => {
      throw new Error("expected a rejection");
    },
    (e: unknown) => e,
  );
  expect(err).toBeInstanceOf(OxideError);
  return err as OxideError;
}

const repo = { id: "r", root: "/repo" };

test("getStatus maps the runtime answer to the public shape", async () => {
  const { transport, sent } = fake((r) => ok(r.id));
  const status = await clientOver(transport).getStatus();
  expect(status).toEqual({
    apiVersion: 1,
    protocolVersion: 1,
    kernelVersion: "9.9.9",
    operations: ["getStatus"],
  });
  expect(sent).toEqual([{ version: 1, id: expect.any(String), op: "status" }]);
});

test("every request gets its own id", async () => {
  const { transport, sent } = fake((r) => ok(r.id));
  const c = clientOver(transport);
  await Promise.all([c.getStatus(), c.getStatus()]);
  expect(sent[0]?.id).not.toBe(sent[1]?.id);
});

test("replacing the transport does not change the public API", async () => {
  await using real = createClient({ runtimeBin: bin });
  const replaced = clientOver(fake((r) => ok(r.id)).transport);
  const [a, b] = await Promise.all([real.getStatus(), replaced.getStatus()]);
  expect(Object.keys(a).sort()).toEqual(Object.keys(b).sort());
  expect(a).toMatchObject({ apiVersion: 1, protocolVersion: 1, kernelVersion: "0.0.0" });
});

test.each([
  ["unknown_operation", "capability", "unsupported_operation"],
  ["unsupported_version", "protocol", "unsupported_version"],
  ["invalid_request", "protocol", "invalid_request"],
] as const)("contract error %s maps to %s/%s", async (wire, category, code) => {
  const { transport, sent } = fake((r) => ({
    version: 1,
    id: r.id,
    ok: false,
    error: { code: wire, message: "m" },
  }));
  const err = await failure(clientOver(transport).getStatus());
  expect(err).toMatchObject({ category, code, message: "m", requestId: sent[0]?.id });
});

test("a contract error with an unread id still belongs to the request", async () => {
  const { transport, sent } = fake(() => ({
    version: 1,
    id: null,
    ok: false,
    error: { code: "invalid_request", message: "too big" },
  }));
  const err = await failure(clientOver(transport).getStatus());
  expect(err).toMatchObject({ category: "protocol", requestId: sent[0]?.id });
});

test("transport failures map to transport, keeping the cause", async () => {
  const cause = new TransportError("oxide-runtime exited 1");
  const thrown = fake(() => {
    throw cause;
  });
  expect(await failure(clientOver(thrown.transport).getStatus())).toMatchObject({
    category: "transport",
    code: "runtime_unavailable",
    cause,
  });
  const other = fake(() => {
    throw new Error("socket reset");
  });
  expect(await failure(clientOver(other.transport).getStatus())).toMatchObject({
    category: "transport",
    code: "transport_failed",
  });
  const missing = await failure(createClient({ runtimeBin: "/nonexistent" }).getStatus());
  expect(missing).toMatchObject({ category: "transport", code: "runtime_unavailable" });
});

test("malformed and uncorrelated responses map to protocol", async () => {
  const malformed = fake(() => decodeResponse({ version: 1, id: "x", ok: true, result: {} }));
  expect(await failure(clientOver(malformed.transport).getStatus())).toMatchObject({
    category: "protocol",
    code: "malformed_response",
  });
  const uncorrelated = fake(() => ok("someone-else"));
  expect(await failure(clientOver(uncorrelated.transport).getStatus())).toMatchObject({
    category: "protocol",
    code: "malformed_response",
  });
});

test.each([
  ["openRepository", (c: OxideClient) => c.openRepository("/repo")],
  ["indexRepository", (c: OxideClient) => c.indexRepository(repo)],
  ["search", (c: OxideClient) => c.search(repo, { query: "q" })],
  ["buildContext", (c: OxideClient) => c.buildContext(repo, { query: "q", budget: { tokens: 1 } })],
] as const)("%s is unsupported and never reaches the runtime", async (op, run) => {
  const { transport, sent } = fake((r) => ok(r.id));
  const err = await failure(run(clientOver(transport)));
  expect(err).toMatchObject({
    category: "capability",
    code: "unsupported_operation",
    requestId: null,
  });
  expect(err.message).toContain(op);
  expect(sent).toEqual([]);
});

test("an aborted call rejects with the signal's reason", async () => {
  const { transport, sent } = fake(hang);
  const c = clientOver(transport);
  await expect(c.getStatus({ signal: AbortSignal.abort() })).rejects.toHaveProperty(
    "name",
    "AbortError",
  );
  await expect(
    c.search(repo, { query: "q" }, { signal: AbortSignal.abort() }),
  ).rejects.toHaveProperty("name", "AbortError");
  expect(sent).toEqual([]);
  const controller = new AbortController();
  const pending = c.getStatus({ signal: controller.signal });
  const reason = new Error("caller gave up");
  controller.abort(reason);
  await expect(pending).rejects.toBe(reason);
});

test("a result that arrives after abort is discarded", async () => {
  const controller = new AbortController();
  const { transport } = fake((r) => {
    controller.abort();
    return ok(r.id);
  });
  await expect(
    clientOver(transport).getStatus({ signal: controller.signal }),
  ).rejects.toHaveProperty("name", "AbortError");
});

test("aborting a real runtime call kills it", async () => {
  await using c = createClient({ runtimeBin: bin });
  const controller = new AbortController();
  const pending = c.getStatus({ signal: controller.signal });
  controller.abort();
  await expect(pending).rejects.toHaveProperty("name", "AbortError");
});

test("close is idempotent, releases the transport once and fails later calls", async () => {
  const f = fake((r) => ok(r.id));
  const c = clientOver(f.transport);
  await Promise.all([c.close(), c.close()]);
  await c.close();
  expect(f.closes()).toBe(1);
  expect(await failure(c.getStatus())).toMatchObject({ category: "closed", requestId: null });
  expect(await failure(c.openRepository("/repo"))).toMatchObject({ category: "closed" });
  expect(f.sent).toEqual([]);
});

test("close rejects in-flight calls as closed", async () => {
  const f = fake(hang);
  const c = clientOver(f.transport);
  const pending = c.getStatus();
  await c.close();
  expect(await failure(pending)).toMatchObject({ category: "closed", requestId: f.sent[0]?.id });
});

test("await using closes the client", async () => {
  const f = fake((r) => ok(r.id));
  {
    await using c = clientOver(f.transport);
    await c.getStatus();
  }
  expect(f.closes()).toBe(1);
});

test("the public entry exports only the public API", async () => {
  expect(Object.keys(await import("./index.ts")).sort()).toEqual([
    "API_VERSION",
    "OxideError",
    "createClient",
  ]);
});
