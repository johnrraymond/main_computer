"""Playwright-backed FLOG smoke for rendered mountable layouts.

This smoke is intentionally separate from the static HTML census. The static
census can tell us what hierarchy and boundary hints exist in source. This
browser smoke opens the rendered Applications shell in Chromium, activates each
mountable, and measures actual geometry: visible area, unclaimed area inside the
mount root, overflow pressure, scroll owners, clipped primary actions, and
candidate layout evidence.

It does not mutate source or promote defaults. It writes reproducibility
artifacts that can be compared over time before defaults are committed.

Run from the repository root:

    python main_computer/flog_mountables_browser_smoke.py

Optional:

    python main_computer/flog_mountables_browser_smoke.py --apps conductor,calculator
    python main_computer/flog_mountables_browser_smoke.py --viewports desktop=1440x900,narrow=390x844
    python main_computer/flog_mountables_browser_smoke.py --headed
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable


DEFAULT_VIEWPORTS: tuple[tuple[str, int, int], ...] = (
    ("desktop", 1440, 900),
    ("tablet", 1024, 768),
    ("narrow", 390, 844),
)

DEFAULT_CHROMES: tuple[str, ...] = ("current",)

REPORT_KIND = "mcel.flog.mountables.browser.report"
REPORT_VERSION = "mcel.flog.browser.v1"

LAYOUT_FAMILIES = (
    "stacked-flow",
    "sectioned-sidebar",
    "split-pane",
    "inspector",
    "bounded-drawer",
    "hub-and-spoke",
    "central-spiral",
    "dashboard-grid",
    "fault-avoidant",
)

ROOT_SELECTOR_OVERRIDES = {
    "desktop": ".viewport",
    "webgl": "#webgl-demo",
    "game-editor": "#game-editor-app",
}

PARTIAL_ALIASES = {
    "game-editor": "layout-builder",
}


@dataclass(frozen=True)
class ViewportProfile:
    name: str
    width: int
    height: int


@dataclass(frozen=True)
class MountableSpec:
    app: str
    title: str
    href: str
    summary: str
    root_selector: str
    expected_partial: str
    alias_of_partial: str | None = None


class _LauncherParser(HTMLParser):
    """Tiny parser for launcher cards in applications.html.

    The browser smoke uses the fully expanded page at runtime, but parsing the
    source launcher gives the Python side a stable list of mountables and their
    expected roots without requiring Playwright just to know the app inventory.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.launchers: list[dict[str, str]] = []
        self._current: dict[str, Any] | None = None
        self._capture: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {name: value or "" for name, value in attrs}
        if tag == "a" and attr.get("data-app"):
            self._current = {
                "app": attr["data-app"].strip(),
                "href": attr.get("href", "").strip(),
                "title": "",
                "summary": "",
            }
            self._capture = None
            return
        if not self._current:
            return
        if tag == "strong":
            self._capture = "title"
        elif tag == "span":
            self._capture = "summary"

    def handle_data(self, data: str) -> None:
        if self._current and self._capture:
            self._current[self._capture] += data.strip()

    def handle_endtag(self, tag: str) -> None:
        if not self._current:
            return
        if tag in {"strong", "span"}:
            self._capture = None
        elif tag == "a":
            self.launchers.append(
                {
                    "app": str(self._current.get("app", "")),
                    "href": str(self._current.get("href", "")),
                    "title": " ".join(str(self._current.get("title", "")).split()),
                    "summary": " ".join(str(self._current.get("summary", "")).split()),
                }
            )
            self._current = None
            self._capture = None


def parse_viewports(text: str | None) -> list[ViewportProfile]:
    if not text:
        return [ViewportProfile(name, width, height) for name, width, height in DEFAULT_VIEWPORTS]
    profiles: list[ViewportProfile] = []
    for chunk in text.split(","):
        part = chunk.strip()
        if not part:
            continue
        if "=" not in part or "x" not in part:
            raise ValueError(f"Invalid viewport profile {part!r}; expected name=WIDTHxHEIGHT")
        name, size = part.split("=", 1)
        width_text, height_text = size.lower().split("x", 1)
        width = int(width_text)
        height = int(height_text)
        if width <= 0 or height <= 0:
            raise ValueError(f"Invalid viewport size {part!r}; dimensions must be positive")
        profiles.append(ViewportProfile(name.strip(), width, height))
    if not profiles:
        raise ValueError("At least one viewport profile is required")
    return profiles


