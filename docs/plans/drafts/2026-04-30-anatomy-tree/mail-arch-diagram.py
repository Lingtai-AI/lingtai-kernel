#!/usr/bin/env python3
"""Generate a living architecture diagram for the mail/email subsystem.

Parses the three source files with `ast`, extracts:
  - Classes, methods, and module-level functions (with line numbers)
  - Cross-file import and call relationships
  - Key data paths (mailbox dirs, schedule files)

Outputs a Mermaid flowchart to stdout (pipe to .md or render with mmdc).

Usage:
    python3 mail-arch-diagram.py [src_root] > diagram.md
    python3 mail-arch-diagram.py [src_root] --mermaid-only  # just the code block
    python3 mail-arch-diagram.py [src_root] --json          # raw extraction as JSON

Defaults: src_root = ../../src/  (relative to this script)
"""
from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ── Source files ────────────────────────────────────────────────────────────

MAIL_FILES = {
    "capability": {
        "path": "lingtai/core/email/__init__.py",
        "label": "Capability Layer",
        "subtitle": "EmailManager — cc/__init__.py",
    },
    "intrinsic": {
        "path": "lingtai_kernel/intrinsics/mail.py",
        "label": "Intrinsic Layer",
        "subtitle": "intrinsics/mail.py",
    },
    "transport": {
        "path": "lingtai_kernel/services/mail.py",
        "label": "Transport Layer",
        "subtitle": "services/mail.py",
    },
    "handshake": {
        "path": "lingtai_kernel/handshake.py",
        "label": "Handshake",
        "subtitle": "handshake.py",
    },
}

# ── Extraction ──────────────────────────────────────────────────────────────


@dataclass
class FuncInfo:
    name: str
    line: int
    end_line: int
    is_private: bool
    doc_brief: str  # first line of docstring, or ""
    calls: list[str] = field(default_factory=list)  # names this function calls


@dataclass
class ClassInfo:
    name: str
    line: int
    methods: list[FuncInfo]


@dataclass
class FileInfo:
    key: str  # e.g. "capability"
    path: str
    label: str
    subtitle: str
    classes: list[ClassInfo]
    functions: list[FuncInfo]  # module-level
    imports_from_others: dict[str, list[str]] = field(default_factory=dict)


def _extract_calls(node: ast.AST) -> list[str]:
    """Extract all function/method names called within a function body."""
    calls = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name):
                calls.append(child.func.id)
            elif isinstance(child.func, ast.Attribute):
                calls.append(child.func.attr)
    return calls


def _docstring_brief(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """First line of the function's docstring, truncated to 60 chars."""
    ds = ast.get_docstring(node)
    if not ds:
        return ""
    first = ds.strip().split("\n")[0].strip()
    if len(first) > 60:
        first = first[:57] + "..."
    return first


def parse_file(src_root: Path, key: str, meta: dict) -> FileInfo:
    """Parse a single source file and extract structure."""
    fpath = src_root / meta["path"]
    if not fpath.is_file():
        return FileInfo(
            key=key, path=meta["path"], label=meta["label"],
            subtitle=meta["subtitle"], classes=[], functions=[],
        )

    source = fpath.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(fpath))
    except SyntaxError:
        return FileInfo(
            key=key, path=meta["path"], label=meta["label"],
            subtitle=meta["subtitle"], classes=[], functions=[],
        )

    classes = []
    functions = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            methods = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(FuncInfo(
                        name=item.name,
                        line=item.lineno,
                        end_line=getattr(item, "end_lineno", item.lineno),
                        is_private=item.name.startswith("_"),
                        doc_brief=_docstring_brief(item),
                        calls=_extract_calls(item),
                    ))
            classes.append(ClassInfo(name=node.name, line=node.lineno, methods=methods))

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(FuncInfo(
                name=node.name,
                line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
                is_private=node.name.startswith("_"),
                doc_brief=_docstring_brief(node),
                calls=_extract_calls(node),
            ))

    # Extract imports from other mail files
    imports_from_others: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for other_key, other_meta in MAIL_FILES.items():
                if other_key == key:
                    continue
                # Check if the import module matches the other file's package path
                other_pkg = other_meta["path"].replace("/", ".").replace(".__init__", "")
                if node.module and other_pkg in node.module:
                    names = [a.name for a in node.names]
                    imports_from_others.setdefault(other_key, []).extend(names)

    return FileInfo(
        key=key, path=meta["path"], label=meta["label"],
        subtitle=meta["subtitle"], classes=classes, functions=functions,
        imports_from_others=imports_from_others,
    )


# ── Key function categorization ─────────────────────────────────────────────

