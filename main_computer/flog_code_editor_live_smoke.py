"""FLOG live Code Editor workbench proof.

This smoke measures the real MCEL Code Editor application rather than a
synthetic hierarchy.  It concentrates on two general laws exposed by the first
live dock-workbench integration:

    A required center primary-work unit must fill its allocated center slot,
    not the whole workbench body when sibling docks own part of that body.

    Active primary-work descendants must remain contained inside their owned
    slot and may not be clipped or intercepted by foreign dock owners.

The smoke does not repair CSS or mutate application source.  It renders the
expanded Applications shell in Chromium, applies semantic layout operations
through ``MainComputerCodeEditorLayoutController``, and records:

* explicit or inferred center-slot ownership;
* center/editor allocation in both axes;
* active-pane and primary work-surface delivery;
* unused owned space;
* wrapper-chain shrink/stretch styles;
* collapsed/expanded proof-dock reclamation;
* clipped, escaped, or foreign-intercepted active controls;
* exact foreign hit owners and compact hit-test stacks;
* PNG evidence and compact JSON/Markdown reports.

Run from the repository root:

    python main_computer/flog_code_editor_live_smoke.py

Useful options:

    python main_computer/flog_code_editor_live_smoke.py \
        --viewports wide=1600x1000,desktop=1440x900,medium=1200x820
    python main_computer/flog_code_editor_live_smoke.py --headed
    python main_computer/flog_code_editor_live_smoke.py --allow-fail

A failing result identifies either a true owned-slot fill defect or a nested
containment/interception defect.  Sibling docks and tab chrome are excluded from
the editor denominator because they own their own declared tracks.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPORT_KIND = "mcel.flog.code-editor.live-workbench"
REPORT_VERSION = "mcel.flog.code-editor.ownership-and-containment.v2"
DEFAULT_OUTPUT_DIR = Path("reports/flog/code-editor-live")
DEFAULT_VIEWPORTS: tuple[tuple[str, int, int], ...] = (
    ("wide", 1600, 1000),
    ("desktop", 1440, 900),
    ("medium", 1200, 820),
    ("compact", 840, 720),
)
DEFAULT_STATES: tuple[str, ...] = (
    "runtime-collapsed-proof",
    "source-collapsed-proof",
    "runtime-expanded-proof",
    "inspector-bottom",
    "inspector-tab-editor",
    "explorer-collapsed",
)
ACCEPTABLE_STATUSES = {"pass", "watch"}

EDITOR_MIN_BLOCK_PX = 320.0
EDITOR_GROUP_FILL_PASS = 0.96
EDITOR_GROUP_FILL_WATCH = 0.92
ACTIVE_PANE_FILL_PASS = 0.96
ACTIVE_PANE_FILL_WATCH = 0.92
PRIMARY_SURFACE_FILL_PASS = 0.90
PRIMARY_SURFACE_FILL_WATCH = 0.82
COLLAPSED_PROOF_MAX_PX = 48.0
EXPANDED_PROOF_MIN_PX = 160.0
CONTROL_ACTIONABLE_SAMPLE_MIN = 1


@dataclass(frozen=True)
class ViewportProfile:
    name: str
    width: int
    height: int


@dataclass(frozen=True)
class TrialSpec:
    name: str
    editor_tab: str = "runtime"
    inspector_placement: str = "right"
    explorer_collapsed: bool = False
    proof_expanded: bool = False


TRIAL_SPECS: dict[str, TrialSpec] = {
    "runtime-collapsed-proof": TrialSpec(
        name="runtime-collapsed-proof",
        editor_tab="runtime",
        inspector_placement="right",
        proof_expanded=False,
    ),
    "source-collapsed-proof": TrialSpec(
        name="source-collapsed-proof",
        editor_tab="source",
        inspector_placement="right",
        proof_expanded=False,
    ),
    "runtime-expanded-proof": TrialSpec(
        name="runtime-expanded-proof",
        editor_tab="runtime",
        inspector_placement="right",
        proof_expanded=True,
    ),
    "inspector-bottom": TrialSpec(
        name="inspector-bottom",
        editor_tab="runtime",
        inspector_placement="bottom",
        proof_expanded=False,
    ),
    "inspector-tab-editor": TrialSpec(
        name="inspector-tab-editor",
        editor_tab="runtime",
        inspector_placement="tab",
        proof_expanded=False,
    ),
    "explorer-collapsed": TrialSpec(
        name="explorer-collapsed",
        editor_tab="runtime",
        inspector_placement="right",
        explorer_collapsed=True,
        proof_expanded=False,
    ),
}


def parse_viewports(text: str | None) -> list[ViewportProfile]:
    if not text:
        return [ViewportProfile(*item) for item in DEFAULT_VIEWPORTS]

    profiles: list[ViewportProfile] = []
    for chunk in text.split(","):
        item = chunk.strip()
        if not item:
            continue
        if "=" not in item or "x" not in item.lower():
            raise ValueError(
                f"Invalid viewport {item!r}; expected name=WIDTHxHEIGHT"
            )
        name, dimensions = item.split("=", 1)
        width_text, height_text = dimensions.lower().split("x", 1)
        width = int(width_text)
        height = int(height_text)
        if width <= 0 or height <= 0:
            raise ValueError(f"Viewport dimensions must be positive: {item!r}")
        profiles.append(ViewportProfile(name.strip(), width, height))
    if not profiles:
        raise ValueError("At least one viewport is required")
    return profiles


def parse_states(text: str | None) -> list[TrialSpec]:
    names = list(DEFAULT_STATES) if not text else [
        part.strip() for part in text.split(",") if part.strip()
    ]
    missing = [name for name in names if name not in TRIAL_SPECS]
    if missing:
        raise ValueError(
            "Unknown state(s): "
            + ", ".join(missing)
            + ". Available: "
            + ", ".join(TRIAL_SPECS)
        )
    return [TRIAL_SPECS[name] for name in names]


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def inspect_static_contract(repo: Path) -> dict[str, Any]:
    """Inspect the source-owned contract without running the browser."""

    html_path = (
        repo
        / "main_computer"
        / "web"
        / "applications"
        / "apps"
        / "code-editor.html"
    )
    contract_path = (
        repo
        / "main_computer"
        / "web"
        / "applications"
        / "scripts"
        / "code-editor-layout-contract.js"
    )

    missing_files = [
        str(path.relative_to(repo))
        for path in (html_path, contract_path)
        if not path.exists()
    ]
    if missing_files:
        return {
            "state": "missing",
            "missingFiles": missing_files,
            "checks": [],
            "failures": [f"missing required file: {item}" for item in missing_files],
        }

    html = _read_text(html_path)
    contract = _read_text(contract_path)

    checks = [
        {
            "id": "editor-primary-work",
            "passed": bool(
                re.search(
                    r'class="[^"]*code-studio-editor-group[^"]*"[^>]*'
                    r'data-mc-layout-prefer="center"[^>]*'
                    r'data-mc-layout-strength="required"',
                    html,
                    flags=re.S,
                )
            ),
            "reason": (
                "the live editor group is authored as a required center unit"
            ),
        },
        {
            "id": "stable-editor-user-id",
            "passed": 'data-mc-layout-user-id="code-editor.editor"' in html,
            "reason": "the primary editor has a stable semantic layout identity",
        },
        {
            "id": "contract-primary-work-role",
            "passed": bool(
                re.search(
                    r'"code-editor\.editor"\s*:\s*\{.*?'
                    r'role\s*:\s*"primary-work".*?'
                    r'required\s*:\s*true',
                    contract,
                    flags=re.S,
                )
            ),
            "reason": "the sidecar contract declares the editor primary and required",
        },
        {
            "id": "editor-minimum-block",
            "passed": bool(
                re.search(
                    r'"code-editor\.editor"\s*:\s*\{.*?'
                    r'minBlock\s*:\s*3[2-9]\d',
                    contract,
                    flags=re.S,
                )
            ),
            "reason": "the sidecar contract carries a useful minimum block size",
        },
        {
            "id": "semantic-layout-controller",
            "passed": "MainComputerCodeEditorLayoutController" in contract,
            "reason": "the live semantic layout controller is available",
        },
    ]

    explicit_fill_hints = {
        "fill": bool(
            re.search(
                r'class="[^"]*code-studio-editor-group[^"]*"[^>]*'
                r'data-mc-layout-fill=',
                html,
                flags=re.S,
            )
        ),
        "grow": bool(
            re.search(
                r'class="[^"]*code-studio-editor-group[^"]*"[^>]*'
                r'data-mc-layout-grow=',
                html,
                flags=re.S,
            )
        ),
        "tracks": bool(
            re.search(
                r'class="[^"]*code-studio-editor-group[^"]*"[^>]*'
                r'data-mc-layout-tracks=',
                html,
                flags=re.S,
            )
        ),
    }

    explicit_center_slot = bool(
        re.search(
            r'data-mc(?:el)?-layout-(?:slot|zone)="center"',
            html,
            flags=re.S,
        )
    )

    failures = [
        check["reason"] for check in checks if not bool(check["passed"])
    ]
    return {
        "state": "complete" if not failures else "incomplete",
        "checks": checks,
        "failures": failures,
        "fillLaw": {
            "source": (
                "explicit"
                if all(explicit_fill_hints.values())
                else "inferred-from-required-center-primary-work"
            ),
            "explicitHints": explicit_fill_hints,
            "requiredBehavior": (
                "the editor group, active editor pane, and active primary surface "
                "must consume their allocated remaining block track"
            ),
        },
        "ownershipSlotLaw": {
            "source": (
                "explicit-center-slot"
                if explicit_center_slot
                else "inferred-from-live-dock-owners"
            ),
            "explicitCenterSlot": explicit_center_slot,
            "requiredBehavior": (
                "editor fill is measured against the center slot after excluding "
                "sibling dock and tab-chrome ownership"
            ),
        },
        "files": {
            "html": str(html_path.relative_to(repo)),
            "contract": str(contract_path.relative_to(repo)),
        },
    }


def _foreign_owner_labels(controls: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for owner in controls.get("foreignOwners") or []:
        identity = str(
            owner.get("layoutUserId")
            or owner.get("componentId")
            or owner.get("region")
            or owner.get("selector")
            or "unknown-owner"
        )
        placement = str(owner.get("placement") or "").strip()
        controls_list = [
            str(item) for item in (owner.get("controls") or []) if str(item)
        ]
        detail = identity
        if placement:
            detail += f" [{placement}]"
        if controls_list:
            detail += " intercepts " + ", ".join(controls_list[:3])
        labels.append(detail)
    return labels


def classify_measurement(measurement: dict[str, Any]) -> dict[str, Any]:
    """Classify one browser measurement against owned-slot geometry."""

    failures: list[str] = []
    warnings: list[str] = []

    visible = measurement.get("visible") or {}
    ratios = measurement.get("fillRatios") or {}
    dimensions = measurement.get("dimensions") or {}
    controls = measurement.get("controls") or {}
    proof = measurement.get("proofDock") or {}
    ownership = measurement.get("ownershipSlot") or {}

    required_visible = (
        "root",
        "shell",
        "body",
        "editorGroup",
        "activePane",
        "primarySurface",
    )
    for key in required_visible:
        if not bool(visible.get(key)):
            failures.append(f"{key} is not visibly realized")

    slot_source = str(ownership.get("source") or "")
    slot_block = float(dimensions.get("centerSlotBlock") or 0)
    slot_inline = float(dimensions.get("centerSlotInline") or 0)
    if not slot_source or slot_block <= 0 or slot_inline <= 0:
        failures.append("center ownership slot could not be resolved")

    editor_fill = float(ratios.get("editorGroupBlock") or 0)
    editor_inline_fill = float(ratios.get("editorGroupInline") or 0)
    pane_fill = float(ratios.get("activePaneBlock") or 0)
    surface_fill = float(ratios.get("primarySurfaceBlock") or 0)
    editor_block = float(dimensions.get("editorGroupBlock") or 0)
    unused_block = float(dimensions.get("unusedPrimaryWorkBlock") or 0)

    if editor_fill < EDITOR_GROUP_FILL_WATCH:
        failures.append(
            f"editor group fills only {editor_fill:.1%} of its owned center slot"
        )
    elif editor_fill < EDITOR_GROUP_FILL_PASS:
        warnings.append(
            f"editor group center-slot fill is marginal at {editor_fill:.1%}"
        )

    if editor_inline_fill < EDITOR_GROUP_FILL_WATCH:
        failures.append(
            f"editor group fills only {editor_inline_fill:.1%} "
            "of its owned center-slot inline span"
        )
    elif editor_inline_fill < EDITOR_GROUP_FILL_PASS:
        warnings.append(
            f"editor group inline fill is marginal at {editor_inline_fill:.1%}"
        )

    if pane_fill < ACTIVE_PANE_FILL_WATCH:
        failures.append(
            f"active pane fills only {pane_fill:.1%} of the editor remainder"
        )
    elif pane_fill < ACTIVE_PANE_FILL_PASS:
        warnings.append(f"active pane fill is marginal at {pane_fill:.1%}")

    if surface_fill < PRIMARY_SURFACE_FILL_WATCH:
        failures.append(
            f"primary work surface fills only {surface_fill:.1%} "
            "of its allocated remaining track"
        )
    elif surface_fill < PRIMARY_SURFACE_FILL_PASS:
        warnings.append(
            f"primary work surface fill is marginal at {surface_fill:.1%}"
        )

    if editor_block + 0.5 < EDITOR_MIN_BLOCK_PX:
        failures.append(
            f"editor block size {editor_block:.1f}px is below "
            f"the authored {EDITOR_MIN_BLOCK_PX:.0f}px minimum"
        )

    expected_surface_block = float(
        dimensions.get("primarySurfaceExpectedBlock") or 0
    )
    allowed_unused = max(24.0, expected_surface_block * 0.08)
    if unused_block > allowed_unused:
        failures.append(
            f"{unused_block:.1f}px of primary-work block space is owned but unused"
        )

    foreign = int(controls.get("foreignIntercepted") or 0)
    clipped = int(controls.get("clipped") or 0)
    no_actionable = int(controls.get("withoutActionableSample") or 0)
    escaped_surface = int(controls.get("outsidePrimarySurface") or 0)
    escaped_slot = int(controls.get("outsideCenterSlot") or 0)

    if foreign:
        labels = _foreign_owner_labels(controls)
        suffix = f": {'; '.join(labels[:4])}" if labels else ""
        failures.append(
            f"{foreign} active critical control(s) are intercepted by "
            f"foreign owners{suffix}"
        )
    if clipped:
        failures.append(f"{clipped} active critical control(s) are clipped")
    if no_actionable:
        failures.append(
            f"{no_actionable} active critical control(s) have no actionable sample"
        )
    if escaped_surface:
        failures.append(
            f"{escaped_surface} active critical control(s) escape their "
            "primary-surface bounds"
        )
    if escaped_slot:
        failures.append(
            f"{escaped_slot} active critical control(s) escape the owned center slot"
        )

    proof_expanded = bool(proof.get("expanded"))
    proof_block = float(proof.get("blockSize") or 0)
    if proof_expanded:
        if proof_block + 0.5 < EXPANDED_PROOF_MIN_PX:
            failures.append(
                f"expanded proof dock is only {proof_block:.1f}px high"
            )
    elif proof_block > COLLAPSED_PROOF_MAX_PX + 0.5:
        failures.append(
            f"collapsed proof dock retains {proof_block:.1f}px instead of a strip"
        )

    status = "fail" if failures else ("watch" if warnings else "pass")
    score = 100
    score -= min(25, max(0, (EDITOR_GROUP_FILL_PASS - editor_fill) * 100))
    score -= min(
        15,
        max(0, (EDITOR_GROUP_FILL_PASS - editor_inline_fill) * 100),
    )
    score -= min(20, max(0, (ACTIVE_PANE_FILL_PASS - pane_fill) * 100))
    score -= min(25, max(0, (PRIMARY_SURFACE_FILL_PASS - surface_fill) * 100))
    score -= min(25, len(failures) * 5)
    score -= min(10, len(warnings) * 2)
    return {
        "status": status,
        "score": max(0, int(round(score))),
        "failures": failures,
        "warnings": warnings,
    }


def classify_reclamation(
    collapsed: dict[str, Any] | None,
    expanded: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compare proof-dock states without crossing responsive ownership modes."""

    if not collapsed or not expanded:
        return {
            "status": "not-measured",
            "reason": "both runtime collapsed and expanded proof trials are required",
        }

    collapsed_dimensions = collapsed.get("dimensions") or {}
    expanded_dimensions = expanded.get("dimensions") or {}
    collapsed_layout = collapsed.get("layout") or {}
    expanded_layout = expanded.get("layout") or {}
    collapsed_ratios = collapsed.get("fillRatios") or {}
    expanded_ratios = expanded.get("fillRatios") or {}

    collapsed_center = float(
        collapsed_dimensions.get("centerSlotBlock")
        or collapsed_dimensions.get("editorGroupBlock")
        or 0
    )
    expanded_center = float(
        expanded_dimensions.get("centerSlotBlock")
        or expanded_dimensions.get("editorGroupBlock")
        or 0
    )
    collapsed_proof = float(
        ((collapsed.get("proofDock") or {}).get("blockSize")) or 0
    )
    expanded_proof = float(
        ((expanded.get("proofDock") or {}).get("blockSize")) or 0
    )

    ownership_keys = (
        "inspectorPlacement",
        "explorerPlacement",
        "centerTab",
    )
    collapsed_signature = tuple(
        str(collapsed_layout.get(key) or "") for key in ownership_keys
    )
    expanded_signature = tuple(
        str(expanded_layout.get(key) or "") for key in ownership_keys
    )

    failures: list[str] = []
    warnings: list[str] = []
    proof_delta = max(0.0, expanded_proof - collapsed_proof)

    if collapsed_signature != expanded_signature:
        collapsed_fill = float(collapsed_ratios.get("editorGroupBlock") or 0)
        expanded_fill = float(expanded_ratios.get("editorGroupBlock") or 0)
        if min(collapsed_fill, expanded_fill) < EDITOR_GROUP_FILL_WATCH:
            failures.append(
                "responsive proof remediation changed dock ownership while one "
                "center slot remained underfilled"
            )
        elif min(collapsed_fill, expanded_fill) < EDITOR_GROUP_FILL_PASS:
            warnings.append(
                "responsive proof remediation changed dock ownership with "
                "marginal center-slot fill"
            )
        return {
            "status": "fail" if failures else ("watch" if warnings else "pass"),
            "comparable": False,
            "mode": "responsive-ownership-remediation",
            "reason": (
                "proof expansion changed the live dock ownership signature; "
                "each state is verified against its own center slot instead of "
                "subtracting unlike layouts"
            ),
            "collapsedOwnershipSignature": collapsed_signature,
            "expandedOwnershipSignature": expanded_signature,
            "collapsedCenterSlotBlock": collapsed_center,
            "expandedCenterSlotBlock": expanded_center,
            "collapsedProofBlock": collapsed_proof,
            "expandedProofBlock": expanded_proof,
            "proofDelta": proof_delta,
            "centerDelta": None,
            "reclaimRatio": None,
            "failures": failures,
            "warnings": warnings,
        }

    center_delta = max(0.0, collapsed_center - expanded_center)
    fill_ratio = center_delta / proof_delta if proof_delta > 0 else 0.0

    if proof_delta < 80:
        failures.append(
            "expanded proof state did not reserve meaningfully more block space"
        )
    elif fill_ratio < 0.80:
        failures.append(
            f"owned center slot reclaimed only {fill_ratio:.1%} "
            "of proof-dock block space"
        )
    elif fill_ratio < 0.92:
        warnings.append(
            f"center-slot proof-dock reclamation is marginal at {fill_ratio:.1%}"
        )

    return {
        "status": "fail" if failures else ("watch" if warnings else "pass"),
        "comparable": True,
        "mode": "same-ownership-signature",
        "collapsedOwnershipSignature": collapsed_signature,
        "expandedOwnershipSignature": expanded_signature,
        "collapsedCenterSlotBlock": collapsed_center,
        "expandedCenterSlotBlock": expanded_center,
        "collapsedProofBlock": collapsed_proof,
        "expandedProofBlock": expanded_proof,
        "proofDelta": proof_delta,
        "centerDelta": center_delta,
        "reclaimRatio": fill_ratio,
        "failures": failures,
        "warnings": warnings,
    }


