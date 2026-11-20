"""MCEL applications shell with opt-in preview and canonical host modes.

The paired HTML declares the complete region hierarchy, the paired JS declares
its executable layout tree, and only the host supplies navigation chrome.
Mounted child applications continue to own all their operational capabilities.
"""
from __future__ import annotations

from html import escape
import hashlib
import json
from pathlib import Path, PureWindowsPath

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_navigation_contract import make_destination
from main_computer.mcel_icon import render_icon
from main_computer.mcel_phase2c_candidate_host import ROOT, CandidateHostError
import re

SOURCE = Path("tools/fixtures/mcel_semantic_applications_shell/application.js")
CSS = Path("main_computer/web/applications/styles/mcel-applications-shell.css")
LEGACY_BASE = Path("main_computer/web/applications/styles/base.css")
LEGACY_HTML = Path("main_computer/web/applications.html")
LEGACY_NAVIGATION = Path("main_computer/web/applications/scripts/dom-bindings/navigation.js")
LEGACY_GLYPHS = Path("main_computer/web/applications/scripts/desktop-icons.js")
SCRIPTS = (
    Path("main_computer/web/applications/scripts/mcel-semantic-layout-inference.js"),
    # Reuse the same in-browser diagnostic registry as the legacy desktop.
    Path("main_computer/web/applications/scripts/mcel-diagnostic-plugins.js"),
    Path("main_computer/web/applications/scripts/mcel-applications-shell-launcher-diagnostic.js"),
    Path("main_computer/web/applications/scripts/mcel-applications-shell.js"),
)
ROUTE = "/applications/mcel-semantic-shell"
APP_ID = "semantic-applications-shell"
REGION_CLASSES = {
    "application": "mcel-applications-shell",
    "shell": "mcel-shell-content",
    "launcher": "mcel-shell-launcher",
    "workspace": "mcel-shell-workspace",
    "application-mount": "mcel-shell-application-mount",
    "status": "mcel-shell-status",
}
PARENTS = {
    "application": None,
    "shell": "application",
    "launcher": "shell",
    "workspace": "shell",
    "application-mount": "workspace",
    "status": "application",
}

# Desktop-only spatial groups. Legacy launcher sources supply labels and an
# independent visual oracle; MCEL Icon supplies the live SVG geometry.
DESKTOP_GROUPS = (
    ("productivity", "Productivity & Communication", ("document", "spreadsheet", "onlyoffice", "email", "calculator")),
    ("development", "Development", ("code-editor", "terminal", "git-tools", "website-builder", "mcel-lab")),
    ("compute", "AI & Compute", ("chat-console", "ai-control", "worker", "conductor", "wallet")),
    ("creative", "Creative & Games", ("game-editor", "webgl", "astrometric")),
    ("system", "System & Files", ("file-explorer", "task-manager", "slh")),
)
DESKTOP_CLUSTERS_CSS = Path("main_computer/web/applications/styles/mcel-desktop-clusters.css")


# Shared source authority for app IDs, routes, and presentation policy.
# Keep the existing shell adapter for tests and launcher integrations.
from main_computer.mcel_applications_registry import (
    REGISTRY_PATH as REGISTRY,
    SEMANTIC_APPS,
    canonical_route_names as _canonical_route_names,
    read_registry as _read_shared_registry,
)


def _read_registry(repo: Path) -> dict:
    return _read_shared_registry(repo, validate_presentation=False)


