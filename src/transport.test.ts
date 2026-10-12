// The persistent runtime: every shared contract case and the framing edge
// cases over one real `oxide-runtime` process (`mise run ts:test` builds it),
// then the supervisor against the real binary and against small stand-in
// runtimes that misbehave on purpose (crash, hang, split or bad frames).

import { afterEach, expect, test } from "bun:test";
import { existsSync } from "node:fs";
import cases from "../oxide_kernel/contract/cases.json";
import { decodeCapabilities, decodeResponse, type Request } from "./contract.ts";
import { MAX_QUEUED, RuntimeSupervisor, TransportError } from "./transport.ts";

const bin =
  process.env.OXIDE_RUNTIME_BIN ??
  `${process.env.CARGO_TARGET_DIR ?? `${import.meta.dir}/../oxide_kernel/target`}/debug/oxide-runtime`;
if (!existsSync(bin)) {
  throw new Error(`no oxide-runtime at ${bin}: run \`mise run ts:test\` or set OXIDE_RUNTIME_BIN`);
}

/** Line-protocol stand-in runtime; `mode` picks how it misbehaves. */
const FAKE = `
const mode = process.argv[1];
let seen = 0, buf = "";
const answer = (r) => JSON.stringify({ version: 1, id: r.id, ok: true, result: { kernel_version: String(seen) } }) + "\\n";
if (mode === "deaf") setInterval(() => {}, 1000); // ignores EOF
process.stdin.on("data", async (d) => {
  buf += d;
  for (let i = buf.indexOf("\\n"); i >= 0; i = buf.indexOf("\\n")) {
    const r = JSON.parse(buf.slice(0, i)); buf = buf.slice(i + 1); seen++;
    if (mode === "echo") process.stdout.write(answer(r));
    if (mode === "split") for (const part of answer(r).match(/.{1,5}|\\n/gs)) {
      process.stdout.write(part); await Bun.sleep(1);
    }
    if (mode === "twice") process.stdout.write(answer(r) + answer(r));
    if (mode === "garbage") process.stdout.write("log line on stdout\\n");
    if (mode === "crash") { console.error("boom"); process.exit(3); }
  }
});
`;
const fake = (mode: string) => [process.execPath, "-e", FAKE, mode];

const owned: RuntimeSupervisor[] = [];
function supervise(argv: string[] = [bin]): RuntimeSupervisor {
  const s = new RuntimeSupervisor(argv);
  owned.push(s);
  return s;
}
afterEach(async () => {
  await Promise.all(owned.splice(0).map((s) => s.close()));
});

let n = 0;
const status = (): Request => ({ version: 1, id: `r${n++}`, op: "status" });