def parse_csv(text: str | None, default: Iterable[str]) -> list[str]:
    if not text:
        return list(default)
    return [item.strip() for item in text.split(",") if item.strip()]


def applications_source_path(repo: Path) -> Path:
    return repo / "main_computer" / "web" / "applications.html"


def parse_mountables(repo: Path) -> list[MountableSpec]:
    source = applications_source_path(repo)
    parser = _LauncherParser()
    parser.feed(source.read_text(encoding="utf-8"))

    specs: list[MountableSpec] = []
    seen: set[str] = set()
    for launcher in parser.launchers:
        app = launcher["app"]
        if not app or app in seen:
            continue
        seen.add(app)
        partial_slug = PARTIAL_ALIASES.get(app, app)
        specs.append(
            MountableSpec(
                app=app,
                title=launcher.get("title") or app,
                href=launcher.get("href") or f"/applications/{app}",
                summary=launcher.get("summary") or "",
                root_selector=ROOT_SELECTOR_OVERRIDES.get(app, f"#{app}-app"),
                expected_partial=f"main_computer/web/applications/apps/{partial_slug}.html"
                if app != "desktop"
                else "main_computer/web/applications.html",
                alias_of_partial=partial_slug if partial_slug != app else None,
            )
        )
    return specs


def load_expanded_applications_html(repo: Path) -> str:
    """Load the rendered Applications HTML using the repo's include expander."""

    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from main_computer.viewport_pages import APPLICATIONS_INDEX_HTML

    return APPLICATIONS_INDEX_HTML


def classify_measurement(measurement: dict[str, Any]) -> dict[str, Any]:
    """Convert raw geometry metrics into conservative FLOG notes.

    The ratios here are not aesthetic truth. They are smoke-test pressure
    signals. A high unclaimed-area ratio means Chromium saw large rendered areas
    inside the mount root that were not covered by meaningful leaf controls,
    evidence, text-bearing regions, or embedded surfaces.
    """

    root = measurement.get("root") or {}
    metrics = measurement.get("metrics") or {}
    clipped_actions = int(metrics.get("clippedCriticalActions") or 0)
    hidden_actions = int(metrics.get("hiddenCriticalActions") or 0)
    scroll_owners = int(metrics.get("scrollOwnerCount") or 0)
    unclaimed = float(metrics.get("unclaimedLeafAreaRatio") or 0)
    viewport_coverage = float(metrics.get("viewportCoverageRatio") or 0)
    root_area = float(root.get("area") or 0)
    document_overflow_x = bool(metrics.get("documentOverflowX"))
    document_overflow_y = bool(metrics.get("documentOverflowY"))

    warnings: list[str] = []
    if root_area <= 0:
        warnings.append("mount root is not visible or has zero area")
    if clipped_actions:
        warnings.append(f"{clipped_actions} critical action(s) are clipped outside the viewport or mount root")
    if hidden_actions:
        warnings.append(f"{hidden_actions} critical action(s) are hidden or zero-sized")
    if document_overflow_x:
        warnings.append("document has horizontal overflow")
    if scroll_owners >= 4:
        warnings.append(f"{scroll_owners} scroll owners detected; layout needs explicit scroll ownership")
    if unclaimed >= 0.55:
        warnings.append(f"high unclaimed leaf area ratio ({unclaimed:.2f})")
    if viewport_coverage < 0.18 and root_area > 0:
        warnings.append(f"mount root covers little viewport area ({viewport_coverage:.2f})")

    score = 100
    score -= min(45, clipped_actions * 15)
    score -= min(30, hidden_actions * 10)
    score -= 12 if document_overflow_x else 0
    score -= min(20, max(0, scroll_owners - 2) * 5)
    score -= min(25, int(max(0.0, unclaimed - 0.35) * 100))
    score -= 12 if root_area <= 0 else 0
    score = max(0, score)

    if score >= 85:
        status = "pass"
    elif score >= 65:
        status = "watch"
    else:
        status = "fail"

    return {
        "status": status,
        "score": score,
        "warnings": warnings,
    }