CONFIGURE_TRIAL_JS = r"""
async ({state}) => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function activateCodeEditor() {
    document.body.dataset.activeApp = "code-editor";
    if (typeof window.setActiveApp === "function") {
      try {
        window.setActiveApp("code-editor", {syncRoute: false});
      } catch (_) {}
    }
    const roots = [
      "#webgl-demo", "#astrometric-app", "#calculator-app", "#document-app",
      "#spreadsheet-app", "#onlyoffice-app", "#task-manager-app",
      "#conductor-app", "#terminal-app", "#chat-console-app",
      "#ai-control-app", "#email-app", "#git-tools-app", "#code-editor-app",
      "#file-explorer-app", "#game-editor-app", "#website-builder-app",
      "#mcel-lab-app", "#worker-app", "#wallet-app"
    ];
    for (const selector of roots) {
      const element = document.querySelector(selector);
      if (!element) continue;
      element.style.display = selector === "#code-editor-app" ? "grid" : "none";
    }
  }

  activateCodeEditor();
  await sleep(100);

  const root = document.querySelector("#code-editor-app");
  if (!root) return {ok: false, reason: "code-editor root missing"};

  let controller = root.__mcelCodeEditorLayoutController
    || window.MainComputerCodeEditorLayoutController
    || null;
  if (!controller && window.MainComputerCodeEditorLayout?.mount) {
    try {
      controller = window.MainComputerCodeEditorLayout.mount(root);
    } catch (error) {
      return {ok: false, reason: String(error?.message || error)};
    }
  }

  if (controller?.reset) controller.reset();

  if (controller?.applyOperation) {
    if (state.inspectorPlacement && state.inspectorPlacement !== "right") {
      controller.applyOperation({
        kind: "dock",
        userId: "code-editor.inspector",
        placement: state.inspectorPlacement,
      });
    }
    if (state.explorerCollapsed) {
      controller.applyOperation({
        kind: "collapse",
        userId: "code-editor.explorer",
        collapsed: true,
      });
    }
  } else {
    root.dataset.mcelLayoutLive = "true";
    root.dataset.mcelInspectorPlacement = state.inspectorPlacement || "right";
    root.dataset.mcelExplorerPlacement = state.explorerCollapsed ? "trigger" : "left";
  }

  const requestedTab = state.editorTab || "runtime";
  const tab = root.querySelector(`[data-code-studio-tab="${CSS.escape(requestedTab)}"]`);
  if (tab) tab.click();
  for (const button of root.querySelectorAll("[data-code-studio-tab]")) {
    button.classList.toggle(
      "active",
      button.getAttribute("data-code-studio-tab") === requestedTab
    );
  }
  for (const pane of root.querySelectorAll("[data-code-studio-pane]")) {
    pane.classList.toggle(
      "active",
      pane.getAttribute("data-code-studio-pane") === requestedTab
    );
  }

  const proof = root.querySelector("#code-studio-bottom-panel");
  const proofButton = root.querySelector("#code-studio-toggle-assistant");
  if (proof) {
    proof.dataset.expanded = state.proofExpanded ? "true" : "false";
  }
  if (proofButton) {
    proofButton.setAttribute(
      "aria-expanded",
      state.proofExpanded ? "true" : "false"
    );
    proofButton.textContent = state.proofExpanded ? "Close proof dock" : "Open proof dock";
  }

  if (controller?.resolve) controller.resolve();

  await new Promise((resolve) => requestAnimationFrame(
    () => requestAnimationFrame(resolve)
  ));
  await sleep(100);

  return {
    ok: true,
    controller: Boolean(controller),
    resolved: controller?.resolved || null,
    layoutLive: root.dataset.mcelLayoutLive || "",
    activePane: root.querySelector(".code-studio-editor-pane.active")
      ?.getAttribute("data-code-studio-pane") || "",
  };
}
"""


