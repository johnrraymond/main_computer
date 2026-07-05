from __future__ import annotations

import gzip
import json
from pathlib import Path
import zipfile

from main_computer.cli import build_parser
from main_computer.log_metric_distribution import analyze_records, build_report_from_paths, discover_default_log_paths, iter_log_records, resolve_log_input_paths
from main_computer.main_log_codec import LexLogWriter


def test_analyzes_jsonl_metrics_and_writes_png_and_report(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"
    rows = [
        {"at": "2026-01-01T00:00:00+00:00", "service": "worker", "duration_ms": 10, "tokens": 100},
        {"at": "2026-01-01T00:01:00+00:00", "service": "worker", "duration_ms": 20, "tokens": 120},
        {"at": "2026-01-01T00:02:00+00:00", "service": "worker", "duration_ms": 30, "tokens": 140},
        {"at": "2026-01-01T00:03:00+00:00", "service": "worker", "duration_ms": 100, "tokens": 160},
    ]
    log_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    report = build_report_from_paths(
        [log_path],
        metrics=["duration_ms", "tokens"],
        output_png=tmp_path / "metrics.png",
        report_json=tmp_path / "metrics.json",
        bins=4,
    )

    assert report.records == 4
    assert [metric.metric for metric in report.metrics] == ["duration_ms", "tokens"]
    duration = report.metrics[0]
    assert duration.count == 4
    assert duration.min == 10
    assert duration.max == 100
    assert duration.p95 > duration.median
    assert (tmp_path / "metrics.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    payload = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert payload["metric_count"] == 2
    assert payload["metrics"][0]["metric"] == "duration_ms"


def test_reads_lex_log_records_for_distribution_analysis(tmp_path: Path) -> None:
    lex_path = tmp_path / "main.log.lex"
    with LexLogWriter(lex_path) as writer:
        for index, latency in enumerate([5, 15, 25], start=1):
            writer.write_record(
                {
                    "at": f"2026-01-01T00:0{index}:00+00:00",
                    "service": "lex-test",
                    "latency_ms": latency,
                    "nested": {"score": latency / 10},
                }
            )

    records = iter_log_records([lex_path])
    summaries = analyze_records(records, metrics=["latency_ms", "nested.score"], bins=3)

    assert len(records) == 3
    assert [summary.metric for summary in summaries] == ["latency_ms", "nested.score"]
    assert summaries[0].count == 3
    assert summaries[0].median == 15


def test_reads_rotated_zip_logs(tmp_path: Path) -> None:
    log_path = tmp_path / "worker.log"
    log_path.write_text(
        "duration_ms=10 retries=0\n"
        "duration_ms=20 retries=1\n"
        "duration_ms=30 retries=1\n",
        encoding="utf-8",
    )
    zip_path = tmp_path / "worker.log.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.write(log_path, "worker.log")

    records = iter_log_records([zip_path])
    summaries = analyze_records(records, metrics=["duration_ms", "retries"], bins=3)

    assert len(records) == 3
    assert summaries[0].metric == "duration_ms"
    assert summaries[0].median == 20
    assert summaries[1].metric == "retries"


def test_cli_parser_exposes_log_metrics_command(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"
    log_path.write_text(
        "\n".join(
            json.dumps({"duration_ms": value, "tokens": value * 10})
            for value in [1, 2, 3]
        )
        + "\n",
        encoding="utf-8",
    )
    png_path = tmp_path / "out.png"

    args = build_parser().parse_args(
        [
            "log-metrics",
            str(log_path),
            "--metric",
            "duration_ms",
            "--output-png",
            str(png_path),
        ]
    )

    assert args.command == "log-metrics"
    result = args.func(args)

    assert result == 0
    assert png_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_extracts_metrics_embedded_in_log_message_strings(tmp_path: Path) -> None:
    log_path = tmp_path / "main.log.jsonl"
    rows = [
        {"at": "2026-01-01T00:00:00+00:00", "message": "worker completed duration_ms=10 tokens: 100"},
        {"at": "2026-01-01T00:01:00+00:00", "message": "worker completed duration_ms=20 tokens: 150"},
        {"at": "2026-01-01T00:02:00+00:00", "message": "worker completed duration_ms=30 tokens: 200"},
    ]
    log_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    summaries = analyze_records(iter_log_records([log_path]), metrics=["duration_ms", "tokens"], bins=3)

    assert [summary.metric for summary in summaries] == ["duration_ms", "tokens"]
    assert summaries[0].median == 20
    assert summaries[1].max == 200


def test_deduplicates_numeric_field_repeated_in_message_text(tmp_path: Path) -> None:
    log_path = tmp_path / "main.log.jsonl"
    rows = [
        {"duration_ms": 10, "message": "worker completed duration_ms=10"},
        {"duration_ms": 20, "message": "worker completed duration_ms=20"},
        {"duration_ms": 30, "message": "worker completed duration_ms=30"},
    ]
    log_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    summaries = analyze_records(iter_log_records([log_path]), metrics=["duration_ms"], bins=3)

    assert summaries[0].count == 3
    assert summaries[0].median == 20


def test_scans_rotated_log_names_and_colon_units_inside_directories(tmp_path: Path) -> None:
    log_path = tmp_path / "worker.log.1"
    log_path.write_text(
        "completed duration: 10ms retries: 0\n"
        "completed duration: 20ms retries: 1\n"
        "completed duration: 30ms retries: 2\n",
        encoding="utf-8",
    )

    report = build_report_from_paths([tmp_path], metrics=["duration_ms", "retries"], output_png=tmp_path / "metrics.png", bins=3)

    assert report.records == 3
    assert [metric.metric for metric in report.metrics] == ["duration_ms", "retries"]
    assert report.metrics[0].median == 20


def test_reads_json_array_and_gzipped_logs(tmp_path: Path) -> None:
    json_path = tmp_path / "metrics.json"
    json_path.write_text(
        json.dumps(
            [
                {"message": "call elapsed 1s", "score": 1},
                {"message": "call elapsed 2s", "score": 2},
                {"message": "call elapsed 3s", "score": 3},
            ]
        ),
        encoding="utf-8",
    )
    gz_path = tmp_path / "worker.log.gz"
    with gzip.open(gz_path, "wt", encoding="utf-8") as handle:
        handle.write("latency_ms=5\nlatency_ms=15\nlatency_ms=25\n")

    summaries = analyze_records(iter_log_records([json_path, gz_path]), metrics=["elapsed_s", "score", "latency_ms"], bins=3)

    assert [summary.metric for summary in summaries] == ["elapsed_s", "score", "latency_ms"]
    assert summaries[0].median == 2
    assert summaries[1].median == 2
    assert summaries[2].median == 15


def test_plain_text_lines_without_key_values_get_derived_metrics(tmp_path: Path) -> None:
    log_path = tmp_path / "plain.log"
    log_path.write_text(
        "2026-01-01T00:00:00Z INFO worker started\n"
        "2026-01-01T00:00:01Z INFO worker finished\n"
        "2026-01-01T00:00:02Z INFO worker idle\n",
        encoding="utf-8",
    )

    report = build_report_from_paths([log_path], output_png=tmp_path / "plain.png", bins=3)

    assert report.records == 3
    assert report.metric_count > 0
    assert any(metric.metric.startswith("record.") for metric in report.metrics)
    assert (tmp_path / "plain.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_extracts_unlabeled_unit_values_from_plain_text(tmp_path: Path) -> None:
    log_path = tmp_path / "units.log"
    log_path.write_text(
        "downloaded 10 bytes in worker\n"
        "downloaded 20 bytes in worker\n"
        "downloaded 30 bytes in worker\n",
        encoding="utf-8",
    )

    summaries = analyze_records(iter_log_records([log_path]), metrics=["bytes"], bins=3)

    assert [summary.metric for summary in summaries] == ["bytes"]
    assert summaries[0].median == 20


def test_json_string_payloads_become_records_with_derived_metrics(tmp_path: Path) -> None:
    log_path = tmp_path / "messages.json"
    log_path.write_text(json.dumps(["alpha", "alpha beta", "alpha beta gamma"]), encoding="utf-8")

    summaries = analyze_records(iter_log_records([log_path]), metrics=["record.message_words"], bins=3)

    assert [summary.metric for summary in summaries] == ["record.message_words"]
    assert summaries[0].min == 1
    assert summaries[0].max == 3


def test_derives_interarrival_metric_from_timestamped_logs(tmp_path: Path) -> None:
    log_path = tmp_path / "main.log.jsonl"
    rows = [
        {"at": "2026-01-01T00:00:00+00:00", "message": "started"},
        {"at": "2026-01-01T00:00:01+00:00", "message": "running"},
        {"at": "2026-01-01T00:00:03+00:00", "message": "finished"},
    ]
    log_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    summaries = analyze_records(iter_log_records([log_path]), metrics=["record.interarrival_ms"], bins=2)

    assert [summary.metric for summary in summaries] == ["record.interarrival_ms"]
    assert summaries[0].count == 2
    assert summaries[0].min == 1000
    assert summaries[0].max == 2000


def test_no_derived_option_can_preserve_strict_numeric_behavior(tmp_path: Path) -> None:
    log_path = tmp_path / "plain.log"
    log_path.write_text("only text\nstill only text\n", encoding="utf-8")

    report = build_report_from_paths([log_path], output_png=tmp_path / "strict.png", include_derived=False)

    assert report.records == 2
    assert report.metric_count == 0


def _make_project_root(path: Path) -> Path:
    root = path / "project"
    (root / "main_computer").mkdir(parents=True)
    (root / "new_patch.py").write_text("# marker\n", encoding="utf-8")
    return root


def test_log_metrics_command_without_paths_reads_known_runtime_main_log(tmp_path: Path, monkeypatch) -> None:
    project_root = _make_project_root(tmp_path)
    log_path = project_root / "runtime" / "main_log" / "main.log.lex"
    with LexLogWriter(log_path) as writer:
        for index, duration in enumerate([10, 20, 30], start=1):
            writer.write_record(
                {
                    "at": f"2026-01-01T00:0{index}:00+00:00",
                    "service": "main-log",
                    "duration_ms": duration,
                    "message": f"request finished duration_ms={duration}",
                }
            )
    output_png = project_root / "metrics.png"

    monkeypatch.chdir(project_root)
    args = build_parser().parse_args(["log-metrics", "--output-png", str(output_png)])

    assert args.paths == []
    result = args.func(args)

    assert result == 0
    assert output_png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_default_log_discovery_includes_runtime_and_archive_logs(tmp_path: Path, monkeypatch) -> None:
    project_root = _make_project_root(tmp_path)
    runtime_log = project_root / "runtime" / "service_supervisor" / "app-20260101.stdout.log"
    runtime_log.parent.mkdir(parents=True)
    runtime_log.write_text("duration_ms=10\n", encoding="utf-8")
    archive_log = project_root.parent / "archive" / "logs" / "worker.log.zip"
    archive_log.parent.mkdir(parents=True)
    source = project_root / "worker.log"
    source.write_text("duration_ms=20\n", encoding="utf-8")
    with zipfile.ZipFile(archive_log, "w") as archive:
        archive.write(source, "worker.log")
    source.unlink()

    monkeypatch.chdir(project_root)
    discovered = discover_default_log_paths()

    assert runtime_log.resolve() in discovered
    assert archive_log.resolve() in discovered


def test_default_logs_alias_falls_back_to_known_project_logs(tmp_path: Path, monkeypatch) -> None:
    project_root = _make_project_root(tmp_path)
    (project_root / "logs").mkdir()
    runtime_log = project_root / "runtime" / "main_log" / "main.log.lex"
    with LexLogWriter(runtime_log) as writer:
        writer.write_record({"duration_ms": 10, "message": "one"})
        writer.write_record({"duration_ms": 20, "message": "two"})
        writer.write_record({"duration_ms": 30, "message": "three"})

    monkeypatch.chdir(project_root)
    resolved = resolve_log_input_paths([Path("logs")])
    report = build_report_from_paths(
        [Path("logs")],
        metrics=["duration_ms"],
        output_png=project_root / "metrics.png",
        bins=3,
    )

    assert runtime_log.resolve() in resolved
    assert report.records == 3
    assert report.metrics[0].median == 20


def test_default_log_discovery_does_not_treat_runtime_config_json_as_log(tmp_path: Path, monkeypatch) -> None:
    project_root = _make_project_root(tmp_path)
    deployment = project_root / "runtime" / "deployments" / "dev" / "latest.json"
    deployment.parent.mkdir(parents=True)
    deployment.write_text(json.dumps({"chain_id": 42424242, "rpc_port": 8545}), encoding="utf-8")
    real_log = project_root / "logs" / "app.log"
    real_log.parent.mkdir(parents=True)
    real_log.write_text("duration_ms=10\n" "duration_ms=20\n" "duration_ms=30\n", encoding="utf-8")

    monkeypatch.chdir(project_root)
    report = build_report_from_paths([], metrics=["duration_ms"], output_png=project_root / "metrics.png", bins=3)

    assert report.records == 3
    assert report.metrics[0].median == 20
