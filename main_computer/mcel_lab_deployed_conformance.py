#!/usr/bin/env python3
"""Deployed-route MCEL Lab runtime conformance fixture.

This fixture measures the real ``/applications/mcel-lab`` route at the
authorized desktop and stacked-layout viewports. It extends the existing MCEL
runtime FLOG report with deterministic geometry evidence while preserving the
``mcel-runtime-flog-report-v2`` schema consumed by the repository truth audit.

Run from the repository root while the viewport server is already running::

    python main_computer/mcel_lab_deployed_conformance.py \
      --base-url http://127.0.0.1:8765

The generated report remains runtime evidence, not a source mutation or a
maturity promotion.
"""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

try:
    from . import flog_mcel_runtime_smoke as flog
except ImportError:  # Direct execution from the repository root.
    import flog_mcel_runtime_smoke as flog


DEFAULT_OUTPUT_DIR = flog.DEFAULT_OUTPUT_DIR
PROFILE_VERSION = "mcel-lab-deployed-conformance-v1"
ROUTE = "/applications/mcel-lab"
HOST_SELECTOR = ".mcel-lab-blueprint-primary"
EDITOR_SELECTOR = "#mcel-blueprint-work-surface"
WORKBENCH_SELECTOR = ".mcel-lab-blueprint-workbench"
RAIL_SELECTOR = ".mcel-lab-blueprint-right-rail"
MIN_DESKTOP_WIDTH = 640
MIN_DESKTOP_HEIGHT = 420


@dataclass(frozen=True)
class ViewportProfile:
    name: str
    width: int
    height: int
    layout: str
    requires_desktop_minimum: bool

    def viewport(self) -> dict[str, int]:
        return {"width": self.width, "height": self.height}

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "layout": self.layout,
            "requiresDesktopMinimum": self.requires_desktop_minimum,
        }


# Keep the 1440x900 desktop probe last. McelAppTruthGate selects the last
# matching app result from a report, so the canonical truth-binding result is
# the widest authorized desktop measurement rather than the stacked probe.
VIEWPORT_PROFILES = (
    ViewportProfile("desktop-1280x720", 1280, 720, "desktop", True),
    ViewportProfile("stacked-900x900", 900, 900, "stacked", False),
    ViewportProfile("desktop-1440x900", 1440, 900, "desktop", True),
)


