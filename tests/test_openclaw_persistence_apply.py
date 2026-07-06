from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
APPLY_PATH = REPO_ROOT / "scripts" / "apply_openclaw_persistence.py"


def load_apply():
    spec = importlib.util.spec_from_file_location("apply_openclaw_persistence", APPLY_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def export_payload(relative_path: str, original: str, edited: str, memory_root: Path) -> dict:
    return {
        "schema_version": "main-computer.openclaw-persistence-export.v1",
        "generated_at_utc": "2099-01-01T00:00:00+00:00",
        "memory_root": str(memory_root),
        "stats": {"file_count": 1},
        "files": [
            {
                "relative_path": relative_path,
                "sha256": sha256_text(original),
                "size_bytes": len(original.encode("utf-8")),
                "newline_style": "lf",
                "text": edited,
            }
        ],
    }


def test_openclaw_persistence_apply_round_trips_edited_json_text(tmp_path: Path) -> None:
    apply = load_apply()
    memory_root = tmp_path / "workspace"
    memory_file = memory_root / "memory" / "2099-01-01.md"
    memory_file.parent.mkdir(parents=True)
    original = "# Daily\n\nRemember alpha.\n"
    edited = "# Daily\n\nRemember alpha.\nRemember beta.\n"
    memory_file.write_text(original, encoding="utf-8", newline="\n")

    export_path = tmp_path / "edited-export.json"
    export_path.write_text(
        json.dumps(export_payload("memory/2099-01-01.md", original, edited, memory_root), indent=2),
        encoding="utf-8",
    )

    dry = apply.plan_and_apply(export_path=export_path, memory_root=memory_root, dry_run=True, verify_after=True)
    assert dry["ok"] is True
    assert dry["files"][0]["status"] == "would_update"
    assert memory_file.read_text(encoding="utf-8") == original

    result = apply.plan_and_apply(export_path=export_path, memory_root=memory_root, verify_after=True)
    assert result["ok"] is True
    assert result["stats"]["changed"] == 1
    assert result["files"][0]["status"] == "updated"
    assert memory_file.read_text(encoding="utf-8") == edited

    repeated = apply.plan_and_apply(export_path=export_path, memory_root=memory_root, verify_after=True)
    assert repeated["ok"] is True
    assert repeated["stats"]["already_applied"] == 1


def test_openclaw_persistence_apply_rejects_stale_exports_without_force(tmp_path: Path) -> None:
    apply = load_apply()
    memory_root = tmp_path / "workspace"
    memory_file = memory_root / "memory" / "2099-01-02.md"
    memory_file.parent.mkdir(parents=True)
    original = "# Daily\n\nRemember alpha.\n"
    edited = "# Daily\n\nRemember beta.\n"
    divergent = "# Daily\n\nOpenClaw changed this after export.\n"
    memory_file.write_text(divergent, encoding="utf-8", newline="\n")

    export_path = tmp_path / "stale-export.json"
    export_path.write_text(
        json.dumps(export_payload("memory/2099-01-02.md", original, edited, memory_root), indent=2),
        encoding="utf-8",
    )

    result = apply.plan_and_apply(export_path=export_path, memory_root=memory_root, verify_after=True)
    assert result["ok"] is False
    assert result["stats"]["conflict_count"] == 1
    assert "current_sha_mismatch" in result["conflicts"][0]["reason"]
    assert memory_file.read_text(encoding="utf-8") == divergent

    forced = apply.plan_and_apply(
        export_path=export_path,
        memory_root=memory_root,
        skip_current_sha_check=True,
        verify_after=True,
    )
    assert forced["ok"] is True
    assert memory_file.read_text(encoding="utf-8") == edited


def test_openclaw_persistence_apply_rejects_unsafe_paths(tmp_path: Path) -> None:
    apply = load_apply()
    payload = export_payload("../outside.md", "old", "new", tmp_path)
    export_path = tmp_path / "unsafe.json"
    export_path.write_text(json.dumps(payload), encoding="utf-8")

    try:
        apply.plan_and_apply(export_path=export_path, memory_root=tmp_path / "workspace")
    except apply.ApplyError as exc:
        assert "unsafe relative_path" in str(exc) or "absolute paths" in str(exc)
    else:
        raise AssertionError("unsafe path should have been rejected")


def test_openclaw_persistence_apply_supports_jsonl_exports(tmp_path: Path) -> None:
    apply = load_apply()
    memory_root = tmp_path / "workspace"
    memory_file = memory_root / "MEMORY.md"
    memory_file.parent.mkdir(parents=True)
    original = "# Memory\n\nAlpha.\n"
    edited = "# Memory\n\nAlpha.\nBeta.\n"
    memory_file.write_text(original, encoding="utf-8", newline="\n")

    payload = export_payload("MEMORY.md", original, edited, memory_root)
    jsonl_path = tmp_path / "edited.jsonl"
    with jsonl_path.open("w", encoding="utf-8", newline="\n") as handle:
        manifest = {k: v for k, v in payload.items() if k != "files"}
        manifest["record_type"] = "manifest"
        handle.write(json.dumps(manifest) + "\n")
        file_record = dict(payload["files"][0])
        file_record["record_type"] = "file"
        handle.write(json.dumps(file_record) + "\n")

    result = apply.plan_and_apply(export_path=jsonl_path, memory_root=memory_root, verify_after=True)
    assert result["ok"] is True
    assert memory_file.read_text(encoding="utf-8") == edited


def test_openclaw_persistence_apply_self_test() -> None:
    completed = subprocess.run(
        [sys.executable, str(APPLY_PATH), "--self-test"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["apply"] == "openclaw-persistence-pushback"
