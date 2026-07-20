from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from main_computer.viewport_routes_mcel import ViewportMcelRoutesMixin


class _FakeMcelRoutes(ViewportMcelRoutesMixin):
    def __init__(self, root: Path) -> None:
        self.path = "/api/mcel/diagnostics/events"
        self.server = SimpleNamespace(
            debug_root=root,
            signals=[],
            signal=lambda name, **fields: self.server.signals.append((name, fields)),
        )


def _sample_widget_payload() -> dict:
    return {
        "schema": "mcel-diagnostics-counter-copy-v4",
        "widgetVersion": "mcel-diagnostics-counter-widget-v4",
        "appId": "website-builder",
        "contractId": "website-builder.contract.default.app-health",
        "route": "http://127.0.0.1:8765/applications/website-builder/hub-site",
        "timestamp": "2026-07-20T00:50:40.031Z",
        "verdict": "pass",
        "rawVerdict": "pass",
        "counts": {"errors": 0, "warnings": 0, "ok": 18},
        "current": {"counts": {"errors": 0, "warnings": 0, "ok": 18}, "issues": []},
        "history": {
            "counts": {
                "errorsSeen": 0,
                "warningsSeen": 0,
                "activeErrors": 0,
                "activeWarnings": 0,
                "resolvedErrors": 0,
                "resolvedWarnings": 0,
            },
            "pageStartedAt": "2026-07-20T00:50:33.222Z",
            "lastUpdatedAt": "2026-07-20T00:50:40.031Z",
        },
        "primarySurface": {
            "expected": "website-builder.surface.preview",
            "usable": True,
            "exactlyOneAuthoritativeSurface": True,
            "host": {
                "exists": True,
                "visible": True,
                "selector": "section.website-builder-preview-card.website-builder-preview",
                "width": 1895,
                "height": 1237,
            },
        },
        "measurements": {"visualIntegrityViolations": []},
        "issues": [],
    }


def test_mcel_diagnostic_event_log_normalizes_widget_payload(tmp_path: Path) -> None:
    routes = _FakeMcelRoutes(tmp_path)

    event = routes._mcel_normalize_diagnostic_event(_sample_widget_payload())

    assert event["schema"] == "mcel-diagnostic-event-v1"
    assert event["sourceSchema"] == "mcel-diagnostics-counter-copy-v4"
    assert event["appId"] == "website-builder"
    assert event["contractId"] == "website-builder.contract.default.app-health"
    assert event["counts"] == {"errors": 0, "warnings": 0, "ok": 18}
    assert event["primarySurface"]["usable"] is True
    assert event["primarySurface"]["host"]["selector"] == "section.website-builder-preview-card.website-builder-preview"
    assert event["measurements"]["visualIntegrityViolations"] == []


def test_mcel_diagnostic_event_log_appends_reads_and_summarizes(tmp_path: Path) -> None:
    routes = _FakeMcelRoutes(tmp_path)
    event = routes._mcel_normalize_diagnostic_event(
        {
            **_sample_widget_payload(),
            "counts": {"errors": 1, "warnings": 2, "ok": 15},
            "current": {
                "counts": {"errors": 1, "warnings": 2, "ok": 15},
                "issues": [
                    {
                        "severity": "critical",
                        "normalizedSeverity": "error",
                        "code": "visual-integrity-violation",
                        "finding": "Readable content overlaps an owned surface.",
                        "recommendedNextProbe": "layout.visualIntegrity",
                    }
                ],
            },
            "issues": [
                {
                    "severity": "critical",
                    "code": "visual-integrity-violation",
                    "finding": "Readable content overlaps an owned surface.",
                }
            ],
            "verdict": "fail",
            "rawVerdict": "fail",
        }
    )

    path = routes._mcel_append_diagnostic_event(event)
    assert path == tmp_path / "runtime" / "mcel_diagnostics" / "events.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["eventId"] == event["eventId"]

    events = routes._mcel_read_diagnostic_events(app_id="website-builder", limit=10)
    assert [item["eventId"] for item in events] == [event["eventId"]]

    summary = routes._mcel_diagnostics_summary()
    assert summary["ok"] is True
    assert summary["totals"]["events"] == 1
    assert summary["totals"]["errors"] == 1
    assert summary["apps"]["website-builder"]["issueCount"] == 1


def test_mcel_diagnostic_logging_routes_and_widget_endpoint_are_wired() -> None:
    root = Path(__file__).resolve().parents[1]
    dispatch = (root / "main_computer" / "viewport_route_dispatch.py").read_text(encoding="utf-8")
    widget = (
        root
        / "main_computer"
        / "web"
        / "applications"
        / "scripts"
        / "mcel-diagnostics-counter-widget.js"
    ).read_text(encoding="utf-8")

    assert 'route_path == "/api/mcel/diagnostics/events"' in dispatch
    assert 'route_path == "/api/mcel/diagnostics/summary"' in dispatch
    assert 'DIAGNOSTIC_EVENT_ENDPOINT = "/api/mcel/diagnostics/events"' in widget
    assert "sendBeacon(DIAGNOSTIC_EVENT_ENDPOINT" in widget
    assert "shouldEmitDiagnosticEvent" in widget