GEOMETRY_PROBE_JS = r"""() => {
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return (
      style.display !== "none" &&
      style.visibility !== "hidden" &&
      Number(style.opacity || 1) > 0 &&
      rect.width > 0 &&
      rect.height > 0
    );
  };

  const rectOf = (element) => {
    if (!element) return null;
    const rect = element.getBoundingClientRect();
    return {
      x: rect.x,
      y: rect.y,
      width: rect.width,
      height: rect.height,
      right: rect.right,
      bottom: rect.bottom
    };
  };

  const labelOf = (element, index) => {
    if (element.id) return `#${element.id}`;
    const classes = Array.from(element.classList || []).slice(0, 3);
    if (classes.length) return `${element.tagName.toLowerCase()}.${classes.join(".")}`;
    return `${element.tagName.toLowerCase()}:nth-child(${index + 1})`;
  };

  const hostMatches = Array.from(document.querySelectorAll(".mcel-lab-blueprint-primary"))
    .filter(visible);
  const editorMatches = Array.from(document.querySelectorAll("#mcel-blueprint-work-surface"))
    .filter(visible);
  const workbench = document.querySelector(".mcel-lab-blueprint-workbench");
  const rail = document.querySelector(".mcel-lab-blueprint-right-rail");

  const railChildren = rail
    ? Array.from(rail.children).filter(visible).map((element, index) => {
        const style = getComputedStyle(element);
        const overflowDelta = element.scrollHeight - element.clientHeight;
        const scrollable = ["auto", "scroll"].includes(style.overflowY);
        return {
          selector: labelOf(element, index),
          rect: rectOf(element),
          clientHeight: element.clientHeight,
          scrollHeight: element.scrollHeight,
          overflowY: style.overflowY,
          internallyClipped: overflowDelta > 1 && !scrollable
        };
      })
    : [];

  const siblings = workbench
    ? Array.from(workbench.children).filter(visible).map((element, index) => ({
        selector: labelOf(element, index),
        rect: rectOf(element)
      }))
    : [];
  const siblingOverlaps = [];
  for (let leftIndex = 0; leftIndex < siblings.length; leftIndex += 1) {
    for (let rightIndex = leftIndex + 1; rightIndex < siblings.length; rightIndex += 1) {
      const left = siblings[leftIndex];
      const right = siblings[rightIndex];
      const intersectionWidth = Math.max(
        0,
        Math.min(left.rect.right, right.rect.right) - Math.max(left.rect.x, right.rect.x)
      );
      const intersectionHeight = Math.max(
        0,
        Math.min(left.rect.bottom, right.rect.bottom) - Math.max(left.rect.y, right.rect.y)
      );
      if (intersectionWidth > 1 && intersectionHeight > 1) {
        siblingOverlaps.push({
          left: left.selector,
          right: right.selector,
          intersectionWidth,
          intersectionHeight
        });
      }
    }
  }

  const workbenchStyle = workbench ? getComputedStyle(workbench) : null;
  const railStyle = rail ? getComputedStyle(rail) : null;
  const gridColumns = workbenchStyle
    ? workbenchStyle.gridTemplateColumns.split(/\s+/).filter(Boolean)
    : [];
  const orderedSiblings = siblings
    .slice()
    .sort((left, right) => left.rect.y - right.rect.y || left.rect.x - right.rect.x);
  const stackedOrder = orderedSiblings.every((item, index) => {
    if (index === 0) return true;
    return orderedSiblings[index - 1].rect.bottom <= item.rect.y + 1;
  });
  const railOverflowRequired = Boolean(
    rail && rail.scrollHeight > rail.clientHeight + 1
  );
  const railScrollableWhenRequired = !railOverflowRequired || Boolean(
    railStyle && ["auto", "scroll"].includes(railStyle.overflowY)
  );

  return {
    schema: "mcel-lab-deployed-geometry-v1",
    route: location.pathname,
    hostMatchCount: hostMatches.length,
    editorMatchCount: editorMatches.length,
    hostRect: rectOf(hostMatches[0] || null),
    editorRect: rectOf(editorMatches[0] || null),
    workbench: workbench ? {
      rect: rectOf(workbench),
      display: workbenchStyle.display,
      gridTemplateAreas: workbenchStyle.gridTemplateAreas,
      gridTemplateColumns: workbenchStyle.gridTemplateColumns,
      gridColumnCount: gridColumns.length,
      overflowY: workbenchStyle.overflowY,
      clientHeight: workbench.clientHeight,
      scrollHeight: workbench.scrollHeight,
      contentSized: workbench.scrollHeight <= workbench.clientHeight + 1
    } : null,
    rail: rail ? {
      rect: rectOf(rail),
      overflowY: railStyle.overflowY,
      clientHeight: rail.clientHeight,
      scrollHeight: rail.scrollHeight,
      overflowRequired: railOverflowRequired,
      scrollableWhenRequired: railScrollableWhenRequired
    } : null,
    railChildren,
    internallyClippedRailChildren: railChildren
      .filter((item) => item.internallyClipped)
      .map((item) => item.selector),
    siblingOverlaps,
    stackedOrder
  };
}"""


def resolve_browser_executable(explicit: str | None = None) -> str | None:
    value = str(explicit or "").strip()
    if value:
        path = Path(value).expanduser()
        if not path.is_file():
            raise ValueError(f"Browser executable does not exist: {value}")
        return str(path.resolve())
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    return None