SEND_PATH = [
    ("capability", "_send"),
    ("capability", "_persist_to_outbox"),
    ("intrinsic", "_mailman"),
    ("intrinsic", "_is_self_send"),
    ("intrinsic", "_persist_to_inbox"),
    ("intrinsic", "_move_to_sent"),
    ("transport", "send"),
    ("handshake", "resolve_address"),
    ("handshake", "is_agent"),
    ("handshake", "is_alive"),
]

RECEIVE_PATH = [
    ("transport", "listen"),
    ("intrinsic", "_list_inbox"),
    ("intrinsic", "_load_message"),
    ("intrinsic", "_mark_read"),
    ("capability", "_check"),
    ("capability", "_read"),
    ("capability", "_search"),
]

SCHEDULE_PATH = [
    ("capability", "_schedule_create"),
    ("capability", "_schedule_cancel"),
    ("capability", "_schedule_reactivate"),
    ("capability", "_scheduler_tick"),
    ("capability", "_scheduler_loop"),
    ("capability", "_write_schedule"),
    ("capability", "_reconcile_schedules_on_startup"),
]

IDENTITY_PATH = [
    ("capability", "_inject_identity"),
    ("capability", "_email_summary"),
    ("intrinsic", "_message_summary"),
]


def _find_func(files: dict[str, FileInfo], layer: str, name: str) -> FuncInfo | None:
    fi = files.get(layer)
    if fi is None:
        return None
    for cls in fi.classes:
        for m in cls.methods:
            if m.name == name:
                return m
    for f in fi.functions:
        if f.name == name:
            return f
    return None


def _func_id(layer: str, name: str) -> str:
    """Mermaid-safe node ID."""
    return f"{layer}_{name}"


# ── Mermaid generation ──────────────────────────────────────────────────────


def generate_mermaid(files: dict[str, FileInfo]) -> str:
    """Generate a Mermaid flowchart from extracted structure."""
    lines = ["flowchart TD"]

    # ── Subgraphs (one per source file) ──
    for key, fi in files.items():
        safe_key = key.replace("-", "_")
        lines.append(f'    subgraph {safe_key}["{fi.label}<br/><i>{fi.subtitle}</i>"]')
        lines.append(f"        direction TB")

        # Classes + their key methods
        for cls in fi.classes:
            cls_id = f"{safe_key}_{cls.name}"
            # Only show public or key private methods
            key_methods = [
                m for m in cls.methods
                if not m.is_private or m.name in {
                    "_send", "_check", "_read", "_search", "_reply", "_reply_all",
                    "_archive", "_delete", "_contacts", "_add_contact", "_remove_contact",
                    "_edit_contact", "_handle_schedule", "_schedule_create", "_schedule_cancel",
                    "_schedule_reactivate", "_schedule_list", "_scheduler_tick", "_scheduler_loop",
                    "_reconcile_schedules_on_startup", "_write_schedule", "_read_schedule",
                    "_set_schedule_status", "_load_email", "_list_emails", "_email_summary",
                    "_inject_identity", "_persist_to_outbox", "_load_contacts",
                }
            ]
            if key_methods:
                method_lines = "<br/>".join(
                    f'<code>{m.name}</code> :{m.line}' for m in key_methods[:8]
                )
                lines.append(
                    f'        {cls_id}["<b>{cls.name}</b><br/>{method_lines}"]'
                )
            else:
                lines.append(f'        {cls_id}["<b>{cls.name}</b>"]')

        # Module-level functions
        for fn in fi.functions:
            if fn.is_private and fn.name.startswith("__"):
                continue
            fid = _func_id(safe_key, fn.name)
            brief = f"<br/><i>{fn.doc_brief}</i>" if fn.doc_brief else ""
            lines.append(f'        {fid}["<code>{fn.name}</code> :{fn.line}{brief}"]')

        lines.append("    ")

    # ── Cross-layer edges (imports) ──
    edge_idx = 0
    for key, fi in files.items():
        safe_key = key.replace("-", "_")
        for other_key, names in fi.imports_from_others.items():
            safe_other = other_key.replace("-", "_")
            label = ", ".join(names[:4])
            if len(names) > 4:
                label += f" +{len(names)-4}"
            lines.append(f'    {safe_key} -.->|"{label}"| {safe_other}')
            edge_idx += 1

    # ── Send path (highlighted) ──
    lines.append("")
    lines.append("    %% === SEND PATH ===")
    prev = None
    for layer, name in SEND_PATH:
        safe_layer = layer.replace("-", "_")
        node_id = _func_id(safe_layer, name)
        if prev:
            lines.append(f"    {prev} --> {node_id}")
        prev = node_id

    # ── Filesystem data nodes ──
    lines.append("")
    lines.append("    %% === DATA STORES ===")
    lines.append('    inbox[("📬 inbox/{uuid}/msg.json")]')
    lines.append('    outbox[("📦 outbox/{uuid}/msg.json")]')
    lines.append('    sent[("📤 sent/{uuid}/msg.json")]')
    lines.append('    schedules[("📅 schedules/{id}/schedule.json")]')
    lines.append('    readjson[("👁 read.json")]')
    lines.append('    contacts[("📇 contacts.json")]')

    # Connect data stores to functions
    lines.append("    capability_EmailManager --> outbox")
    lines.append("    outbox --> intrinsic__mailman")
    lines.append("    transport_send --> inbox")
    lines.append("    intrinsic__mailman --> sent")
    lines.append("    intrinsic__mailman --> inbox")
    lines.append("    capability_EmailManager --> sent")
    lines.append("    capability_EmailManager --> schedules")
    lines.append("    capability_EmailManager --> contacts")
    lines.append("    intrinsic__mark_read --> readjson")

    # Styling
    lines.append("")
    lines.append("    %% === STYLING ===")
    lines.append("    classDef capability fill:#e1f5fe,stroke:#0288d1,stroke-width:2px")
    lines.append("    classDef intrinsic fill:#fff3e0,stroke:#f57c00,stroke-width:2px")
    lines.append("    classDef transport fill:#e8f5e9,stroke:#388e3c,stroke-width:2px")
    lines.append("    classDef data fill:#fce4ec,stroke:#c62828,stroke-width:2px,stroke-dasharray:5")
    lines.append("    classDef handshake fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px")

    return "\n".join(lines)


