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
    assert result["embedding"]["method"] == "pca"
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


def test_log_profile_map_pca_uses_top_orthogonal_vector_dimensions(tmp_path: Path) -> None:
    log_path = tmp_path / "runtime" / "main_log" / "main.log.lex"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    events = []
    seq = 1
    # Four behavioral regions that differ along two independent dimensions:
    # app/executor on one axis and healthy/error on the other.
    regions = [
        ("app", "ready", "main-computer-app-service: health ready state=ON phase=serving"),
        ("app", "error", "Traceback RuntimeError app failed status=500 phase=serving"),
        ("executor", "ready", "main-computer-executor-service: health ready state=ON phase=worker"),
        ("executor", "error", "Traceback RuntimeError executor failed status=500 phase=worker"),
    ]
    for service, state, message in regions:
        for index in range(8):
            events.append(
                {
                    "schema_version": 1,
                    "ingest_seq": seq,
                    "at": f"2026-07-12T22:{seq // 60:02d}:{seq % 60:02d}+00:00",
                    "kind": "child-stream",
                    "service": f"main-computer-{service}-service",
                    "source_service": service,
                    "stream": "stderr" if state == "error" else "stdout",
                    "process_name": f"{service}_service.py",
                    "message": f"{message} loop_count={index}",
                }
            )
            seq += 1
    with LexLogWriter(log_path) as writer:
        for event in events:
            writer.write_record(event)

    result = build_log_profile_map(
        root=tmp_path,
        options=ProfileMapOptions(
            window="events",
            event_window=8,
            event_stride=8,
            max_profiles=10,
            embedding="pca",
            normalize="binary",
            feature_weighting="none",
            max_df_fraction=1.0,
        ),
    )

    assert result["embedding"]["method"] == "pca"
    diagnostics = result["embedding"]["diagnostics"]
    assert diagnostics["dimensions_filled"] >= 2
    ratios = diagnostics["explained_variance_ratio_kept"]
    assert len(ratios) >= 2
    assert ratios[0] > 0
    assert ratios[1] > 0
    points = result["embedding"]["points"]
    assert len({point["x"] for point in points}) > 1
    assert len({point["y"] for point in points}) > 1



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



def test_log_profile_map_supports_overlap_distance_for_better_dispersion(tmp_path: Path) -> None:
    log_path = tmp_path / "runtime" / "main_log" / "main.log.lex"
    _write_log(log_path)

    result = build_log_profile_map(
        root=tmp_path,
        options=ProfileMapOptions(
            window="events",
            event_window=3,
            event_stride=3,
            max_profiles=10,
            max_coverage_points=10_000,
            normalize="log1p_l1",
            distance="weighted_jaccard",
            feature_weighting="tfidf",
            max_df_fraction=0.95,
            include_distance_matrix=True,
        ),
    )

    assert result["ok"] is True
    assert result["distance"]["metric"] == "weighted_jaccard"
    assert result["distance"]["feature_weighting"] == "tfidf"
    assert result["distance"]["diagnostics"]["pair_count"] >= 1
    assert result["summary"]["vector_features_retained"] > 0
    assert len(result["embedding"]["points"]) == result["summary"]["profile_count"]

    svg = render_profile_map_svg(result, label_limit=2)
    assert "pca embedding" in svg
    assert "Main log behavior profile map" in svg


def test_log_profile_map_nmds_uses_distance_ranks(tmp_path: Path) -> None:
    log_path = tmp_path / "runtime" / "main_log" / "main.log.lex"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    events = []
    seq = 1
    # Four distinct behavioral sample windows arranged so NMDS has both
    # service and outcome variation to preserve from the distance ranks.
    regions = [
        ("app", "ready", "main-computer-app-service: health ready state=ON phase=serving"),
        ("app", "error", "Traceback RuntimeError app failed status=500 phase=serving"),
        ("executor", "ready", "main-computer-executor-service: health ready state=ON phase=worker"),
        ("executor", "error", "Traceback RuntimeError executor failed status=500 phase=worker"),
    ]
    for service, state, message in regions:
        for index in range(6):
            events.append(
                {
                    "schema_version": 1,
                    "ingest_seq": seq,
                    "at": f"2026-07-12T23:{seq // 60:02d}:{seq % 60:02d}+00:00",
                    "kind": "child-stream",
                    "service": f"main-computer-{service}-service",
                    "source_service": service,
                    "stream": "stderr" if state == "error" else "stdout",
                    "process_name": f"{service}_service.py",
                    "message": f"{message} loop_count={index}",
                }
            )
            seq += 1
    with LexLogWriter(log_path) as writer:
        for event in events:
            writer.write_record(event)

    result = build_log_profile_map(
        root=tmp_path,
        options=ProfileMapOptions(
            window="events",
            event_window=6,
            event_stride=6,
            max_profiles=10,
            embedding="nmds",
            normalize="binary",
            distance="manhattan",
            feature_weighting="none",
            max_df_fraction=1.0,
            nmds_iterations=25,
            nmds_restarts=2,
            nmds_seed=123,
        ),
    )

    assert result["embedding"]["method"] == "nmds"
    diagnostics = result["embedding"]["diagnostics"]
    assert diagnostics["distance_matrix_used"] is True
    assert diagnostics["stress"] is not None
    assert diagnostics["stress"] >= 0
    assert diagnostics["attempts"]
    points = result["embedding"]["points"]
    assert len(points) == 4
    assert len({point["x"] for point in points}) > 1
    assert len({point["y"] for point in points}) > 1

    svg = render_profile_map_svg(result, label_limit=4)
    assert "nmds embedding" in svg

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


def test_profile_coverage_preserves_routes_commands_and_stable_pathway_fields(tmp_path: Path) -> None:
    log_path = tmp_path / "runtime" / "main_log" / "main.log.lex"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    events = [
        {
            "schema_version": 1,
            "ingest_seq": 1,
            "at": "2026-07-12T22:00:00+00:00",
            "kind": "child-stream",
            "service": "main-computer-service-supervisor",
            "source_service": "app",
            "stream": "stdout",
            "process_name": "app_control.py",
            "message": "[signal] http-request method=GET path=/api/activity/snapshot",
        },
        {
            "schema_version": 1,
            "ingest_seq": 2,
            "at": "2026-07-12T22:00:01+00:00",
            "kind": "subprocess-stream",
            "service": "main-computer-applications-service",
            "source_service": "main-computer-applications-service",
            "stream": "stdout",
            "process_name": "applications_service.py",
            "command": "docker compose --project-name main-computer-applications ps --format json",
            "message": "Coolify health endpoint is reachable",
        },
    ]
    with LexLogWriter(log_path) as writer:
        for event in events:
            writer.write_record(event)

    result = build_log_profile_map(
        root=tmp_path,
        options=ProfileMapOptions(
            window="events",
            event_window=2,
            event_stride=2,
            max_profiles=4,
            normalize="binary",
            feature_weighting="none",
            max_df_fraction=1.0,
        ),
    )

    labels = [point["label"] for point in result["coverage_points"].values()]
    assert any("service=main-computer-service-supervisor" == label for label in labels)
    assert any("process_name=app_control.py" == label for label in labels)
    assert any("http GET route:api.activity.snapshot" == label for label in labels)
    assert any("command compose:ps" == label for label in labels)
    assert not any("service=<random_string>" in label for label in labels)
    assert not any("process_name=<random_string>" in label for label in labels)