def collect_geometry(page: Any, _scenario: flog.RuntimeScenario) -> dict[str, Any]:
    result = page.evaluate(GEOMETRY_PROBE_JS)
    if not isinstance(result, dict):
        raise TypeError("MCEL Lab deployed geometry probe returned a non-object result")
    return result


def _diagnostic_no_throw_passed(trial: dict[str, Any]) -> bool:
    classification = trial.get("classification")
    if not isinstance(classification, dict):
        return False
    conformance = classification.get("appSurfaceConformance")
    if not isinstance(conformance, dict):
        return False
    layers = conformance.get("layers")
    if not isinstance(layers, list):
        return False
    return any(
        isinstance(layer, dict)
        and layer.get("id") == "diagnostic-no-throw"
        and layer.get("status") == "pass"
        for layer in layers
    )


def classify_geometry(
    profile: ViewportProfile,
    trial: dict[str, Any],
) -> dict[str, Any]:
    geometry = trial.get("supplementalEvidence")
    if not isinstance(geometry, dict):
        geometry = {}
    classification = trial.get("classification")
    if not isinstance(classification, dict):
        classification = {}

    failures: list[str] = []
    warnings: list[str] = []
    route = str(geometry.get("route") or "")
    if route != ROUTE:
        failures.append(f"deployed route mismatch: expected {ROUTE}, observed {route or 'missing'}")

    if int(geometry.get("hostMatchCount") or 0) != 1:
        failures.append("expected exactly one visible MCEL Lab primary host")
    if int(geometry.get("editorMatchCount") or 0) != 1:
        failures.append("expected exactly one visible MCEL Lab authoritative work surface")

    editor_rect = geometry.get("editorRect")
    if not isinstance(editor_rect, dict):
        failures.append("authoritative work-surface rectangle is missing")
    elif profile.requires_desktop_minimum:
        width = float(editor_rect.get("width") or 0)
        height = float(editor_rect.get("height") or 0)
        if width + 0.5 < MIN_DESKTOP_WIDTH:
            failures.append(
                f"authoritative work surface width {width:.1f}px is below {MIN_DESKTOP_WIDTH}px"
            )
        if height + 0.5 < MIN_DESKTOP_HEIGHT:
            failures.append(
                f"authoritative work surface height {height:.1f}px is below {MIN_DESKTOP_HEIGHT}px"
            )

    clipped = geometry.get("internallyClippedRailChildren")
    if not isinstance(clipped, list):
        clipped = []
    if clipped:
        failures.append(
            "right-rail children do not contain accessible internal content: "
            + ", ".join(map(str, clipped))
        )

    rail = geometry.get("rail")
    if not isinstance(rail, dict):
        failures.append("right rail is missing")
    elif rail.get("scrollableWhenRequired") is not True:
        failures.append("right rail is not scrollable while its content exceeds its height")

    overlaps = geometry.get("siblingOverlaps")
    if not isinstance(overlaps, list):
        overlaps = []
    if overlaps:
        failures.append(f"{len(overlaps)} workbench sibling overlap(s) detected")

    workbench = geometry.get("workbench")
    if not isinstance(workbench, dict):
        failures.append("workbench geometry is missing")
    elif profile.layout == "stacked":
        if int(workbench.get("gridColumnCount") or 0) != 1:
            failures.append("stacked layout did not collapse to one grid column")
        if str(workbench.get("overflowY") or "") not in {"visible", "clip"}:
            failures.append("stacked workbench retains an internal vertical scroll owner")
        if workbench.get("contentSized") is not True:
            failures.append("stacked workbench did not return to content-sized block flow")
        if geometry.get("stackedOrder") is not True:
            failures.append("stacked workbench siblings are not in non-overlapping source order")

    if classification.get("status") != "pass":
        base_failures = classification.get("failures")
        if isinstance(base_failures, list) and base_failures:
            failures.extend(f"runtime diagnosis: {item}" for item in base_failures)
        else:
            failures.append("runtime diagnosis did not pass")
    if not _diagnostic_no_throw_passed(trial):
        failures.append("diagnostic-no-throw layer did not pass")

    return {
        "schema": "mcel-lab-deployed-conformance-result-v1",
        "profile": profile.to_dict(),
        "status": "pass" if not failures else "fail",
        "failures": list(dict.fromkeys(failures)),
        "warnings": warnings,
        "geometry": geometry,
    }