MEASURE_JS = r"""
() => {
  const root = document.querySelector("#code-editor-app");
  const shell = root?.querySelector(".code-studio-shell");
  const body = root?.querySelector(".code-studio-body");
  const titlebar = root?.querySelector(".code-studio-titlebar");
  const statusbar = root?.querySelector(".code-studio-statusbar");
  const activity = root?.querySelector(".code-studio-activitybar");
  const sidebar = root?.querySelector(".code-studio-sidebar");
  const editorGroup = root?.querySelector(".code-studio-editor-group");
  const inspector = root?.querySelector(".code-studio-inspector");
  const centerTabs = root?.querySelector(".code-editor-layout-center-tabs");
  const tabs = editorGroup?.querySelector(".code-studio-tabs");
  const activePane = editorGroup?.querySelector(".code-studio-editor-pane.active");
  const toolbar = activePane?.querySelector(".code-studio-pane-toolbar");
  const proof = root?.querySelector("#code-studio-bottom-panel");

  const paneName = activePane?.getAttribute("data-code-studio-pane") || "";
  const surfaceSelectors = {
    source: ".code-studio-code-frame",
    runtime: ".code-studio-runtime-preview",
    serialized: ".code-studio-output",
    contract: ".code-studio-contract-report",
  };
  const primarySurface = activePane?.querySelector(
    surfaceSelectors[paneName]
    || ".code-studio-code-frame, .code-studio-runtime-preview, .code-studio-output, .code-studio-contract-report"
  );

  const devicePixelRatio = Number(window.devicePixelRatio || 1) || 1;
  const devicePixelLineWidth = 1 / Math.max(1, devicePixelRatio);

  const pixelRectFromCssRect = (value) => {
    const leftCss = Number(value.left ?? value.x ?? 0);
    const topCss = Number(value.top ?? value.y ?? 0);
    const rightCss = Number(value.right ?? (leftCss + Number(value.width || 0)));
    const bottomCss = Number(value.bottom ?? (topCss + Number(value.height || 0)));
    const left = Math.round(leftCss * devicePixelRatio);
    const top = Math.round(topCss * devicePixelRatio);
    const right = Math.round(rightCss * devicePixelRatio);
    const bottom = Math.round(bottomCss * devicePixelRatio);
    const width = Math.max(0, right - left);
    const height = Math.max(0, bottom - top);
    return {left, top, right, bottom, width, height, area: width * height};
  };

  const withPixelRect = (value) => ({
    ...value,
    pixelRect: pixelRectFromCssRect(value),
  });

  const isVisible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    if (
      style.display === "none"
      || style.visibility === "hidden"
      || Number(style.opacity || "1") === 0
    ) return false;
    const value = element.getBoundingClientRect();
    return value.width > 0 && value.height > 0;
  };

  const rect = (element) => {
    if (!element) return withPixelRect({
      left: 0, top: 0, right: 0, bottom: 0,
      width: 0, height: 0, area: 0
    });
    const value = element.getBoundingClientRect();
    const width = Math.max(0, value.width);
    const height = Math.max(0, value.height);
    return withPixelRect({
      left: value.left,
      top: value.top,
      right: value.right,
      bottom: value.bottom,
      width,
      height,
      area: width * height,
    });
  };

  const rectFromBounds = (left, top, right, bottom) => withPixelRect({
    left,
    top,
    right,
    bottom,
    width: Math.max(0, right - left),
    height: Math.max(0, bottom - top),
    area: Math.max(0, right - left) * Math.max(0, bottom - top),
  });

  const intersection = (a, b) => rectFromBounds(
    Math.max(a.left, b.left),
    Math.max(a.top, b.top),
    Math.min(a.right, b.right),
    Math.min(a.bottom, b.bottom),
  );

  const ratio = (actual, expected) => expected > 0 ? actual / expected : 0;

  const selectorFor = (element) => {
    if (!element) return "";
    if (element.id) return `#${element.id}`;
    const classes = Array.from(element.classList || []).slice(0, 4);
    return `${element.tagName.toLowerCase()}${classes.map((item) => `.${item}`).join("")}`;
  };

  const placementForOwner = (element) => {
    const userId = element?.getAttribute?.("data-mc-layout-user-id") || "";
    const region = element?.getAttribute?.("data-code-studio-workbench-region") || "";
    if (userId === "code-editor.inspector" || region === "scm-ai-inspector") {
      return root?.dataset.mcelInspectorPlacement || "";
    }
    if (userId === "code-editor.explorer" || region === "workspace-sidebar") {
      return root?.dataset.mcelExplorerPlacement || "";
    }
    if (userId === "code-editor.proof" || region === "proof-dock") {
      return root?.dataset.mcelProofPlacement || "";
    }
    if (userId === "code-editor.editor" || region === "editor-workbench") {
      return "center";
    }
    if (element?.classList?.contains("code-editor-layout-center-tabs")) {
      return "center-tab-chrome";
    }
    return "";
  };

  const nearestOwner = (element) => element?.closest?.([
    "[data-mc-layout-user-id]",
    "[data-mc-component-id]",
    "[data-code-studio-workbench-region]",
    "[data-mcel-layout-slot]",
    "[data-mc-layout-slot]",
    "[data-mc-layout-zone]",
  ].join(",")) || null;

  const ownerRecord = (element) => {
    if (!element) return {};
    const owner = nearestOwner(element) || element;
    const style = getComputedStyle(owner);
    return {
      selector: selectorFor(owner),
      targetSelector: selectorFor(element),
      id: owner.id || "",
      tag: owner.tagName?.toLowerCase?.() || "",
      componentId: owner.getAttribute?.("data-mc-component-id") || "",
      layoutUserId: owner.getAttribute?.("data-mc-layout-user-id") || "",
      region: owner.getAttribute?.("data-code-studio-workbench-region") || "",
      layoutSlot: owner.getAttribute?.("data-mcel-layout-slot")
        || owner.getAttribute?.("data-mc-layout-slot")
        || "",
      layoutZone: owner.getAttribute?.("data-mc-layout-zone") || "",
      placement: placementForOwner(owner),
      pointerEvents: style.pointerEvents,
      position: style.position,
      zIndex: style.zIndex,
      rect: rect(owner),
    };
  };

  const styleRecord = (element) => {
    if (!element) return {};
    const style = getComputedStyle(element);
    return {
      selector: selectorFor(element),
      display: style.display,
      position: style.position,
      minHeight: style.minHeight,
      height: style.height,
      maxHeight: style.maxHeight,
      alignSelf: style.alignSelf,
      justifySelf: style.justifySelf,
      alignItems: style.alignItems,
      gridTemplateRows: style.gridTemplateRows,
      gridRow: style.gridRow,
      gridColumn: style.gridColumn,
      flex: style.flex,
      overflowY: style.overflowY,
      blockSize: rect(element).height,
    };
  };

  const overflowAgainst = (value, bounds) => {
    const overflow = {
      left: Math.max(0, bounds.left - value.left),
      right: Math.max(0, value.right - bounds.right),
      top: Math.max(0, bounds.top - value.top),
      bottom: Math.max(0, value.bottom - bounds.bottom),
    };
    overflow.inline = overflow.left + overflow.right;
    overflow.block = overflow.top + overflow.bottom;
    overflow.escaped = Math.max(
      overflow.left,
      overflow.right,
      overflow.top,
      overflow.bottom,
    ) > 0.5;
    return overflow;
  };

  const rootRect = rect(root);
  const shellRect = rect(shell);
  const bodyRect = rect(body);
  const activityRect = rect(activity);
  const sidebarRect = rect(sidebar);
  const editorRect = rect(editorGroup);
  const inspectorRect = rect(inspector);
  const centerTabsRect = rect(centerTabs);
  const tabsRect = rect(tabs);
  const paneRect = rect(activePane);
  const toolbarRect = rect(toolbar);
  const surfaceRect = rect(primarySurface);
  const proofRect = rect(proof);
  const titleRect = rect(titlebar);
  const statusRect = rect(statusbar);

  const explicitCenterSlot = root?.querySelector(
    '[data-mcel-layout-slot="center"],'
    + '[data-mc-layout-slot="center"],'
    + '[data-mc-layout-zone="center"]'
  );
  const explicitCenterSlotVisible = (
    explicitCenterSlot
    && explicitCenterSlot !== editorGroup
    && isVisible(explicitCenterSlot)
  );

  const ownershipDeductions = [];
  let centerSlotRect = explicitCenterSlotVisible
    ? rect(explicitCenterSlot)
    : {...bodyRect};
  let centerSlotSource = explicitCenterSlotVisible
    ? "explicit-center-slot"
    : "inferred-live-dock-ownership";

  if (!explicitCenterSlotVisible) {
    let left = bodyRect.left;
    let right = bodyRect.right;
    let top = bodyRect.top;
    let bottom = bodyRect.bottom;

    if (isVisible(activity)) {
      left = Math.max(left, activityRect.right);
      ownershipDeductions.push({
        edge: "left",
        amount: Math.max(0, activityRect.width),
        owner: ownerRecord(activity),
      });
    }

    const explorerPlacement = root?.dataset.mcelExplorerPlacement || "";
    if (explorerPlacement === "left" && isVisible(sidebar)) {
      left = Math.max(left, sidebarRect.right);
      ownershipDeductions.push({
        edge: "left",
        amount: Math.max(0, sidebarRect.width),
        owner: ownerRecord(sidebar),
      });
    }

    const inspectorPlacement = root?.dataset.mcelInspectorPlacement || "";
    if (inspectorPlacement === "right" && isVisible(inspector)) {
      right = Math.min(right, inspectorRect.left);
      ownershipDeductions.push({
        edge: "right",
        amount: Math.max(0, inspectorRect.width),
        owner: ownerRecord(inspector),
      });
    } else if (inspectorPlacement === "bottom" && isVisible(inspector)) {
      bottom = Math.min(bottom, inspectorRect.top);
      ownershipDeductions.push({
        edge: "bottom",
        amount: Math.max(0, inspectorRect.height),
        owner: ownerRecord(inspector),
      });
    } else if (inspectorPlacement === "tab" && isVisible(centerTabs)) {
      top = Math.max(top, centerTabsRect.bottom);
      ownershipDeductions.push({
        edge: "top",
        amount: Math.max(0, centerTabsRect.height),
        owner: ownerRecord(centerTabs),
      });
    }

    centerSlotRect = rectFromBounds(left, top, right, bottom);
  }

  const editorCenterIntersection = intersection(editorRect, centerSlotRect);
  const expectedPaneBlock = Math.max(0, editorRect.height - tabsRect.height);
  const expectedSurfaceBlock = Math.max(0, paneRect.height - toolbarRect.height);

  const criticalSelector = [
    "button",
    "input",
    "select",
    "textarea",
    "a[href]",
    "[role=button]",
    "[tabindex]:not([tabindex='-1'])"
  ].join(",");

  const controls = [];
  const foreignOwnerMap = new Map();

  for (const element of activePane?.querySelectorAll(criticalSelector) || []) {
    if (!isVisible(element)) continue;
    const controlRect = rect(element);
    const inPrimarySurface = Boolean(primarySurface?.contains(element));
    const surfaceOverflow = inPrimarySurface
      ? overflowAgainst(controlRect, surfaceRect)
      : {left: 0, right: 0, top: 0, bottom: 0, inline: 0, block: 0, escaped: false};
    const slotOverflow = overflowAgainst(controlRect, centerSlotRect);
    const samples = [
      [controlRect.left + controlRect.width / 2, controlRect.top + controlRect.height / 2],
      [controlRect.left + Math.min(4, controlRect.width / 3), controlRect.top + Math.min(4, controlRect.height / 3)],
      [controlRect.right - Math.min(4, controlRect.width / 3), controlRect.top + Math.min(4, controlRect.height / 3)],
      [controlRect.left + Math.min(4, controlRect.width / 3), controlRect.bottom - Math.min(4, controlRect.height / 3)],
      [controlRect.right - Math.min(4, controlRect.width / 3), controlRect.bottom - Math.min(4, controlRect.height / 3)],
    ];
    let actionable = 0;
    let foreign = 0;
    const sampleEvidence = [];

    for (const [x, y] of samples) {
      if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) continue;
      const stack = document.elementsFromPoint(x, y);
      const top = stack.find(
        (candidate) => getComputedStyle(candidate).pointerEvents !== "none"
      );
      if (!top) continue;

      const ownHit = (
        top === element
        || element.contains(top)
        || top.closest("label")?.control === element
      );
      const foreignHit = !ownHit && !activePane.contains(top);
      if (ownHit) actionable += 1;
      if (foreignHit) foreign += 1;

      const topOwner = ownerRecord(top);
      const evidence = {
        x,
        y,
        outcome: ownHit ? "actionable" : (foreignHit ? "foreign" : "same-pane-nonactionable"),
        top: topOwner,
        stack: stack.slice(0, 8).map(ownerRecord),
      };
      if (foreignHit || !ownHit) sampleEvidence.push(evidence);

      if (foreignHit) {
        const key = (
          topOwner.layoutUserId
          || topOwner.componentId
          || topOwner.region
          || topOwner.selector
          || "unknown-owner"
        );
        if (!foreignOwnerMap.has(key)) {
          foreignOwnerMap.set(key, {
            ...topOwner,
            sampleCount: 0,
            controls: new Set(),
            examples: [],
          });
        }
        const entry = foreignOwnerMap.get(key);
        entry.sampleCount += 1;
        entry.controls.add(element.id || selectorFor(element));
        if (entry.examples.length < 4) {
          entry.examples.push({
            control: element.id || selectorFor(element),
            x,
            y,
            top: topOwner,
          });
        }
      }
    }

    const clipped = (
      controlRect.left < rootRect.left - 0.5
      || controlRect.right > rootRect.right + 0.5
      || controlRect.top < rootRect.top - 0.5
      || controlRect.bottom > rootRect.bottom + 0.5
      || controlRect.left < 0
      || controlRect.right > innerWidth
      || controlRect.top < 0
      || controlRect.bottom > innerHeight
    );
    controls.push({
      id: element.id || "",
      selector: selectorFor(element),
      label: String(
        element.getAttribute("aria-label")
        || element.getAttribute("title")
        || element.textContent
        || ""
      ).replace(/\s+/g, " ").trim().slice(0, 80),
      owner: ownerRecord(element),
      actionable,
      foreign,
      clipped,
      inPrimarySurface,
      surfaceOverflow,
      slotOverflow,
      rect: controlRect,
      sampleEvidence,
    });
  }

  const foreignOwners = Array.from(foreignOwnerMap.values()).map((entry) => ({
    ...entry,
    controls: Array.from(entry.controls),
  }));

  const ancestors = [];
  let current = primarySurface;
  while (current && current instanceof Element) {
    ancestors.push(styleRecord(current));
    if (current === editorGroup) break;
    current = current.parentElement;
  }

  const outsidePrimarySurfaceControls = controls.filter(
    (item) => item.inPrimarySurface && item.surfaceOverflow.escaped
  );
  const outsideCenterSlotControls = controls.filter(
    (item) => item.slotOverflow.escaped
  );

  return {
    devicePixelRatio,
    pixelGeometry: "css-edge-rounded-device-pixels-v1",
    visible: {
      root: isVisible(root),
      shell: isVisible(shell),
      body: isVisible(body),
      centerSlot: centerSlotRect.width > 0 && centerSlotRect.height > 0,
      editorGroup: isVisible(editorGroup),
      activePane: isVisible(activePane),
      primarySurface: isVisible(primarySurface),
    },
    activePane: paneName,
    rects: {
      root: rootRect,
      shell: shellRect,
      body: bodyRect,
      centerSlot: centerSlotRect,
      activity: activityRect,
      sidebar: sidebarRect,
      editorGroup: editorRect,
      inspector: inspectorRect,
      centerTabs: centerTabsRect,
      tabs: tabsRect,
      activePane: paneRect,
      toolbar: toolbarRect,
      primarySurface: surfaceRect,
      proofDock: proofRect,
      titlebar: titleRect,
      statusbar: statusRect,
    },
    ownershipSlot: {
      source: centerSlotSource,
      explicitSelector: explicitCenterSlotVisible
        ? selectorFor(explicitCenterSlot)
        : "",
      rect: centerSlotRect,
      deductions: ownershipDeductions,
      editorIntersection: editorCenterIntersection,
    },
    fillRatios: {
      editorGroupBlock: ratio(editorCenterIntersection.height, centerSlotRect.height),
      editorGroupInline: ratio(editorCenterIntersection.width, centerSlotRect.width),
      editorGroupArea: ratio(editorCenterIntersection.area, centerSlotRect.area),
      activePaneBlock: ratio(paneRect.height, expectedPaneBlock),
      primarySurfaceBlock: ratio(surfaceRect.height, expectedSurfaceBlock),
    },
    dimensions: {
      bodyBlock: bodyRect.height,
      bodyInline: bodyRect.width,
      centerSlotBlock: centerSlotRect.height,
      centerSlotInline: centerSlotRect.width,
      editorGroupBlock: editorRect.height,
      editorGroupInline: editorRect.width,
      centerSlotUnusedBlock: Math.max(0, centerSlotRect.height - editorCenterIntersection.height),
      centerSlotUnusedInline: Math.max(0, centerSlotRect.width - editorCenterIntersection.width),
      centerSlotOffsetBlock: Math.abs(editorRect.top - centerSlotRect.top)
        + Math.abs(editorRect.bottom - centerSlotRect.bottom),
      centerSlotOffsetInline: Math.abs(editorRect.left - centerSlotRect.left)
        + Math.abs(editorRect.right - centerSlotRect.right),
      activePaneBlock: paneRect.height,
      primarySurfaceBlock: surfaceRect.height,
      primarySurfaceExpectedBlock: expectedSurfaceBlock,
      unusedPrimaryWorkBlock: Math.max(0, expectedSurfaceBlock - surfaceRect.height),
      tabsBlock: tabsRect.height,
      toolbarBlock: toolbarRect.height,
    },
    proofDock: {
      expanded: proof?.dataset.expanded === "true",
      blockSize: proofRect.height,
      placement: root?.dataset.mcelProofPlacement || "",
    },
    layout: {
      inspectorPlacement: root?.dataset.mcelInspectorPlacement || "",
      explorerPlacement: root?.dataset.mcelExplorerPlacement || "",
      centerTab: root?.dataset.mcelCenterTab || "",
      live: root?.dataset.mcelLayoutLive || "",
    },
    controls: {
      count: controls.length,
      actionableSamples: controls.reduce((total, item) => total + item.actionable, 0),
      foreignIntercepted: controls.filter((item) => item.foreign > 0).length,
      clipped: controls.filter((item) => item.clipped).length,
      withoutActionableSample: controls.filter((item) => item.actionable < 1).length,
      outsidePrimarySurface: outsidePrimarySurfaceControls.length,
      outsideCenterSlot: outsideCenterSlotControls.length,
      maxOverflowBlock: Math.max(
        0,
        ...controls.map((item) => Math.max(
          Number(item.surfaceOverflow?.block || 0),
          Number(item.slotOverflow?.block || 0),
        ))
      ),
      foreignOwners,
      examples: controls.filter(
        (item) => (
          item.foreign > 0
          || item.clipped
          || item.actionable < 1
          || item.surfaceOverflow.escaped
          || item.slotOverflow.escaped
        )
      ).slice(0, 12),
    },
    wrapperChain: ancestors,
    viewport: {width: innerWidth, height: innerHeight},
  };
}
"""


