#!/usr/bin/env python3
"""Run the actual bridge viewscreen encounter runtime inside Playwright Chromium."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
GENERIC_RUNNER_ROOT = REPO_ROOT / "tools"
if str(GENERIC_RUNNER_ROOT) not in sys.path:
    sys.path.insert(0, str(GENERIC_RUNNER_ROOT))

import playwright_chromium_code_smoke as browser_code_smoke

SCHEMA = "game.spaceCaptainBridgeViewscreenChromiumSmoke.v1"
CONTRACT_JS = REPO_ROOT / "game_projects" / "webgl-demo" / "web" / "scripts" / "space-captain-multirate-contract.js"
AUTHORITY_JS = REPO_ROOT / "game_projects" / "webgl-demo" / "web" / "scripts" / "bridge-encounter-runtime.js"
PROJECTION_JS = REPO_ROOT / "game_projects" / "webgl-demo" / "web" / "scripts" / "bridge-viewscreen-projection.js"
RUNTIME_JS = REPO_ROOT / "game_projects" / "webgl-demo" / "web" / "scripts" / "bridge-viewscreen-encounter-runtime.js"
PROBE_JS = HERE / "bridge_viewscreen_encounter_chromium_probe.js"


def run(*, headed: bool = False, timeout_seconds: float = 30.0, screenshot: Path | None = None) -> dict[str, Any]:
    source = CONTRACT_JS.read_text(encoding="utf-8") + "\n" + AUTHORITY_JS.read_text(encoding="utf-8") + "\n" + PROJECTION_JS.read_text(encoding="utf-8") + "\n" + RUNTIME_JS.read_text(encoding="utf-8") + "\n" + PROBE_JS.read_text(encoding="utf-8")
    browser = browser_code_smoke.run_smoke(
        source=source,
        headed=headed,
        viewport=(1280, 800),
        timeout_seconds=timeout_seconds,
        screenshot=screenshot,
        fail_on_console_error=True,
    )
    result = ((browser.get("execution") or {}).get("result")) or {}
    checks = {
        "playwrightChromiumHarnessPasses": browser.get("ok") is True,
        "bridgeViewscreenRuntimeProbePasses": isinstance(result, dict) and result.get("ok") is True,
    }
    failed = [name for name, passed in checks.items() if not passed]
    return {"ok": not failed, "schema": SCHEMA, "checks": checks, "failedChecks": failed, "chromium": browser}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--output", type=Path)
    ns = parser.parse_args(argv)
    try:
        report = run(headed=ns.headed, timeout_seconds=ns.timeout_seconds, screenshot=ns.screenshot)
    except Exception as exc:
        report = {"ok": False, "schema": SCHEMA, "harnessError": {"type": type(exc).__name__, "message": str(exc)}}
    rendered = json.dumps(report, indent=2)
    print(rendered)
    if ns.output:
        ns.output.parent.mkdir(parents=True, exist_ok=True)
        ns.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