def build_report(
    *,
    repo: Path,
    base_url: str,
    scenario: flog.RuntimeScenario,
    trials: list[dict[str, Any]],
    profile_results: list[dict[str, Any]],
) -> dict[str, Any]:
    canonical_viewport = VIEWPORT_PROFILES[-1].viewport()
    report = flog.build_report(
        repo=repo,
        base_url=base_url,
        scenarios=[scenario],
        trials=trials,
        viewport=canonical_viewport,
    )
    profile_status = "pass" if all(
        item.get("status") == "pass" for item in profile_results
    ) else "fail"
    report["source"]["deployedConformanceSource"] = (
        "real /applications/mcel-lab route measured by Playwright at authorized viewports"
    )
    report["viewportProfiles"] = [profile.to_dict() for profile in VIEWPORT_PROFILES]
    report["mcelLabDeployedConformance"] = {
        "schema": "mcel-lab-deployed-conformance-summary-v1",
        "version": PROFILE_VERSION,
        "route": ROUTE,
        "hostSelector": HOST_SELECTOR,
        "editorSelector": EDITOR_SELECTOR,
        "workbenchSelector": WORKBENCH_SELECTOR,
        "railSelector": RAIL_SELECTOR,
        "desktopMinimum": {
            "width": MIN_DESKTOP_WIDTH,
            "height": MIN_DESKTOP_HEIGHT,
        },
        "status": profile_status,
        "profiles": profile_results,
    }
    report["summary"]["deployedConformanceStatus"] = profile_status
    if profile_status != "pass":
        report["summary"]["status"] = "fail"
    return report


