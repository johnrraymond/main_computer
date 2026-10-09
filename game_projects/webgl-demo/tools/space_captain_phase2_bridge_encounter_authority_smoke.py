#!/usr/bin/env python3
"""Phase 2: verify one authoritative bridge encounter owns combat truth in Chromium."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
ROOT_TOOLS = REPO_ROOT / "tools"
if str(ROOT_TOOLS) not in sys.path:
    sys.path.insert(0, str(ROOT_TOOLS))

import playwright_chromium_code_smoke as browser_code_smoke

SCHEMA = "game.spaceCaptainPhase2BridgeEncounterAuthoritySmoke.v1"
SCRIPT_ROOT = REPO_ROOT / "game_projects" / "webgl-demo" / "web" / "scripts"
CONTRACT_JS = SCRIPT_ROOT / "space-captain-multirate-contract.js"
RUNTIME_JS = SCRIPT_ROOT / "bridge-encounter-runtime.js"
PROBE_JS = HERE / "space_captain_phase2_bridge_encounter_authority_probe.js"


def run(*, headed: bool = False, timeout_seconds: float = 30.0) -> dict[str, Any]:
    source = "\n".join(path.read_text(encoding="utf-8") for path in (CONTRACT_JS, RUNTIME_JS, PROBE_JS))
    chromium = browser_code_smoke.run_smoke(
        source=source,
        headed=headed,
        viewport=(1280, 800),
        timeout_seconds=timeout_seconds,
        fail_on_console_error=True,
    )
    result = ((chromium.get("execution") or {}).get("result")) or {}
    checks = {
        "playwrightChromiumHarnessPasses": chromium.get("ok") is True,
        "bridgeEncounterAuthorityProbePasses": isinstance(result, dict) and result.get("ok") is True,
    }
    failed = [name for name, passed in checks.items() if not passed]
    return {"ok": not failed, "schema": SCHEMA, "checks": checks, "failedChecks": failed, "chromium": chromium}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    ns = parser.parse_args(argv)
    if ns.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    try:
        report = run(headed=bool(ns.headed), timeout_seconds=float(ns.timeout_seconds))
    except Exception as exc:
        report = {"ok": False, "schema": SCHEMA, "error": f"{type(exc).__name__}: {exc}"}
    rendered = json.dumps(report, indent=2)
    print(rendered)
    if ns.output:
        ns.output.parent.mkdir(parents=True, exist_ok=True)
        ns.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
