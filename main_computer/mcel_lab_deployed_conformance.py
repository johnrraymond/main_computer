#!/usr/bin/env python3
"""Deployed-route MCEL Lab runtime conformance fixture.

This fixture measures the real ``/applications/mcel-lab`` route and its
registry-backed Form aspect at the authorized desktop and stacked-layout
viewports. It extends the existing MCEL runtime FLOG report with deterministic
semantic-form provenance and geometry evidence while preserving the
``mcel-runtime-flog-report-v2`` schema consumed by the repository truth audit.

Run from the repository root while the viewport server is already running::

    python main_computer/mcel_lab_deployed_conformance.py \
      --base-url http://127.0.0.1:8765

The generated report remains runtime evidence, not a source mutation or a
maturity promotion. By default it writes app-scoped evidence outside the
canonical all-app FLOG report directory.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

try:
    from . import flog_mcel_runtime_smoke as flog
except ImportError:  # Direct execution from the repository root.
    import flog_mcel_runtime_smoke as flog


DEFAULT_OUTPUT_DIR = Path("runtime/reports/flog/mcel-lab-deployed-conformance")
PROFILE_VERSION = "mcel-lab-deployed-conformance-v2"
ROUTE = "/applications/mcel-lab"
HOST_SELECTOR = ".mcel-lab-blueprint-primary"
EDITOR_SELECTOR = "#mcel-blueprint-work-surface"
WORKBENCH_SELECTOR = ".mcel-lab-blueprint-workbench"
RAIL_SELECTOR = ".mcel-lab-blueprint-right-rail"
MIN_DESKTOP_WIDTH = 640
MIN_DESKTOP_HEIGHT = 420
EXPECTED_FORM_CARD_COUNT = 9
EXPECTED_FORM_GROUP_COUNT = 8
EXPECTED_FORM_KIND_COUNTS = {
    "subject": 1,
    "action": 1,
    "work-surface": 1,
    "context": 2,
    "feedback": 1,
    "constraint": 1,
    "transient": 1,
    "interruption": 1,
}


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


GEOMETRY_PROBE_JS = r"""async () => {
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
    const primitiveId = element.dataset?.mcelFormPrimitiveId;
    if (primitiveId) return `[data-mcel-form-primitive-id="${primitiveId}"]`;
    const classes = Array.from(element.classList || []).slice(0, 3);
    if (classes.length) return `${element.tagName.toLowerCase()}.${classes.join(".")}`;
    return `${element.tagName.toLowerCase()}:nth-child(${index + 1})`;
  };

  const overlapPairs = (elements) => {
    const items = elements.map((element, index) => ({
      selector: labelOf(element, index),
      rect: rectOf(element)
    }));
    const overlaps = [];
    for (let leftIndex = 0; leftIndex < items.length; leftIndex += 1) {
      for (let rightIndex = leftIndex + 1; rightIndex < items.length; rightIndex += 1) {
        const left = items[leftIndex];
        const right = items[rightIndex];
        const intersectionWidth = Math.max(
          0,
          Math.min(left.rect.right, right.rect.right) - Math.max(left.rect.x, right.rect.x)
        );
        const intersectionHeight = Math.max(
          0,
          Math.min(left.rect.bottom, right.rect.bottom) - Math.max(left.rect.y, right.rect.y)
        );
        if (intersectionWidth > 1 && intersectionHeight > 1) {
          overlaps.push({
            left: left.selector,
            right: right.selector,
            intersectionWidth,
            intersectionHeight
          });
        }
      }
    }
    return overlaps;
  };

  const nextFrame = () => new Promise((resolve) => {
    if (typeof requestAnimationFrame === "function") {
      requestAnimationFrame(() => resolve());
    } else {
      setTimeout(resolve, 0);
    }
  });

  const appSelect = document.querySelector("#mcel-blueprint-app-select");
  if (appSelect && appSelect.value !== "mcel-lab") {
    appSelect.value = "mcel-lab";
    appSelect.dispatchEvent(new Event("change", {bubbles: true}));
    await nextFrame();
    await nextFrame();
  }

  const aspectSelect = document.querySelector("#mcel-blueprint-aspect-select");
  if (aspectSelect && aspectSelect.value !== "form") {
    aspectSelect.value = "form";
    aspectSelect.dispatchEvent(new Event("change", {bubbles: true}));
    await nextFrame();
    await nextFrame();
  }

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
    ? Array.from(workbench.children).filter(visible)
    : [];
  const siblingOverlaps = overlapPairs(siblings);

  const workbenchStyle = workbench ? getComputedStyle(workbench) : null;
  const railStyle = rail ? getComputedStyle(rail) : null;
  const gridColumns = workbenchStyle
    ? workbenchStyle.gridTemplateColumns.split(/\s+/).filter(Boolean)
    : [];
  const orderedSiblings = siblings
    .map((element, index) => ({selector: labelOf(element, index), rect: rectOf(element)}))
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

  const workSurface = editorMatches[0] || null;
  const workSurfaceStyle = workSurface ? getComputedStyle(workSurface) : null;
  const formViewers = workSurface
    ? Array.from(workSurface.querySelectorAll(
        '.mcel-lab-form-primitive-viewer[data-mcel-form-primitive-mode="work-surface"]'
      )).filter(visible)
    : [];
  const formViewer = formViewers[0] || null;
  const formCards = formViewer
    ? Array.from(formViewer.querySelectorAll(".mcel-lab-form-primitive-card")).filter(visible)
    : [];
  const formGroups = formViewer
    ? Array.from(formViewer.querySelectorAll(".mcel-lab-form-primitive-group")).filter(visible)
    : [];
  const primitiveKindCounts = {};
  formCards.forEach((card) => {
    const kind = String(card.dataset.mcelFormPrimitiveKind || "").trim();
    if (kind) primitiveKindCounts[kind] = (primitiveKindCounts[kind] || 0) + 1;
  });
  const sourceBoundCards = formCards.filter((card) => {
    const start = Number.parseInt(card.dataset.mcelFormPrimitiveSourceStart || "", 10);
    const end = Number.parseInt(card.dataset.mcelFormPrimitiveSourceEnd || "", 10);
    return Boolean(
      card.dataset.mcelFormPrimitiveSourceFile &&
      Number.isFinite(start) &&
      start > 0 &&
      Number.isFinite(end) &&
      end >= start
    );
  });
  const cardsWithFact = (label) => formCards.filter((card) =>
    Array.from(card.querySelectorAll(".mcel-lab-form-primitive-facts dt"))
      .some((term) => term.textContent.trim() === label)
  );
  const internallyClippedFormCards = formCards.filter((card) => {
    const style = getComputedStyle(card);
    const horizontalOverflow = card.scrollWidth > card.clientWidth + 1;
    const verticalOverflow = card.scrollHeight > card.clientHeight + 1;
    const horizontalScrollable = ["auto", "scroll"].includes(style.overflowX);
    const verticalScrollable = ["auto", "scroll"].includes(style.overflowY);
    return (
      (horizontalOverflow && !horizontalScrollable) ||
      (verticalOverflow && !verticalScrollable)
    );
  });
  const workSurfaceOverflowRequired = Boolean(
    workSurface && workSurface.scrollHeight > workSurface.clientHeight + 1
  );
  const workSurfaceScrollableWhenRequired = !workSurfaceOverflowRequired || Boolean(
    workSurfaceStyle && ["auto", "scroll"].includes(workSurfaceStyle.overflowY)
  );

  return {
    schema: "mcel-lab-deployed-geometry-v2",
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
    stackedOrder,
    semanticForm: {
      selectedApp: appSelect ? appSelect.value : "",
      selectedAspect: aspectSelect ? aspectSelect.value : "",
      viewerCount: formViewers.length,
      viewerRect: rectOf(formViewer),
      cardCount: formCards.length,
      groupCount: formGroups.length,
      primitiveKindCounts,
      primitiveIds: formCards.map((card) => card.dataset.mcelFormPrimitiveId || ""),
      sourceBoundCardCount: sourceBoundCards.length,
      contractStatusCardCount: cardsWithFact("Contract status").length,
      sourceFactCardCount: cardsWithFact("Source").length,
      internallyClippedCardIds: internallyClippedFormCards.map(
        (card) => card.dataset.mcelFormPrimitiveId || ""
      ),
      cardOverlaps: overlapPairs(formCards),
      workSurfaceOverflowRequired,
      workSurfaceScrollableWhenRequired
    }
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

    semantic_form = geometry.get("semanticForm")
    if not isinstance(semantic_form, dict):
        failures.append("semantic-form evidence is missing")
        semantic_form = {}
    else:
        if semantic_form.get("selectedApp") != "mcel-lab":
            failures.append("semantic-form probe did not select MCEL Lab")
        if semantic_form.get("selectedAspect") != "form":
            failures.append("semantic-form probe did not select the Form aspect")
        if int(semantic_form.get("viewerCount") or 0) != 1:
            failures.append("expected exactly one authoritative semantic-form work-surface viewer")
        if int(semantic_form.get("cardCount") or 0) != EXPECTED_FORM_CARD_COUNT:
            failures.append(
                "semantic-form viewer rendered "
                f"{int(semantic_form.get('cardCount') or 0)} cards; "
                f"expected {EXPECTED_FORM_CARD_COUNT}"
            )
        if int(semantic_form.get("groupCount") or 0) != EXPECTED_FORM_GROUP_COUNT:
            failures.append(
                "semantic-form viewer rendered "
                f"{int(semantic_form.get('groupCount') or 0)} groups; "
                f"expected {EXPECTED_FORM_GROUP_COUNT}"
            )
        kind_counts = semantic_form.get("primitiveKindCounts")
        if not isinstance(kind_counts, dict):
            kind_counts = {}
        observed_kind_counts = {
            str(kind): int(kind_counts.get(kind) or 0)
            for kind in EXPECTED_FORM_KIND_COUNTS
        }
        if observed_kind_counts != EXPECTED_FORM_KIND_COUNTS:
            failures.append(
                "semantic-form primitive kind counts differ from the registry-backed "
                f"MCEL Lab contract: {observed_kind_counts}"
            )
        if int(semantic_form.get("sourceBoundCardCount") or 0) != EXPECTED_FORM_CARD_COUNT:
            failures.append("not every semantic-form primitive card has exact source provenance")
        if int(semantic_form.get("contractStatusCardCount") or 0) != EXPECTED_FORM_CARD_COUNT:
            failures.append("not every semantic-form primitive card exposes Contract status")
        if int(semantic_form.get("sourceFactCardCount") or 0) != EXPECTED_FORM_CARD_COUNT:
            failures.append("not every semantic-form primitive card exposes its Source")
        clipped_form_cards = semantic_form.get("internallyClippedCardIds")
        if not isinstance(clipped_form_cards, list):
            clipped_form_cards = []
        if clipped_form_cards:
            failures.append(
                "semantic-form primitive cards clip readable content: "
                + ", ".join(map(str, clipped_form_cards))
            )
        form_overlaps = semantic_form.get("cardOverlaps")
        if not isinstance(form_overlaps, list):
            form_overlaps = []
        if form_overlaps:
            failures.append(
                f"{len(form_overlaps)} semantic-form primitive card overlap(s) detected"
            )
        if semantic_form.get("workSurfaceScrollableWhenRequired") is not True:
            failures.append(
                "semantic-form work surface is not scrollable while primitive content exceeds it"
            )

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
        "schema": "mcel-lab-deployed-conformance-result-v2",
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
        evidence_scope=flog.build_evidence_scope(
            scenarios=[scenario],
            selected_apps=["mcel-lab"],
            selected_scenarios=[trial.get("scenarioId", "") for trial in trials],
            force_partial_kind="deployed-app-scoped",
        ),
    )
    profile_status = "pass" if all(
        item.get("status") == "pass" for item in profile_results
    ) else "fail"
    report["source"]["deployedConformanceSource"] = (
        "real /applications/mcel-lab route and registry-backed Form aspect measured by Playwright at authorized viewports"
    )
    report["viewportProfiles"] = [profile.to_dict() for profile in VIEWPORT_PROFILES]
    report["mcelLabDeployedConformance"] = {
        "schema": "mcel-lab-deployed-conformance-summary-v2",
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
    report["summary"]["semanticFormConformanceStatus"] = profile_status
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
        "| Viewport profile | Layout | Status | Work surface | Form cards | Provenance | Form clipping | Form overlaps |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in conformance.get("profiles") or []:
        profile = item.get("profile") if isinstance(item, dict) else {}
        geometry = item.get("geometry") if isinstance(item, dict) else {}
        editor = geometry.get("editorRect") if isinstance(geometry, dict) else {}
        if not isinstance(editor, dict):
            editor = {}
        semantic_form = geometry.get("semanticForm") if isinstance(geometry, dict) else {}
        if not isinstance(semantic_form, dict):
            semantic_form = {}
        form_clipping = semantic_form.get("internallyClippedCardIds")
        form_overlaps = semantic_form.get("cardOverlaps")
        lines.append(
            "| {name} | {layout} | {status} | {width:.1f}×{height:.1f} | {cards} | {sources} | {clipped} | {overlaps} |".format(
                name=profile.get("name", ""),
                layout=profile.get("layout", ""),
                status=item.get("status", "unknown") if isinstance(item, dict) else "unknown",
                width=float(editor.get("width") or 0),
                height=float(editor.get("height") or 0),
                cards=int(semantic_form.get("cardCount") or 0),
                sources=int(semantic_form.get("sourceBoundCardCount") or 0),
                clipped=len(form_clipping) if isinstance(form_clipping, list) else 0,
                overlaps=len(form_overlaps) if isinstance(form_overlaps, list) else 0,
            )
        )
        for failure in item.get("failures") or []:
            lines.append(f"- `{profile.get('name', '')}` failure: {failure}")
    lines.extend(
        [
            "",
            "This fixture is read-only. It navigates to the deployed route, selects MCEL Lab's existing Form aspect, reads registry-backed primitive provenance plus geometry and computed styles, and emits repository-bound evidence.",
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
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=f"Report directory (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--overwrite-canonical",
        action="store_true",
        help="Allow this MCEL Lab-only fixture to replace the canonical all-app FLOG report.",
    )
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
    requested_output = args.output_dir
    try:
        if requested_output is None:
            output_dir = (
                flog.DEFAULT_OUTPUT_DIR if args.overwrite_canonical else DEFAULT_OUTPUT_DIR
            )
        else:
            output_dir = Path(requested_output)
            output_resolved = (
                output_dir.resolve()
                if output_dir.is_absolute()
                else (repo / output_dir).resolve()
            )
            canonical_resolved = (repo / flog.DEFAULT_OUTPUT_DIR).resolve()
            if (
                output_resolved == canonical_resolved
                and not args.overwrite_canonical
            ):
                raise ValueError(
                    "MCEL Lab deployed evidence is app-scoped and cannot replace the canonical "
                    "all-app FLOG report without --overwrite-canonical."
                )
        browser_executable = resolve_browser_executable(args.browser_executable)
    except ValueError as exc:
        print(f"mcel lab deployed conformance error: {exc}", file=sys.stderr)
        return 2
    report = run_deployed_profile(
        repo=repo,
        base_url=args.base_url,
        headed=bool(args.headed),
        timeout_ms=max(1000, int(args.timeout_ms)),
        startup_wait_ms=max(0, int(args.startup_wait_ms)),
        require_zero_warnings=not bool(args.allow_warnings),
        browser_executable=browser_executable,
    )
    paths = write_report_files(report, output_dir)
    report["artifacts"] = paths

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        summary = report.get("summary") or {}
        conformance = report.get("mcelLabDeployedConformance") or {}
        print(PROFILE_VERSION)
        print(f"base_url: {report.get('baseUrl', '')}")
        print(f"status: {summary.get('status', 'unknown')}")
        print(f"evidence_scope: {(report.get('evidenceScope') or {}).get('kind', 'unknown')}")
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