def summarize_app(measurements: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter((item.get("classification") or {}).get("status", "unknown") for item in measurements)
    worst_score = min(int((item.get("classification") or {}).get("score", 0)) for item in measurements) if measurements else 0
    worst_unclaimed = max(float(((item.get("metrics") or {}).get("unclaimedLeafAreaRatio")) or 0) for item in measurements) if measurements else 0
    max_scroll_owners = max(int(((item.get("metrics") or {}).get("scrollOwnerCount")) or 0) for item in measurements) if measurements else 0
    total_clipped = sum(int(((item.get("metrics") or {}).get("clippedCriticalActions")) or 0) for item in measurements)
    total_hidden = sum(int(((item.get("metrics") or {}).get("hiddenCriticalActions")) or 0) for item in measurements)

    if statuses.get("fail"):
        status = "fail"
    elif statuses.get("watch"):
        status = "watch"
    else:
        status = "pass"

    return {
        "status": status,
        "worstScore": worst_score,
        "worstUnclaimedLeafAreaRatio": round(worst_unclaimed, 4),
        "maxScrollOwners": max_scroll_owners,
        "totalClippedCriticalActions": total_clipped,
        "totalHiddenCriticalActions": total_hidden,
    }


BROWSER_MEASURE_JS = r"""
async ({app, rootSelector, chromeName}) => {
  const viewport = {
    width: window.innerWidth || document.documentElement.clientWidth || 0,
    height: window.innerHeight || document.documentElement.clientHeight || 0
  };

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function activateApp(name) {
    document.body.dataset.activeApp = name;
    document.body.dataset.flogChrome = chromeName || "current";
    document.documentElement.dataset.flogChrome = chromeName || "current";
    if (typeof window.setActiveApp === "function") {
      try {
        window.setActiveApp(name, {syncRoute: false});
      } catch (error) {
        window.__flogActivationError = String(error && error.message ? error.message : error);
      }
    }

    const rootMap = {
      desktop: ".viewport",
      webgl: "#webgl-demo",
      "game-editor": "#game-editor-app"
    };
    const selectedRoot = rootMap[name] || `#${name}-app`;
    const allRoots = [
      "#webgl-demo",
      "#astrometric-app",
      "#calculator-app",
      "#document-app",
      "#spreadsheet-app",
      "#onlyoffice-app",
      "#task-manager-app",
      "#conductor-app",
      "#terminal-app",
      "#chat-console-app",
      "#ai-control-app",
      "#email-app",
      "#git-tools-app",
      "#code-editor-app",
      "#file-explorer-app",
      "#game-editor-app",
      "#website-builder-app",
      "#mcel-lab-app",
      "#worker-app",
      "#wallet-app"
    ];
    for (const selector of allRoots) {
      const element = document.querySelector(selector);
      if (!element) continue;
      if (name !== "desktop" && selector === selectedRoot) {
        if (!element.style.display || element.style.display === "none") {
          element.style.display = "grid";
        }
      } else if (name !== "desktop") {
        element.style.display = "none";
      }
    }
    const desktopOverlay = document.querySelector("#desktop-overlay");
    if (desktopOverlay) desktopOverlay.style.display = name === "desktop" ? "grid" : "none";
  }

  activateApp(app);
  await sleep(80);
  if (document.fonts && document.fonts.ready) {
    try { await Promise.race([document.fonts.ready, sleep(250)]); } catch (_) {}
  }
  await sleep(80);

  const viewportRect = {x: 0, y: 0, left: 0, top: 0, right: viewport.width, bottom: viewport.height, width: viewport.width, height: viewport.height};
  const root = document.querySelector(rootSelector);
  const activationError = window.__flogActivationError || "";

  function rectObject(rect) {
    return {
      x: Number(rect.x) || 0,
      y: Number(rect.y) || 0,
      left: Number(rect.left) || 0,
      top: Number(rect.top) || 0,
      right: Number(rect.right) || 0,
      bottom: Number(rect.bottom) || 0,
      width: Number(rect.width) || 0,
      height: Number(rect.height) || 0,
      area: Math.max(0, (Number(rect.width) || 0) * (Number(rect.height) || 0))
    };
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

  function isVisible(element) {
    if (!element || !(element instanceof Element)) return false;
    const style = window.getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity || "1") === 0) return false;
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function selectorFor(element) {
    if (!element || !element.tagName) return "";
    if (element.id) return `#${CSS.escape(element.id)}`;
    const cls = Array.from(element.classList || []).slice(0, 3).map((name) => `.${CSS.escape(name)}`).join("");
    return `${element.tagName.toLowerCase()}${cls}`;
  }

  function labelFor(element) {
    return String(
      element.getAttribute("aria-label") ||
      element.getAttribute("data-mc-component-label") ||
      element.getAttribute("data-mc-widget-label") ||
      element.getAttribute("data-widget-label") ||
      element.getAttribute("title") ||
      element.textContent ||
      ""
    ).replace(/\s+/g, " ").trim().slice(0, 140);
  }

  function unionArea(rects) {
    const clean = rects.filter((rect) => rect.width > 0 && rect.height > 0);
    if (!clean.length) return 0;
    const xs = Array.from(new Set(clean.flatMap((rect) => [rect.left, rect.right]))).sort((a, b) => a - b);
    let area = 0;
    for (let i = 0; i < xs.length - 1; i += 1) {
      const x1 = xs[i];
      const x2 = xs[i + 1];
      if (x2 <= x1) continue;
      const intervals = clean
        .filter((rect) => rect.left < x2 && rect.right > x1)
        .map((rect) => [rect.top, rect.bottom])
        .sort((a, b) => a[0] - b[0]);
      let covered = 0;
      let start = null;
      let end = null;
      for (const [top, bottom] of intervals) {
        if (start === null) {
          start = top;
          end = bottom;
        } else if (top <= end) {
          end = Math.max(end, bottom);
        } else {
          covered += Math.max(0, end - start);
          start = top;
          end = bottom;
        }
      }
      if (start !== null) covered += Math.max(0, end - start);
      area += (x2 - x1) * covered;
    }
    return area;
  }

  const meaningfulSelector = [
    "button",
    "input",
    "select",
    "textarea",
    "a[href]",
    "form",
    "nav",
    "main",
    "aside",
    "section",
    "article",
    "header",
    "footer",
    "[role]",
    "[data-mc]",
    "[data-mcel-layout]",
    "[data-mc-component-id]",
    "[data-mc-widget-id]",
    "[data-widget-label]",
    "pre",
    "code",
    "output",
    "canvas",
    "iframe",
    "table",
    "ul",
    "ol",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "label",
    "[aria-label]"
  ].join(",");

  const criticalActionSelector = [
    "button",
    "a[href]",
    "input",
    "select",
    "textarea",
    "[role='button']",
    "[data-mc-component-kind='action']"
  ].join(",");

  if (!root) {
    return {
      app,
      chrome: chromeName || "current",
      viewport,
      rootSelector,
      rootFound: false,
      activationError,
      root: {area: 0, width: 0, height: 0},
      metrics: {
        viewportCoverageRatio: 0,
        directChildCoverageRatio: 0,
        meaningfulLeafCoverageRatio: 0,
        unclaimedLeafAreaRatio: 1,
        scrollOwnerCount: 0,
        clippedCriticalActions: 0,
        hiddenCriticalActions: 0,
        documentOverflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
        documentOverflowY: document.documentElement.scrollHeight > document.documentElement.clientHeight + 1
      },
      examples: {
        scrollOwners: [],
        clippedCriticalActions: [],
        hiddenCriticalActions: []
      }
    };
  }

  const rootRectRaw = rectObject(root.getBoundingClientRect());
  const clippedRoot = intersect(rootRectRaw, viewportRect);
  const rootClip = intersect(rootRectRaw, viewportRect);
  const descendants = Array.from(root.querySelectorAll(meaningfulSelector)).filter((element) => element !== root);
  const visibleMeaningful = descendants.filter(isVisible);

  function clippedRectFor(element) {
    return intersect(intersect(rectObject(element.getBoundingClientRect()), rootClip), viewportRect);
  }

  function hasVisibleMeaningfulDescendant(element) {
    return Array.from(element.children || []).some((child) => {
      if (!isVisible(child)) return false;
      if (child.matches && child.matches(meaningfulSelector)) return true;
      return Boolean(child.querySelector && child.querySelector(meaningfulSelector));
    });
  }

  const leafMeaningful = visibleMeaningful.filter((element) => {
    const tag = element.tagName.toLowerCase();
    if (["canvas", "iframe", "pre", "code", "output", "input", "select", "textarea", "button", "a", "table"].includes(tag)) return true;
    if (/^h[1-6]$/.test(tag) || tag === "label") return true;
    return !hasVisibleMeaningfulDescendant(element);
  });

  const directChildren = Array.from(root.children || []).filter(isVisible);
  const directRects = directChildren.map(clippedRectFor).filter((rect) => rect.area > 0);
  const leafRects = leafMeaningful.map(clippedRectFor).filter((rect) => rect.area > 0);
  const directArea = unionArea(directRects);
  const leafArea = unionArea(leafRects);
  const rootArea = clippedRoot.area || 0;

  const scrollOwners = visibleMeaningful.filter((element) => {
    const style = window.getComputedStyle(element);
    const overflowY = style.overflowY;
    const overflowX = style.overflowX;
    const canScrollY = ["auto", "scroll"].includes(overflowY) && element.scrollHeight > element.clientHeight + 1;
    const canScrollX = ["auto", "scroll"].includes(overflowX) && element.scrollWidth > element.clientWidth + 1;
    return canScrollY || canScrollX;
  });

  const criticalActions = Array.from(root.querySelectorAll(criticalActionSelector));
  const clippedCriticalActions = [];
  const hiddenCriticalActions = [];
  for (const element of criticalActions) {
    const rect = rectObject(element.getBoundingClientRect());
    const clippedToViewport = intersect(rect, viewportRect);
    const clippedToRoot = intersect(rect, rootClip);
    const item = {selector: selectorFor(element), label: labelFor(element), rect};
    if (!isVisible(element) || rect.area <= 0) {
      hiddenCriticalActions.push(item);
    } else if (clippedToViewport.area < rect.area * 0.92 || clippedToRoot.area < rect.area * 0.92) {
      clippedCriticalActions.push(item);
    }
  }

  const metrics = {
    viewportCoverageRatio: rootArea && viewport.width * viewport.height ? Number((rootArea / (viewport.width * viewport.height)).toFixed(4)) : 0,
    directChildCoverageRatio: rootArea ? Number((directArea / rootArea).toFixed(4)) : 0,
    meaningfulLeafCoverageRatio: rootArea ? Number((leafArea / rootArea).toFixed(4)) : 0,
    unclaimedLeafAreaRatio: rootArea ? Number((Math.max(0, rootArea - leafArea) / rootArea).toFixed(4)) : 1,
    scrollOwnerCount: scrollOwners.length,
    clippedCriticalActions: clippedCriticalActions.length,
    hiddenCriticalActions: hiddenCriticalActions.length,
    documentOverflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    documentOverflowY: document.documentElement.scrollHeight > document.documentElement.clientHeight + 1,
    bodyScrollWidth: document.documentElement.scrollWidth,
    bodyScrollHeight: document.documentElement.scrollHeight,
    viewportWidth: viewport.width,
    viewportHeight: viewport.height
  };

  return {
    app,
    chrome: chromeName || "current",
    viewport,
    rootSelector,
    rootFound: true,
    activationError,
    root: {
      raw: rootRectRaw,
      clipped: clippedRoot,
      area: rootArea,
      width: clippedRoot.width,
      height: clippedRoot.height
    },
    metrics,
    examples: {
      scrollOwners: scrollOwners.slice(0, 8).map((element) => ({
        selector: selectorFor(element),
        label: labelFor(element),
        scrollHeight: element.scrollHeight,
        clientHeight: element.clientHeight,
        scrollWidth: element.scrollWidth,
        clientWidth: element.clientWidth
      })),
      clippedCriticalActions: clippedCriticalActions.slice(0, 8),
      hiddenCriticalActions: hiddenCriticalActions.slice(0, 8),
      leafMeaningful: leafMeaningful.slice(0, 8).map((element) => ({selector: selectorFor(element), label: labelFor(element), tag: element.tagName.toLowerCase()}))
    }
  };
}
"""


def browser_dependency_error() -> str:
    return (
        "Playwright is required for rendered geometry FLOG smoke. Install the harness extra "
        "or explicit dependency, then install Chromium:\n\n"
        "    pip install -e .[harness]\n"
        "    python -m playwright install chromium\n\n"
        "The static FLOG smoke can classify source hierarchy, but it cannot measure wasted "
        "rendered space, clipped actions, or real scroll pressure."
    )


def run_browser_smoke(
    repo: Path,
    apps: list[str] | None,
    viewports: list[ViewportProfile],
    chromes: list[str],
    headed: bool = False,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - depends on optional package
        raise RuntimeError(browser_dependency_error()) from exc

    html = load_expanded_applications_html(repo)
    mountables = parse_mountables(repo)
    if apps:
        selected = set(apps)
        mountables = [item for item in mountables if item.app in selected]
        missing = selected - {item.app for item in mountables}
        if missing:
            raise ValueError(f"Unknown mountable app(s): {', '.join(sorted(missing))}")

    generated_at = datetime.now(timezone.utc).isoformat()
    report: dict[str, Any] = {
        "kind": REPORT_KIND,
        "version": REPORT_VERSION,
        "generatedAt": generated_at,
        "smokeLevel": "browser-geometry",
        "hierarchySource": "html",
        "geometryEngine": "playwright-chromium",
        "layoutFamilies": list(LAYOUT_FAMILIES),
        "viewports": [{"name": item.name, "width": item.width, "height": item.height} for item in viewports],
        "chromes": chromes,
        "mountables": [],
        "summary": {},
    }

    console_messages: list[dict[str, str]] = []
    page_errors: list[str] = []

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=not headed)
        except PlaywrightError as exc:  # pragma: no cover - depends on local browser install
            raise RuntimeError(
                "Playwright is installed, but Chromium could not be launched. "
                "Install the browser runtime with:\n\n"
                "    python -m playwright install chromium\n\n"
                f"Original Playwright error: {exc}"
            ) from exc
        try:
            for viewport in viewports:
                context = browser.new_context(
                    viewport={"width": viewport.width, "height": viewport.height},
                    device_scale_factor=1,
                )
                page = context.new_page()

                def handle_route(route: Any) -> None:
                    request = route.request
                    url = request.url
                    if url.startswith("http://flog.local/"):
                        route.fulfill(status=200, content_type="text/html", body=html)
                    elif url.startswith("data:") or url.startswith("blob:"):
                        route.continue_()
                    else:
                        route.abort()

                page.route("**/*", handle_route)
                page.on("console", lambda msg: console_messages.append({"type": msg.type, "text": msg.text[:500]}))
                page.on("pageerror", lambda exc: page_errors.append(str(exc)[:500]))

                try:
                    page.goto("http://flog.local/applications", wait_until="domcontentloaded", timeout=15_000)
                    page.wait_for_timeout(250)
                    for chrome_name in chromes:
                        for spec in mountables:
                            raw = page.evaluate(
                                BROWSER_MEASURE_JS,
                                {
                                    "app": spec.app,
                                    "rootSelector": spec.root_selector,
                                    "chromeName": chrome_name,
                                },
                            )
                            raw["viewportProfile"] = viewport.name
                            raw["appTitle"] = spec.title
                            raw["href"] = spec.href
                            raw["summary"] = spec.summary
                            raw["expectedPartial"] = spec.expected_partial
                            raw["aliasOfPartial"] = spec.alias_of_partial
                            raw["classification"] = classify_measurement(raw)
                            report["mountables"].append(raw)
                except PlaywrightError as exc:
                    raise RuntimeError(f"Playwright failed while measuring viewport {viewport.name}: {exc}") from exc
                finally:
                    context.close()
        finally:
            browser.close()

    by_app: dict[str, list[dict[str, Any]]] = {}
    for item in report["mountables"]:
        by_app.setdefault(str(item["app"]), []).append(item)

    app_summaries = {app: summarize_app(items) for app, items in sorted(by_app.items())}
    status_counts = Counter(summary["status"] for summary in app_summaries.values())
    report["summary"] = {
        "mountableCount": len(by_app),
        "measurementCount": len(report["mountables"]),
        "statusCounts": dict(status_counts),
        "apps": app_summaries,
        "consoleMessageSamples": console_messages[:20],
        "pageErrorSamples": page_errors[:20],
        "notes": [
            "Unclaimed area is measured from rendered Chromium geometry. It is a pressure signal, not an aesthetic verdict.",
            "Static source hierarchy still matters; browser geometry proves whether the chosen layout consumes space, overflows, or clips actions.",
            "External CDN/network requests are blocked so the smoke remains reproducible and local.",
        ],
    }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# FLOG Mountables Browser Geometry Smoke Report")
    lines.append("")
    lines.append(f"- Generated: `{report.get('generatedAt')}`")
    lines.append(f"- Smoke level: `{report.get('smokeLevel')}`")
    lines.append(f"- Geometry engine: `{report.get('geometryEngine')}`")
    lines.append(f"- Hierarchy source: `{report.get('hierarchySource')}`")
    summary = report.get("summary") or {}
    lines.append(f"- Mountables: `{summary.get('mountableCount', 0)}`")
    lines.append(f"- Measurements: `{summary.get('measurementCount', 0)}`")
    lines.append("")
    lines.append("## Status counts")
    lines.append("")
    for status, count in sorted((summary.get("statusCounts") or {}).items()):
        lines.append(f"- `{status}`: {count}")
    lines.append("")
    lines.append("## App summaries")
    lines.append("")
    for app, item in (summary.get("apps") or {}).items():
        lines.append(f"### `{app}`")
        lines.append("")
        lines.append(f"- Status: `{item.get('status')}`")
        lines.append(f"- Worst score: `{item.get('worstScore')}`")
        lines.append(f"- Worst unclaimed leaf area ratio: `{item.get('worstUnclaimedLeafAreaRatio')}`")
        lines.append(f"- Max scroll owners: `{item.get('maxScrollOwners')}`")
        lines.append(f"- Total clipped critical actions: `{item.get('totalClippedCriticalActions')}`")
        lines.append(f"- Total hidden critical actions: `{item.get('totalHiddenCriticalActions')}`")
        app_measurements = [m for m in report.get("mountables", []) if m.get("app") == app]
        worst = sorted(
            app_measurements,
            key=lambda m: int((m.get("classification") or {}).get("score", 0)),
        )[:3]
        if worst:
            lines.append("- Worst measurements:")
            for measurement in worst:
                cls = measurement.get("classification") or {}
                metrics = measurement.get("metrics") or {}
                warnings = cls.get("warnings") or []
                warning_text = "; ".join(warnings) if warnings else "no warnings"
                lines.append(
                    f"  - `{measurement.get('viewportProfile')}` / `{measurement.get('chrome')}`: "
                    f"score={cls.get('score')}, unclaimed={metrics.get('unclaimedLeafAreaRatio')}, "
                    f"scrollOwners={metrics.get('scrollOwnerCount')}, {warning_text}"
                )
        lines.append("")
    lines.append("## Measurement details")
    lines.append("")
    for measurement in report.get("mountables", []):
        cls = measurement.get("classification") or {}
        metrics = measurement.get("metrics") or {}
        root = measurement.get("root") or {}
        lines.append(
            f"- `{measurement.get('app')}` `{measurement.get('viewportProfile')}` `{measurement.get('chrome')}`: "
            f"status=`{cls.get('status')}`, score=`{cls.get('score')}`, "
            f"rootArea=`{root.get('area')}`, viewportCoverage=`{metrics.get('viewportCoverageRatio')}`, "
            f"unclaimedLeaf=`{metrics.get('unclaimedLeafAreaRatio')}`, "
            f"scrollOwners=`{metrics.get('scrollOwnerCount')}`, "
            f"clippedActions=`{metrics.get('clippedCriticalActions')}`, "
            f"hiddenActions=`{metrics.get('hiddenCriticalActions')}`"
        )
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    for note in summary.get("notes") or []:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def write_report(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "mountables-browser-report.json"
    md_path = output_dir / "mountables-browser-report.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a Playwright/Chromium FLOG geometry smoke over mountable apps.")
    parser.add_argument("--repo", type=Path, default=Path("."), help="Repository root. Defaults to current directory.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runtime/reports/flog"),
        help="Directory for mountables-browser-report.json and .md.",
    )
    parser.add_argument("--apps", help="Comma-separated app slugs to measure. Defaults to all launcher mountables.")
    parser.add_argument(
        "--viewports",
        help="Comma-separated viewport profiles, e.g. desktop=1440x900,narrow=390x844.",
    )
    parser.add_argument(
        "--chromes",
        help="Comma-separated chrome/theme names to set on body/html data-flog-chrome. Defaults to current.",
    )
    parser.add_argument("--headed", action="store_true", help="Run Chromium headed for visual debugging.")
    parser.add_argument("--no-write", action="store_true", help="Print JSON to stdout instead of writing report files.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    repo = args.repo.resolve()
    viewports = parse_viewports(args.viewports)
    apps = parse_csv(args.apps, []) if args.apps else None
    chromes = parse_csv(args.chromes, DEFAULT_CHROMES)
    try:
        report = run_browser_smoke(repo=repo, apps=apps, viewports=viewports, chromes=chromes, headed=args.headed)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.no_write:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        json_path, md_path = write_report(report, args.output_dir)
        print(f"Wrote {json_path}")
        print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
