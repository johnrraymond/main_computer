from __future__ import annotations

import io
import json
from pathlib import Path
import zipfile

from main_computer.main_log_codec import encode_records
from main_computer.main_log_pack import MainLogPackOptions, build_main_log_pack_zip_bytes, build_surprise_pack


def _write_sample_lex(path: Path) -> None:
    records = [
        {
            "schema_version": 1,
            "at": f"2026-07-12T21:30:{index:02d}+00:00",
            "kind": "child-stream",
            "service": "test-service",
            "source_service": "app",
            "stream": "stdout",
            "process_name": "app_control.py",
            "message": f"[signal] health check request_id={100000000000 + index} state=OK",
        }
        for index in range(30)
    ]
    records.append(
        {
            "schema_version": 1,
            "at": "2026-07-12T21:31:00+00:00",
            "kind": "child-stream",
            "service": "test-service",
            "source_service": "database",
            "stream": "stderr",
            "process_name": "app_control.py",
            "message": "ERROR database timeout duration_ms=9312 status=500 request_id=999999999999",
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        encode_records(records, fh)


def test_build_surprise_pack_compresses_repetition_and_keeps_surprise_codes(tmp_path: Path) -> None:
    root = tmp_path
    log_path = root / "runtime" / "main_log" / "main.log.lex"
    _write_sample_lex(log_path)

    pack = build_surprise_pack(root=root, options=MainLogPackOptions(top=10, surprise_threshold_bits=4.0))

    assert pack["ok"] is True
    assert pack["schema"] == "mclog-surprise-pack-v1"
    assert pack["summary"]["total_events"] == 31
    assert pack["summary"]["unique_signatures"] == 2
    assert pack["summary"]["run_count"] == 2
    assert pack["summary"]["dominant_signature_fraction"] > 0.9
    assert pack["runs"][0][2] == 30
    assert pack["histograms"]["surprise_bits"]
    assert pack["top_surprise_events"]
    assert any("database timeout" in event["signature_preview"] for event in pack["top_surprise_events"])
    assert pack["mode"]["lossless_reconstruction"] is False


def test_build_main_log_pack_zip_contains_compact_surprise_pack(tmp_path: Path) -> None:
    root = tmp_path
    log_path = root / "runtime" / "main_log" / "main.log.lex"
    _write_sample_lex(log_path)

    data = build_main_log_pack_zip_bytes(root=root, options=MainLogPackOptions(top=10, surprise_threshold_bits=4.0))

    with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
        names = set(zf.namelist())
        assert "manifest.json" in names
        assert "main-log-surprise-pack.json" in names
        pack = json.loads(zf.read("main-log-surprise-pack.json").decode("utf-8"))

    assert pack["summary"]["total_events"] == 31
    assert pack["summary"]["run_count"] == 2
    assert pack["summary"]["semantic_to_source_file_ratio_before_zip"] < 1.0