function alive(pid: number | undefined): boolean {
  if (pid === undefined) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

/** Spawns the real runtime directly and reads `count` response lines. */
async function raw(writes: (string | Uint8Array)[], count: number): Promise<unknown[]> {
  const child = Bun.spawn([bin], { stdin: "pipe", stdout: "pipe", stderr: "pipe" });
  for (const w of writes) {
    child.stdin.write(w);
    await child.stdin.flush();
    await Bun.sleep(2);
  }
  await child.stdin.end();
  const out = await new Response(child.stdout).text();
  expect(await child.exited).toBe(0);
  const lines = out.split("\n");
  expect(lines.pop()).toBe("");
  expect(lines).toHaveLength(count);
  return lines.map((l) => JSON.parse(l));
}

test("every contract case answers in order over one process", async () => {
  const answers = await raw([cases.map((c) => `${c.request}\n`).join("")], cases.length);
  expect(answers.map((a) => decodeResponse(a))).toEqual(cases.map((c) => c.response as never));
});

test("frames split across writes, and blank lines, are handled", async () => {
  const req = JSON.stringify(status());
  const answers = await raw([req.slice(0, 5), req.slice(5, 20), `${req.slice(20)}\n\n`, req], 2);
  expect(answers).toMatchObject([{ ok: true }, { ok: true }]);
});

test("oversized and malformed lines are answered and the stream continues", async () => {
  const big = new Uint8Array((1 << 20) + 10).fill(0x20);
  const answers = await raw(
    [big, "\n", "{oops\n", '{"version":9,"id":"v","op":"status"}\n', JSON.stringify(status())],
    4,
  );
  expect(answers).toMatchObject([
    { id: null, error: { code: "invalid_request", message: "request exceeds 1 MiB" } },
    { id: null, error: { code: "invalid_request", message: "request is not JSON" } },
    { id: "v", error: { code: "unsupported_version" } },
    { ok: true },
  ]);
});

test("requests reuse one process, sequentially and concurrently, correlated", async () => {
  const s = supervise();
  expect(s.pid).toBeUndefined(); // started lazily
  const first = await s.send(status());
  const pid = s.pid;
  expect(alive(pid)).toBe(true);
  expect(first.ok).toBe(true);
  for (let i = 0; i < 3; i++) await s.send(status());
  const requests = Array.from(
    { length: 40 },
    (_, i): Request => ({
      version: 1,
      id: `c${i}`,
      op: i % 2 ? "status" : "capabilities",
    }),
  );
  const answers = await Promise.all(requests.map((r) => s.send(r)));
  answers.forEach((a, i) => {
    expect(a.id).toBe(`c${i}`);
    expect(a.ok && Object.keys(a.result)).toContain(i % 2 ? "kernel_version" : "operations");
  });
  expect(s.pid).toBe(pid);
});

test("an oversized request gets its null-id error and the process survives", async () => {
  const s = supervise();
  const answer = await s.send({ version: 1, id: "x".repeat(2 << 20), op: "status" });
  expect(answer).toMatchObject({ ok: false, id: null, error: { code: "invalid_request" } });
  const pid = s.pid;
  expect((await s.send(status())).ok).toBe(true);
  expect(s.pid).toBe(pid);
});

test("reported capabilities are exactly the operations the runtime answers", async () => {
  const s = supervise();
  const caps = await s.send({ version: 1, id: "k", op: "capabilities" });
  if (!caps.ok) throw new Error("capabilities failed");
  const { operations, protocol_version } = decodeCapabilities(caps.result);
  expect(protocol_version).toBe(1);
  for (const op of operations) {
    const answer = await s.send({ version: 1, id: op, op } as Request);
    expect(answer.ok).toBe(true);
  }
  for (const op of ["index", "search", "build_context", "open"]) {
    const answer = await s.send({ version: 1, id: op, op } as unknown as Request);
    expect(answer).toMatchObject({ ok: false, error: { code: "unknown_operation" } });
  }
});

test("abort before send never starts or reaches the runtime", async () => {
  const s = supervise();
  await expect(s.send(status(), AbortSignal.abort())).rejects.toHaveProperty("name", "AbortError");
  expect(s.pid).toBeUndefined();
});

test("abort while queued removes the request before it is sent", async () => {
  const s = supervise(fake("echo"));
  const controller = new AbortController();
  const a = s.send(status());
  const b = s.send(status(), controller.signal);
  controller.abort();
  const c = s.send(status());
  await expect(b).rejects.toHaveProperty("name", "AbortError");
  // The stand-in reports how many requests it has seen: b never arrived.
  expect(await a).toMatchObject({ result: { kernel_version: "1" } });
  expect(await c).toMatchObject({ result: { kernel_version: "2" } });
});

test("abort during an active request releases the caller; the runtime stays usable", async () => {
  const s = supervise();
  const controller = new AbortController();
  const active = s.send(status(), controller.signal); // idle supervisor: written at once
  controller.abort();
  await expect(active).rejects.toHaveProperty("name", "AbortError");
  const pid = s.pid;
  expect((await s.send(status())).ok).toBe(true); // the abandoned answer was dropped
  expect(s.pid).toBe(pid);
});

test("abort during a request the runtime never answers rejects promptly", async () => {
  const s = supervise(fake("hang"));
  const controller = new AbortController();
  const pending = s.send(status(), controller.signal);
  await Bun.sleep(20);
  controller.abort(new Error("gave up"));
  await expect(pending).rejects.toThrow("gave up");
});

test("a crash rejects the active and queued calls, and nothing restarts", async () => {
  const s = supervise(fake("crash"));
  const results = await Promise.allSettled([s.send(status()), s.send(status())]);
  for (const r of results) {
    expect(r.status).toBe("rejected");
    const reason = (r as PromiseRejectedResult).reason;
    expect(reason).toBeInstanceOf(TransportError);
    expect(reason.message).toMatch(/exited 3: boom/);
  }
  const pid = s.pid;
  await expect(s.send(status())).rejects.toThrow(/exited 3/);
  expect(s.pid).toBe(pid);
  expect(alive(pid)).toBe(false);
});

test("EOF without an answer rejects the pending call", async () => {
  const s = supervise(["sh", "-c", "read line"]);
  await expect(s.send(status())).rejects.toThrow(/exited 0/);
});

test("a killed real runtime is reported, not restarted", async () => {
  const s = supervise();
  await s.send(status());
  process.kill(s.pid as number, "SIGKILL");
  await expect(s.send(status())).rejects.toBeInstanceOf(TransportError);
  await expect(s.send(status())).rejects.toThrow(/SIGKILL/);
});

test("responses split into small writes are reassembled", async () => {
  const s = supervise(fake("split"));
  expect(await s.send(status())).toMatchObject({ ok: true, result: { kernel_version: "1" } });
  expect(await s.send(status())).toMatchObject({ ok: true, result: { kernel_version: "2" } });
});

test.each([
  ["garbage", /JSON/],
  ["twice", /no request pending/],
])("stdout contamination (%s) is a protocol error and stops the runtime", async (mode, why) => {
  const s = supervise(fake(mode));
  const first = s.send(status());
  if (mode === "twice") expect((await first).ok).toBe(true);
  else await expect(first).rejects.toThrow(why);
  await Bun.sleep(50);
  expect(alive(s.pid)).toBe(false);
  await expect(s.send(status())).rejects.toThrow(why);
});

test("the wait queue is bounded", async () => {
  const s = supervise(fake("hang"));
  const settled = Promise.allSettled(
    Array.from({ length: MAX_QUEUED + 1 }, () => s.send(status())),
  );
  await expect(s.send(status())).rejects.toThrow("queue is full");
  await s.close();
  expect((await settled).every((r) => r.status === "rejected")).toBe(true);
});

test("a missing binary fails as a transport error", async () => {
  const s = supervise(["/nonexistent/oxide-runtime"]);
  await expect(s.send(status())).rejects.toThrow(/cannot start/);
  await expect(s.send(status())).rejects.toThrow(/cannot start/);
});

test("close is idempotent, graceful, and leaves no child behind", async () => {
  const s = supervise();
  await s.send(status());
  const pid = s.pid;
  const pending = s.send(status()).catch((err: unknown) => err);
  const closing = s.close();
  expect(s.close()).toBe(closing);
  await closing;
  expect(await pending).toHaveProperty("message", "runtime transport is closed");
  expect(alive(pid)).toBe(false);
  await expect(s.send(status())).rejects.toThrow("closed");
  await s.close();
});

test("close kills a runtime that ignores EOF after the grace period", async () => {
  const s = supervise(fake("deaf"));
  s.send(status()).catch(() => {});
  await Bun.sleep(50);
  const pid = s.pid;
  await s.close();
  expect(alive(pid)).toBe(false);
}, 10_000);

test("close before any request spawns nothing", async () => {
  const s = supervise();
  await s.close();
  expect(s.pid).toBeUndefined();
});
