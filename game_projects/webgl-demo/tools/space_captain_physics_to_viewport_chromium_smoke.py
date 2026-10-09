#!/usr/bin/env python3
"""Verify the Space Captain physics -> Chromium viewport update cycle.

This smoke intentionally joins two already-proven primitives:

1. ``space_captain_boarding_encounter_smoke.py`` produces authoritative Battle 2
   physics samples, exact Impact/tactical-boundary anchors, and the golden soft-lock
   camera fixture.
2. ``tools/playwright_chromium_code_smoke.py`` runs the companion JavaScript probe
   in real Chromium.

The browser receives roughly 10 Hz authoritative physics/state anchors, immediately
re-anchors on semantic events, predicts motion between anchors from position /
velocity / acceleration, and renders through ``requestAnimationFrame``.  The smoke
passes only when Chromium reproduces the authoritative trajectory without ordinary
re-anchor snaps while preserving the soft target lock.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
GENERIC_RUNNER_ROOT = REPO_ROOT / "tools"
if str(GENERIC_RUNNER_ROOT) not in sys.path:
    sys.path.insert(0, str(GENERIC_RUNNER_ROOT))

import playwright_chromium_code_smoke as browser_code_smoke

SCHEMA = "game.spaceCaptainPhysicsToViewportChromiumSmoke.v1"
BOARDING_SMOKE = HERE / "space_captain_boarding_encounter_smoke.py"
PROBE_JS = HERE / "space_captain_physics_to_viewport_chromium_probe.js"


class PhysicsViewportSmokeError(RuntimeError):
    pass


def _positive_float(text: str) -> float:
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("value must be > 0")
    return value


def _one_to_five(text: str) -> float:
    value = float(text)
    if value < 1.0 or value > 5.0:
        raise argparse.ArgumentTypeError("time step must be between 1 and 5 seconds")
    return value


def _extract_json(stdout: str) -> dict[str, Any]:
    start = stdout.find("{")
    if start < 0:
        raise PhysicsViewportSmokeError("boarding encounter emitted no JSON object")
    try:
        value = json.loads(stdout[start:])
    except json.JSONDecodeError as exc:
        raise PhysicsViewportSmokeError(f"boarding encounter emitted invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise PhysicsViewportSmokeError("boarding encounter JSON root must be an object")
    return value


def build_fixture(*, time_step_seconds: float, physics_step_seconds: float, reference_view_sample_hz: float) -> dict[str, Any]:
    command = [
        sys.executable,
        str(BOARDING_SMOKE),
        "--time-step-seconds",
        str(time_step_seconds),
        "--physics-step-seconds",
        str(physics_step_seconds),
        "--reference-view-sample-hz",
        str(reference_view_sample_hz),
        "--include-samples",
    ]
    completed = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    fixture = _extract_json(completed.stdout)
    if completed.returncode != 0 or fixture.get("ok") is not True:
        raise PhysicsViewportSmokeError(
            "boarding encounter fixture failed before Chromium verification: "
            f"exit={completed.returncode}; failedChecks={fixture.get('failedChecks')}; stderr={completed.stderr.strip()}"
        )
    for key in ("physicsSamples", "viewportFrames", "viewScreenSamples"):
        rows = fixture.get(key)
        if not isinstance(rows, list) or not rows:
            raise PhysicsViewportSmokeError(f"boarding encounter fixture missing populated {key}")
    return fixture


def run(
    *,
    time_step_seconds: float = 5.0,
    physics_step_seconds: float = 0.1,
    reference_view_sample_hz: float = 60.0,
    headed: bool = False,
    timeout_seconds: float = 30.0,
    screenshot: Path | None = None,
) -> dict[str, Any]:
    fixture = build_fixture(
        time_step_seconds=time_step_seconds,
        physics_step_seconds=physics_step_seconds,
        reference_view_sample_hz=reference_view_sample_hz,
    )
    source = PROBE_JS.read_text(encoding="utf-8")
    browser_report = browser_code_smoke.run_smoke(
        source=source,
        user_args={"fixture": fixture},
        headed=headed,
        viewport=(1280, 800),
        timeout_seconds=timeout_seconds,
        screenshot=screenshot,
        fail_on_console_error=True,
    )
    browser_result = ((browser_report.get("execution") or {}).get("result")) or {}
    checks = {
        "boardingEncounterFixturePasses": fixture.get("ok") is True,
        "playwrightChromiumHarnessPasses": browser_report.get("ok") is True,
        "browserPhysicsViewportProbePasses": isinstance(browser_result, dict) and browser_result.get("ok") is True,
    }
    failed_checks = [name for name, passed in checks.items() if not passed]
    return {
        "ok": not failed_checks,
        "schema": SCHEMA,
        "contract": {
            "authority": "Battle 2 physics/state anchors remain authoritative; Chromium only predicts between anchors",
            "physics": "authoritative sub-slice updates arrive around the configured physics step",
            "events": "Impact and tactical-boundary states re-anchor immediately at their exact simulation timestamps",
            "viewport": "Chromium requestAnimationFrame renders faster than authority updates from position, velocity, and acceleration",
            "camera": "soft target lock runs per render frame without hard-centering or camera snaps",
        },
        "checks": checks,
        "failedChecks": failed_checks,
        "fixture": {
            "schema": fixture.get("schema"),
            "metrics": fixture.get("metrics"),
            "viewScreen": fixture.get("viewScreen"),
            "physicsSampleCount": len(fixture.get("physicsSamples") or []),
            "viewportFrameCount": len(fixture.get("viewportFrames") or []),
            "viewScreenSampleCount": len(fixture.get("viewScreenSamples") or []),
        },
        "chromium": browser_report,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--time-step-seconds", type=_one_to_five, default=5.0)
    parser.add_argument("--physics-step-seconds", type=_positive_float, default=0.1)
    parser.add_argument("--reference-view-sample-hz", "--viewport-hz", dest="reference_view_sample_hz", type=_positive_float, default=60.0, help="Reference trajectory-sample cadence only; Chromium renders with requestAnimationFrame.")
    parser.add_argument("--timeout-seconds", type=_positive_float, default=30.0)
    parser.add_argument("--headed", action="store_true", help="Show Chromium while the smoke runs.")
    parser.add_argument("--screenshot", type=Path, help="Optional final Chromium canvas screenshot path.")
    parser.add_argument("--output", type=Path, help="Optional JSON report path; report is always printed to stdout.")
    return parser


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    try:
        report = run(
            time_step_seconds=ns.time_step_seconds,
            physics_step_seconds=ns.physics_step_seconds,
            reference_view_sample_hz=ns.reference_view_sample_hz,
            headed=ns.headed,
            timeout_seconds=ns.timeout_seconds,
            screenshot=ns.screenshot,
        )
    except Exception as exc:
        report = {
            "ok": False,
            "schema": SCHEMA,
            "harnessError": {"type": type(exc).__name__, "message": str(exc)},
        }

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if ns.output is not None:
        ns.output.parent.mkdir(parents=True, exist_ok=True)
        ns.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
