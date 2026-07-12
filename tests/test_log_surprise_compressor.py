from __future__ import annotations

import json
from pathlib import Path

from main_computer.log_surprise_compressor import LogSurpriseCompressor, normalize_log_text, signature_for_event


def test_normalization_discounts_dates_and_obvious_random_numbers() -> None:
    first = "2026-07-11T20:41:03Z INFO health check request_id=839204817263 trace_id=abc9182fae292"
    second = "2026-07-11T20:41:04Z INFO health check request_id=192837465091 trace_id=def9182fae292"

    assert normalize_log_text(first) == normalize_log_text(second)
    normalized = normalize_log_text(first)
    assert "<ts>" in normalized
    assert "request_id=<request_id>" in normalized
    assert "trace_id=<trace_id>" in normalized


def test_repeated_noisy_logs_compress_while_rare_error_remains_visible(tmp_path: Path) -> None:
    compressor = LogSurpriseCompressor()
    for index in range(100):
        compressor.observe(
            {
                "service": "worker",
                "stream": "stdout",
                "message": f"2026-07-11T20:41:{index % 60:02d}Z INFO health check request_id={10_000_000_000 + index}",
            }
        )

    rare = compressor.observe(
        {
            "service": "worker",
            "stream": "stderr",
            "message": "2026-07-11T20:42:59Z ERROR database timeout duration_ms=9312 status=500 request_id=999999999999",
        }
    )
    snapshot = compressor.snapshot(limit=10)

    assert snapshot["ok"] is True
    assert snapshot["summary"]["total_events"] == 101
    assert snapshot["summary"]["unique_signatures"] <= 3
    assert snapshot["summary"]["dominant_signature_fraction"] > 0.95
    assert rare["surprise_bits"] > 5.0
    assert snapshot["histograms"]["surprise_bits"]

    top = snapshot["top_surprise_events"][0]
    assert top["surprise_bits"] == rare["surprise_bits"]
    assert "database timeout" in top["signature_preview"]
    assert "request_id=<request_id>" in top["signature_preview"]
    assert "<ts>" in top["signature_preview"]

    sidecar = tmp_path / "main.log.surprise.json"
    compressor.write_snapshot(sidecar, limit=10)
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["mode"] == "semantic_surprise_summary"
    assert payload["compression"]["raw_bytes_seen"] > 0


def test_signature_keeps_operational_status_while_bucketing_latency() -> None:
    sig = signature_for_event(
        {
            "service": "api",
            "stream": "stderr",
            "message": "WARN request_id=abc9182fae292 duration_ms=9312 status=500",
        }
    )

    assert "request_id=<request_id>" in sig
    assert "duration_ms=<latency:very_slow>" in sig
    assert "status=500" in sig
