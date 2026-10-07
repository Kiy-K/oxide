// Control-plane boundary (docs/spec/SPEC.md § TypeScript control plane): root
// src/ holds only regular TypeScript files, and they import only Bun/Node
// built-ins, other files inside src/, and the shared contract cases.
// Repository intelligence stays in Rust (oxide_kernel/); adding an import here
// needs a SPEC-backed reason.

import { expect, test } from "bun:test";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, resolve, sep } from "node:path";

const SRC = import.meta.dir;
const CASES = resolve(SRC, "../oxide_kernel/contract/cases.json");

const entries = readdirSync(SRC, { recursive: true, withFileTypes: true }).map((entry) => ({
  path: `${entry.parentPath}/${entry.name}`,
  entry,
}));

const transpiler = new Bun.Transpiler({ loader: "ts" });

/** Import violations in `text` (the contents of `file`). */
function violations(file: string, text: string): string[] {
  const found: string[] = [];
  // Parsed, not pattern-matched: static, side-effect, re-export, literal
  // dynamic imports and require() calls, never text inside strings/comments.
  for (const { path: specifier } of transpiler.scanImports(text)) {
    if (/^(bun|node):/.test(specifier)) continue;
    const target = resolve(dirname(file), specifier);
    const local = target.startsWith(SRC + sep);
    if (!specifier.startsWith(".") || !(local || target === CASES)) {
      found.push(`${file} imports ${specifier}`);
    }
  }
  // A computed specifier cannot be checked statically, and require() is not
  // how this ESM code loads modules. Bun prints every static import argument
  // as a plain string, so any other `import(` argument is computed; any use
  // of the `require` identifier fails, however it is called.
  const code = codeOnly(transpiler.transformSync(text));
  for (const [use] of code.matchAll(/\bimport\s*\((?!\s*""\s*[,)])|\brequire\b/g)) {
    found.push(`${file} uses ${use}`);
  }
  return found;
}

/**
 * Comment-free transpiled code with the text of strings, template literals and
 * regex literals removed, keeping template `${...}` expressions as code, so
 * only real syntax is matched.
 */
function codeOnly(js: string): string {
  let out = "";
  let depth = 0;
  const substitutions: number[] = []; // brace depth at each open `${`
  for (let i = 0; i < js.length; i++) {
    const c = js[i] ?? "";
    if (c === '"' || c === "'") {
      for (i++; i < js.length && js[i] !== c; i++) if (js[i] === "\\") i++;
      out += '""';
    } else if (c === "`" || (c === "}" && substitutions.at(-1) === depth)) {
      if (c === "}") substitutions.pop();
      for (i++; i < js.length && js[i] !== "`" && !(js[i] === "$" && js[i + 1] === "{"); i++) {
        if (js[i] === "\\") i++;
      }
      if (js[i] === "$") {
        substitutions.push(depth);
        i++; // the `{` of `${`
        out += "`${";
      } else {
        out += "``";
      }
    } else if (c === "/" && /(^|[(,=:[!&|?{};]|\breturn)\s*$/.test(out)) {
      let inClass = false;
      for (i++; i < js.length && (inClass || js[i] !== "/"); i++) {
        if (js[i] === "\\") i++;
        else if (js[i] === "[") inClass = true;
        else if (js[i] === "]") inClass = false;
      }
      while (/[a-z]/.test(js[i + 1] ?? "")) i++;
      out += "/r/";
    } else {
      if (c === "{") depth++;
      if (c === "}") depth--;
      out += c;
    }
  }
  return out;
}

test("src/ contains only regular TypeScript files", () => {
  const files = entries.filter(({ entry }) => !entry.isDirectory());
  expect(files.length).toBeGreaterThan(0);
  // Symlinks and other non-regular entries fail too: they could point outside src/.
  expect(
    files.filter(({ path, entry }) => !entry.isFile() || !path.endsWith(".ts")).map((f) => f.path),
  ).toEqual([]);
});

test("src/ imports only built-ins, src/ modules and the contract cases", () => {
  const found = entries
    .filter(({ entry }) => entry.isFile())
    .flatMap(({ path }) => violations(path, readFileSync(path, "utf8")));
  expect(found).toEqual([]);
});

test("the import scan catches known bypasses", () => {
  const file = `${SRC}/x.ts`;
  for (const bad of [
    'import "@oxide/protocol";',
    'import x from "@oxide/client";',
    'export * from "../packages/protocol/src/index.ts";',
    'import x from "./../tests/legacy.ts";',
    'import y from "../oxide_kernel/contract/../crates/x";',
    'const m = await import("@oxide/mcp");',
    "const m = await import(name);",
    'const p = require("./contract.ts");',
    "export const load = (n: string) => import(`@oxide/${n}`);",
    'export const load = (n: string) => import("@oxide/" + n);',
    'const r = (0, require)("@oxide/client");',
    'const r = globalThis.require?.("@oxide/client");',
    'const s = `it\'s ${require("@oxide/client")}`;',
  ]) {
    expect(violations(file, bad)).not.toEqual([]);
  }
  for (const good of [
    'import { test } from "bun:test";',
    'import { readFileSync } from "node:fs";',
    'import { call } from "./transport.ts";',
    'import cases from "../oxide_kernel/contract/cases.json";',
    'const m = await import("./contract.ts");',
    "const doc = 'call require(x) or import(name) here';",
    "const re = /[\"'`]import\\(/g; const dir = import.meta.dir;",
    "const s = `${1 + 2} ok`;",
  ]) {
    expect(violations(file, good)).toEqual([]);
  }
});