ANNOTATE_JS = r"""
({measurement, classification, state, viewport}) => {
  document.querySelector("#flog-code-editor-overlay")?.remove();
  const overlay = document.createElement("div");
  overlay.id = "flog-code-editor-overlay";
  Object.assign(overlay.style, {
    position: "fixed",
    inset: "0",
    zIndex: "2147483647",
    pointerEvents: "none",
    fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace",
  });

  const devicePixelRatio = Number(window.devicePixelRatio || 1) || 1;
  const cssRectFromPixelRect = (value) => {
    const left = Number(value.left || 0) / devicePixelRatio;
    const top = Number(value.top || 0) / devicePixelRatio;
    const right = Number(value.right || 0) / devicePixelRatio;
    const bottom = Number(value.bottom || 0) / devicePixelRatio;
    const width = Math.max(0, right - left);
    const height = Math.max(0, bottom - top);
    return {left, top, right, bottom, width, height};
  };

  const colors = {
    body: "#22d3ee",
    centerSlot: "#38bdf8",
    editorGroup: "#22c55e",
    activePane: "#facc15",
    primarySurface: "#f97316",
    proofDock: "#a855f7",
  };

  for (const key of ["body", "centerSlot", "editorGroup", "activePane", "primarySurface", "proofDock"]) {
    const measuredRect = measurement.rects?.[key];
    const rect = measuredRect?.pixelRect ? cssRectFromPixelRect(measuredRect.pixelRect) : measuredRect;
    if (!rect || rect.width <= 0 || rect.height <= 0) continue;
    const box = document.createElement("div");
    Object.assign(box.style, {
      position: "fixed",
      left: `${rect.left}px`,
      top: `${rect.top}px`,
      width: `${rect.width}px`,
      height: `${rect.height}px`,
      border: `${devicePixelLineWidth}px solid ${colors[key]}`,
      borderRadius: "0",
      boxSizing: "border-box",
    });
    const label = document.createElement("span");
    label.textContent = key;
    Object.assign(label.style, {
      position: "absolute",
      left: "2px",
      top: "2px",
      padding: "1px 4px",
      background: colors[key],
      color: "#050505",
      fontSize: "10px",
      fontWeight: "700",
    });
    box.appendChild(label);
    overlay.appendChild(box);
  }

  const legend = document.createElement("div");
  const ratios = measurement.fillRatios || {};
  legend.textContent = [
    `${viewport} / ${state}`,
    `status=${classification.status} score=${classification.score}`,
    `center=${(Number(ratios.editorGroupBlock || 0) * 100).toFixed(1)}% block/${(Number(ratios.editorGroupInline || 0) * 100).toFixed(1)}% inline`,
    `pane=${(Number(ratios.activePaneBlock || 0) * 100).toFixed(1)}%`,
    `surface=${(Number(ratios.primarySurfaceBlock || 0) * 100).toFixed(1)}%`,
    ...(classification.failures || []).slice(0, 2),
  ].join(" · ");
  Object.assign(legend.style, {
    position: "fixed",
    left: "8px",
    right: "8px",
    bottom: "8px",
    padding: "7px 10px",
    background: "rgba(0,0,0,.88)",
    border: `1px solid ${classification.status === "fail" ? "#ef4444" : "#22c55e"}`,
    color: "#f8fafc",
    fontSize: "11px",
    lineHeight: "1.35",
    whiteSpace: "normal",
  });
  overlay.appendChild(legend);
  document.body.appendChild(overlay);
}
"""


