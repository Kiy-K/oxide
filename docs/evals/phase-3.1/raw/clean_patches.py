#!/usr/bin/env python3
"""Post-hoc cleanup: raw diff -ruN captures picked up opencode's own
node_modules cache written into .opencode/ (a runtime dependency, not an
agent edit) plus __pycache__ and harness-setup files (AGENTS.md, the
copied SKILL.md), bloating each patch to ~55MB of noise. The run
directories are gone (cleaned up per-run), so this filters the
already-captured diff text down to the sections that reflect real,
substantive changes, rather than regenerating from scratch."""
import re
from pathlib import Path

PATCH_DIR = Path(__file__).resolve().parents[1] / "patches"
EXCLUDE_PATTERNS = [
    "/node_modules/", "/__pycache__/", "/.oxide/", "/bin/oxide",
    "/.opencode/skills/", "/AGENTS.md", "/.git/",
]

SECTION_RE = re.compile(r"^(diff -ruN .*|Only in .*|Binary files .* differ)$")


def should_keep(header_line: str) -> bool:
    return not any(p in header_line for p in EXCLUDE_PATTERNS)


def clean_file(path: Path):
    text = path.read_text(errors="replace")
    lines = text.splitlines(keepends=True)
    sections = []
    current = []
    for line in lines:
        if SECTION_RE.match(line.rstrip("\n")):
            if current:
                sections.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append(current)

    kept = [s for s in sections if should_keep(s[0])]
    before_bytes = len(text.encode())
    new_text = "".join(l for s in kept for l in s)
    after_bytes = len(new_text.encode())
    path.write_text(new_text)
    return before_bytes, after_bytes


def main():
    total_before = total_after = 0
    for f in sorted(PATCH_DIR.glob("*.diff")):
        before, after = clean_file(f)
        total_before += before
        total_after += after
        print(f"{f.name}: {before} -> {after} bytes")
    print(f"TOTAL: {total_before} -> {total_after} bytes")


if __name__ == "__main__":
    main()
