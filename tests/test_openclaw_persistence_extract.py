from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT_PATH = REPO_ROOT / "scripts" / "extract_openclaw_persistence.py"


def load_extractor():
    spec = importlib.util.spec_from_file_location("extract_openclaw_persistence", EXTRACT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_openclaw_persistence_extractor_preserves_full_text_and_provenance(tmp_path: Path) -> None:
    extractor = load_extractor()
    memory_root = tmp_path / "workspace"
    (memory_root / "memory").mkdir(parents=True)
    text = "# Durable facts\n\n- Project: Main Computer\n\n## Session marker\n\nExact phrase: high-fidelity-export.\n"
    memory_file = memory_root / "memory" / "2099-01-02.md"
    memory_file.write_text(text, encoding="utf-8", newline="\n")

    export = extractor.build_export(memory_root)

    assert export["schema_version"] == "main-computer.openclaw-persistence-export.v1"
    assert export["stats"]["file_count"] == 1
    file_record = export["files"][0]
    assert file_record["relative_path"] == "memory/2099-01-02.md"
    assert file_record["text"] == text
    assert file_record["sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert file_record["newline_style"] == "lf"
    assert file_record["line_count"] == len(text.splitlines())
    assert file_record["decode_replacement_count"] == 0
    assert any(heading["title"] == "Session marker" for heading in file_record["headings"])

    section = next(item for item in file_record["sections"] if item.get("heading") == "Session marker")
    assert section["heading_path"] == ["Durable facts", "Session marker"]
    assert section["line_start"] == 5
    assert "high-fidelity-export" in section["text"]
    assert section["sha256"] == hashlib.sha256(section["text"].encode("utf-8")).hexdigest()


def test_openclaw_persistence_extractor_writes_json_jsonl_and_markdown(tmp_path: Path) -> None:
    memory_root = tmp_path / "workspace"
    (memory_root / "memory").mkdir(parents=True)
    (memory_root / "MEMORY.md").write_text("# Long term\n\nRemember alpha.\n", encoding="utf-8", newline="\n")
    (memory_root / "memory" / "2099-01-03.md").write_text(
        "# Daily\n\n## Thread\n\nRemember beta.\n",
        encoding="utf-8",
        newline="\n",
    )

    json_out = tmp_path / "export" / "memory.json"
    jsonl_out = tmp_path / "export" / "memory.jsonl"
    markdown_out = tmp_path / "export" / "memory.md"

    completed = subprocess.run(
        [
            sys.executable,
            str(EXTRACT_PATH),
            "--memory-root",
            str(memory_root),
            "--out",
            str(json_out),
            "--jsonl-out",
            str(jsonl_out),
            "--markdown-out",
            str(markdown_out),
            "--summary-json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["ok"] is True
    assert summary["stats"]["file_count"] == 2

    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["files"][0]["relative_path"] == "MEMORY.md"
    assert "Remember alpha." in payload["files"][0]["text"]

    lines = [json.loads(line) for line in jsonl_out.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["record_type"] == "manifest"
    assert any(line["record_type"] == "file" for line in lines)
    assert any(line["record_type"] == "section" and line.get("heading") == "Thread" for line in lines)

    rendered = markdown_out.read_text(encoding="utf-8")
    assert "# OpenClaw persistence export" in rendered
    assert "Remember beta." in rendered


def test_openclaw_persistence_extractor_self_test() -> None:
    completed = subprocess.run(
        [sys.executable, str(EXTRACT_PATH), "--self-test"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["extract"] == "openclaw-persistence-high-fidelity"