def _expanded_applications_html(repo: Path) -> str:
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from main_computer.viewport_pages import APPLICATIONS_INDEX_HTML

    return APPLICATIONS_INDEX_HTML


def _playwright_error(exc: Exception) -> str:
    return (
        "Playwright Chromium is required for the live Code Editor FLOG. "
        "Install it with:\n\n"
        "    python -m playwright install chromium\n\n"
        f"Original error: {exc}"
    )


def run_browser_trials(
    *,
    repo: Path,
    viewports: list[ViewportProfile],
    states: list[TrialSpec],
    output_dir: Path,
    headed: bool = False,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(_playwright_error(exc)) from exc

    html = _expanded_applications_html(repo)
    output_dir.mkdir(parents=True, exist_ok=True)
    measurements: list[dict[str, Any]] = []
    pngs: list[str] = []
    console_messages: list[dict[str, str]] = []
    page_errors: list[str] = []

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=not headed)
        except PlaywrightError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(_playwright_error(exc)) from exc

        try:
            for viewport in viewports:
                context = browser.new_context(
                    viewport={"width": viewport.width, "height": viewport.height},
                    device_scale_factor=1,
                )
                page = context.new_page()

                def route_handler(route: Any) -> None:
                    url = route.request.url
                    if url.startswith("http://flog.local/"):
                        route.fulfill(
                            status=200,
                            content_type="text/html",
                            body=html,
                        )
                    elif url.startswith("data:") or url.startswith("blob:"):
                        route.continue_()
                    else:
                        route.abort()

                page.route("**/*", route_handler)
                page.on(
                    "console",
                    lambda msg: console_messages.append(
                        {"type": msg.type, "text": msg.text[:500]}
                    ),
                )
                page.on(
                    "pageerror",
                    lambda exc: page_errors.append(str(exc)[:500]),
                )
                page.goto(
                    "http://flog.local/applications",
                    wait_until="domcontentloaded",
                    timeout=20_000,
                )
                page.wait_for_timeout(250)

                for state in states:
                    configured = page.evaluate(
                        CONFIGURE_TRIAL_JS,
                        {
                            "state": {
                                "editorTab": state.editor_tab,
                                "inspectorPlacement": state.inspector_placement,
                                "explorerCollapsed": state.explorer_collapsed,
                                "proofExpanded": state.proof_expanded,
                            }
                        },
                    )
                    measurement = page.evaluate(MEASURE_JS)
                    measurement["viewportProfile"] = viewport.name
                    measurement["state"] = state.name
                    measurement["configuration"] = configured
                    measurement["classification"] = classify_measurement(
                        measurement
                    )

                    png_name = (
                        f"code-editor--{slugify(viewport.name)}--"
                        f"{slugify(state.name)}--fill-proof.png"
                    )
                    page.evaluate(
                        ANNOTATE_JS,
                        {
                            "measurement": measurement,
                            "classification": measurement["classification"],
                            "state": state.name,
                            "viewport": viewport.name,
                        },
                    )
                    page.screenshot(
                        path=str(output_dir / png_name),
                        full_page=False,
                    )
                    page.evaluate(
                        "() => document.querySelector('#flog-code-editor-overlay')?.remove()"
                    )
                    measurement["png"] = png_name
                    pngs.append(png_name)
                    measurements.append(measurement)

                context.close()
        finally:
            browser.close()

    return {
        "measurements": measurements,
        "pngFiles": pngs,
        "consoleMessages": console_messages[-100:],
        "pageErrors": page_errors[-100:],
    }


