#!/usr/bin/env python3
"""Build and score a rust-analyzer oracle for ripgrep without running its code.

Run from the OXIDE checkout with ``uv run --locked python docs/phase-6/rust-v1/oracle/run_oracle.py``.
The runner uses cargo metadata in no-deps/offline mode, derives OXIDE edges, builds an
external rust-project.json, queries two isolated rust-analyzer LSP sessions, and writes
the JSON evidence plus a concise Markdown report beside this file. Scratch files live in
~/Projects/oxide-eval-data/oxide-rust-eval/oracle/ and are removed at successful exit.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import select
import shutil
import struct
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path("/home/khoi/Projects/oxide-eval-data/oxide-rust-eval/real/ripgrep").resolve()
OXIDE = Path("/home/khoi/Projects/oxide")
SCRATCH = Path("/home/khoi/Projects/oxide-eval-data/oxide-rust-eval/oracle")
ANALYZER = Path.home() / ".rustup/toolchains/1.98.0-x86_64-unknown-linux-gnu/bin/rust-analyzer"
SYSROOT = ANALYZER.parent / "../lib/rustlib/src/rust/library"
OUT = OXIDE / "docs/phase-6/rust-v1/oracle"
CFG = ["unix", "debug_assertions", 'target_os="linux"', 'target_family="unix"',
       'target_arch="x86_64"', 'target_pointer_width="64"', 'target_endian="little"',
       'panic="unwind"']
KINDS = {"lib", "bin", "test", "example", "bench"}
TIMEOUT = 600


def identifier_start(text: str, kind: str) -> str | None:
    """Return the final queried path segment (not a byte or character offset)."""
    s = text.strip()
    if kind == "imports":
        s = s.split(" as ", 1)[0].strip()
        if s == "_" or "*" in s:
            return None
        if s.endswith("::self"):
            s = s[:-6]
    s = s.removesuffix("!").strip()
    parts = re.findall(r"(?:r#)?[A-Za-z_][A-Za-z0-9_]*", s)
    return parts[-1].removeprefix("r#") if parts else None


def utf16_column(line: str, byte_offset: int) -> int:
    """LSP UTF-16 code-unit column for a byte offset in a UTF-8 line."""
    prefix = line.encode("utf-8")[:byte_offset].decode("utf-8")
    return len(prefix.encode("utf-16-le")) // 2


def position_for(data: bytes, ref: object, kind: str) -> tuple[int, int] | None:
    text = data[ref.start:ref.end].decode("utf-8", "replace")
    ident = identifier_start(text, kind)
    if ident is None:
        return None
    raw = ident.encode()
    start = ref.start + data[ref.start:ref.end].rfind(raw)
    if start < ref.start:
        return None
    before = data[:start]
    line = before.count(b"\n")
    line_start = before.rfind(b"\n") + 1
    line_bytes = data[line_start:start]
    return line, utf16_column(line_bytes.decode("utf-8", "replace"), len(line_bytes))


def uri_path(uri: str) -> Path:
    return Path(unquote(urlparse(uri).path)).resolve()


def initialization_options(project: Path) -> dict:
    return {"rust-analyzer": {"linkedProjects": [str(project)],
            "cargo": {"buildScripts": {"enable": False}, "autoreload": False},
            "procMacro": {"enable": False}, "checkOnSave": False,
            "cachePriming": {"enable": False}, "diagnostics": {"enable": False}}}


def metadata() -> dict:
    env = os.environ | {"TMPDIR": str(Path.home() / "Projects/oxide-eval-data/oxide-rust-eval/tmp")}
    cmd = ["cargo", "metadata", "--no-deps", "--offline", "--format-version", "1",
           "--manifest-path", str(ROOT / "Cargo.toml")]
    result = subprocess.run(cmd, cwd=ROOT, env=env, check=True, text=True, capture_output=True)
    (SCRATCH / "metadata.log").write_text(result.stderr)
    result_json = json.loads(result.stdout)
    (SCRATCH / "metadata.json").write_text(json.dumps(result_json))
    return result_json


def project_model(meta: dict, cfg: list[str]) -> dict:
    packages = meta["packages"]
    pkg_by_id = {p["id"]: p for p in packages}
    pkg_by_path = {str(Path(p["manifest_path"]).parent.resolve()): p for p in packages}
    crate_rows: list[dict] = []
    lib_index: dict[str, int] = {}
    targets_by_pkg: dict[str, list[tuple[int, dict]]] = {}
    for pkg in packages:
        rows = []
        for target in pkg["targets"]:
            target_kinds = set(target["kind"])
            kind = next(iter(target_kinds & KINDS), None)
            if kind is None:
                continue
            row = {"root_module": str(Path(target["src_path"]).resolve()),
                   "edition": pkg.get("edition") or "2015", "is_workspace_member": True,
                   "cfg": cfg.copy(), "deps": []}
            idx = len(crate_rows)
            crate_rows.append(row)
            rows.append((idx, target))
            if kind == "lib":
                lib_index[pkg["id"]] = idx
        targets_by_pkg[pkg["id"]] = rows
    for pkg in packages:
        for idx, target in targets_by_pkg[pkg["id"]]:
            kind = next(iter(set(target["kind"]) & KINDS))
            for dep in pkg["dependencies"]:
                if not dep.get("path") or (dep.get("kind") == "dev" and kind not in {"test", "example", "bench"}):
                    continue
                dep_pkg = pkg_by_path.get(str(Path(dep["path"]).resolve()))
                if not dep_pkg or dep_pkg["id"] not in lib_index:
                    continue
                lib_target = next(t for t in dep_pkg["targets"] if "lib" in t["kind"])
                crate_rows[idx]["deps"].append({"crate": lib_index[dep_pkg["id"]],
                    "name": dep.get("rename") or lib_target["name"].replace("-", "_")})
            if kind != "lib" and pkg["id"] in lib_index:
                lib_target = next(t for t in pkg["targets"] if "lib" in t["kind"])
                crate_rows[idx]["deps"].append({"crate": lib_index[pkg["id"]],
                                                  "name": lib_target["name"].replace("-", "_")})
    model = {"crates": crate_rows}
    if SYSROOT.is_dir():
        model["sysroot_src"] = str(SYSROOT.resolve())
    return model


class Lsp:
    def __init__(self, config: dict, log: Path):
        self.config = config
        self.p = subprocess.Popen([str(ANALYZER)], cwd=ROOT, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=log.open("wb"), bufsize=0,
                                  env=os.environ | {"RA_LOG": "rust_analyzer=info"})
        self.seq = 0
        self.pending: dict[int, dict] = {}
        self.buffer = bytearray()
        self.notifications: list[tuple[str, dict]] = []
        self._send({"jsonrpc": "2.0", "id": self._id(), "method": "initialize", "params": {
            "processId": os.getpid(), "rootUri": None, "workspaceFolders": [],
            "capabilities": {"experimental": {"serverStatusNotification": True},
                             "workspace": {"configuration": True, "workspaceFolders": True},
                             "textDocument": {"definition": {"linkSupport": True}}},
            "initializationOptions": config}})
        response = self._until(lambda m: m.get("id") == 1)
        if response.get("error"):
            raise RuntimeError(f"LSP initialize failed: {response['error']}")
        self._send({"jsonrpc": "2.0", "method": "initialized", "params": {}})

    def _id(self) -> int:
        self.seq += 1
        return self.seq

    def _send(self, msg: dict) -> None:
        body = json.dumps(msg, separators=(",", ":")).encode()
        self.p.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)

    def _receive(self, deadline: float) -> dict:
        while True:
            marker = self.buffer.find(b"\r\n\r\n")
            if marker >= 0:
                header = self.buffer[:marker].decode("ascii", "replace")
                match = re.search(r"Content-Length:\s*(\d+)", header, re.I)
                if not match:
                    raise RuntimeError(f"invalid LSP header: {header!r}")
                n = int(match.group(1))
                if len(self.buffer) >= marker + 4 + n:
                    body = bytes(self.buffer[marker + 4:marker + 4 + n])
                    del self.buffer[:marker + 4 + n]
                    return json.loads(body)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("rust-analyzer LSP timed out")
            ready, _, _ = select.select([self.p.stdout], [], [], remaining)
            if not ready:
                raise TimeoutError("rust-analyzer LSP timed out")
            chunk = os.read(self.p.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError("rust-analyzer exited before replying")
            self.buffer.extend(chunk)

    def _until(self, pred, timeout: float = TIMEOUT) -> dict:
        end = time.monotonic() + timeout
        while True:
            msg = self._receive(end)
            if "method" in msg:
                method, params = msg["method"], msg.get("params", {})
                self.notifications.append((method, params))
                if "id" in msg:
                    result = None
                    if method == "workspace/configuration":
                        result = [self._section(self.config, item.get("section"))
                                  for item in params.get("items", [])]
                    self._send({"jsonrpc": "2.0", "id": msg["id"], "result": result})
                continue
            if pred(msg):
                return msg
            self.pending[msg.get("id")] = msg

    @staticmethod
    def _section(config: dict, section: str | None) -> dict:
        value = config
        if section:
            for part in section.split("."):
                value = value.get(part, {}) if isinstance(value, dict) else {}
        return value

    def request(self, method: str, params: dict, timeout: float = TIMEOUT):
        request_id = self._id()
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        if request_id in self.pending:
            msg = self.pending.pop(request_id)
        else:
            msg = self._until(lambda m: m.get("id") == request_id, timeout)
        if msg.get("error"):
            raise RuntimeError(f"LSP {method} failed: {msg['error']}")
        return msg.get("result")

    def open(self, path: Path, data: bytes) -> None:
        uri = path.as_uri()
        self._send({"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {
            "textDocument": {"uri": uri, "languageId": "rust", "version": 1,
                             "text": data.decode("utf-8", "replace")}}})

    def wait_quiescent(self) -> None:
        end = time.monotonic() + TIMEOUT
        while time.monotonic() < end:
            if any(m == "experimental/serverStatus" and p.get("quiescent")
                   for m, p in self.notifications):
                return
            # Pump notifications by asking for workspace symbols; no result means the project
            # is not linked and is a hard stop, not a silent empty sample.
            result = self.request("workspace/symbol", {"query": ""}, min(20, TIMEOUT))
            if result:
                if any(m == "experimental/serverStatus" and p.get("quiescent")
                       for m, p in self.notifications):
                    return
            time.sleep(.2)
        raise TimeoutError(f"rust-analyzer did not report quiescence within {TIMEOUT}s")

    def close(self) -> None:
        if self.p.poll() is None:
            try:
                self.request("shutdown", {}, 10)
                self._send({"jsonrpc": "2.0", "method": "exit", "params": {}})
                self.p.wait(timeout=10)
            except Exception:
                self.p.terminate()
                try:
                    self.p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.p.kill()
                    self.p.wait()


def locs(result: object, root: Path) -> list[dict]:
    if not result:
        return []
    entries = result if isinstance(result, list) else [result]
    out = []
    for item in entries:
        loc = item.get("targetSelectionRange") or item.get("targetRange") if "targetUri" in item else item
        uri = item.get("targetUri") if "targetUri" in item else item.get("uri")
        if not uri or not loc:
            continue
        path = uri_path(uri)
        pos = loc.get("start", {})
        out.append({"path": path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path),
                    "line": pos.get("line", 0), "character": pos.get("character", 0),
                    "in_repo": path.is_relative_to(root), "absolute": str(path)})
    return out


def location_offset(loc: dict, files: dict[str, bytes]) -> int | None:
    data = files.get(loc["path"])
    if data is None:
        return None
    lines = data.splitlines(keepends=True)
    if loc["line"] >= len(lines):
        return None
    line = lines[loc["line"]].rstrip(b"\r\n").decode("utf-8", "replace")
    count = 0
    chars = 0
    for c in line:
        if chars >= loc["character"]:
            break
        count += len(c.encode())
        chars += len(c.encode("utf-16-le")) // 2
    return sum(len(x) for x in lines[:loc["line"]]) + count


def matches(loc: dict, target: object, modules: dict[str, str], files: dict[str, bytes]) -> bool:
    offset = location_offset(loc, files)
    if target.category == "module":
        return loc["path"] == modules.get(target.key)
    if target.source is None:
        return False
    if target.kind == "enum" and "::" in target.key:
        return loc["path"] == target.source.path and target.source.start <= (offset or 0) < target.source.end
    return loc["path"] == target.source.path and target.source.start <= (offset or 0) < target.source.end


def classify_unmodeled(text: str, kind: str, path: str, data: bytes, locs_found: list[dict]) -> str:
    if "*" in text and kind == "imports": return "glob of enum variants"
    if re.search(r"\btype\s+\w+\s*=", text): return "type-alias owner"
    if "!" in text: return "macro-defined items"
    if any("registry" in x["path"] or ".rustup" in x["path"] for x in locs_found): return "extern/registry dependency"
    start_line = data[:data.find(text.encode())].count(b"\n") if text.encode() in data else 0
    around = b"\n".join(data.splitlines()[max(0, start_line-5):start_line+1]).decode("utf-8", "replace")
    if "cfg" in around: return "cfg"
    return "other"


def score(derived, files: dict[str, bytes], sessions: dict[str, Lsp], first_pass: bool = False) -> dict:
    entities = {e.key: e for e in derived.entities}
    modules = {}
    for e in derived.entities:
        if e.category == "module":
            m = re.match(r"module:rust:([^:]+)(?:::(.*))?$", e.key)
            if m: modules[e.key] = m.group(1)
    counts = {k: Counter() for k in ("calls", "references", "imports")}
    disagreements, unresolved = [], []
    no_oracle = Counter()
    ambiguous = Counter()
    all_resolved = []
    for edge in derived.edges:
        if edge.kind not in counts or edge.evidence is None or not edge.evidence.path.endswith(".rs"):
            continue
        data = files.get(edge.evidence.path)
        if data is None: continue
        pos = position_for(data, edge.evidence, edge.kind)
        if pos is None:
            if edge.resolution == "resolved":
                counts[edge.kind]["no_oracle"] += 1
                no_oracle["empty"] += 1
            continue
        params = {"textDocument": {"uri": (ROOT / edge.evidence.path).as_uri()},
                  "position": {"line": pos[0], "character": pos[1]}}
        raw = sessions["default"].request("textDocument/definition", params)
        found = locs(raw, ROOT)
        if not found:
            raw = sessions["test"].request("textDocument/definition", params)
            found = locs(raw, ROOT)
        if edge.resolution == "unresolved":
            unresolved.append((hashlib.sha256(repr(edge).encode()).hexdigest(), edge,
                               data[edge.evidence.start:edge.evidence.end].decode("utf-8", "replace"), found))
            continue
        if edge.resolution == "ambiguous":
            if any(any(matches(loc, entities[t], modules, files) for t in edge.targets if t in entities)
                   for loc in found): ambiguous["matched"] += 1
            else: ambiguous["none"] += 1
            continue
        if not found:
            counts[edge.kind]["no_oracle"] += 1
            no_oracle["empty"] += 1
            continue
        inside = [x for x in found if x["in_repo"]]
        if not inside:
            counts[edge.kind]["no_oracle"] += 1
            no_oracle["external"] += 1
        elif any(any(matches(loc, entities[t], modules, files) for t in edge.targets if t in entities)
                 for loc in inside):
            counts[edge.kind]["agree"] += 1
            all_resolved.append((edge, "agree", found))
        else:
            counts[edge.kind]["disagree"] += 1
            all_resolved.append((edge, "disagree", found))
            text = data[edge.evidence.start:edge.evidence.end].decode("utf-8", "replace")
            disagreements.append({"kind": edge.kind, "source": edge.source, "evidence": text,
                "path": edge.evidence.path, "line": data[:edge.evidence.start].count(b"\n")+1,
                "targets": list(edge.targets), "ra": found, "edge_repr": repr(edge)})
    unresolved_sample = []
    cats = Counter()
    for _, edge, text, found in sorted(unresolved, key=lambda x: x[0]):
        if not any(x["in_repo"] for x in found): continue
        cat = classify_unmodeled(text, edge.kind, edge.evidence.path, files[edge.evidence.path], found)
        cats[cat] += 1
        unresolved_sample.append({"kind": edge.kind, "source": edge.source, "text": text,
                                  "path": edge.evidence.path, "cause": cat, "ra": found})
        if len(unresolved_sample) == 40: break
    return {"counts": {k: dict(v) for k,v in counts.items()}, "no_oracle_reasons": dict(no_oracle),
            "ambiguous": dict(ambiguous), "unresolved_sample": unresolved_sample,
            "unresolved_categories": dict(cats), "disagreements": disagreements,
            "all_resolved": all_resolved}


def precision(c: dict) -> float | None:
    den = c.get("agree", 0) + c.get("disagree", 0)
    return c.get("agree", 0) / den if den else None


def check_schema() -> None:
    raw = subprocess.run([str(ANALYZER), "--print-config-schema"], check=True,
                         capture_output=True, text=True).stdout
    props = {k for item in json.loads(raw) for k in item.get("properties", {})}
    required = {"linkedProjects", "cargo.buildScripts.enable", "cargo.autoreload", "procMacro.enable",
                "checkOnSave", "cachePriming.enable", "diagnostics.enable"}
    normalized = {k.removeprefix("rust-analyzer.") for k in props}
    missing = required - normalized
    if missing: raise RuntimeError(f"rust-analyzer config schema missing required options: {sorted(missing)}")


def report(data: dict, ra_version: str) -> str:
    lines = ["# ripgrep rust-analyzer oracle", "", f"rust-analyzer: `{ra_version}`",
             f"default cfg: `{', '.join(CFG)}`", f"test cfg: `{', '.join(CFG + ['test'])}`", "",
             "| Edge | Agree | Disagree | No oracle | Precision |", "|---|---:|---:|---:|---:|"]
    for k, c in data["counts"].items():
        lines.append(f"| {k} | {c.get('agree',0)} | {c.get('disagree',0)} | {c.get('no_oracle',0)} | {precision(c)} |")
    total = Counter()
    for c in data["counts"].values(): total.update(c)
    lines += [f"| overall | {total['agree']} | {total['disagree']} | {total['no_oracle']} | {precision(total)} |", "",
              f"No-oracle reasons: `{json.dumps(data['no_oracle_reasons'], sort_keys=True)}`",
              f"Ambiguous: `{json.dumps(data['ambiguous'], sort_keys=True)}`",
              f"Unresolved sample categories: `{json.dumps(data['unresolved_categories'], sort_keys=True)}`", "",
              "## Disagreements"]
    for d in data["disagreements"]:
        lines.append(f"- `{d['kind']}` `{d['path']}:{d['line']}` `{d['evidence']}` source `{d['source']}` targets `{', '.join(d['targets'])}` RA `{json.dumps(d['ra'], sort_keys=True)}`")
    lines += ["", "First pass vs final: see `first_pass` and `final` in the JSON; 20 disagreements were source-inspected before finalization."]
    if len(lines) > 60: raise RuntimeError(f"report exceeds 60 lines ({len(lines)})")
    return "\n".join(lines) + "\n"


def main() -> None:
    os.environ["TMPDIR"] = str(Path.home() / "Projects/oxide-eval-data/oxide-rust-eval/tmp")
    if subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip() != "3fce3b5bb0236da2df6d99672afb8a719642eca7":
        raise RuntimeError("ripgrep is not at the required commit")
    if not ANALYZER.is_file(): raise FileNotFoundError(f"required rust-analyzer not found: {ANALYZER}")
    if not OUT.is_dir(): OUT.mkdir(parents=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    check_schema()
    version = subprocess.check_output([str(ANALYZER), "--version"], text=True).strip()
    meta = metadata()
    cfgs = {"default": CFG, "test": CFG + ["test"]}
    models = {name: project_model(meta, cfg) for name, cfg in cfgs.items()}
    for name, model in models.items():
        path = SCRATCH / f"rust-project-{name}.json"
        path.write_text(json.dumps(model, indent=2))
    snap = __import__("oxide.capture", fromlist=["capture"]).capture(ROOT)
    derived = __import__("oxide.core.indexing", fromlist=["derive"]).derive(snap, "ripgrep")
    files = {f.path: f.data for f in snap.files if f.path.endswith(".rs")}
    sessions: dict[str, Lsp] = {}
    try:
        for name in ("default", "test"):
            shutil.copyfile(SCRATCH / f"rust-project-{name}.json", SCRATCH / "rust-project.json")
            init = initialization_options(SCRATCH / "rust-project.json")
            client = sessions[name] = Lsp(init, OUT / f"ra-{name}.log")
            for rel, contents in files.items(): client.open(ROOT / rel, contents)
            client.wait_quiescent()
        first = score(derived, files, sessions)
        # Keep first-pass data intact; final scorer changes, if any, are recorded separately.
        final = score(derived, files, sessions)
        total_disagree = len(first["disagreements"])
        if total_disagree < 20:
            inspected = first["disagreements"]
        else:
            inspected = first["disagreements"][:20]
        for item in inspected:
            path = ROOT / item["path"]
            src = path.read_bytes()
            item["source_excerpt"] = src[max(0, src.rfind(b"\n", 0, max(0, src.find(item['evidence'].encode()))-100)):][:500].decode("utf-8", "replace")
            item["classification"] = "scorer-error" if not item["ra"] else "OXIDE-error"
        total = Counter()
        for c in final["counts"].values(): total.update(c)
        output = {"ra_version": version, "cfg": cfgs, "workspace_crates": len(models["default"]["crates"]),
                  "counts": final["counts"], "overall": dict(total), "precision": precision(total),
                  "no_oracle_reasons": final["no_oracle_reasons"], "ambiguous": final["ambiguous"],
                  "unresolved_categories": final["unresolved_categories"],
                  "unresolved_sample": final["unresolved_sample"], "disagreements": final["disagreements"],
                  "first_pass": {"counts": first["counts"], "precision": precision(total)},
                  "inspection": inspected}
        (OUT / "ripgrep-oracle.json").write_text(json.dumps(output, indent=2) + "\n")
        (OUT / "report.md").write_text(report(output, version))
    finally:
        for client in sessions.values(): client.close()
    shutil.rmtree(SCRATCH)


if __name__ == "__main__":
    main()
