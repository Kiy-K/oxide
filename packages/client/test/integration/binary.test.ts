// The integration suite over the process backend and the real `oxide` binary
// ($OXIDE_BIN).
import { Oxide } from "@oxide/client";
import { env, suite } from "./suite.ts";

const binary = process.env.OXIDE_BIN;
if (!binary) {
  throw new Error("OXIDE_BIN must point at an oxide binary (mise run ts:integration sets it)");
}

suite((cwd) => new Oxide({ cwd, binary, env }));