def slugify(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9]+", "-", str(value)).strip("-").lower()
    return clean or "item"


def summarize_report(
    *,
    static_contract: dict[str, Any],
    measurements: list[dict[str, Any]],
) -> dict[str, Any]:
    counts = {"pass": 0, "watch": 0, "fail": 0}
    for item in measurements:
        status = str((item.get("classification") or {}).get("status") or "fail")
        counts[status] = counts.get(status, 0) + 1

    by_viewport: dict[str, dict[str, Any]] = {}
    for item in measurements:
        viewport = str(item.get("viewportProfile") or "")
        entry = by_viewport.setdefault(
            viewport,
            {
                "statusCounts": {"pass": 0, "watch": 0, "fail": 0},
                "worstCenterBlockFill": 1.0,
                "worstCenterInlineFill": 1.0,
                "worstSurfaceFill": 1.0,
                "foreignOwnerCount": 0,
                "reclamation": {},
            },
        )
        status = str((item.get("classification") or {}).get("status") or "fail")
        entry["statusCounts"][status] += 1
        ratios = item.get("fillRatios") or {}
        center_block_fill = float(ratios.get("editorGroupBlock") or 0)
        center_inline_fill = float(ratios.get("editorGroupInline") or 0)
        surface_fill = float(ratios.get("primarySurfaceBlock") or 0)
        entry["worstCenterBlockFill"] = min(
            float(entry["worstCenterBlockFill"]), center_block_fill
        )
        entry["worstCenterInlineFill"] = min(
            float(entry["worstCenterInlineFill"]), center_inline_fill
        )
        entry["worstSurfaceFill"] = min(
            float(entry["worstSurfaceFill"]), surface_fill
        )
        entry["foreignOwnerCount"] += len(
            ((item.get("controls") or {}).get("foreignOwners")) or []
        )

    for viewport, entry in by_viewport.items():
        collapsed = next(
            (
                item
                for item in measurements
                if item.get("viewportProfile") == viewport
                and item.get("state") == "runtime-collapsed-proof"
            ),
            None,
        )
        expanded = next(
            (
                item
                for item in measurements
                if item.get("viewportProfile") == viewport
                and item.get("state") == "runtime-expanded-proof"
            ),
            None,
        )
        entry["reclamation"] = classify_reclamation(collapsed, expanded)

    reclamation_failures = sum(
        1
        for item in by_viewport.values()
        if (item.get("reclamation") or {}).get("status") == "fail"
    )
    overall_status = (
        "fail"
        if counts.get("fail", 0)
        or reclamation_failures
        or static_contract.get("state") != "complete"
        else ("watch" if counts.get("watch", 0) else "pass")
    )

    return {
        "status": overall_status,
        "trialCount": len(measurements),
        "statusCounts": counts,
        "staticContractState": static_contract.get("state"),
        "viewportCount": len(by_viewport),
        "reclamationFailureCount": reclamation_failures,
        "byViewport": by_viewport,
    }


