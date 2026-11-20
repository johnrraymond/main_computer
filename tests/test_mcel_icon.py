"""MCEL Icon independent-library acceptance; production consumers untouched."""
from __future__ import annotations

from copy import deepcopy
from html import unescape
import json
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from main_computer.mcel_icon import (
    ICON_FAMILIES, IconRegistryError, get_icon, icon_ids, load_registry,
    render_icon, validate_registry,
)
from main_computer.mcel_applications_shell_host import legacy_desktop_reference
from tools.mcel_icon_catalog import make_catalog

ROOT = Path(__file__).resolve().parents[1]
BROWSER = ROOT / "main_computer/web/applications/scripts/mcel-icon.js"
REGISTRY = ROOT / "main_computer/web/applications/icons/mcel-icons.v1.json"


def test_registry_is_complete_and_semantic():
    icons = load_registry()["icons"]
    assert len(icons) == 108
    assert len(icon_ids(family="application")) == 22
    assert len(icon_ids(family="frober")) == 14
    assert set(icon["family"] for icon in icons) == ICON_FAMILIES
    assert len({i["id"] for i in icons}) == len(icons)
    assert {"status.pass", "status.fail", "action.run", "action.apply", "action.enable", "action.disable", "frober.layout", "resource.folder"}.issubset(icon_ids())



def test_accessibility_and_deploy_icons_have_distinct_semantics():
    # Accessibility must not have a central stroke extending below the hips.
    access = get_icon("frober.accessibility")["shapes"]
    assert access[0] == {"tag": "circle", "attrs": {"cx": "12", "cy": "12", "r": "10"}}
    body = access[-1]["attrs"]["d"]
    assert "M12 11.8v3.2" in body
    assert body.count("M12 15l") == 2
    assert "v10" not in body

    # Deploy/undeploy are server lifecycle operations, not data transfers.
    deploy = get_icon("action.deploy")["shapes"]
    undeploy = get_icon("action.undeploy")["shapes"]
    assert deploy[:-1] == undeploy[:-1]
    assert sum(shape["tag"] == "rect" for shape in deploy) == 2
    assert deploy[-1] != undeploy[-1]  # plus vs. cross badge
    assert deploy != get_icon("action.download")["shapes"]
    assert undeploy != get_icon("action.upload")["shapes"]
    for icon_id in ("frober.accessibility", "action.deploy", "action.undeploy"):
        ET.fromstring(render_icon(icon_id, size=16))
        ET.fromstring(render_icon(icon_id, size=36))


def test_exact_original_desktop_drawing_geometry_preserved():
    old = legacy_desktop_reference(ROOT)
    assert set(old["svg"]) | {"browser"} == {id.removeprefix("application.") for id in icon_ids(family="application")}
    for app_id, drawing in old["svg"].items():
        source = ET.fromstring(f"<root>{drawing}</root>")
        new = get_icon("application." + app_id)["shapes"]
        assert [(element.tag, element.attrib) for element in source] == [
            (shape["tag"], shape["attrs"]) for shape in new
        ], app_id


def test_server_svg_is_accessible_and_not_a_control():
    svg = render_icon("frober.layout", size=36, aria_label='Layout "frob" & diagnostics')
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    assert root.attrib["width"] == "36"
    assert root.attrib["aria-label"] == 'Layout "frob" & diagnostics'
    assert root.attrib["role"] == "img"
    assert root.attrib["data-mcel-icon-id"] == "frober.layout"
    assert not any(k in root.attrib for k in ("onclick", "href", "data-status", "aria-pressed"))
    decorative = ET.fromstring(render_icon("status.pass"))
    assert decorative.attrib["aria-hidden"] == "true"
    assert "role" not in decorative.attrib


@pytest.mark.parametrize("field,change", [
    ("tag", "script"), ("attrs", {"onload": "alert(1)"}),
])
def test_reject_unsafe_shapes(field, change):
    registry = deepcopy(load_registry())
    shape = registry["icons"][0]["shapes"][0]
    shape[field] = change
    with pytest.raises(IconRegistryError):
        validate_registry(registry)


