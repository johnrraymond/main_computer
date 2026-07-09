"""Static FLOG smoke for mountable application surfaces.

This script inventories the HTML hierarchy of every mountable app surface in
``main_computer/web/applications.html`` and the corresponding
``main_computer/web/applications/apps/*.html`` partials. It does not mutate app
source. It produces a reproducible JSON report and a human-readable Markdown
summary that can later be used to promote stable layout defaults per app.

Run from the repository root:

    python main_computer/flog_mountables_smoke.py

Optional:

    python main_computer/flog_mountables_smoke.py --repo . --output-dir runtime/reports/flog
    python main_computer/flog_mountables_smoke.py --no-write --json
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


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

BOUNDARY_TYPES = (
    "root",
    "region",
    "command",
    "navigation",
    "content",
    "collection",
    "detail",
    "status",
    "evidence",
    "action",
    "control",
    "scroll",
    "fault",
    "authority",
    "chrome-pressure",
    "embedded-surface",
)

DATA_TRAIT_PREFIXES = ("data-mc", "data-mcel", "data-widget")


@dataclass
class HtmlElement:
    tag: str
    attrs: dict[str, str]
    depth: int
    source_index: int
    parent_index: int | None = None
    text_chunks: list[str] = field(default_factory=list)

    @property
    def element_id(self) -> str:
        return self.attrs.get("id", "")

    @property
    def classes(self) -> list[str]:
        value = self.attrs.get("class", "")
        return [part for part in re.split(r"\s+", value.strip()) if part]

    @property
    def label(self) -> str:
        for key in (
            "aria-label",
            "data-mc-component-label",
            "data-mc-widget-label",
            "data-widget-label",
            "title",
        ):
            value = self.attrs.get(key)
            if value:
                return value.strip()
        if self.tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            return " ".join(chunk.strip() for chunk in self.text_chunks if chunk.strip())
        return ""

    def selector(self) -> str:
        if self.element_id:
            return f"#{self.element_id}"
        classes = self.classes
        if classes:
            return "." + ".".join(classes[:2])
        return self.tag


class PartialParser(HTMLParser):
    """Small permissive HTML parser for static hierarchy inventory."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[HtmlElement] = []
        self._stack: list[int] = []
        self._void_tags = {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_attrs = {name.lower(): (value or "") for name, value in attrs}
        parent = self._stack[-1] if self._stack else None
        element = HtmlElement(
            tag=tag.lower(),
            attrs=normalized_attrs,
            depth=len(self._stack),
            source_index=len(self.elements),
            parent_index=parent,
        )
        self.elements.append(element)
        if element.tag not in self._void_tags:
            self._stack.append(element.source_index)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self._stack and self.elements[self._stack[-1]].tag == tag.lower():
            self._stack.pop()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for offset in range(len(self._stack) - 1, -1, -1):
            element_index = self._stack[offset]
            if self.elements[element_index].tag == tag:
                del self._stack[offset:]
                return

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text or not self._stack:
            return
        current = self.elements[self._stack[-1]]
        if current.tag in {"h1", "h2", "h3", "h4", "h5", "h6", "button", "label", "strong"}:
            current.text_chunks.append(text)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _parse_html(text: str) -> list[HtmlElement]:
    parser = PartialParser()
    parser.feed(text)
    parser.close()
    return parser.elements


def _is_data_trait(name: str) -> bool:
    return any(name == prefix or name.startswith(prefix + "-") for prefix in DATA_TRAIT_PREFIXES)


def _slug_from_partial(path: str) -> str:
    return Path(path.replace("\\", "/")).stem


def _extract_launcher_apps(applications_html: str) -> list[dict[str, str]]:
    apps: list[dict[str, str]] = []
    for match in re.finditer(
        r"<a\b(?P<attrs>[^>]*?\bdata-app=[\"'](?P<app>[^\"']+)[\"'][^>]*)>",
        applications_html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        attrs = match.group("attrs")
        title_match = re.search(r"<strong>(.*?)</strong>", applications_html[match.end() : match.end() + 500], re.DOTALL)
        summary_match = re.search(r"<span>(.*?)</span>", applications_html[match.end() : match.end() + 500], re.DOTALL)
        href_match = re.search(r"\bhref=[\"']([^\"']+)[\"']", attrs)
        glyph_match = re.search(r"\bdata-glyph=[\"']([^\"']+)[\"']", attrs)
        apps.append(
            {
                "app": match.group("app"),
                "href": href_match.group(1) if href_match else "",
                "glyph": glyph_match.group(1) if glyph_match else "",
                "title": _clean_inline_text(title_match.group(1)) if title_match else "",
                "summary": _clean_inline_text(summary_match.group(1)) if summary_match else "",
            }
        )
    return apps


def _clean_inline_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(value.split())


def _extract_included_partials(applications_html: str) -> list[str]:
    includes = []
    for match in re.finditer(r"@include\s+([^\s<>]+applications/apps/[^\s<>]+?\.html)", applications_html):
        includes.append(match.group(1).strip())
    # Some include comments are relative to applications.html and start at applications/apps/.
    if not includes:
        for match in re.finditer(r"@include\s+(applications/apps/[^\s<>]+?\.html)", applications_html):
            includes.append(match.group(1).strip())
    return includes


def _first_mount_root(elements: list[HtmlElement], fallback_slug: str) -> dict[str, str]:
    candidates: list[HtmlElement] = []
    for element in elements:
        classes = set(element.classes)
        attr_values = " ".join(element.attrs.values()).lower()
        if element.element_id.endswith("-app") or "mc-app" in classes or "app-shell" in classes:
            candidates.append(element)
        elif "app" in attr_values and element.tag in {"div", "section", "main"}:
            candidates.append(element)

    chosen = candidates[0] if candidates else (elements[0] if elements else None)
    if not chosen:
        return {"selector": "", "tag": "", "id": "", "label": "", "match": "missing"}

    expected_id = f"{fallback_slug}-app"
    match = "expected" if chosen.element_id == expected_id else "inferred"
    return {
        "selector": chosen.selector(),
        "tag": chosen.tag,
        "id": chosen.element_id,
        "label": chosen.label,
        "match": match,
    }


def _classify_element_boundaries(element: HtmlElement) -> set[str]:
    tag = element.tag
    attrs = element.attrs
    classes = {item.lower() for item in element.classes}
    attr_blob = " ".join([tag, *classes, *attrs.keys(), *attrs.values()]).lower()
    boundaries: set[str] = set()

    if element.depth == 0 or element.element_id.endswith("-app") or "mc-app" in classes or "app-shell" in classes:
        boundaries.add("root")
    if tag in {"section", "article", "aside", "header", "footer"}:
        boundaries.add("region")
    if tag == "main" or "main" in classes or "content" in classes or "workspace" in attr_blob:
        boundaries.add("content")
    if tag == "nav" or "nav" in attr_blob or "menu" in attr_blob:
        boundaries.add("navigation")
    if tag == "aside" or "sidebar" in attr_blob:
        boundaries.add("navigation")
        boundaries.add("region")
    if tag == "form" or "command" in attr_blob or "action" in attr_blob or "toolbar" in attr_blob:
        boundaries.add("command")
        boundaries.add("authority")
    if tag == "button" or attrs.get("role") == "button":
        boundaries.add("action")
        if "confirm" in attr_blob or "apply" in attr_blob or "submit" in attr_blob or "run" in attr_blob:
            boundaries.add("authority")
    if tag in {"input", "select", "textarea", "label"}:
        boundaries.add("control")
    if tag in {"ul", "ol", "table"} or "list" in attr_blob or "grid" in attr_blob or "collection" in attr_blob:
        boundaries.add("collection")
    if tag in {"pre", "code", "output"} or "result" in attr_blob or "log" in attr_blob or "evidence" in attr_blob:
        boundaries.add("evidence")
        boundaries.add("status")
    if "status" in attr_blob or "alert" in attr_blob or attrs.get("role") in {"status", "alert"}:
        boundaries.add("status")
    if "detail" in attr_blob or "inspector" in attr_blob or "properties" in attr_blob:
        boundaries.add("detail")
    if tag in {"iframe", "canvas", "video"} or "editor" in attr_blob or "terminal" in attr_blob:
        boundaries.add("embedded-surface")
    if "scroll" in attr_blob or attrs.get("data-mc-scroll-policy") or attrs.get("data-mc-scroll-owner"):
        boundaries.add("scroll")
    if "fault" in attr_blob or "safe-region" in attr_blob:
        boundaries.add("fault")
    if attrs.get("data-mc-theme") or attrs.get("data-mcel-theme") or "theme" in attr_blob or "chrome" in attr_blob:
        boundaries.add("chrome-pressure")
    if attrs.get("data-mc-slot") or attrs.get("data-mc-connects") or attrs.get("data-mcel-role"):
        boundaries.add("authority")
    return boundaries


def _boundary_inventory(elements: list[HtmlElement]) -> tuple[Counter[str], list[dict[str, Any]]]:
    counts: Counter[str] = Counter()
    examples: dict[str, list[dict[str, str]]] = defaultdict(list)

    for element in elements:
        boundaries = _classify_element_boundaries(element)
        for boundary in boundaries:
            counts[boundary] += 1
            if len(examples[boundary]) < 5:
                examples[boundary].append(
                    {
                        "selector": element.selector(),
                        "tag": element.tag,
                        "label": element.label,
                    }
                )

    sample = [{"type": boundary, "examples": examples[boundary]} for boundary in sorted(examples)]
    return counts, sample


def _trait_inventory(elements: list[HtmlElement]) -> dict[str, Any]:
    all_traits: Counter[str] = Counter()
    mc_elements = 0
    widget_elements = 0
    mcel_elements = 0
    for element in elements:
        trait_names = [name for name in element.attrs if _is_data_trait(name)]
        if trait_names:
            mc_elements += 1
        if any(name.startswith("data-widget") for name in trait_names):
            widget_elements += 1
        if any(name.startswith("data-mcel") for name in trait_names):
            mcel_elements += 1
        for name in trait_names:
            all_traits[name] += 1
    return {
        "elementsWithAnyDataTrait": mc_elements,
        "elementsWithDataWidgetTraits": widget_elements,
        "elementsWithDataMcelTraits": mcel_elements,
        "topTraits": dict(all_traits.most_common(25)),
        "totalDistinctTraits": len(all_traits),
    }


def _tag_inventory(elements: list[HtmlElement]) -> dict[str, int]:
    return dict(Counter(element.tag for element in elements).most_common())


def _heading_outline(elements: list[HtmlElement]) -> list[dict[str, Any]]:
    outline = []
    for element in elements:
        if element.tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            text = element.label
            outline.append(
                {
                    "level": int(element.tag[1]),
                    "selector": element.selector(),
                    "text": text,
                    "depth": element.depth,
                }
            )
    return outline[:50]


def _layout_scores(boundaries: Counter[str], stats: dict[str, int], traits: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    scores: dict[str, int] = {family: 0 for family in LAYOUT_FAMILIES}
    reasons: dict[str, list[str]] = {family: [] for family in LAYOUT_FAMILIES}
    rejected: list[dict[str, str]] = []

    actions = boundaries["action"]
    commands = boundaries["command"]
    collections = boundaries["collection"]
    embedded = boundaries["embedded-surface"]
    navigation = boundaries["navigation"]
    evidence = boundaries["evidence"]
    status = boundaries["status"]
    detail = boundaries["detail"]
    scroll = boundaries["scroll"]
    root = boundaries["root"]

    if stats["maxDepth"] >= 6 or stats["elementCount"] >= 80:
        scores["sectioned-sidebar"] += 3
        scores["split-pane"] += 2
        reasons["sectioned-sidebar"].append("deep or large HTML hierarchy benefits from persistent sections")
        reasons["split-pane"].append("large hierarchy can keep selected work and support surfaces visible together")
    else:
        scores["stacked-flow"] += 2
        reasons["stacked-flow"].append("small hierarchy can remain readable as a linear flow")

    if commands or actions:
        scores["sectioned-sidebar"] += 2
        scores["inspector"] += 1
        reasons["sectioned-sidebar"].append("command/action boundaries can remain persistent without owning all space")
        reasons["inspector"].append("primary work can keep related actions close")
        if actions >= 8:
            scores["bounded-drawer"] += 1
            reasons["bounded-drawer"].append("secondary actions may be grouped behind a bounded temporary surface")

    if navigation:
        scores["sectioned-sidebar"] += 3
        reasons["sectioned-sidebar"].append("navigation/aside boundaries are present")
        scores["bounded-drawer"] += 1
        reasons["bounded-drawer"].append("navigation can become temporary on narrow screens")

    if collections:
        scores["dashboard-grid"] += 2
        scores["split-pane"] += 1
        reasons["dashboard-grid"].append("collection boundaries can be compared as peer panels")
        reasons["split-pane"].append("collections pair well with detail or evidence panes")

    if detail:
        scores["inspector"] += 3
        scores["split-pane"] += 1
        reasons["inspector"].append("detail/inspector boundaries are explicit")

    if evidence or status:
        scores["split-pane"] += 2
        scores["inspector"] += 1
        reasons["split-pane"].append("status/evidence should remain visible alongside the work that produced it")
        reasons["inspector"].append("evidence can orbit the primary work without replacing it")

    if embedded:
        scores["split-pane"] += 3
        scores["inspector"] += 2
        scores["stacked-flow"] -= 1
        reasons["split-pane"].append("embedded surfaces need bounded real estate")
        reasons["inspector"].append("embedded surfaces benefit from surrounding controls/details")

    if root > 1:
        scores["hub-and-spoke"] += 1
        scores["dashboard-grid"] += 1
        reasons["hub-and-spoke"].append("multiple root-like surfaces may represent peer subsystems")

    if scroll >= 2:
        scores["sectioned-sidebar"] += 1
        scores["split-pane"] += 1
        rejected.append(
            {
                "family": "unbounded-nested-scroll",
                "reason": "multiple scroll boundaries require explicit ownership before promotion",
            }
        )

    if traits["elementsWithAnyDataTrait"] == 0:
        scores["stacked-flow"] += 1
        rejected.append(
            {
                "family": "authority-sensitive-auto-layout",
                "reason": "no MCEL/data traits were found, so authority-preserving layout should remain conservative",
            }
        )

    if not (commands or actions or navigation or collections or embedded):
        scores["stacked-flow"] += 3
        reasons["stacked-flow"].append("few strong boundaries were found")

    if commands and actions and scores["bounded-drawer"] > 0:
        rejected.append(
            {
                "family": "bounded-drawer-as-default",
                "reason": "drawers may hold secondary controls, but primary actions must not be hidden by default",
            }
        )

    if collections and commands:
        rejected.append(
            {
                "family": "flat-dashboard-as-default",
                "reason": "command authority and collection content should not be flattened without explicit evidence",
            }
        )

    if embedded and stats["elementCount"] > 30:
        rejected.append(
            {
                "family": "stacked-flow-as-desktop-default",
                "reason": "large embedded surfaces are likely to make a pure stack waste desktop real estate",
            }
        )

    if boundaries["fault"]:
        scores["fault-avoidant"] += 5
        reasons["fault-avoidant"].append("fault/safe-region boundary traits are present")

    # Central spiral is intentionally conservative: only prefer it when explicit connects imply graph relations.
    if any_connects := traits["topTraits"].get("data-mc-connects", 0):
        scores["hub-and-spoke"] += 2
        scores["central-spiral"] += 2
        reasons["hub-and-spoke"].append(f"{any_connects} explicit data-mc-connects relationship trait(s) found")
        reasons["central-spiral"].append(f"{any_connects} explicit data-mc-connects relationship trait(s) found")
    else:
        rejected.append(
            {
                "family": "central-spiral",
                "reason": "no explicit relationship traits found; spiral/tentacle layout needs graph evidence",
            }
        )

    ranked = []
    for family, score in sorted(scores.items(), key=lambda item: (-item[1], item[0])):
        if score <= 0:
            continue
        ranked.append(
            {
                "family": family,
                "score": score,
                "reasons": reasons[family][:5] or ["baseline candidate"],
            }
        )

    if not ranked:
        ranked.append({"family": "stacked-flow", "score": 1, "reasons": ["safe conservative fallback"]})

    # Deduplicate rejection entries while preserving order.
    seen = set()
    unique_rejected = []
    for item in rejected:
        key = (item["family"], item["reason"])
        if key not in seen:
            seen.add(key)
            unique_rejected.append(item)

    return ranked[:8], unique_rejected


def _recommended_defaults(candidates: list[dict[str, Any]], boundaries: Counter[str], stats: dict[str, int]) -> dict[str, str]:
    families = [candidate["family"] for candidate in candidates]

    def first_available(preferred: list[str], fallback: str = "stacked-flow") -> str:
        for family in preferred:
            if family in families:
                return family
        return families[0] if families else fallback

    desktop = first_available(["split-pane", "sectioned-sidebar", "inspector", "dashboard-grid", "stacked-flow"])
    narrow = "stacked-flow"
    if boundaries["navigation"] and "bounded-drawer" in families:
        narrow = "bounded-drawer"
    if boundaries["embedded-surface"] and "inspector" in families:
        dense = "inspector"
    else:
        dense = first_available(["split-pane", "sectioned-sidebar", "stacked-flow"])
    spacious = first_available(["stacked-flow", "inspector", "hub-and-spoke", desktop])

    if stats["elementCount"] < 12:
        desktop = "stacked-flow"
        dense = "stacked-flow"
        spacious = "stacked-flow"

    return {
        "desktop": desktop,
        "narrow": narrow,
        "denseChrome": dense,
        "spaciousChrome": spacious,
    }


def _missing_trait_recommendations(elements: list[HtmlElement], boundaries: Counter[str], root: dict[str, str]) -> list[str]:
    recommendations = []
    has_any_mcel = any(any(_is_data_trait(name) for name in element.attrs) for element in elements)
    if not has_any_mcel:
        recommendations.append("Add MCEL/data traits to the root and primary regions so FLOG can read source intent.")
    if root.get("match") != "expected":
        recommendations.append("Confirm the mount root selector; it was inferred rather than matched to the app slug.")
    if boundaries["command"] and not any("data-mc-slot" in element.attrs for element in elements):
        recommendations.append("Add data-mc-slot traits to command, content, status, and evidence regions.")
    if boundaries["collection"] and not any("data-mc-scroll-policy" in element.attrs for element in elements):
        recommendations.append("Declare scroll ownership for collection regions before enabling automatic layout promotion.")
    if boundaries["embedded-surface"] and not any("data-mc-size-policy" in element.attrs for element in elements):
        recommendations.append("Declare size policy for embedded editor/canvas/iframe/terminal surfaces.")
    if boundaries["action"] and not any("data-mc-connects" in element.attrs for element in elements):
        recommendations.append("Add data-mc-connects where actions produce status, evidence, jobs, or detail changes.")
    if not recommendations:
        recommendations.append("No immediate static trait gap found; browser geometry smoke should verify fit and scroll ownership.")
    return recommendations


def _analyze_html(app_slug: str, text: str, source_label: str) -> dict[str, Any]:
    elements = _parse_html(text)
    root = _first_mount_root(elements, app_slug)
    boundaries, boundary_examples = _boundary_inventory(elements)
    traits = _trait_inventory(elements)
    stats = {
        "elementCount": len(elements),
        "maxDepth": max((element.depth for element in elements), default=0),
        "formCount": sum(1 for element in elements if element.tag == "form"),
        "buttonCount": sum(1 for element in elements if element.tag == "button"),
        "inputCount": sum(1 for element in elements if element.tag in {"input", "select", "textarea"}),
        "headingCount": sum(1 for element in elements if element.tag in {"h1", "h2", "h3", "h4", "h5", "h6"}),
    }
    candidates, rejected = _layout_scores(boundaries, stats, traits)
    defaults = _recommended_defaults(candidates, boundaries, stats)
    return {
        "app": app_slug,
        "partial": source_label,
        "sourceRoot": root,
        "hierarchySource": "html",
        "stats": stats,
        "tags": _tag_inventory(elements),
        "traits": traits,
        "headingOutline": _heading_outline(elements),
        "boundaryInventory": dict(sorted(boundaries.items())),
        "boundaryExamples": boundary_examples,
        "stableLayouts": candidates,
        "rejectedLayouts": rejected,
        "recommendedDefaults": defaults,
        "traitRecommendations": _missing_trait_recommendations(elements, boundaries, root),
    }


def _analyze_partial(app_slug: str, partial_path: Path, repo_root: Path) -> dict[str, Any]:
    text = _read_text(partial_path)
    rel_path = partial_path.relative_to(repo_root).as_posix()
    return _analyze_html(app_slug, text, rel_path)


def build_report(repo_root: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    applications_html_path = repo_root / "main_computer" / "web" / "applications.html"
    apps_dir = repo_root / "main_computer" / "web" / "applications" / "apps"

    if not applications_html_path.exists():
        raise FileNotFoundError(f"applications shell not found: {applications_html_path}")
    if not apps_dir.exists():
        raise FileNotFoundError(f"applications apps directory not found: {apps_dir}")

    applications_html = _read_text(applications_html_path)
    launcher_apps = _extract_launcher_apps(applications_html)
    launcher_by_app = {item["app"]: item for item in launcher_apps}
    included_partials = _extract_included_partials(applications_html)
    included_by_slug = {_slug_from_partial(path): path for path in included_partials}

    physical_partials = {
        path.stem: path
        for path in sorted(apps_dir.glob("*.html"))
        if path.name != "spreadsheet-smoke.html"
    }

    alias_partials: dict[str, str] = {}
    if "game-editor" in launcher_by_app and "layout-builder" in included_by_slug:
        alias_partials["game-editor"] = "layout-builder"

    # Mountables are user-addressable apps plus any included partial that is not
    # already represented through an explicit launcher alias. Physical partials
    # that are not mounted remain shell mismatches instead of layout targets.
    alias_targets = set(alias_partials.values())
    mountable_slugs = sorted(set(launcher_by_app) | (set(included_by_slug) - alias_targets))

    mountables = []
    for slug in mountable_slugs:
        if slug == "desktop":
            app_report = _analyze_html(
                "desktop",
                applications_html,
                applications_html_path.relative_to(repo_root).as_posix(),
            )
            app_report["launcher"] = launcher_by_app.get(slug, {})
            app_report["includedInShell"] = True
            app_report["physicalPartialExists"] = False
            app_report["traitRecommendations"].insert(
                0,
                "Desktop is the Applications shell itself; treat shell and launcher-card boundaries as its source hierarchy.",
            )
            mountables.append(app_report)
            continue

        partial_slug = alias_partials.get(slug, slug)
        partial_ref = included_by_slug.get(partial_slug)
        partial_path = physical_partials.get(partial_slug)
        if partial_ref and not partial_path:
            candidate = repo_root / "main_computer" / "web" / partial_ref
            if candidate.exists():
                partial_path = candidate
        if not partial_path:
            mountables.append(
                {
                    "app": slug,
                    "partial": "",
                    "hierarchySource": "missing",
                    "sourceRoot": {"selector": "", "tag": "", "id": "", "label": "", "match": "missing"},
                    "stats": {},
                    "boundaryInventory": {},
                    "stableLayouts": [],
                    "rejectedLayouts": [{"family": "all", "reason": "no mounted app partial found"}],
                    "recommendedDefaults": {},
                    "traitRecommendations": ["Create or map an app partial before FLOG layout defaults can be generated."],
                }
            )
            continue

        app_report = _analyze_partial(slug, partial_path, repo_root)
        app_report["launcher"] = launcher_by_app.get(slug, {})
        app_report["includedInShell"] = partial_slug in included_by_slug
        app_report["physicalPartialExists"] = True
        if partial_slug != slug:
            app_report["aliasOfPartial"] = partial_slug
            app_report["traitRecommendations"].insert(
                0,
                f"Launcher app {slug} is backed by {partial_slug}.html; keep the alias explicit for reproducibility.",
            )
        mountables.append(app_report)

    shell_mismatches = []
    launcher_names = set(launcher_by_app)
    include_names = set(included_by_slug)
    physical_names = set(physical_partials)
    for app in sorted(launcher_names - include_names):
        if app == "desktop":
            shell_mismatches.append(
                {"kind": "launcher-without-partial", "app": app, "reason": "desktop is provided by the shell itself"}
            )
        elif app == "game-editor" and "layout-builder" in include_names:
            shell_mismatches.append(
                {
                    "kind": "launcher-alias",
                    "app": app,
                    "partial": "layout-builder",
                    "reason": "launcher name differs from included partial name",
                }
            )
        else:
            shell_mismatches.append(
                {"kind": "launcher-without-included-partial", "app": app, "reason": "launcher has no matching include"}
            )
    for app in sorted(include_names - launcher_names):
        if app == "layout-builder" and "game-editor" in launcher_names:
            continue
        shell_mismatches.append(
            {"kind": "included-partial-without-launcher", "app": app, "reason": "include has no matching launcher card"}
        )
    for app in sorted(physical_names - include_names - launcher_names):
        shell_mismatches.append(
            {"kind": "physical-partial-not-mounted", "app": app, "reason": "file exists but is not mounted by shell"}
        )

    family_counts: Counter[str] = Counter()
    default_counts: Counter[str] = Counter()
    missing_trait_apps: list[str] = []
    for item in mountables:
        for candidate in item.get("stableLayouts", [])[:3]:
            family_counts[candidate["family"]] += 1
        desktop_default = item.get("recommendedDefaults", {}).get("desktop")
        if desktop_default:
            default_counts[desktop_default] += 1
        if item.get("traits", {}).get("elementsWithAnyDataTrait", 0) == 0:
            missing_trait_apps.append(item["app"])

    return {
        "kind": "mcel.flog.mountables.report",
        "version": "0.1.0",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "repoRoot": str(repo_root),
        "source": {
            "applicationsHtml": applications_html_path.relative_to(repo_root).as_posix(),
            "appsDir": apps_dir.relative_to(repo_root).as_posix(),
            "hierarchySource": "html",
            "smokeLevel": "static-census",
        },
        "layoutFamilies": list(LAYOUT_FAMILIES),
        "boundaryTypes": list(BOUNDARY_TYPES),
        "summary": {
            "launcherCount": len(launcher_apps),
            "includedPartialCount": len(included_partials),
            "physicalPartialCount": len(physical_partials),
            "mountableCount": len(mountables),
            "appsMissingAnyDataTrait": missing_trait_apps,
            "topStableFamilies": dict(family_counts.most_common()),
            "desktopDefaultCounts": dict(default_counts.most_common()),
            "shellMismatchCount": len(shell_mismatches),
        },
        "shell": {
            "launcherApps": launcher_apps,
            "includedPartials": included_partials,
            "shellMismatches": shell_mismatches,
        },
        "mountables": mountables,
        "nextSteps": [
            "Add or normalize MCEL traits on mount roots and primary regions that lack them.",
            "Promote only stable defaults that survive repeated static and browser geometry smoke runs.",
            "Add browser geometry smoke to verify fit, scroll ownership, hidden actions, and fault/safe regions.",
            "Keep generated layout reports runtime artifacts; promote defaults to source only after review.",
        ],
    }


def write_report(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "mountables-report.json"
    md_path = output_dir / "mountables-report.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    summary = report["summary"]
    lines.append("# FLOG Mountables Smoke Report")
    lines.append("")
    lines.append(f"- Generated: `{report['generatedAt']}`")
    lines.append(f"- Smoke level: `{report['source']['smokeLevel']}`")
    lines.append(f"- Hierarchy source: `{report['source']['hierarchySource']}`")
    lines.append(f"- Mountables: `{summary['mountableCount']}`")
    lines.append(f"- Shell mismatches: `{summary['shellMismatchCount']}`")
    lines.append("")
    lines.append("## Layout family defaults")
    lines.append("")
    for family, count in summary["desktopDefaultCounts"].items():
        lines.append(f"- `{family}`: {count}")
    if not summary["desktopDefaultCounts"]:
        lines.append("- No defaults generated.")
    lines.append("")
    if summary["appsMissingAnyDataTrait"]:
        lines.append("## Apps missing MCEL/data traits")
        lines.append("")
        for app in summary["appsMissingAnyDataTrait"]:
            lines.append(f"- `{app}`")
        lines.append("")
    lines.append("## Shell mismatches")
    lines.append("")
    if report["shell"]["shellMismatches"]:
        for mismatch in report["shell"]["shellMismatches"]:
            detail = ", ".join(f"{key}={value!r}" for key, value in mismatch.items())
            lines.append(f"- {detail}")
    else:
        lines.append("- None.")
    lines.append("")
    lines.append("## Mountables")
    lines.append("")
    for app in report["mountables"]:
        lines.extend(_render_mountable_markdown(app))
    return "\n".join(lines).rstrip() + "\n"


def _render_mountable_markdown(app: dict[str, Any]) -> list[str]:
    lines = [f"### `{app['app']}`", ""]
    root = app.get("sourceRoot", {})
    lines.append(f"- Partial: `{app.get('partial') or 'missing'}`")
    lines.append(f"- Source root: `{root.get('selector', '') or 'missing'}` ({root.get('match', 'unknown')})")
    stats = app.get("stats", {})
    if stats:
        lines.append(
            "- HTML stats: "
            + ", ".join(
                [
                    f"elements={stats.get('elementCount', 0)}",
                    f"maxDepth={stats.get('maxDepth', 0)}",
                    f"forms={stats.get('formCount', 0)}",
                    f"buttons={stats.get('buttonCount', 0)}",
                    f"inputs={stats.get('inputCount', 0)}",
                ]
            )
        )
    boundaries = app.get("boundaryInventory", {})
    if boundaries:
        top = ", ".join(f"{name}={count}" for name, count in sorted(boundaries.items(), key=lambda item: (-item[1], item[0]))[:8])
        lines.append(f"- Boundaries: {top}")
    defaults = app.get("recommendedDefaults", {})
    if defaults:
        lines.append(
            "- Recommended defaults: "
            + ", ".join(f"{key}=`{value}`" for key, value in defaults.items())
        )
    stable = app.get("stableLayouts", [])
    if stable:
        lines.append("- Stable layout candidates:")
        for candidate in stable[:4]:
            reason = "; ".join(candidate.get("reasons", [])[:2])
            lines.append(f"  - `{candidate['family']}` score={candidate['score']}: {reason}")
    rejected = app.get("rejectedLayouts", [])
    if rejected:
        lines.append("- Rejections:")
        for item in rejected[:4]:
            lines.append(f"  - `{item['family']}`: {item['reason']}")
    recommendations = app.get("traitRecommendations", [])
    if recommendations:
        lines.append("- Reproducibility notes:")
        for item in recommendations[:4]:
            lines.append(f"  - {item}")
    lines.append("")
    return lines


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a static FLOG smoke report for application mountables.")
    parser.add_argument(
        "--repo",
        default=".",
        help="Repository root. Defaults to the current working directory.",
    )
    parser.add_argument(
        "--output-dir",
        default="runtime/reports/flog",
        help="Report output directory relative to repo unless absolute.",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Build the report but do not write report files.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the JSON report to stdout.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo).resolve()
    report = build_report(repo_root)

    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = repo_root / output_dir

    if not args.no_write:
        json_path, md_path = write_report(report, output_dir)
        print(f"Wrote {json_path}")
        print(f"Wrote {md_path}")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