def compact_measurement(item: dict[str, Any]) -> dict[str, Any]:
    """Return report-safe evidence without duplicating the entire DOM."""

    return {
        "viewportProfile": item.get("viewportProfile"),
        "state": item.get("state"),
        "activePane": item.get("activePane"),
        "fillRatios": item.get("fillRatios"),
        "dimensions": item.get("dimensions"),
        "ownershipSlot": item.get("ownershipSlot"),
        "proofDock": item.get("proofDock"),
        "layout": item.get("layout"),
        "controls": item.get("controls"),
        "wrapperChain": item.get("wrapperChain"),
        "classification": item.get("classification"),
        "configuration": item.get("configuration"),
        "png": item.get("png"),
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    static_contract = report.get("staticContract") or {}
    lines = [
        "# FLOG Live Code Editor Workbench Report",
        "",
        f"- Generated: `{report.get('generatedAt', '')}`",
        f"- Report kind: `{report.get('kind', '')}`",
        f"- Version: `{report.get('version', '')}`",
        f"- Status: **{summary.get('status', 'unknown')}**",
        f"- Trials: `{summary.get('trialCount', 0)}`",
        f"- PNG proofs: `{len(report.get('pngFiles') or [])}`",
        "",
        "## Contract under test",
        "",
        "A required center `primary-work` unit must fill its owned center slot. "
        "Sibling docks and tab chrome are excluded from that denominator. Every "
        "wrapper between the center slot and active primary surface must permit "
        "two-axis stretch and block-axis shrink, and active controls must remain "
        "contained and actionable.",
        "",
        f"- Static contract state: `{static_contract.get('state', '')}`",
        f"- Fill-law source: `{((static_contract.get('fillLaw') or {}).get('source') or '')}`",
        f"- Ownership-slot source: `{((static_contract.get('ownershipSlotLaw') or {}).get('source') or '')}`",
        "",
        "## Summary",
        "",
        "| Viewport | Pass | Watch | Fail | Worst center block | Worst center inline | Worst surface fill | Foreign owners | Proof reclaim |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for viewport, item in (summary.get("byViewport") or {}).items():
        counts = item.get("statusCounts") or {}
        reclaim = item.get("reclamation") or {}
        lines.append(
            f"| {viewport} | {counts.get('pass', 0)} | "
            f"{counts.get('watch', 0)} | {counts.get('fail', 0)} | "
            f"{float(item.get('worstCenterBlockFill') or 0):.1%} | "
            f"{float(item.get('worstCenterInlineFill') or 0):.1%} | "
            f"{float(item.get('worstSurfaceFill') or 0):.1%} | "
            f"{int(item.get('foreignOwnerCount') or 0)} | "
            f"{reclaim.get('status', 'not-measured')} |"
        )

    lines.extend(["", "## Trial evidence", ""])
    for item in report.get("measurements") or []:
        classification = item.get("classification") or {}
        ratios = item.get("fillRatios") or {}
        dimensions = item.get("dimensions") or {}
        ownership = item.get("ownershipSlot") or {}
        controls = item.get("controls") or {}
        lines.extend(
            [
                f"### `{item.get('viewportProfile')}` / `{item.get('state')}`",
                "",
                f"- Status: **{classification.get('status')}** "
                f"(score `{classification.get('score')}`)",
                f"- Center-slot source: `{ownership.get('source', '')}`",
                f"- Editor center block fill: `{float(ratios.get('editorGroupBlock') or 0):.1%}`",
                f"- Editor center inline fill: `{float(ratios.get('editorGroupInline') or 0):.1%}`",
                f"- Active pane fill: `{float(ratios.get('activePaneBlock') or 0):.1%}`",
                f"- Primary surface fill: `{float(ratios.get('primarySurfaceBlock') or 0):.1%}`",
                f"- Center slot: `{float(dimensions.get('centerSlotInline') or 0):.1f}px × "
                f"{float(dimensions.get('centerSlotBlock') or 0):.1f}px`",
                f"- Unused owned block: `{float(dimensions.get('unusedPrimaryWorkBlock') or 0):.1f}px`",
                f"- Foreign owner count: `{len(controls.get('foreignOwners') or [])}`",
                f"- PNG: `{item.get('png', '')}`",
            ]
        )
        for owner in controls.get("foreignOwners") or []:
            identity = (
                owner.get("layoutUserId")
                or owner.get("componentId")
                or owner.get("region")
                or owner.get("selector")
                or "unknown-owner"
            )
            lines.append(
                f"- Foreign owner: `{identity}` placement=`{owner.get('placement', '')}` "
                f"samples=`{owner.get('sampleCount', 0)}` controls="
                f"`{', '.join(str(value) for value in (owner.get('controls') or []))}`"
            )
        for failure in classification.get("failures") or []:
            lines.append(f"- Failure: {failure}")
        for warning in classification.get("warnings") or []:
            lines.append(f"- Warning: {warning}")
        lines.append("")

    lines.extend(
        [
            "## Interpretation",
            "",
            "This smoke is intentionally not a generic screenshot score. It tests "
            "whether the semantic center editor fills the center slot left after "
            "declared sibling owners take their tracks, then checks that nested "
            "primary-work controls remain contained and actionable. A bottom dock "
            "or center-tab strip is valid ownership, not editor underfill.",
            "",
        ]
    )
    return "\n".join(lines)


def build_report(
    *,
    repo: Path,
    viewports: list[ViewportProfile],
    states: list[TrialSpec],
    browser_result: dict[str, Any],
) -> dict[str, Any]:
    static_contract = inspect_static_contract(repo)
    measurements = [
        compact_measurement(item)
        for item in browser_result.get("measurements") or []
    ]
    summary = summarize_report(
        static_contract=static_contract,
        measurements=measurements,
    )
    return {
        "kind": REPORT_KIND,
        "version": REPORT_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "hierarchySource": "live-code-editor-html-and-contract",
        "geometryEngine": "playwright-chromium",
        "fillLaw": "required-center-primary-work-fills-owned-center-slot",
        "viewports": [
            {"name": item.name, "width": item.width, "height": item.height}
            for item in viewports
        ],
        "states": [item.name for item in states],
        "staticContract": static_contract,
        "measurements": measurements,
        "pngFiles": browser_result.get("pngFiles") or [],
        "consoleMessages": browser_result.get("consoleMessages") or [],
        "pageErrors": browser_result.get("pageErrors") or [],
        "summary": summary,
    }


def write_report(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "code-editor-live-flog-report.json"
    md_path = output_dir / "code-editor-live-flog-report.md"
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="FLOG proof for live Code Editor center ownership and containment."
    )
    result.add_argument(
        "--repo",
        type=Path,
        default=repo_root_from_script(),
        help="Repository root.",
    )
    result.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for JSON, Markdown, and PNG evidence.",
    )
    result.add_argument(
        "--viewports",
        help="Comma-separated viewport definitions: name=WIDTHxHEIGHT.",
    )
    result.add_argument(
        "--states",
        help="Comma-separated trial states.",
    )
    result.add_argument(
        "--headed",
        action="store_true",
        help="Run Chromium headed.",
    )
    result.add_argument(
        "--allow-fail",
        action="store_true",
        help="Return zero even when the FLOG reports a failure.",
    )
    result.add_argument(
        "--static-only",
        action="store_true",
        help="Inspect the source contract without launching Chromium.",
    )
    return result


def main(argv: Iterable[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    repo = args.repo.resolve()
    output_dir = (
        args.output_dir
        if args.output_dir.is_absolute()
        else repo / args.output_dir
    )
    viewports = parse_viewports(args.viewports)
    states = parse_states(args.states)

    if args.static_only:
        static_contract = inspect_static_contract(repo)
        report = {
            "kind": REPORT_KIND,
            "version": REPORT_VERSION,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "hierarchySource": "live-code-editor-html-and-contract",
            "geometryEngine": "not-run",
            "fillLaw": "required-center-primary-work-fills-owned-center-slot",
            "viewports": [],
            "states": [],
            "staticContract": static_contract,
            "measurements": [],
            "pngFiles": [],
            "consoleMessages": [],
            "pageErrors": [],
            "summary": {
                "status": (
                    "pass"
                    if static_contract.get("state") == "complete"
                    else "fail"
                ),
                "trialCount": 0,
                "statusCounts": {"pass": 0, "watch": 0, "fail": 0},
                "staticContractState": static_contract.get("state"),
                "viewportCount": 0,
                "reclamationFailureCount": 0,
                "byViewport": {},
            },
        }
    else:
        browser_result = run_browser_trials(
            repo=repo,
            viewports=viewports,
            states=states,
            output_dir=output_dir,
            headed=args.headed,
        )
        report = build_report(
            repo=repo,
            viewports=viewports,
            states=states,
            browser_result=browser_result,
        )

    json_path, md_path = write_report(report, output_dir)
    summary = report.get("summary") or {}
    print(f"FLOG status: {summary.get('status', 'unknown')}")
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {md_path}")
    print(f"PNG proofs: {len(report.get('pngFiles') or [])}")

    failed = summary.get("status") == "fail"
    return 0 if args.allow_fail or not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