# ── JSON export ─────────────────────────────────────────────────────────────

def to_json(files: dict[str, FileInfo]) -> dict:
    """Export extracted structure as JSON."""
    result = {}
    for key, fi in files.items():
        entry = {
            "path": fi.path,
            "label": fi.label,
            "classes": [],
            "functions": [],
            "imports_from": fi.imports_from_others,
        }
        for cls in fi.classes:
            entry["classes"].append({
                "name": cls.name,
                "line": cls.line,
                "methods": [
                    {"name": m.name, "line": m.line, "end_line": m.end_line,
                     "private": m.is_private, "doc": m.doc_brief, "calls": m.calls}
                    for m in cls.methods
                ],
            })
        for fn in fi.functions:
            entry["functions"].append({
                "name": fn.name, "line": fn.line, "end_line": fn.end_line,
                "private": fn.is_private, "doc": fn.doc_brief, "calls": fn.calls,
            })
        result[key] = entry
    return result


# ── Markdown wrapper ────────────────────────────────────────────────────────

def generate_markdown(files: dict[str, FileInfo]) -> str:
    """Full markdown document with the Mermaid diagram + stats."""
    mermaid = generate_mermaid(files)
    total_funcs = sum(
        len(fi.functions) + sum(len(c.methods) for c in fi.classes)
        for fi in files.values()
    )

    return f"""# Mail Architecture — Living Diagram

> Auto-generated from source by `mail-arch-diagram.py`.
> Re-run: `python3 mail-arch-diagram.py src/ > mail-arch-diagram.md`

## Diagram

```mermaid
{mermaid}
```

## Stats

| Layer | File | Classes | Functions/Methods |
|-------|------|---------|-------------------|
{chr(10).join(
    f"| {fi.label} | `{fi.path}` | {len(fi.classes)} | {len(fi.functions) + sum(len(c.methods) for c in fi.classes)} |"
    for fi in files.values()
)}

**Total:** {total_funcs} functions/methods across {len(files)} source files.

## Source

| File | Lines |
|------|-------|
{chr(10).join(
    f"| `{fi.path}` | {1}-{max((m.end_line for c in fi.classes for m in c.methods), default=0) or '?'} |"
    for fi in files.values()
)}
"""


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    mermaid_only = "--mermaid-only" in args
    json_mode = "--json" in args
    args = [a for a in args if not a.startswith("--")]

    if args:
        src_root = Path(args[0])
    else:
        src_root = Path(__file__).parent.parent.parent.parent / "src"

    if not src_root.is_dir():
        print(f"ERROR: Source root not found: {src_root}", file=sys.stderr)
        sys.exit(2)

    # Parse all files
    files: dict[str, FileInfo] = {}
    for key, meta in MAIL_FILES.items():
        files[key] = parse_file(src_root, key, meta)

    if json_mode:
        print(json.dumps(to_json(files), indent=2, default=str))
    elif mermaid_only:
        print(generate_mermaid(files))
    else:
        print(generate_markdown(files))


if __name__ == "__main__":
    main()
