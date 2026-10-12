// openRepository / closeRepository end to end: Bun client → persistent
// oxide-runtime → registry → LadybugDB. Repositories are indexed by the
// Phase 2B rebuild pipeline through the `prepare_repository` example
// (`mise run ts:test` builds it), in a private XDG data directory passed
// explicitly to every child: Bun children do not see process.env mutations.

import { afterAll, beforeAll, expect, test } from "bun:test";
import { existsSync, mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { clientOver, type OxideClient, OxideError } from "./client.ts";
import { RuntimeSupervisor } from "./transport.ts";

const target = process.env.CARGO_TARGET_DIR ?? `${import.meta.dir}/../oxide_kernel/target`;
const bin = process.env.OXIDE_RUNTIME_BIN ?? `${target}/debug/oxide-runtime`;
const prepareBin = process.env.OXIDE_PREPARE_BIN ?? `${target}/debug/examples/prepare_repository`;
for (const path of [bin, prepareBin]) {
  if (!existsSync(path)) {
    throw new Error(
      `no ${path}: run \`mise run ts:test\` or set OXIDE_RUNTIME_BIN/OXIDE_PREPARE_BIN`,
    );
  }
}

let base = "";
const data = () => join(base, "data");

/** The real runtime, confined to this test's data directory (`env` execs: same PID). */
const supervisor = () => new RuntimeSupervisor(["env", `XDG_DATA_HOME=${data()}`, bin]);
const client = () => clientOver(supervisor());

interface Prepared {
  repo_id: string;
  store_directory: string;
  snapshot: string | null;
}

/** A small Python checkout, registered and (unless `registerOnly`) indexed. */
function prepare(name: string, registerOnly = false): { root: string; prepared: Prepared } {
  const root = join(base, name);
  mkdirSync(join(root, "pkg"), { recursive: true });
  writeFileSync(join(root, "pkg/__init__.py"), "");
  writeFileSync(join(root, "pkg/auth.py"), `def login(user):\n    return "${name}"\n`);
  const run = Bun.spawnSync([prepareBin, root, ...(registerOnly ? ["--register-only"] : [])], {
    env: { ...process.env, XDG_DATA_HOME: data() },
  });
  if (run.exitCode !== 0) throw new Error(`prepare_repository failed: ${run.stderr}`);
  return { root: realpathSync(root), prepared: JSON.parse(run.stdout.toString()) };
}

let indexed: { root: string; prepared: Prepared };

beforeAll(() => {
  base = mkdtempSync(join(tmpdir(), "oxide-open-"));
  indexed = prepare("indexed");
  // The fixture landed in the private data directory, not the user's.
  expect(indexed.prepared.store_directory.startsWith(`${data()}/`)).toBe(true);
});

afterAll(() => {
  rmSync(base, { recursive: true, force: true });
});

async function failure(promise: Promise<unknown>): Promise<OxideError> {
  const err = await promise.then(
    () => new Error("expected a rejection"),
    (e: unknown) => e,
  );
  expect(err).toBeInstanceOf(OxideError);
  return err as OxideError;
}

function alive(pid: number | undefined): boolean {
  try {
    return pid !== undefined && process.kill(pid, 0);
  } catch {
    return false;
  }
}

test("opens an indexed repository with its published snapshot", async () => {
  await using c = client();
  expect((await c.getStatus()).operations).toEqual([
    "getStatus",
    "openRepository",
    "closeRepository",
  ]);
  const handle = await c.openRepository(indexed.root);
  expect(handle).toEqual({
    id: expect.stringMatching(/^[0-9a-f]{32}$/),
    root: indexed.root,
    snapshot: indexed.prepared.snapshot as string,
  });
  await c.closeRepository(handle);
});

test("repeated and concurrent opens reuse one session in one runtime", async () => {
  const runtime = supervisor();
  await using c = clientOver(runtime);
  const first = await c.openRepository(indexed.root);
  const pid = runtime.pid;
  const again = await Promise.all([
    c.openRepository(indexed.root),
    c.openRepository(join(indexed.root, "pkg", "..")),
    c.getStatus(),
    c.openRepository(indexed.root),
  ]);
  expect([again[0], again[1], again[3]]).toEqual([first, first, first]);
  expect(runtime.pid).toBe(pid);
});

test.each([
  ["a relative path", () => "relative/repo"],
  ["a missing path", () => join(base, "missing")],
  ["a file", () => join(indexed.root, "pkg/auth.py")],
])("%s is invalid_path", async (_, path) => {
  await using c = client();
  expect(await failure(c.openRepository(path()))).toMatchObject({
    category: "operation",
    code: "invalid_path",
  });
});

test("unregistered and never-published repositories are not_indexed", async () => {
  await using c = client();
  const stranger = join(base, "stranger");
  mkdirSync(stranger);
  expect(await failure(c.openRepository(stranger))).toMatchObject({
    category: "operation",
    code: "not_indexed",
  });
  const registered = prepare("registered", true);
  expect(registered.prepared.snapshot).toBeNull();
  expect(await failure(c.openRepository(registered.root))).toMatchObject({ code: "not_indexed" });
});

test("corrupt stored state is repository_unavailable", async () => {
  const broken = prepare("broken");
  writeFileSync(join(broken.prepared.store_directory, "CURRENT"), "garbage\n");
  await using c = client();
  expect(await failure(c.openRepository(broken.root))).toMatchObject({
    category: "operation",
    code: "repository_unavailable",
  });
});

test("a second runtime gets ownership_conflict until the owner closes", async () => {
  await using owner = client();
  await using other = client();
  const handle = await owner.openRepository(indexed.root);
  expect(await failure(other.openRepository(indexed.root))).toMatchObject({
    category: "operation",
    code: "ownership_conflict",
  });
  await owner.closeRepository(handle);
  const reopened = await other.openRepository(indexed.root);
  expect(reopened.snapshot).toBe(handle.snapshot);
  expect(reopened.id).not.toBe(handle.id);
});

test("closing the client releases its sessions for the next runtime", async () => {
  const first = client();
  const handle = await first.openRepository(indexed.root);
  await first.close();
  await using next = client();
  const reopened = await next.openRepository(indexed.root);
  expect(reopened.snapshot).toBe(handle.snapshot);
  // Handles never carry over to another runtime.
  expect(await failure(next.closeRepository(handle))).toMatchObject({
    category: "operation",
    code: "unknown_handle",
  });
});

test("closed, forged and repeated-close handles are unknown_handle", async () => {
  await using c = client();
  const handle = await c.openRepository(indexed.root);
  await c.closeRepository(handle);
  for (const stale of [handle, { ...handle, id: "0".repeat(32) }, { ...handle, id: "" }]) {
    expect(await failure(c.closeRepository(stale))).toMatchObject({ code: "unknown_handle" });
  }
});

test("a runtime crash invalidates its handles; a new runtime reopens cleanly", async () => {
  const runtime = supervisor();
  const crashed: OxideClient = clientOver(runtime);
  const handle = await crashed.openRepository(indexed.root);
  const pid = runtime.pid;
  process.kill(pid as number, "SIGKILL");
  expect(await failure(crashed.closeRepository(handle))).toMatchObject({
    category: "transport",
  });
  await crashed.close();
  expect(alive(pid)).toBe(false);
  // The OS released the dead runtime's store lock; nothing is restarted.
  await using next = client();
  expect((await next.openRepository(indexed.root)).id).not.toBe(handle.id);
  expect(await failure(next.closeRepository(handle))).toMatchObject({ code: "unknown_handle" });
});