def render_markdown(report: dict[str, Any]) -> str:
    base = flog.render_markdown(report).rstrip()
    conformance = report.get("mcelLabDeployedConformance")
    if not isinstance(conformance, dict):
        return base + "\n"
    lines = [
        base,
        "",
        "## MCEL Lab deployed-route conformance",
        "",
        f"- Profile version: `{conformance.get('version', '')}`",
        f"- Route: `{conformance.get('route', '')}`",
        f"- Status: **{conformance.get('status', 'unknown')}**",
        "",
        "| Viewport profile | Layout | Status | Work surface | Rail clipping | Sibling overlaps |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for item in conformance.get("profiles") or []:
        profile = item.get("profile") if isinstance(item, dict) else {}
        geometry = item.get("geometry") if isinstance(item, dict) else {}
        editor = geometry.get("editorRect") if isinstance(geometry, dict) else {}
        if not isinstance(editor, dict):
            editor = {}
        clipped = geometry.get("internallyClippedRailChildren") if isinstance(geometry, dict) else []
        overlaps = geometry.get("siblingOverlaps") if isinstance(geometry, dict) else []
        lines.append(
            "| {name} | {layout} | {status} | {width:.1f}×{height:.1f} | {clipped} | {overlaps} |".format(
                name=profile.get("name", ""),
                layout=profile.get("layout", ""),
                status=item.get("status", "unknown") if isinstance(item, dict) else "unknown",
                width=float(editor.get("width") or 0),
                height=float(editor.get("height") or 0),
                clipped=len(clipped) if isinstance(clipped, list) else 0,
                overlaps=len(overlaps) if isinstance(overlaps, list) else 0,
            )
        )
        for failure in item.get("failures") or []:
            lines.append(f"- `{profile.get('name', '')}` failure: {failure}")
    lines.extend(
        [
            "",
            "This fixture is read-only. It navigates to the deployed route, waits for the existing diagnostics APIs, reads geometry and computed styles, and emits repository-bound evidence.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report_files(report: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "mcel-runtime-flog-report.json"
    markdown_path = output_dir / "mcel-runtime-flog-report.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report) + "\n", encoding="utf-8")
    return {"json": str(json_path), "markdown": str(markdown_path)}


def run_deployed_profile(
    *,
    repo: Path,
    base_url: str,
    headed: bool = False,
    timeout_ms: int = flog.DEFAULT_TIMEOUT_MS,
    startup_wait_ms: int = flog.DEFAULT_STARTUP_WAIT_MS,
    require_zero_warnings: bool = True,
    browser_executable: str | None = None,
) -> dict[str, Any]:
    scenarios = flog.build_scenarios(
        repo,
        apps=["mcel-lab"],
        startup_wait_ms=max(0, int(startup_wait_ms)),
    )
    if len(scenarios) != 1:
        raise RuntimeError("MCEL Lab deployed fixture expected exactly one registry scenario")
    scenario = scenarios[0]

    trials: list[dict[str, Any]] = []
    profile_results: list[dict[str, Any]] = []
    for profile in VIEWPORT_PROFILES:
        profile_trials = flog.run_browser_scenarios(
            [scenario],
            base_url=base_url,
            headed=headed,
            timeout_ms=max(1000, int(timeout_ms)),
            emit_events=False,
            require_zero_warnings=require_zero_warnings,
            viewport=profile.viewport(),
            trial_probe=collect_geometry,
            browser_executable=browser_executable,
        )
        if len(profile_trials) != 1:
            raise RuntimeError(
                f"MCEL Lab deployed fixture expected one trial for {profile.name}"
            )
        trial = profile_trials[0]
        trial["scenarioId"] = f"mcel-lab.deployed-{profile.name}"
        trial["viewportProfile"] = profile.to_dict()
        result = classify_geometry(profile, trial)
        trial["deployedConformance"] = {
            key: value for key, value in result.items() if key != "geometry"
        }
        trials.append(trial)
        profile_results.append(result)

    return build_report(
        repo=repo,
        base_url=base_url,
        scenario=scenario,
        trials=trials,
        profile_results=profile_results,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--startup-wait-ms", type=int, default=flog.DEFAULT_STARTUP_WAIT_MS)
    parser.add_argument("--timeout-ms", type=int, default=flog.DEFAULT_TIMEOUT_MS)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--allow-warnings", action="store_true")
    parser.add_argument("--allow-fail", action="store_true")
    parser.add_argument("--browser-executable", default="")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo = flog.repo_root_from_script()
    browser_executable = resolve_browser_executable(args.browser_executable)
    report = run_deployed_profile(
        repo=repo,
        base_url=args.base_url,
        headed=bool(args.headed),
        timeout_ms=max(1000, int(args.timeout_ms)),
        startup_wait_ms=max(0, int(args.startup_wait_ms)),
        require_zero_warnings=not bool(args.allow_warnings),
        browser_executable=browser_executable,
    )
    paths = write_report_files(report, args.output_dir)
    report["artifacts"] = paths

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        summary = report.get("summary") or {}
        conformance = report.get("mcelLabDeployedConformance") or {}
        print(PROFILE_VERSION)
        print(f"base_url: {report.get('baseUrl', '')}")
        print(f"status: {summary.get('status', 'unknown')}")
        print(f"deployed_conformance: {conformance.get('status', 'unknown')}")
        print(f"repository_fingerprint: {(report.get('repositoryProvenance') or {}).get('fingerprint', '')}")
        print(f"json: {paths['json']}")
        print(f"markdown: {paths['markdown']}")
        for result in conformance.get("profiles") or []:
            profile = result.get("profile") or {}
            suffix = ""
            if result.get("failures"):
                suffix = " :: " + "; ".join(result["failures"])
            print(f"  {result.get('status', 'unknown')}: {profile.get('name', '')}{suffix}")

    return 0 if args.allow_fail or report["summary"]["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
