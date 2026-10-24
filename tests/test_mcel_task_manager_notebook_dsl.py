"""Phase-1 Task Manager notebook semantics: candidate, never live authority."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from main_computer.mcel_dsl_compiler import compile_dsl_application

ROOT = Path(__file__).resolve().parents[1]
DSL = ROOT / "mcel_experiments/task-manager-notebook/application.js"
HOST = ROOT / "main_computer/web/applications/apps/task-manager.html"
EXPECTED_TABS = (
    ("server-processes", "#task-tab-processes", "#task-panel-processes"),
    ("all-processes", "#task-tab-all-processes", "#task-panel-all-processes"),
    ("connections", "#task-tab-connections", "#task-panel-connections"),
    ("hardware", "#task-tab-hardware", "#task-panel-hardware"),
)


class _TagInventory(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements: dict[str, dict[str, str]] = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            self.elements[attributes["id"]] = {"tag": tag, **attributes}


def _candidate_ir():
    report = compile_dsl_application(DSL)
    assert report.valid, report.diagnostics
    assert report.status == "pass"
    assert report.normalized_ir
    return report, report.normalized_ir


def _validate_tab_map(surface: dict, elements: dict) -> list[str]:
    errors: list[str] = []
    model = surface["semanticSurface"]["tabGroup"]
    links = model["tabs"]
    if len(links) != len({item["id"] for item in links}):
        errors.append("duplicate-tab-ids")
    if len(links) != len({item["panel"] for item in links}):
        errors.append("duplicate-panel-owners")
    if model["defaultTabId"] not in {item["id"] for item in links}:
        errors.append("missing-default-tab")
    for item in links:
        tab = elements.get(item["tab"].lstrip("#"))
        panel = elements.get(item["panel"].lstrip("#"))
        if not tab or not panel:
            errors.append(f"missing-control-or-panel:{item['id']}")
        elif (tab.get("role") != "tab" or panel.get("role") != "tabpanel" or
              tab.get("aria-controls") != item["panel"].lstrip("#") or
              panel.get("aria-labelledby") != item["tab"].lstrip("#") or
              tab.get("data-task-tab") != item["id"] or
              panel.get("data-task-panel") != item["id"]):
            errors.append(f"broken-tab-panel-relationship:{item['id']}")
    return errors


def test_dsl_compiles_without_live_application_or_package_enrollment():
    report, ir = _candidate_ir()
    second, _ = _candidate_ir()
    assert report.semantic_fingerprint == second.semantic_fingerprint
    assert report.source_binding_fingerprint == second.source_binding_fingerprint
    assert ir["application"]["appId"] == "task-manager-notebook"
    assert ir["provenance"]["frontend"]["id"] == "mcel.dsl.v1"
    assert len(ir["states"]) == 3
    assert len(ir["intents"]) == 1
    assert len(ir["surfaces"]) == 1
    assert not (ROOT / "mcel_apps/task-manager-notebook").exists()
    assert not (ROOT / "main_computer/web/applications/apps/task-manager-notebook.html").exists()
    assert report.to_dict()["authority"] == {
        "liveApplicationChanged": False,
        "contractsGenerated": False,
        "candidatePromoted": False,
        "evidenceReused": False,
    }


def test_notebook_semantics_match_observed_read_only_host_surface():
    _, ir = _candidate_ir()
    surface = ir["surfaces"][0]
    semantic = surface["semanticSurface"]
    assert surface["presentationAuthority"] == "existing-host-html"
    assert surface["route"] == "/applications/task-manager"
    assert surface["root"] == "#task-manager-app"
    assert semantic["tabGroup"]["defaultTabId"] == "server-processes"
    actual = tuple((x["id"], x["tab"], x["panel"]) for x in semantic["tabGroup"]["tabs"])
    assert actual == EXPECTED_TABS
    assert len(semantic["controls"]) == 4
    assert {x["intent"] for x in semantic["controls"]} == {"selectTab"}
    assert all(x["role"] == "select-tab" for x in semantic["controls"])
    assert all(region.get("readOnly") for region in semantic["regions"] if region["role"] == "notebook-data-panel")
    tree = _TagInventory()
    tree.feed(HOST.read_text(encoding="utf-8"))
    assert _validate_tab_map(surface, tree.elements) == []
    selected = [tree.elements[x["tab"].lstrip("#")] for x in semantic["tabGroup"]["tabs"]]
    assert sum(x.get("aria-selected") == "true" for x in selected) == 1
    assert selected[0]["aria-selected"] == "true"
    assert tree.elements["task-panel-processes"].get("hidden") is None
    assert all("hidden" in tree.elements[x["panel"].lstrip("#")] for x in semantic["tabGroup"]["tabs"][1:])


def test_compiled_dsl_does_not_fake_generated_interaction_or_external_authority():
    _, ir = _candidate_ir()
    intent = ir["intents"][0]
    assert intent["sourceName"] == "selectTab"
    assert intent["risk"] == "local-state"
    assert len(intent["input"]) == 1
    assert intent["input"][0]["sourceName"] == "selectedTabId"
    assert intent["input"][0]["schema"]["enum"] == [item[0] for item in EXPECTED_TABS]
    assert intent["writes"] == []  # renderer-local state is never a canonical write
    assert intent["viewWrites"] == [{"ref": "state:task-manager-notebook.active-tab-id"}]
    assert intent["viewTransition"]["kind"] == "transition.assign"
    assert intent["viewTransition"]["invalidInput"] == "refuse"
    assert "transition" not in intent  # canonical mutation remains distinct
    assert intent["effectRefs"] == []
    assert ir["capabilities"] == []
    assert ir["effects"] == []
    assert intent["executionBinding"] == "task-manager-notebook-runtime.select-tab"
    # Merely declaring this proposed binding is not evidence that it exists.
    assert "hostMarkup" not in ir["surfaces"][0].get("semanticSurface", {})


def test_bad_tab_panel_composition_is_caught_by_baseline_check_not_yet_compiler():
    _, ir = _candidate_ir()
    tree = _TagInventory()
    tree.feed(HOST.read_text(encoding="utf-8"))
    broken = {key: value.copy() for key, value in tree.elements.items()}
    broken["task-tab-processes"]["aria-controls"] = "task-panel-hardware"
    assert _validate_tab_map(ir["surfaces"][0], broken) == [
        "broken-tab-panel-relationship:server-processes"
    ]
    # Host regression still checks legacy DOM, separately from generic
    # compiler composition validation of new MCEL declarations.


def test_compiler_enforces_declared_tab_panel_composition(tmp_path):
    original = DSL.read_text(encoding="utf-8")
    broken_source = original.replace('panel: "#task-panel-processes"', 'panel: "#task-panel-hardware"', 1)
    assert original != broken_source
    candidate = tmp_path / "application.js"
    candidate.write_text(broken_source, encoding="utf-8")
    broken_report = compile_dsl_application(candidate)
    assert broken_report.valid is False
    assert "MCEL_IR_TAB_GROUP_COMPOSITION_INVALID" in {d["code"] for d in broken_report.diagnostics}