def compile_applications_shell(repo: Path = ROOT) -> tuple[dict, dict]:
    report = compile_dsl_application(repo / SOURCE)
    if not report.valid or report.normalized_ir is None:
        raise CandidateHostError(f"MCEL applications shell source invalid: {report.diagnostics}")
    ir = report.normalized_ir
    if ir.get("application", {}).get("appId") != APP_ID or any(ir.get(k) for k in ("capabilities", "intents", "effects")):
        raise CandidateHostError("MCEL applications shell cannot claim child operational authority")
    surfaces = ir.get("surfaces") or []
    layout = ir.get("layouts") or []
    if len(surfaces) != 1 or len(layout) != 1:
        raise CandidateHostError("MCEL applications shell must declare one surface and layout")
    tree = layout[0].get("layoutTree") or {}
    if tree.get("schema") != "mcel.layout-tree.v1" or tree.get("root", {}).get("selector") != ".mcel-applications-shell":
        raise CandidateHostError("MCEL applications shell authored layout is missing")
    intent = surfaces[0].get("interfaceIntent") or {}
    regions = intent.get("regions") or []
    names = {str(r["id"]).split(".")[-1]: r for r in regions}
    if set(names) != set(REGION_CLASSES) or len(names) != len(regions):
        raise CandidateHostError("MCEL applications shell region contract changed")
    for name, parent in PARENTS.items():
        region = names[name]
        expected_parent = f"interface-region:{APP_ID}.{parent}" if parent else None
        if region.get("parentId") != expected_parent:
            raise CandidateHostError(f"MCEL applications shell region parent mismatch: {name}")
    registry = _read_registry(repo)
    authoritative = legacy_desktop_reference(repo)["launcher"]
    # The original launcher is the sole authority for ordering and visible
    # labels.  The static registry is only an app identity / route / mount
    # contract.  A second copy of display text is not another authority.
    indexed = {entry["id"]: entry for entry in registry["apps"]}
    source_ids = [entry["id"] for entry in authoritative]
    if len(source_ids) != len(indexed) or set(source_ids) != set(indexed):
        raise CandidateHostError(
            "MCEL launcher inventory differs from legacy source: "
            f"missing={sorted(set(source_ids) - set(indexed))}, "
            f"unexpected={sorted(set(indexed) - set(source_ids))}"
        )
    synchronized = []
    for entry in authoritative:
        registered = indexed[entry["id"]]
        if registered["route"] != entry["route"]:
            raise CandidateHostError(
                f"MCEL launcher route mismatch for {entry['id']}: "
                f"registry={registered['route']!r}, source={entry['route']!r}"
            )
        # Every app is an addressable destination, not only an in-shell click.
        # This contract is shared by the desktop and expanded Apps launcher.
        make_destination("applications." + entry["id"], entry["route"])
        synchronized.append({
            "id": entry["id"],
            "title": entry["title"],
            "description": entry["description"],
            "route": entry["route"],
            "mount": registered["mount"],
        })
    return ir, {"schema": registry["schema"], "apps": synchronized}


def _portable_repo_key(path: Path | str) -> str:
    """Use the same reference-asset key on Windows and POSIX."""
    return PureWindowsPath(str(path)).as_posix()


def legacy_desktop_reference(repo: Path = ROOT) -> dict:
    """Extract the frozen legacy icon/launcher reference for independent parity.

    This source remains an oracle, not the deployed desktop SVG authority.
    """
    nav = (repo / LEGACY_NAVIGATION).read_text(encoding="utf-8")
    html = (repo / LEGACY_HTML).read_text(encoding="utf-8")
    glyphs = (repo / LEGACY_GLYPHS).read_text(encoding="utf-8")
    css = (repo / LEGACY_BASE).read_text(encoding="utf-8")
    start = nav.index("const desktopApps = [")
    end = nav.index("\n    ];", start)
    items = [dict(app=m.group(1), glyph=m.group(2), title=m.group(3), summary=m.group(4))
             for m in re.finditer(r'\{app: "([^"]+)", glyph: "([^"]*)", title: "([^"]+)", summary: "([^"]+)"\}', nav[start:end])]
    icon_start = glyphs.index("const desktopIconSvgByApp = {")
    icon_end = glyphs.index("\n    };", icon_start)
    path_map = {}
    for m in re.finditer(r'(?m)^\s*("?[a-z][a-z0-9-]*"?): `([\s\S]*?)`,?\s*$', glyphs[icon_start:icon_end]):
        path_map[m.group(1).strip('"')] = m.group(2).strip()
    css_start = css.index("    .desktop-overlay {")
    css_end = css.index("    .stub-message {", css_start)
    icon_css = css[css_start:css_end]
    launcher_css = css[css.index('    .launcher {'):css.index('    .stage {')]
    launcher = [{'route':route,'id':slug,'title':title.strip(),'description':description.strip()}
                for route,slug,title,description in re.findall(
                    r'<a class="app-card[^"]*" href="([^"]+)" data-app="([^"]+)"[^>]*>\s*<strong>(.*?)</strong>\s*<span>(.*?)</span>\s*</a>',html,re.S)]
    if not launcher or len({x["id"] for x in launcher}) != len(launcher):
        raise CandidateHostError("Original application launcher oracle empty or contains duplicate IDs")
    if len(items) != 21 or len({x["app"] for x in items}) != 21 or len(path_map) != 21:
        raise CandidateHostError("Legacy desktop icon oracle incomplete")
    if set(path_map) != {x["app"] for x in items}:
        raise CandidateHostError("Legacy desktop source inventory and SVGs disagree")
    if ".desktop-overlay.desktop-home" not in icon_css or ".desktop-icon .desktop-glyph-svg" not in icon_css:
        raise CandidateHostError("Legacy desktop visual CSS missing")
    for item in items:
        if f'.desktop-icon[data-app="{item["app"]}"]' not in icon_css:
            raise CandidateHostError(f'Legacy desktop accent missing: {item["app"]}')
    # The oracle's expected keys are repo-relative, slash-normalized paths.
    # str(Path(...)) on Windows produces backslashes and spuriously flags all
    # four files as changed even when their bytes match the pinned sources.
    hashes = {_portable_repo_key(p): hashlib.sha256((repo / p).read_bytes()).hexdigest()
              for p in (LEGACY_BASE, LEGACY_NAVIGATION, LEGACY_GLYPHS, LEGACY_HTML)}
    return {"icons": items, "svg": path_map, "css": icon_css, "launcher": launcher, "launcher_css": launcher_css, "source_sha256": hashes}