def test_duplicate_unknown_and_size_refused():
    registry = deepcopy(load_registry())
    registry["icons"].append(deepcopy(registry["icons"][0]))
    with pytest.raises(IconRegistryError):
        validate_registry(registry)
    with pytest.raises(IconRegistryError):
        render_icon("application.nonexistent")
    with pytest.raises(IconRegistryError):
        render_icon("action.run", size=0)
    with pytest.raises(IconRegistryError):
        render_icon("action.run", class_name='x" onclick="boom')
    assert get_icon("action.run")["id"] == "action.run"
    edited = get_icon("action.run")
    edited["shapes"].clear()
    assert get_icon("action.run")["shapes"]


def test_offline_catalog_has_every_icon_and_no_remote_assets():
    html = make_catalog()
    assert html.count('class="icon-card"') == len(icon_ids())
    assert 'type="application/json"' in html
    assert 'window.MCELIcon.fromDocument()' in html
    assert 'https://' not in html.replace('xmlns="http://www.w3.org/2000/svg"', '')
    assert 'data-mcel-icon-family="frober"' in html
    assert "mcelIconCatalogReady" in html


def test_node_browser_adapter_geometry_matches_python_svg():
    if not shutil.which("node"):
        pytest.skip("Node runtime unavailable")
    script = r'''
const fs = require("fs");
const api = require(process.argv[1]);
const registry = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const icons = api.create(registry);
const result = icons.list().map(icon => ({id:icon.id, svg:icons.svg(icon.id)}));
let rejected = false;
try { icons.svg("action.run", {className:'x" onclick="bad'}); } catch (_) { rejected = true; }
if (!rejected) throw Error("unsafe class accepted");
if (icons.get("frober.layout").shapes.length === 0) throw Error("missing shapes");
console.log(JSON.stringify(result));
'''
    output = subprocess.check_output(
        ["node", "-e", script, str(BROWSER), str(REGISTRY)],
        text=True, cwd=ROOT, timeout=20,
    )
    entries = json.loads(output)
    assert len(entries) == len(icon_ids())
    for item in entries:
        node_root = ET.fromstring(item["svg"])
        py_root = ET.fromstring(render_icon(item["id"]))
        # Python and JS render exactly the same semantic/shape attributes.
        assert node_root.attrib == py_root.attrib, item["id"]
        assert [(child.tag, child.attrib) for child in node_root] == [
            (child.tag, child.attrib) for child in py_root
        ], item["id"]


def test_existing_consumers_not_rewritten():
    # This first patch is additive; this test documents migration boundaries.
    source = (ROOT / "main_computer/web/applications/scripts/desktop-icons.js").read_text()
    host = (ROOT / "main_computer/mcel_applications_shell_host.py").read_text()
    assert "const desktopIconSvgByApp = {" in source
    assert 'LEGACY_GLYPHS = Path(' in host


def test_browser_catalog_filters_and_mounts_without_external_assets():
    """Static catalog doubles as the browser adapter's real DOM acceptance."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("Playwright unavailable")
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        except Exception as exc:
            pytest.skip(f"Chromium unavailable: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 840})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            # set_content avoids policies that block file:// navigation in CI.
            page.set_content(make_catalog(), wait_until="load")
            assert page.locator("body").get_attribute("data-mcel-icon-catalog-ready") == "true"
            assert page.locator(".icon-card:visible").count() == len(icon_ids())
            page.locator("#family").select_option("frober")
            assert page.locator(".icon-card:visible").count() == len(icon_ids(family="frober"))
            page.locator("#search").fill("layout")
            assert page.locator(".icon-card:visible").count() == 1
            page.locator("#size").fill("64")
            page.locator("#size").dispatch_event("input")
            assert page.locator(".icon-card:visible svg").get_attribute("width") == "64"
            assert page.locator(".icon-card:visible svg").get_attribute("aria-hidden") == "true"
            assert not errors
        finally:
            browser.close()
