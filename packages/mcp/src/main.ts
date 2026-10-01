// `@oxide/mcp`: a reference TypeScript MCP server over @oxide/client. The
// Rust `oxide mcp` is canonical; this adapter serves its exact surface
// (`fixtures/mcp/surface.json`, written by tests/protocol_fixtures.rs from the
// Rust server) and runs each tool call as one `oxide` process.
//
// Environment: OXIDE_BIN (default `oxide` on PATH). Every other variable is
// passed through to `oxide` unchanged.
import { isSpecType, Server } from "@modelcontextprotocol/server";
import { serveStdio } from "@modelcontextprotocol/server/stdio";
import surface from "../../../fixtures/mcp/surface.json" with { type: "json" };
import manifest from "../package.json" with { type: "json" };
import { callTool, type Runtime } from "./tools.ts";

const listed = { tools: surface.tools };
if (!isSpecType.ListToolsResult(listed)) {
  throw new Error("fixtures/mcp/surface.json is not a valid tools/list result");
}

const runtime: Runtime = { binary: process.env.OXIDE_BIN ?? "oxide", cwd: process.cwd() };

serveStdio(() => {
  const server = new Server(
    { name: surface.serverName, version: manifest.version },
    { capabilities: { tools: {} }, instructions: surface.instructions },
  );
  server.setRequestHandler("tools/list", () => listed);
  server.setRequestHandler("tools/call", (request) =>
    callTool(request.params.name, request.params.arguments ?? {}, runtime),
  );
  return server;
});