def legacy_desktop_icon_markup(reference: dict, *, launch: bool, grouped: bool = False) -> str:
    result = []
    cards_by_id = {}
    routes = {app["id"]: app["route"] for app in reference["launcher"]}
    for item in reference["icons"]:
        slug, title, summary = (escape(item[key], quote=True) for key in ("app", "title", "summary"))
        # Live desktop icons are supplied exclusively by MCEL Icon.  Keep the
        # source-bound legacy SVGs in `reference` as an independent parity
        # oracle, not as a fallback or a second production drawing authority.
        drawing = render_icon("application." + item["app"], class_name="desktop-glyph-svg")
        summary_tail = "" if grouped and launch else f"<span>{summary}</span>"
        if launch:
            # A real anchor is required: Ctrl/Shift-click, context-menu Open in
            # New Window, copy link, and browser history must all use the same
            # canonical route. Plain clicks may still mount within the shell.
            route = escape(make_destination("applications." + item["app"],
                                            routes[item["app"]])["href"], quote=True)
            markup = (f'<a class="desktop-icon" data-app="{slug}"'
                      f' data-shell-launch="{slug}" data-mcel-destination-id="applications.{slug}"'
                      f' href="{route}">'
                      f'<span class="desktop-glyph" aria-hidden="true">{drawing}</span>'
                      f'<strong>{title}</strong>{summary_tail}</a>')
        else:
            markup = (f'<button type="button" class="desktop-icon" data-app="{slug}">'
                      f'<span class="desktop-glyph" aria-hidden="true">{drawing}</span>'
                      f'<strong>{title}</strong><span>{summary}</span></button>')
        result.append(markup)
        cards_by_id[item["app"]] = markup
    if not grouped:
        return "\n".join(result)
    arranged = [app for _, _, apps in DESKTOP_GROUPS for app in apps]
    if len(arranged) != len(set(arranged)) or set(arranged) != set(cards_by_id):
        raise CandidateHostError("Desktop groups must partition the canonical desktop icon inventory")
    clusters = []
    for slug, title, apps in DESKTOP_GROUPS:
        # Keep each related run together for wrapping, but no visible group
        # label or container chrome. The palette and proximity do the work.
        clusters.append(
            f'<div class="desktop-cluster" data-desktop-group="{slug}"'
            f' role="group" aria-label="{escape(title, quote=True)}">'
            f'<div class="desktop-cluster-apps">'
            + "\n".join(cards_by_id[app] for app in apps)
            + '</div></div>'
        )
    return '<div class="desktop-clusters">' + "\n".join(clusters) + '</div>'


def _static(region: dict) -> str:
    nodes = []
    for part in sorted(region.get("staticContent") or [], key=lambda part: part["ordinal"]):
        tag = part["tag"]
        if tag not in {"h1", "h2", "h3", "p", "small", "span"}:
            raise CandidateHostError("MCEL shell has unsupported static content")
        nodes.append(f"<{tag}>{escape(part['text'])}</{tag}>")
    return "".join(nodes)


