import { readdirSync, readFileSync } from "node:fs";

/** The committed Rust fixtures; `tests/protocol_fixtures.rs` owns them. */
const dir = new URL("../../../fixtures/protocol/", import.meta.url);

export const fixtureNames: string[] = readdirSync(dir)
  .filter((file) => file.endsWith(".json"))
  .map((file) => file.slice(0, -".json".length))
  .sort();

export function fixture(name: string): unknown {
  return JSON.parse(readFileSync(new URL(`${name}.json`, dir), "utf8"));
}
