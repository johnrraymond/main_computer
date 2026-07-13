from __future__ import annotations

import json
from pathlib import Path

from main_computer.log_profile_mds import ProfileMapOptions, build_log_profile_map, render_profile_map_svg
from main_computer.main_log_codec import LexLogWriter


def _write_log(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    events = []
    for index in range(8):
        events.append(
            {
                "schema_version": 1,
                "ingest_seq": index + 1,
                "at": f"2026-07-12T21:00:{index:02d}+00:00",
                "kind": "child-stream",
                "service": "main-computer-service-supervisor",
                "source_service": "app",
                "stream": "stdout",
                "process_name": "app_control.py",
                "message": f"[signal] worker-runtime-supervisor-reconcile reason=periodic state=OFF phase=not_accepting loop_count={index}",
            }
        )
    events.append(
        {
            "schema_version": 1,
            "ingest_seq": 9,
            "at": "2026-07-12T21:01:00+00:00",
            "kind": "subprocess-stream",
            "service": "main-computer-applications-service",
            "source_service": "main-computer-applications-service",
            "stream": "stderr",
            "process_name": "applications_service.py",
            "returncode": 1,
            "message": "Traceback (most recent call last): RuntimeError: application boot failed status=500 phase=deploy",
        }
    )
    events.append(
        {
            "schema_version": 1,
            "ingest_seq": 10,
            "at": "2026-07-12T21:01:05+00:00",
            "kind": "child-stream",
            "service": "main-computer-service-supervisor",
            "source_service": "applications",
            "stream": "stdout",
            "process_name": "app_control.py",
            "message": "main-computer-applications-service: initial boot complete; application_servers=skipped coolify=ready",
        }
    )
    with LexLogWriter(path) as writer:
        for event in events:
            writer.write_record(event)


def test_log_profile_map_builds_sparse_profiles_and_2d_embedding(tmp_path: Path) -> None:
    log_path = tmp_path / "runtime" / "main_log" / "main.log.lex"
    _write_log(log_path)

    result = build_log_profile_map(
        root=tmp_path,
        options=ProfileMapOptions(
            window="events",
            event_window=5,
            event_stride=5,
            max_profiles=10,
            max_coverage_points=10_000,
            normalize="log1p",
            include_distance_matrix=True,
        ),
    )

    assert result["ok"] is True
    assert result["schema"] == "mclog-profile-map-v1"
    assert result["summary"]["event_count"] == 10
    assert result["summary"]["profile_count"] == 2
    assert result["summary"]["coverage_point_count"] > 0
    assert result["distance"]["metric"] == "manhattan"
    assert len(result["distance"]["matrix"]) == 2
    assert len(result["embedding"]["points"]) == 2
    assert all("x" in point and "y" in point for point in result["embedding"]["points"])

    labels = [point["label"] for point in result["coverage_points"].values()]
    assert any("state=off" in label for label in labels)
    assert any("traceback" in label for label in labels)
    assert any("skipped" in label for label in labels)

    assert any(profile["skip_counts"] for profile in result["profiles"])
    assert any(profile["failure_counts"] for profile in result["profiles"])

    svg = render_profile_map_svg(result)
    assert svg.startswith("<svg")
    assert "Main log behavior profile map" in svg


def test_log_profile_map_information_windows_are_sparse(tmp_path: Path) -> None:
    log_path = tmp_path / "runtime" / "main_log" / "main.log.lex"
    _write_log(log_path)

    result = build_log_profile_map(
        root=tmp_path,
        options=ProfileMapOptions(
            window="information",
            target_surprise_bits=4.0,
            stride_surprise_bits=4.0,
            max_profiles=20,
            max_coverage_points=10_000,
            normalize="binary",
        ),
    )

    assert result["ok"] is True
    assert result["summary"]["profile_count"] >= 1
    for profile in result["profiles"]:
        assert profile["nonzero_points"] < result["summary"]["coverage_point_count"]
        assert isinstance(profile["positive_counts"], dict)


def test_profile_map_svg_uses_readable_defaults_for_outlier_heavy_maps() -> None:
    points = []
    for idx in range(30):
        points.append(
            {
                "profile_id": f"P{idx:06d}",
                "x": float(idx % 6),
                "y": 0.001 * float(idx % 3),
                "seq_start": idx * 10,
                "seq_end": idx * 10 + 9,
                "event_count": 10,
                "surprise_bits_total": float(idx + 1),
                "nonzero_points": 3,
                "dominant_points": [{"label": "example", "count": 1, "type": "signature"}],
            }
        )
    points.append(
        {
            "profile_id": "P999999",
            "x": 1000.0,
            "y": 1000.0,
            "seq_start": 999,
            "seq_end": 1000,
            "event_count": 1,
            "surprise_bits_total": 500.0,
            "nonzero_points": 1,
            "dominant_points": [{"label": "outlier", "count": 1, "type": "signature"}],
        }
    )
    svg = render_profile_map_svg(
        {
            "summary": {"profile_count": len(points), "coverage_point_count": 12},
            "embedding": {"points": points, "diagnostics": {"negative_eigenvalue_fraction": 0.0}},
        },
        width=900,
        height=500,
        label_limit=4,
        scale="robust",
    )

    assert svg.startswith("<svg")
    assert "labels=4" in svg
    assert "scale=robust" in svg
    assert "Main log behavior profile map" in svg
    assert "P999999" in svg
