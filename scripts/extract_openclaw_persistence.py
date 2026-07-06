from __future__ import annotations

"""Export OpenClaw Markdown persistence with exact source fidelity.

This script intentionally does not summarize memory. It extracts the persisted
Markdown files as source records Main Computer can ingest later with provenance:
repository-local path, byte/line spans, SHA-256, timestamps, headings, sections,
and exact text payloads.

Default memory root:
  OPENCLAW_WORKSPACE, or ~/.openclaw/workspace

Typical Docker helper workspace:
  %LOCALAPPDATA%\\MainComputer\\openclaw-docker\\workspace
"""

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "main-computer.openclaw-persistence-export.v1"
DEFAULT_MAX_FILE_BYTES = 25 * 1024 * 1024
MEMORY_FILE_NAMES = ("MEMORY.md", "DREAMS.md")
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")


class ExtractError(RuntimeError):
    """Extraction failed before a trustworthy export could be produced."""


def utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def iso_from_timestamp(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return _dt.datetime.fromtimestamp(timestamp, _dt.timezone.utc).replace(microsecond=0).isoformat()


def default_memory_root() -> Path:
    return Path(os.environ.get("OPENCLAW_WORKSPACE", "~/.openclaw/workspace")).expanduser()


def safe_relative(path: Path, root: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ExtractError(f"path escaped memory root: {path}") from exc
    parts = relative.parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ExtractError(f"unsafe relative path for memory export: {relative}")
    return relative.as_posix()


def detect_newline_style(raw: bytes) -> str:
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    cr = raw.count(b"\r") - crlf
    kinds = [name for name, count in (("crlf", crlf), ("lf", lf), ("cr", cr)) if count > 0]
    if not kinds:
        return "none"
    if len(kinds) == 1:
        return kinds[0]
    return "mixed"


def decode_bytes(raw: bytes) -> tuple[str, str, int]:
    encoding = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
    text = raw.decode(encoding, errors="replace")
    return text, encoding, text.count("\ufffd")


def line_records(raw: bytes, encoding: str) -> list[dict[str, Any]]:
    raw_lines = raw.splitlines(keepends=True)
    if not raw_lines and raw == b"":
        return []
    if not raw_lines:
        raw_lines = [raw]

    records: list[dict[str, Any]] = []
    byte_offset = 0
    char_offset = 0
    for index, raw_line in enumerate(raw_lines, start=1):
        text = raw_line.decode(encoding, errors="replace")
        # Keep line text out of the normal line index to avoid bloating every
        # record. The full exact file text is carried separately.
        records.append(
            {
                "line": index,
                "byte_start": byte_offset,
                "byte_end": byte_offset + len(raw_line),
                "char_start": char_offset,
                "char_end": char_offset + len(text),
            }
        )
        byte_offset += len(raw_line)
        char_offset += len(text)
    return records


def line_texts_with_offsets(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines(keepends=True)
    if not lines and text == "":
        return []
    if not lines:
        lines = [text]

    records: list[dict[str, Any]] = []
    char_offset = 0
    for line_number, line_text in enumerate(lines, start=1):
        records.append(
            {
                "line": line_number,
                "text": line_text,
                "char_start": char_offset,
                "char_end": char_offset + len(line_text),
            }
        )
        char_offset += len(line_text)
    return records


def heading_title(raw_title: str) -> str:
    title = raw_title.strip()
    # Remove a closing ATX heading marker without damaging titles that contain #.
    title = re.sub(r"[ \t]+#+[ \t]*$", "", title).strip()
    return title


def heading_path(stack: list[dict[str, Any]], heading: dict[str, Any]) -> list[str]:
    active = [item["title"] for item in stack if item["level"] < heading["level"]]
    active.append(heading["title"])
    return active


def extract_headings_and_sections(
    *,
    relative_path: str,
    text: str,
    lines: list[dict[str, Any]],
    include_section_text: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    line_offsets = line_texts_with_offsets(text)
    headings: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = []

    for item in line_offsets:
        match = HEADING_RE.match(item["text"].rstrip("\r\n"))
        if not match:
            continue
        level = len(match.group(1))
        heading = {
            "id": f"{relative_path}#h{len(headings) + 1}",
            "level": level,
            "title": heading_title(match.group(2)),
            "line_start": item["line"],
            "char_start": item["char_start"],
            "char_end": item["char_end"],
        }
        while stack and stack[-1]["level"] >= level:
            stack.pop()
        heading["path"] = heading_path(stack, heading)
        stack.append(heading)
        headings.append(heading)

    sections: list[dict[str, Any]] = []
    if line_offsets:
        document_section = {
            "id": f"{relative_path}#document",
            "kind": "document",
            "source_path": relative_path,
            "heading_path": [],
            "line_start": 1,
            "line_end": line_offsets[-1]["line"],
            "char_start": 0,
            "char_end": len(text),
            "sha256": hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest(),
        }
        if include_section_text:
            document_section["text"] = text
        sections.append(document_section)

    for index, heading in enumerate(headings):
        next_same_or_higher = None
        for other in headings[index + 1 :]:
            if other["level"] <= heading["level"]:
                next_same_or_higher = other
                break

        char_start = heading["char_start"]
        char_end = next_same_or_higher["char_start"] if next_same_or_higher else len(text)
        line_start = heading["line_start"]
        if next_same_or_higher:
            line_end = max(line_start, next_same_or_higher["line_start"] - 1)
        elif line_offsets:
            line_end = line_offsets[-1]["line"]
        else:
            line_end = line_start

        section_text = text[char_start:char_end]
        section = {
            "id": f"{relative_path}#section-{index + 1}",
            "kind": "heading_section",
            "source_path": relative_path,
            "heading": heading["title"],
            "heading_level": heading["level"],
            "heading_path": heading["path"],
            "line_start": line_start,
            "line_end": line_end,
            "char_start": char_start,
            "char_end": char_end,
            "sha256": hashlib.sha256(section_text.encode("utf-8", errors="surrogatepass")).hexdigest(),
        }
        if include_section_text:
            section["text"] = section_text
        sections.append(section)

    return headings, sections


def iter_memory_paths(memory_root: Path) -> list[Path]:
    root = memory_root.expanduser().resolve()
    candidates: list[Path] = []

    for name in MEMORY_FILE_NAMES:
        path = root / name
        if path.is_file():
            candidates.append(path)

    memory_dir = root / "memory"
    if memory_dir.is_dir():
        candidates.extend(path for path in memory_dir.rglob("*.md") if path.is_file())

    # Preserve deterministic order and avoid duplicates if a symlink points at
    # the same file. Symlinks themselves are not followed unless the OS presents
    # them as files under the resolved workspace.
    unique: dict[str, Path] = {}
    for path in candidates:
        relative = safe_relative(path, root)
        unique[relative] = path
    return [unique[key] for key in sorted(unique)]


def extract_file(
    path: Path,
    *,
    memory_root: Path,
    include_full_text: bool,
    include_section_text: bool,
    max_file_bytes: int,
) -> dict[str, Any]:
    stat = path.stat()
    if max_file_bytes > 0 and stat.st_size > max_file_bytes:
        raise ExtractError(f"{path} is {stat.st_size} bytes, above --max-file-bytes {max_file_bytes}")

    raw = path.read_bytes()
    text, encoding, replacement_count = decode_bytes(raw)
    relative_path = safe_relative(path, memory_root)
    line_index = line_records(raw, encoding)
    headings, sections = extract_headings_and_sections(
        relative_path=relative_path,
        text=text,
        lines=line_index,
        include_section_text=include_section_text,
    )

    record: dict[str, Any] = {
        "relative_path": relative_path,
        "absolute_path": str(path),
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "modified_at_utc": iso_from_timestamp(stat.st_mtime),
        "encoding": encoding,
        "decode_replacement_count": replacement_count,
        "newline_style": detect_newline_style(raw),
        "line_count": len(line_index),
        "line_index": line_index,
        "headings": headings,
        "sections": sections,
    }
    if include_full_text:
        record["text"] = text
    return record


def build_export(
    memory_root: Path,
    *,
    include_full_text: bool = True,
    include_section_text: bool = True,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> dict[str, Any]:
    root = memory_root.expanduser().resolve()
    if not root.exists():
        raise ExtractError(f"memory root does not exist: {root}")
    if not root.is_dir():
        raise ExtractError(f"memory root is not a directory: {root}")

    files = [
        extract_file(
            path,
            memory_root=root,
            include_full_text=include_full_text,
            include_section_text=include_section_text,
            max_file_bytes=max_file_bytes,
        )
        for path in iter_memory_paths(root)
    ]
    section_count = sum(len(file["sections"]) for file in files)
    heading_count = sum(len(file["headings"]) for file in files)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_now_iso(),
        "memory_root": str(root),
        "source_policy": {
            "included_files": ["MEMORY.md", "DREAMS.md", "memory/**/*.md"],
            "summary": "No LLM summarization. Exact source text is preserved unless disabled by CLI flags.",
            "max_file_bytes": max_file_bytes,
        },
        "stats": {
            "file_count": len(files),
            "heading_count": heading_count,
            "section_count": section_count,
            "total_size_bytes": sum(file["size_bytes"] for file in files),
        },
        "files": files,
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def iter_jsonl_records(export: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = [
        {
            "record_type": "manifest",
            "schema_version": export["schema_version"],
            "generated_at_utc": export["generated_at_utc"],
            "memory_root": export["memory_root"],
            "stats": export["stats"],
            "source_policy": export["source_policy"],
        }
    ]
    for file in export["files"]:
        file_record = {key: value for key, value in file.items() if key != "sections"}
        file_record["record_type"] = "file"
        records.append(file_record)
        for section in file["sections"]:
            section_record = dict(section)
            section_record["record_type"] = "section"
            section_record["source_file_sha256"] = file["sha256"]
            records.append(section_record)
    return records


def write_jsonl(path: Path, export: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in iter_jsonl_records(export):
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def write_markdown(path: Path, export: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# OpenClaw persistence export\n\n")
    lines.append(f"- Schema: `{export['schema_version']}`\n")
    lines.append(f"- Generated: `{export['generated_at_utc']}`\n")
    lines.append(f"- Memory root: `{export['memory_root']}`\n")
    lines.append(f"- Files: `{export['stats']['file_count']}`\n")
    lines.append(f"- Sections: `{export['stats']['section_count']}`\n\n")

    for file in export["files"]:
        lines.append(f"## `{file['relative_path']}`\n\n")
        lines.append(f"- SHA-256: `{file['sha256']}`\n")
        lines.append(f"- Size: `{file['size_bytes']}` bytes\n")
        lines.append(f"- Modified: `{file['modified_at_utc']}`\n")
        lines.append(f"- Lines: `{file['line_count']}`\n")
        lines.append(f"- Newlines: `{file['newline_style']}`\n\n")
        if "text" in file:
            lines.append("```markdown\n")
            lines.append(file["text"])
            if file["text"] and not file["text"].endswith("\n"):
                lines.append("\n")
            lines.append("```\n\n")
    path.write_text("".join(lines), encoding="utf-8", newline="\n")


def summary_from_export(export: dict[str, Any], outputs: dict[str, str]) -> dict[str, Any]:
    return {
        "ok": True,
        "extract": "openclaw-persistence-high-fidelity",
        "schema_version": export["schema_version"],
        "memory_root": export["memory_root"],
        "stats": export["stats"],
        "outputs": outputs,
        "files": [
            {
                "relative_path": file["relative_path"],
                "sha256": file["sha256"],
                "size_bytes": file["size_bytes"],
                "line_count": file["line_count"],
                "heading_count": len(file["headings"]),
                "section_count": len(file["sections"]),
            }
            for file in export["files"]
        ],
    }


def run_self_test() -> dict[str, Any]:
    root = Path(tempfile.gettempdir()) / f"openclaw-persistence-extract-selftest-{os.getpid()}"
    if root.exists():
        shutil.rmtree(root)
    try:
        (root / "memory").mkdir(parents=True)
        (root / "MEMORY.md").write_text("# Durable facts\n\n- Alpha: one\n", encoding="utf-8", newline="\n")
        (root / "memory" / "2099-01-01.md").write_text(
            "# Daily memory\n\n## Conversation\n\nThe exact phrase is high-fidelity-selftest.\n",
            encoding="utf-8",
            newline="\n",
        )
        export = build_export(root)
        if export["stats"]["file_count"] != 2:
            raise ExtractError("self-test expected two memory files")
        daily = next(file for file in export["files"] if file["relative_path"] == "memory/2099-01-01.md")
        if "high-fidelity-selftest" not in daily.get("text", ""):
            raise ExtractError("self-test lost exact file text")
        if not any(section.get("heading") == "Conversation" for section in daily["sections"]):
            raise ExtractError("self-test failed heading section extraction")
        if not daily["sha256"]:
            raise ExtractError("self-test missing file hash")
        return summary_from_export(export, outputs={})
    finally:
        if root.exists():
            shutil.rmtree(root)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract OpenClaw Markdown persistence into high-fidelity JSON/JSONL/Markdown records.",
    )
    parser.add_argument(
        "--memory-root",
        type=Path,
        default=default_memory_root(),
        help="OpenClaw workspace root. Default: OPENCLAW_WORKSPACE or ~/.openclaw/workspace.",
    )
    parser.add_argument("--out", type=Path, help="Write full JSON export to this file. Defaults to stdout.")
    parser.add_argument("--jsonl-out", type=Path, help="Write JSONL manifest/file/section records to this file.")
    parser.add_argument("--markdown-out", type=Path, help="Write a readable Markdown export to this file.")
    parser.add_argument(
        "--no-full-text",
        action="store_true",
        help="Do not include full file text in the JSON export. Metadata and section text remain unless separately disabled.",
    )
    parser.add_argument(
        "--no-section-text",
        action="store_true",
        help="Do not include exact section text in JSON/JSONL records.",
    )
    parser.add_argument(
        "--max-file-bytes",
        type=int,
        default=DEFAULT_MAX_FILE_BYTES,
        help=f"Refuse to read a single memory file above this size. Use 0 for no limit. Default: {DEFAULT_MAX_FILE_BYTES}.",
    )
    parser.add_argument(
        "--summary-json",
        action="store_true",
        help="Print a compact JSON summary instead of the full export when --out is used.",
    )
    parser.add_argument("--self-test", action="store_true", help="Run parser/export self-test with a temp workspace.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))
    outputs: dict[str, str] = {}

    try:
        if args.self_test:
            result = run_self_test()
            print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
            return 0

        export = build_export(
            args.memory_root,
            include_full_text=not args.no_full_text,
            include_section_text=not args.no_section_text,
            max_file_bytes=max(0, args.max_file_bytes),
        )

        if args.out:
            write_json(args.out, export)
            outputs["json"] = str(args.out)
        if args.jsonl_out:
            write_jsonl(args.jsonl_out, export)
            outputs["jsonl"] = str(args.jsonl_out)
        if args.markdown_out:
            write_markdown(args.markdown_out, export)
            outputs["markdown"] = str(args.markdown_out)

        if args.out and args.summary_json:
            print(json.dumps(summary_from_export(export, outputs), indent=2, ensure_ascii=False, sort_keys=True))
        elif args.out:
            print(f"Wrote OpenClaw persistence export: {args.out}")
            if args.jsonl_out:
                print(f"Wrote JSONL records: {args.jsonl_out}")
            if args.markdown_out:
                print(f"Wrote Markdown export: {args.markdown_out}")
            print(json.dumps(summary_from_export(export, outputs), indent=2, ensure_ascii=False, sort_keys=True))
        else:
            print(json.dumps(export, indent=2, ensure_ascii=False, sort_keys=True))
    except ExtractError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "extract": "openclaw-persistence-high-fidelity",
                    "error": str(exc),
                },
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
