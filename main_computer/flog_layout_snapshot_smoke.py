#!/usr/bin/env python3
"""Capture human-reviewable FLOG layout inference snapshots.

This smoke is intentionally narrower than a full layout engine.  It opens one or
more mounted application surfaces in Playwright/Chromium, measures the rendered
geometry that Chromium actually produced, overlays the measurements, and writes
PNG snapshots directly into the report directory plus JSON/Markdown reports.

The point is to keep a human in the loop:

* Geometry facts are treated as proved by Chromium.
* Layout-family suggestions are treated as inferences.
* Missing MCEL/source hierarchy is called out as an unknown rather than guessed.

Default run focuses on the simple file-explorer mountable because it has a small
three-region shape that is easier to reason about before promoting FLOG to the
large app surfaces.

Examples:

    python main_computer/flog_layout_snapshot_smoke.py
    python main_computer/flog_layout_snapshot_smoke.py --apps file-explorer --viewports desktop=1440x900,narrow=390x844
    python main_computer/flog_layout_snapshot_smoke.py --apps all --screenshot-mode both
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_APPS = "file-explorer"
DEFAULT_VIEWPORTS = "desktop=1440x900"
DEFAULT_CHROME = "current"

ROOT_SELECTOR_OVERRIDES = {
    "desktop": ".viewport",
    "webgl": "#webgl-demo",
    "game-editor": "#game-editor-app",
}

PARTIAL_OVERRIDES = {
    "desktop": "main_computer/web/applications.html",
    "game-editor": "main_computer/web/applications/apps/layout-builder.html",
}


@dataclass(frozen=True)
class AppEntry:
    app: str
    title: str
    summary: str
    href: str
    partial: str
    root_selector: str


@dataclass(frozen=True)
class ViewportProfile:
    name: str
    width: int
    height: int


def slugify(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip("-")
    return clean or "snapshot"


def repo_path(repo: Path, rel: str) -> Path:
    return repo.joinpath(*Path(rel).parts)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def html_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", value).strip()


def parse_launcher_entries(applications_html: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for match in re.finditer(r"<a\b(?P<attrs>[^>]*\bdata-app=\"(?P<app>[^\"]+)\"[^>]*)>(?P<body>.*?)</a>", applications_html, re.S | re.I):
        attrs = match.group("attrs")
        body = match.group("body")
        app = match.group("app").strip()
        href_match = re.search(r"\bhref=\"([^\"]+)\"", attrs, re.I)
        title_match = re.search(r"<strong[^>]*>(.*?)</strong>", body, re.S | re.I)
        summary_match = re.search(r"<span[^>]*>(.*?)</span>", body, re.S | re.I)
        entries.append(
            {
                "app": app,
                "href": href_match.group(1).strip() if href_match else f"/applications/{app}",
                "title": html_text(title_match.group(1) if title_match else app),
                "summary": html_text(summary_match.group(1) if summary_match else ""),
            }
        )
    return entries


def root_selector_for(app: str) -> str:
    if app in ROOT_SELECTOR_OVERRIDES:
        return ROOT_SELECTOR_OVERRIDES[app]
    return f"#{app}-app"


def partial_for(app: str) -> str:
    if app in PARTIAL_OVERRIDES:
        return PARTIAL_OVERRIDES[app]
    return f"main_computer/web/applications/apps/{app}.html"


def enumerate_apps(repo: Path) -> list[AppEntry]:
    applications_html = read_text(repo / "main_computer/web/applications.html")
    entries = []
    seen: set[str] = set()
    for item in parse_launcher_entries(applications_html):
        app = item["app"]
        if app in seen:
            continue
        seen.add(app)
        entries.append(
            AppEntry(
                app=app,
                title=item["title"],
                summary=item["summary"],
                href=item["href"],
                partial=partial_for(app),
                root_selector=root_selector_for(app),
            )
        )
    return entries


def parse_apps_arg(value: str, available: list[AppEntry]) -> list[AppEntry]:
    by_name = {entry.app: entry for entry in available}
    if value.strip().lower() == "all":
        return available
    selected: list[AppEntry] = []
    for name in [part.strip() for part in value.split(",") if part.strip()]:
        if name not in by_name:
            known = ", ".join(sorted(by_name))
            raise SystemExit(f"Unknown app {name!r}. Known apps: {known}")
        selected.append(by_name[name])
    return selected


def parse_viewports(value: str) -> list[ViewportProfile]:
    profiles: list[ViewportProfile] = []
    for part in [chunk.strip() for chunk in value.split(",") if chunk.strip()]:
        if "=" not in part or "x" not in part:
            raise SystemExit(f"Viewport must look like name=WIDTHxHEIGHT, got {part!r}")
        name, dims = part.split("=", 1)
        width_s, height_s = dims.lower().split("x", 1)
        try:
            width = int(width_s)
            height = int(height_s)
        except ValueError as exc:
            raise SystemExit(f"Invalid viewport dimensions in {part!r}") from exc
        if width <= 0 or height <= 0:
            raise SystemExit(f"Viewport dimensions must be positive in {part!r}")
        profiles.append(ViewportProfile(slugify(name), width, height))
    if not profiles:
        raise SystemExit("At least one viewport is required")
    return profiles


INCLUDE_PATTERN = re.compile(r"<!--\s*@include\s+([^>]+?)\s*-->")


def expand_applications_html(repo: Path) -> str:
    """Expand the repository's HTML include comments into a standalone page.

    The production shell uses include comments for styles, app partials, and
    scripts.  The smoke expands them into one temporary HTML file so Chromium can
    render the same source tree from disk without requiring the local server.
    """

    applications_path = repo / "main_computer/web/applications.html"
    source = read_text(applications_path)

    def replace_include(match: re.Match[str]) -> str:
        rel = match.group(1).strip()
        path = repo / "main_computer/web" / rel
        if not path.exists():
            return f"\n/* FLOG snapshot smoke: missing include {rel} */\n"
        text = read_text(path)
        if rel.startswith("applications/styles/"):
            return f"\n/* begin {rel} */\n{text}\n/* end {rel} */\n"
        if rel.startswith("applications/scripts/"):
            return f"\n/* begin {rel} */\n{text}\n/* end {rel} */\n"
        return f"\n<!-- begin {rel} -->\n{text}\n<!-- end {rel} -->\n"

    return INCLUDE_PATTERN.sub(replace_include, source)


MEASURE_AND_OVERLAY_JS = r"""
({rootSelector, app, chrome, viewportProfile, overlayMode}) => {
  const doc = document;
  const win = window;
  const root = doc.querySelector(rootSelector);
  const viewport = {
    width: win.innerWidth,
    height: win.innerHeight,
    left: 0,
    top: 0,
    right: win.innerWidth,
    bottom: win.innerHeight,
  };

  function rectObj(rect) {
    const left = Number(rect.left);
    const top = Number(rect.top);
    const width = Math.max(0, Number(rect.width));
    const height = Math.max(0, Number(rect.height));
    return {
      x: left,
      y: top,
      left,
      top,
      right: left + width,
      bottom: top + height,
      width,
      height,
      area: width * height,
    };
  }

  function emptyRect() {
    return {x: 0, y: 0, left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0, area: 0};
  }

  function intersect(a, b) {
    const left = Math.max(a.left, b.left);
    const top = Math.max(a.top, b.top);
    const right = Math.min(a.right, b.right);
    const bottom = Math.min(a.bottom, b.bottom);
    const width = Math.max(0, right - left);
    const height = Math.max(0, bottom - top);
    return {x: left, y: top, left, top, right, bottom, width, height, area: width * height};
  }

  function containsMostly(container, child, tolerance = 2) {
    if (!child || child.area <= 0) return false;
    return child.left >= container.left - tolerance &&
      child.right <= container.right + tolerance &&
      child.top >= container.top - tolerance &&
      child.bottom <= container.bottom + tolerance;
  }

  function cssPath(el) {
    if (!el || !el.tagName) return "";
    if (el.id) return `#${CSS.escape(el.id)}`;
    const className = String(el.className || "").trim().split(/\s+/).filter(Boolean).slice(0, 3);
    if (className.length) return `${el.tagName.toLowerCase()}.${className.map((name) => CSS.escape(name)).join(".")}`;
    return el.tagName.toLowerCase();
  }

  function labelFor(el) {
    if (!el) return "";
    const aria = el.getAttribute("aria-label") || el.getAttribute("title") || el.getAttribute("placeholder") || "";
    const text = (aria || el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
    return text.slice(0, 140);
  }

  function isVisibleByStyle(el) {
    const style = win.getComputedStyle(el);
    return style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || "1") !== 0;
  }

  function isInactiveOrDeferred(el) {
    if (!el) return false;
    if (el.closest("[hidden], [aria-hidden='true'], template")) return true;
    if (el.closest("details:not([open])")) return true;
    const input = el instanceof HTMLInputElement ? el : null;
    if (input && input.type === "file") return true;
    return false;
  }

  function elementRecord(el) {
    const raw = rectObj(el.getBoundingClientRect());
    const clippedViewport = intersect(raw, viewport);
    return {
      selector: cssPath(el),
      tag: el.tagName.toLowerCase(),
      label: labelFor(el),
      rect: raw,
      documentRect: {
        x: raw.left + win.scrollX,
        y: raw.top + win.scrollY,
        left: raw.left + win.scrollX,
        top: raw.top + win.scrollY,
        right: raw.right + win.scrollX,
        bottom: raw.bottom + win.scrollY,
        width: raw.width,
        height: raw.height,
        area: raw.area,
      },
      visibleArea: clippedViewport.area,
    };
  }

  function unionArea(rects, bounds) {
    const clipped = rects
      .map((rect) => intersect(rect, bounds))
      .filter((rect) => rect.width > 0.5 && rect.height > 0.5);
    if (!clipped.length || bounds.area <= 0) return 0;
    const xs = Array.from(new Set([bounds.left, bounds.right, ...clipped.flatMap((rect) => [rect.left, rect.right])])).sort((a, b) => a - b);
    const ys = Array.from(new Set([bounds.top, bounds.bottom, ...clipped.flatMap((rect) => [rect.top, rect.bottom])])).sort((a, b) => a - b);
    let area = 0;
    for (let xi = 0; xi < xs.length - 1; xi += 1) {
      const left = xs[xi];
      const right = xs[xi + 1];
      if (right <= left) continue;
      const midX = (left + right) / 2;
      for (let yi = 0; yi < ys.length - 1; yi += 1) {
        const top = ys[yi];
        const bottom = ys[yi + 1];
        if (bottom <= top) continue;
        const midY = (top + bottom) / 2;
        if (clipped.some((rect) => midX >= rect.left && midX <= rect.right && midY >= rect.top && midY <= rect.bottom)) {
          area += (right - left) * (bottom - top);
        }
      }
    }
    return Math.min(area, bounds.area);
  }

  function kindFor(el) {
    const tag = el.tagName.toLowerCase();
    const role = (el.getAttribute("role") || "").toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    const classText = String(el.className || "").toLowerCase();
    const idText = String(el.id || "").toLowerCase();
    if (tag === "canvas" || tag === "iframe" || tag === "video" || tag === "svg") return "embedded-surface";
    if (el.isContentEditable || tag === "textarea" || classText.includes("editor") || classText.includes("terminal")) return "embedded-surface";
    if (el.hasAttribute("data-file-explorer-tree") || classText.includes("tree") || classText.includes("grid") || classText.includes("list")) return "collection-surface";
    if (["button", "summary"].includes(tag) || role === "button") return "action";
    if (tag === "a" && el.hasAttribute("href")) return "action";
    if (["input", "select", "textarea"].includes(tag) && type !== "hidden") return "control";
    if (/^h[1-6]$/.test(tag) || tag === "label" || tag === "strong") return "label";
    if (tag === "pre" || tag === "code" || tag === "output") return "evidence";
    if (["main", "section", "aside", "article", "nav", "header", "footer"].includes(tag)) return "region";
    if ((el.innerText || "").trim().length > 24 && el.children.length === 0) return "text";
    return "";
  }

  function isMeaningful(el) {
    if (!root.contains(el) || el === root) return false;
    if (!isVisibleByStyle(el)) return false;
    const rect = rectObj(el.getBoundingClientRect());
    if (rect.area <= 4) return false;
    const kind = kindFor(el);
    if (kind) return true;
    return false;
  }

  function isCriticalAction(el) {
    if (!root.contains(el) || el === root) return false;
    const tag = el.tagName.toLowerCase();
    const role = (el.getAttribute("role") || "").toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (tag === "input" && type === "hidden") return false;
    return tag === "button" || tag === "summary" || role === "button" || (tag === "a" && el.hasAttribute("href")) || ["input", "select", "textarea"].includes(tag);
  }

  if (!root) {
    return {
      app,
      chrome,
      viewportProfile,
      rootSelector,
      rootFound: false,
      geometryFacts: {
        provedBy: "playwright-chromium",
        viewport,
      },
      overlayLegend: [],
      inference: {
        confidence: "none",
        suggestion: "unknown",
        reason: "root selector was not found",
      },
      unknowns: ["The selected mount root was not found, so no layout inference is valid."],
    };
  }

  const rootRaw = rectObj(root.getBoundingClientRect());
  const rootClipped = intersect(rootRaw, viewport);
  const rootDocumentRect = {
    x: rootRaw.left + win.scrollX,
    y: rootRaw.top + win.scrollY,
    left: rootRaw.left + win.scrollX,
    top: rootRaw.top + win.scrollY,
    right: rootRaw.right + win.scrollX,
    bottom: rootRaw.bottom + win.scrollY,
    width: rootRaw.width,
    height: rootRaw.height,
    area: rootRaw.area,
  };

  const allInside = Array.from(root.querySelectorAll("*"));
  const meaningful = allInside.filter(isMeaningful).map((el) => ({...elementRecord(el), kind: kindFor(el)}));
  const criticalElements = allInside.filter(isCriticalAction);

  const clippedCriticalActions = [];
  const hiddenCriticalActions = [];
  const intentionallyDeferredActions = [];
  for (const el of criticalElements) {
    const record = elementRecord(el);
    const visibleByStyle = isVisibleByStyle(el);
    const hasArea = record.rect.area > 4;
    const deferred = isInactiveOrDeferred(el);
    if (!visibleByStyle || !hasArea) {
      if (deferred) intentionallyDeferredActions.push({...record, reason: "deferred-or-proxied"});
      else hiddenCriticalActions.push({...record, reason: "hidden-or-zero-sized"});
      continue;
    }
    const inViewport = containsMostly(viewport, record.rect);
    const inRoot = containsMostly(rootRaw, record.rect);
    if (!inViewport || !inRoot) {
      clippedCriticalActions.push({
        ...record,
        reason: !inViewport ? "outside-viewport" : "outside-root",
      });
    }
  }

  const scrollOwners = [];
  for (const el of [root, ...allInside]) {
    if (!isVisibleByStyle(el)) continue;
    const rect = rectObj(el.getBoundingClientRect());
    if (rect.area <= 4) continue;
    const style = win.getComputedStyle(el);
    const overflowText = `${style.overflow} ${style.overflowX} ${style.overflowY}`;
    const canScroll = /(auto|scroll|overlay)/.test(overflowText);
    const scrolls = el.scrollHeight > el.clientHeight + 2 || el.scrollWidth > el.clientWidth + 2;
    if (scrolls && canScroll) {
      scrollOwners.push({
        selector: cssPath(el),
        label: labelFor(el),
        rect,
        clientWidth: el.clientWidth,
        clientHeight: el.clientHeight,
        scrollWidth: el.scrollWidth,
        scrollHeight: el.scrollHeight,
      });
    }
  }

  const meaningfulRects = meaningful.map((item) => item.rect);
  const meaningfulArea = unionArea(meaningfulRects, rootClipped);
  const meaningfulCoverageRatio = rootClipped.area > 0 ? meaningfulArea / rootClipped.area : 0;
  const unclaimedAreaRatio = rootClipped.area > 0 ? Math.max(0, 1 - meaningfulCoverageRatio) : 1;
  const rootViewportCoverageRatio = viewport.width * viewport.height > 0 ? rootClipped.area / (viewport.width * viewport.height) : 0;

  const dataTraitElements = allInside.filter((el) => Array.from(el.attributes).some((attr) => attr.name.startsWith("data-mc") || attr.name.startsWith("data-widget")));
  const mcelTraitElements = allInside.filter((el) => Array.from(el.attributes).some((attr) => attr.name.startsWith("data-mc-") || attr.name === "data-mc"));
  const explicitSlots = allInside.filter((el) => el.hasAttribute("data-mc-slot"));
  const explicitConnects = allInside.filter((el) => el.hasAttribute("data-mc-connects"));

  const tagCounts = allInside.reduce((acc, el) => {
    const tag = el.tagName.toLowerCase();
    acc[tag] = (acc[tag] || 0) + 1;
    return acc;
  }, {});
  const directChildren = Array.from(root.children).filter((el) => isVisibleByStyle(el) && rectObj(el.getBoundingClientRect()).area > 4);
  const directChildTags = directChildren.map((el) => el.tagName.toLowerCase());
  const hasAside = allInside.some((el) => el.tagName.toLowerCase() === "aside");
  const hasMainOrSection = allInside.some((el) => ["main", "section", "article"].includes(el.tagName.toLowerCase()));
  const hasCollection = meaningful.some((item) => item.kind === "collection-surface");
  const hasEmbedded = meaningful.some((item) => item.kind === "embedded-surface");

  let suggestion = "stacked-flow";
  const reasons = [];
  let inferenceConfidence = "low";
  if (directChildren.length >= 2 && hasAside && hasMainOrSection) {
    suggestion = "split-pane-or-sectioned-sidebar";
    reasons.push("rendered HTML has side/support and main/content boundaries");
  } else if (hasEmbedded && meaningful.length <= 6) {
    suggestion = "embedded-surface-priority";
    reasons.push("one or more embedded surfaces dominate the meaningful geometry");
  } else if (hasCollection && hasMainOrSection) {
    suggestion = "split-pane";
    reasons.push("collection and content boundaries can be kept visible together");
  } else if (meaningful.length <= 12 && rootViewportCoverageRatio > 0.35) {
    suggestion = "stacked-flow";
    reasons.push("small meaningful hierarchy can remain linear without obvious crowding");
  }
  if (explicitSlots.length || explicitConnects.length) {
    inferenceConfidence = "medium";
    reasons.push("some MCEL relationship traits are present");
  }
  if (mcelTraitElements.length >= 5 && explicitSlots.length >= 2) {
    inferenceConfidence = "high";
  }
  if (!explicitSlots.length) {
    reasons.push("no data-mc-slot traits found, so concern placement remains partly human-reviewed");
  }
  if (!explicitConnects.length) {
    reasons.push("no data-mc-connects traits found, so action/result relationships are not machine-proven");
  }

  const warnings = [];
  if (rootClipped.area <= 1) warnings.push("mount root is not visible or has zero clipped area");
  if (unclaimedAreaRatio > 0.6) warnings.push(`high measured unclaimed meaningful area (${unclaimedAreaRatio.toFixed(2)})`);
  if (clippedCriticalActions.length) warnings.push(`${clippedCriticalActions.length} critical action(s) extend outside viewport/root`);
  if (hiddenCriticalActions.length) warnings.push(`${hiddenCriticalActions.length} critical action(s) are hidden or zero-sized`);
  if (scrollOwners.length > 3) warnings.push(`${scrollOwners.length} scroll owner(s) detected; scroll ownership needs human review`);

  const score = Math.max(
    0,
    Math.round(
      100 -
        clippedCriticalActions.length * 7 -
        hiddenCriticalActions.length * 10 -
        Math.max(0, scrollOwners.length - 1) * 8 -
        (unclaimedAreaRatio > 0.6 ? 20 : unclaimedAreaRatio > 0.45 ? 8 : 0) -
        (rootViewportCoverageRatio < 0.25 ? 12 : 0)
    )
  );
  const status = score >= 85 && !warnings.length ? "pass" : score >= 65 ? "watch" : "fail";

  const proved = [
    "Chromium viewport size",
    "mount-root rectangle",
    "meaningful rendered rectangles",
    "visible/clipped/hidden control rectangles",
    "scroll-owner rectangles",
    "approximate union area of meaningful rendered elements inside the mount root",
  ];
  const inferred = [
    "whether unclaimed area is actually wasted vs intentionally calm spacing",
    "which stable layout family should be tried first",
    "whether hidden/proxied controls are acceptable app state",
  ];
  const unknowns = [];
  if (!explicitSlots.length) unknowns.push("Concern hierarchy is weak because data-mc-slot traits are missing.");
  if (!explicitConnects.length) unknowns.push("Action/result relationships are weak because data-mc-connects traits are missing.");
  if (unclaimedAreaRatio > 0.6) unknowns.push("High unclaimed area needs human review: it may be waste, or the detector may not yet understand a meaningful surface.");
  if (intentionallyDeferredActions.length) unknowns.push("Some controls are intentionally deferred/proxied and should not be treated as layout failures without human confirmation.");

  // Draw overlay into the page. Coordinates are document-relative so viewport and
  // full-page screenshots stay aligned.
  doc.querySelectorAll("[data-flog-snapshot-overlay]").forEach((node) => node.remove());
  const overlay = doc.createElement("div");
  overlay.setAttribute("data-flog-snapshot-overlay", "root");
  overlay.style.position = "absolute";
  overlay.style.left = "0";
  overlay.style.top = "0";
  overlay.style.width = `${Math.max(doc.documentElement.scrollWidth, doc.body.scrollWidth, viewport.width)}px`;
  overlay.style.height = `${Math.max(doc.documentElement.scrollHeight, doc.body.scrollHeight, viewport.height)}px`;
  overlay.style.pointerEvents = "none";
  overlay.style.zIndex = "2147483647";
  overlay.style.fontFamily = "monospace";
  overlay.style.fontSize = "11px";
  overlay.style.lineHeight = "1.2";
  doc.body.appendChild(overlay);

  function addBox(record, color, label, fill = "transparent") {
    const rect = record.documentRect || {
      left: record.left + win.scrollX,
      top: record.top + win.scrollY,
      width: record.width,
      height: record.height,
    };
    if (!rect || rect.width <= 1 || rect.height <= 1) return;
    const box = doc.createElement("div");
    box.setAttribute("data-flog-snapshot-overlay", "box");
    box.style.position = "absolute";
    box.style.left = `${rect.left}px`;
    box.style.top = `${rect.top}px`;
    box.style.width = `${rect.width}px`;
    box.style.height = `${rect.height}px`;
    box.style.border = `2px solid ${color}`;
    box.style.background = fill;
    box.style.boxSizing = "border-box";
    box.style.borderRadius = "3px";
    const tag = doc.createElement("div");
    tag.textContent = label;
    tag.style.position = "absolute";
    tag.style.left = "0";
    tag.style.top = "0";
    tag.style.maxWidth = "260px";
    tag.style.padding = "2px 4px";
    tag.style.color = "#111";
    tag.style.background = color;
    tag.style.opacity = "0.92";
    tag.style.whiteSpace = "nowrap";
    tag.style.overflow = "hidden";
    tag.style.textOverflow = "ellipsis";
    box.appendChild(tag);
    overlay.appendChild(box);
  }

  addBox({documentRect: rootDocumentRect}, "#43a7ff", `root ${rootSelector}`, "rgba(67, 167, 255, 0.05)");
  meaningful.slice(0, 36).forEach((item) => addBox(item, "#38d66b", `${item.kind}: ${item.selector}`, "rgba(56, 214, 107, 0.04)"));
  scrollOwners.slice(0, 18).forEach((item) => {
    const record = {
      documentRect: {
        left: item.rect.left + win.scrollX,
        top: item.rect.top + win.scrollY,
        width: item.rect.width,
        height: item.rect.height,
      }
    };
    addBox(record, "#ffbf3f", `scroll: ${item.selector}`, "rgba(255, 191, 63, 0.06)");
  });
  clippedCriticalActions.slice(0, 18).forEach((item) => addBox(item, "#ff4d4d", `clipped: ${item.selector}`, "rgba(255, 77, 77, 0.05)"));
  hiddenCriticalActions.slice(0, 12).forEach((item) => {
    // Hidden zero-size elements cannot be boxed; mark the top-left of the root.
    const marker = {
      documentRect: {
        left: rootDocumentRect.left + 6,
        top: rootDocumentRect.top + 24 + hiddenCriticalActions.indexOf(item) * 18,
        width: 180,
        height: 16,
      }
    };
    addBox(marker, "#ff4d4d", `hidden: ${item.selector}`, "rgba(255, 77, 77, 0.16)");
  });

  const legend = doc.createElement("div");
  legend.setAttribute("data-flog-snapshot-overlay", "legend");
  legend.style.position = "fixed";
  legend.style.left = "12px";
  legend.style.bottom = "12px";
  legend.style.maxWidth = "520px";
  legend.style.padding = "10px 12px";
  legend.style.background = "rgba(0, 0, 0, 0.78)";
  legend.style.color = "white";
  legend.style.border = "1px solid rgba(255,255,255,0.35)";
  legend.style.borderRadius = "8px";
  legend.style.boxShadow = "0 8px 28px rgba(0,0,0,0.35)";
  legend.innerHTML = [
    `<strong>FLOG human-review snapshot</strong>`,
    `app=${app} viewport=${viewportProfile} chrome=${chrome}`,
    `score=${score} status=${status} unclaimed=${unclaimedAreaRatio.toFixed(3)}`,
    `<span style="color:#43a7ff">blue=root</span> <span style="color:#38d66b">green=meaningful</span> <span style="color:#ffbf3f">orange=scroll</span> <span style="color:#ff4d4d">red=clipped/hidden</span>`,
    `inference=${suggestion} confidence=${inferenceConfidence}`,
  ].join("<br>");
  overlay.appendChild(legend);

  return {
    app,
    chrome,
    viewportProfile,
    rootSelector,
    rootFound: true,
    title: root.getAttribute("aria-label") || labelFor(root),
    geometryFacts: {
      provedBy: "playwright-chromium",
      viewport,
      root: {
        raw: rootRaw,
        clipped: rootClipped,
        documentRect: rootDocumentRect,
      },
      bodyScrollWidth: doc.body.scrollWidth,
      bodyScrollHeight: doc.body.scrollHeight,
      documentOverflowX: doc.body.scrollWidth > viewport.width + 2,
      documentOverflowY: doc.body.scrollHeight > viewport.height + 2,
      meaningfulCount: meaningful.length,
      meaningfulCoverageRatio,
      unclaimedAreaRatio,
      rootViewportCoverageRatio,
      clippedCriticalActionCount: clippedCriticalActions.length,
      hiddenCriticalActionCount: hiddenCriticalActions.length,
      intentionallyDeferredActionCount: intentionallyDeferredActions.length,
      scrollOwnerCount: scrollOwners.length,
      directChildTags,
      tagCounts,
      dataTraitElementCount: dataTraitElements.length,
      mcelTraitElementCount: mcelTraitElements.length,
      explicitSlotCount: explicitSlots.length,
      explicitConnectCount: explicitConnects.length,
    },
    examples: {
      meaningful: meaningful.slice(0, 20),
      clippedCriticalActions: clippedCriticalActions.slice(0, 20),
      hiddenCriticalActions: hiddenCriticalActions.slice(0, 20),
      intentionallyDeferredActions: intentionallyDeferredActions.slice(0, 20),
      scrollOwners: scrollOwners.slice(0, 20),
    },
    classification: {
      score,
      status,
      warnings,
    },
    inference: {
      suggestion,
      confidence: inferenceConfidence,
      reasons,
    },
    humanLoop: {
      required: inferenceConfidence !== "high" || warnings.length > 0,
      proved,
      inferred,
      unknowns,
      reviewPrompt: "Use the PNG overlay to decide whether the inferred layout family makes sense or whether the HTML hierarchy needs better MCEL traits first.",
    },
    overlayLegend: [
      {color: "blue", meaning: "mount root"},
      {color: "green", meaning: "meaningful occupied surfaces/controls"},
      {color: "orange", meaning: "scroll owners"},
      {color: "red", meaning: "clipped or hidden critical controls"},
    ],
  };
}
"""


def playwright_missing_message(exc: BaseException) -> str:
    return (
        "Playwright/Chromium is required for PNG geometry snapshots. "
        "Install the browser with: python -m playwright install chromium. "
        f"Original error: {exc}"
    )


def verify_png_written(path: Path) -> None:
    """Fail loudly when the whole purpose of the run did not happen."""
    if not path.exists():
        raise RuntimeError(f"Expected PNG snapshot was not created: {path}")
    if path.stat().st_size <= 0:
        raise RuntimeError(f"Expected PNG snapshot is empty: {path}")


def capture_png(page: Any, path: Path, *, full_page: bool) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(path), full_page=full_page)
    verify_png_written(path)
    return path.name


def run_browser_snapshots(
    *,
    repo: Path,
    apps: list[AppEntry],
    viewports: list[ViewportProfile],
    chrome: str,
    output_dir: Path,
    screenshot_mode: str,
    keep_expanded_html: bool,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - exercised only without dependency
        raise SystemExit(playwright_missing_message(exc)) from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    # Put PNGs directly in the report directory by default. The report should be
    # self-contained and obvious to inspect after a smoke run.
    snapshot_dir = output_dir

    expanded_html = expand_applications_html(repo)
    temp_dir_cm = tempfile.TemporaryDirectory(prefix="flog-layout-snapshot-")
    temp_dir = Path(temp_dir_cm.name)
    expanded_path = temp_dir / "applications-expanded.html"
    expanded_path.write_text(expanded_html, encoding="utf-8")
    if keep_expanded_html:
        expanded_copy = output_dir / "applications-expanded.html"
        expanded_copy.write_text(expanded_html, encoding="utf-8")

    measurements: list[dict[str, Any]] = []
    generated_at = datetime.now(timezone.utc).isoformat()

    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(headless=True)
            except Exception as exc:  # pragma: no cover - depends on local browser install
                raise SystemExit(playwright_missing_message(exc)) from exc

            try:
                for viewport in viewports:
                    context = browser.new_context(
                        viewport={"width": viewport.width, "height": viewport.height},
                        device_scale_factor=1,
                    )
                    page = context.new_page()
                    page.on("console", lambda msg: None)
                    for app in apps:
                        url = expanded_path.as_uri()
                        page.goto(url, wait_until="domcontentloaded")
                        page.evaluate(
                            """({app}) => {
                              try {
                                if (typeof window.setActiveApp === "function") {
                                  window.setActiveApp(app, {syncRoute: false, replaceRoute: true});
                                } else {
                                  document.body.dataset.activeApp = app;
                                }
                              } catch (error) {
                                document.body.dataset.activeApp = app;
                                window.__flogActivationError = String(error && error.message || error || "");
                              }
                            }""",
                            {"app": app.app},
                        )
                        page.wait_for_timeout(150)
                        measurement = page.evaluate(
                            MEASURE_AND_OVERLAY_JS,
                            {
                                "rootSelector": app.root_selector,
                                "app": app.app,
                                "chrome": chrome,
                                "viewportProfile": viewport.name,
                                "overlayMode": screenshot_mode,
                            },
                        )
                        measurement["partial"] = app.partial
                        measurement["href"] = app.href
                        measurement["appTitle"] = app.title
                        measurement["summary"] = app.summary
                        measurement["activationError"] = page.evaluate("window.__flogActivationError || ''")
                        measurement["snapshots"] = {}

                        base_name = f"{slugify(app.app)}--{slugify(viewport.name)}--{slugify(chrome)}"
                        page.wait_for_timeout(50)
                        if screenshot_mode in {"viewport", "both"}:
                            png = snapshot_dir / f"{base_name}--viewport.png"
                            measurement["snapshots"]["viewport"] = capture_png(page, png, full_page=False)
                        if screenshot_mode in {"full-page", "both"}:
                            png = snapshot_dir / f"{base_name}--full-page.png"
                            measurement["snapshots"]["fullPage"] = capture_png(page, png, full_page=True)
                        if not measurement["snapshots"]:
                            raise RuntimeError(f"No PNG snapshots were requested or written for {app.app} / {viewport.name}.")
                        measurements.append(measurement)
                    context.close()
            finally:
                browser.close()
    finally:
        temp_dir_cm.cleanup()

    report = {
        "kind": "mcel.flog.layout.snapshot.report",
        "generatedAt": generated_at,
        "smokeLevel": "browser-geometry-human-review",
        "geometryEngine": "playwright-chromium",
        "hierarchySource": "html",
        "chrome": chrome,
        "apps": [app.app for app in apps],
        "viewports": [{"name": vp.name, "width": vp.width, "height": vp.height} for vp in viewports],
        "screenshotMode": screenshot_mode,
        "snapshotDirectory": ".",
        "snapshotFiles": [
            rel
            for measurement in measurements
            for rel in (measurement.get("snapshots") or {}).values()
        ],
        "humanLoop": {
            "required": True,
            "reason": "The tool can prove geometry, but layout-family promotion still needs human review when MCEL hierarchy traits are weak.",
        },
        "measurements": measurements,
    }
    return report


def write_reports(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "layout-snapshot-report.json"
    md_path = output_dir / "layout-snapshot-report.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    lines: list[str] = []
    lines.append("# FLOG Layout Snapshot Report")
    lines.append("")
    lines.append(f"- Generated: `{report['generatedAt']}`")
    lines.append(f"- Smoke level: `{report['smokeLevel']}`")
    lines.append(f"- Geometry engine: `{report['geometryEngine']}`")
    lines.append(f"- Hierarchy source: `{report['hierarchySource']}`")
    lines.append(f"- Chrome/theme input: `{report['chrome']}`")
    lines.append(f"- Apps: `{', '.join(report['apps'])}`")
    lines.append(f"- Viewports: `{', '.join(v['name'] for v in report['viewports'])}`")
    lines.append(f"- PNG directory: `{report['snapshotDirectory']}`")
    files = report.get("snapshotFiles") or []
    lines.append(f"- PNG files written: `{len(files)}`")
    if files:
        lines.append(f"- First PNG: `{files[0]}`")
    lines.append("")
    lines.append("## Human-in-the-loop boundary")
    lines.append("")
    lines.append("This smoke proves rendered geometry with Chromium, but it does not promote a layout by itself.")
    lines.append("The PNG overlays are meant for review: blue is the mount root, green is meaningful occupied space, orange is scroll ownership, and red is clipped/hidden critical controls.")
    lines.append("")
    lines.append("## Measurements")
    lines.append("")
    for item in report["measurements"]:
        facts = item.get("geometryFacts", {})
        classification = item.get("classification", {})
        inference = item.get("inference", {})
        human = item.get("humanLoop", {})
        lines.append(f"### `{item.get('app')}` / `{item.get('viewportProfile')}` / `{item.get('chrome')}`")
        lines.append("")
        lines.append(f"- Status: `{classification.get('status')}`")
        lines.append(f"- Score: `{classification.get('score')}`")
        lines.append(f"- Root selector: `{item.get('rootSelector')}`")
        lines.append(f"- Suggested layout family: `{inference.get('suggestion')}`")
        lines.append(f"- Inference confidence: `{inference.get('confidence')}`")
        lines.append(f"- Unclaimed meaningful area ratio: `{facts.get('unclaimedAreaRatio', 0):.4f}`")
        lines.append(f"- Meaningful coverage ratio: `{facts.get('meaningfulCoverageRatio', 0):.4f}`")
        lines.append(f"- Root viewport coverage ratio: `{facts.get('rootViewportCoverageRatio', 0):.4f}`")
        lines.append(f"- Clipped critical actions: `{facts.get('clippedCriticalActionCount', 0)}`")
        lines.append(f"- Hidden critical actions: `{facts.get('hiddenCriticalActionCount', 0)}`")
        lines.append(f"- Intentionally deferred/proxied actions: `{facts.get('intentionallyDeferredActionCount', 0)}`")
        lines.append(f"- Scroll owners: `{facts.get('scrollOwnerCount', 0)}`")
        snaps = item.get("snapshots", {})
        if snaps:
            lines.append("- PNG snapshots:")
            for key, rel in snaps.items():
                lines.append(f"  - `{key}`: `{rel}`")
        warnings = classification.get("warnings") or []
        if warnings:
            lines.append("- Warnings:")
            for warning in warnings:
                lines.append(f"  - {warning}")
        proved = human.get("proved") or []
        inferred = human.get("inferred") or []
        unknowns = human.get("unknowns") or []
        if proved:
            lines.append("- Proved by Chromium:")
            for entry in proved:
                lines.append(f"  - {entry}")
        if inferred:
            lines.append("- Inferred, not proved:")
            for entry in inferred:
                lines.append(f"  - {entry}")
        if unknowns:
            lines.append("- Unknown / needs human review:")
            for entry in unknowns:
                lines.append(f"  - {entry}")
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture FLOG layout inference PNG snapshots for human review.")
    parser.add_argument("--repo", default=".", help="Repository root. Default: current directory.")
    parser.add_argument("--apps", default=DEFAULT_APPS, help="Comma-separated app slugs or 'all'. Default: file-explorer.")
    parser.add_argument("--viewports", default=DEFAULT_VIEWPORTS, help="Comma-separated profiles like desktop=1440x900,narrow=390x844.")
    parser.add_argument("--chrome", default=DEFAULT_CHROME, help="Chrome/theme label to record as layout input. Default: current.")
    parser.add_argument("--output-dir", default="runtime/reports/flog", help="Output report directory. PNGs are written directly here.")
    parser.add_argument(
        "--screenshot-mode",
        choices=["viewport", "full-page", "both"],
        default="both",
        help="Which PNG snapshots to write. Default: both.",
    )
    parser.add_argument("--keep-expanded-html", action="store_true", help="Write the expanded HTML used for Chromium into the output directory.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    output_dir = (repo / args.output_dir).resolve() if not Path(args.output_dir).is_absolute() else Path(args.output_dir).resolve()
    available = enumerate_apps(repo)
    apps = parse_apps_arg(args.apps, available)
    viewports = parse_viewports(args.viewports)

    report = run_browser_snapshots(
        repo=repo,
        apps=apps,
        viewports=viewports,
        chrome=args.chrome,
        output_dir=output_dir,
        screenshot_mode=args.screenshot_mode,
        keep_expanded_html=args.keep_expanded_html,
    )
    json_path, md_path = write_reports(report, output_dir)
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    png_count = 0
    for measurement in report["measurements"]:
        for rel in (measurement.get("snapshots") or {}).values():
            png_count += 1
            print(f"Wrote PNG {output_dir / rel}")
    if png_count == 0:
        raise SystemExit("No PNG snapshots were written; refusing to call this smoke successful.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
