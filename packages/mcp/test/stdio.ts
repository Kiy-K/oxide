// A minimal MCP stdio client for tests and the bench: newline-delimited
// JSON-RPC to one server process, responses matched by id.
import { type ChildProcessWithoutNullStreams, spawn } from "node:child_process";
import { createInterface } from "node:readline";

export interface Reply {
  result?: Record<string, unknown>;
  error?: { code: number; message: string };
}

export interface Session {
  readonly child: ChildProcessWithoutNullStreams;
  /** The `initialize` result. */
  readonly initialized: Record<string, unknown>;
  request(method: string, params?: unknown): Promise<Reply>;
  call(name: string, args: Record<string, unknown>): Promise<Reply>;
  close(): Promise<void>;
}

export async function connect(
  command: string,
  args: string[],
  options: { cwd: string; env: NodeJS.ProcessEnv },
): Promise<Session> {
  const child = spawn(command, args, { ...options, stdio: ["pipe", "pipe", "pipe"] });
  const pending = new Map<number, (reply: Reply) => void>();
  createInterface({ input: child.stdout }).on("line", (line) => {
    const message = JSON.parse(line) as Reply & { id?: number };
    if (typeof message.id === "number") pending.get(message.id)?.(message);
  });
  let next = 1;
  const send = (message: object) => child.stdin.write(`${JSON.stringify(message)}\n`);
  const request = (method: string, params?: unknown) =>
    new Promise<Reply>((resolve) => {
      const id = next++;
      pending.set(id, resolve);
      send({ jsonrpc: "2.0", id, method, params });
    });
  const init = await request("initialize", {
    protocolVersion: "2025-06-18",
    capabilities: {},
    clientInfo: { name: "oxide-mcp-test", version: "0" },
  });
  if (init.result === undefined) throw new Error(`initialize failed: ${JSON.stringify(init)}`);
  send({ jsonrpc: "2.0", method: "notifications/initialized" });
  return {
    child,
    initialized: init.result,
    request,
    call: (name, args) => request("tools/call", { name, arguments: args }),
    close: () =>
      new Promise((resolve) => {
        child.once("close", () => resolve());
        child.stdin.end();
      }),
  };
}