def _markup(ir: dict, reference: dict) -> str:
    records = ir["surfaces"][0]["interfaceIntent"]["regions"]
    children: dict[str | None, list[dict]] = {}
    for region in records:
        children.setdefault(region["parentId"], []).append(region)
    for item in children.values():
        item.sort(key=lambda r: r["ordinal"])

    def render(region: dict) -> str:
        name = region["id"].split(".")[-1]
        tag = region["nativeTag"]
        if tag not in {"main", "header", "nav", "section"}:
            raise CandidateHostError("Unsupported semantic shell native tag")
        class_name = REGION_CLASSES[name]
        extra = f' id="mcel-applications-shell"' if name == "application" else f' id="mcel-shell-{name}"'
        if name == "status":
            extra = ' id="mcel-shell-status" role="status" aria-live="polite"'
        if name == "application-mount":
            extra = ' id="mcel-shell-application-mount" data-mode="overview"'
        attrs = f' class="{class_name}" data-mcel-region-id="{escape(region["id"])}"{extra}'
        if name == "launcher":
            # Desktop has no desktop app glyph; use MCEL Icon's home glyph.
            home_icon = render_icon("navigation.home", class_name="mcel-shell-app-icon-svg")
            body = (f'<template id="mcel-shell-home-icon">{home_icon}</template>'
                    + '''<div id="mcel-shell-launcher-panel" class="mcel-shell-launcher-panel" data-open="false">'''
                    + _static(region) + '''
              <div class="mcel-shell-launcher-items" id="mcel-shell-launcher-items"></div></div>''')
        elif name == "application-mount":
            body = ('''<div class="mcel-shell-overview" id="mcel-shell-overview">
             <div id="mcel-shell-desktop-overlay" class="desktop-overlay desktop-home" aria-label="Desktop application launcher">
             '''
             + legacy_desktop_icon_markup(reference, launch=True, grouped=True) + "</div></div>"
             + '''<iframe id="mcel-shell-frame" class="mcel-shell-frame" title="MCEL application" referrerpolicy="same-origin"></iframe>''')
        elif name == "status":
            body = "Compiling semantic shell..."
        else:
            body = _static(region) + "".join(render(c) for c in children.get(region["id"], []))
        return f"<{tag}{attrs}>{body}</{tag}>"

    roots = children.get(None, [])
    if len(roots) != 1 or roots[0]["id"] != f"interface-region:{APP_ID}.application":
        raise CandidateHostError("MCEL shell must have one semantic root")
    return render(roots[0])


def _safe_asset(repo: Path, path: Path, *, tag: str) -> str:
    asset = (repo / path).read_text(encoding="utf-8")
    if f"</{tag}" in asset.lower():
        raise CandidateHostError(f"MCEL shell unsafe inline {tag} asset")
    return asset


def render_applications_shell_candidate(
    repo: Path = ROOT, *, route_preview: bool = False, canonical_promoted: bool = False
) -> str:
    ir, registry = compile_applications_shell(repo)
    reference = legacy_desktop_reference(repo)
    markup = _markup(ir, reference)
    payload = json.dumps(ir, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    apps_json = json.dumps(registry, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    css = (_safe_asset(repo, CSS, tag="style") + "\n" + reference["css"] + "\n"
           + _safe_asset(repo, DESKTOP_CLUSTERS_CSS, tag="style"))
    if "</style" in css.lower():
        raise CandidateHostError("Legacy desktop icon CSS contains unsafe inline content")
    scripts = "\n".join("<script>\n" + _safe_asset(repo, path, tag="script") + "\n</script>" for path in SCRIPTS)
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MCEL Shell · Applications</title>
<style>{css}</style></head><body class="mcel-applications-candidate" data-mcel-shell-route-preview="{str(route_preview).lower()}" data-mcel-shell-canonical-promoted="{str(canonical_promoted).lower()}">
{markup}
<script id="mcel-applications-shell-ir" type="application/json">{payload}</script>
<script id="mcel-applications-shell-registry" type="application/json">{apps_json}</script>
{scripts}
</body></html>'''
