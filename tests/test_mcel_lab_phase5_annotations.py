from __future__ import annotations

from http import HTTPStatus
import json
from pathlib import Path

from main_computer.viewport_routes_mcel import ViewportMcelRoutesMixin


ROOT = Path(__file__).resolve().parents[1]
WEB_APP = ROOT / "main_computer" / "web" / "applications"
MCEL_LAB_HTML = WEB_APP / "apps" / "mcel-lab.html"
MCEL_LAB_JS = WEB_APP / "scripts" / "mcel-lab.js"
MCEL_LAB_CSS = WEB_APP / "styles" / "mcel-lab.css"
DISPATCH = ROOT / "main_computer" / "viewport_route_dispatch.py"
SERVER = ROOT / "main_computer" / "viewport_server.py"
ANNOTATIONS_ROOT = WEB_APP / "mcel" / "annotations"

REQUIRED_CHECKS = ["handlers", "tests", "docs", "sourceOwners", "replacementPath"]


class _FakeServer:
    def __init__(self, root: Path) -> None:
        self.debug_root = root
        self.signals: list[tuple[str, dict]] = []

    def signal(self, name: str, **fields: object) -> None:
        self.signals.append((name, fields))


class _FakeHandler(ViewportMcelRoutesMixin):
    def __init__(self, root: Path, body: dict) -> None:
        self.server = _FakeServer(root)
        self.body = body
        self.response: dict | None = None
        self.status = HTTPStatus.OK

    def _read_json(self) -> dict:
        return self.body

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.response = payload
        self.status = status


def _annotation(intent: str = "rework") -> dict:
    return {
        "annotationId": "document-editor:#document-title",
        "recordId": "document-editor:#document-title",
        "appId": "document-editor",
        "targetSelector": "#document-title",
        "visibleText": "Editable writing workspace",
        "mcelRole": "role:span",
        "layoutZone": "menu",
        "intent": intent,
        "purpose": "Explain the editor workspace.",
        "currentProblem": "The copy is vague.",
        "desiredBehavior": "Use a concise product description.",
        "layoutRole": "identity",
        "workflowRole": "orientation",
        "riskPolicy": "Do not remove editor identity.",
        "doNotChange": ["Document Editor title"],
        "allowedFixes": ["Rewrite the supporting copy"],
        "forbiddenFixes": ["Remove the identity region"],
        "dependencyChecks": REQUIRED_CHECKS,
        "dependencyCheckNotes": "Verify title selectors and screenshot tests.",
        "sourceHints": ["main_computer/web/applications/apps/document.html"],
        "testExpectations": ["Document Editor identity remains visible"],
        "priority": "high",
        "userReasoning": "The copy needs rework.",
    }


def test_phase_five_shell_exposes_real_annotation_editor() -> None:
    source = MCEL_LAB_HTML.read_text(encoding="utf-8")
    required_ids = [
        "mcel-blueprint-annotation-form",
        "mcel-blueprint-annotation-intent",
        "mcel-blueprint-annotation-priority",
        "mcel-blueprint-annotation-purpose",
        "mcel-blueprint-annotation-problem",
        "mcel-blueprint-annotation-desired",
        "mcel-blueprint-annotation-dependency-list",
        "mcel-blueprint-annotation-save",
        "mcel-blueprint-annotation-delete",
        "mcel-blueprint-annotation-status",
    ]
    for element_id in required_ids:
        assert f'id="{element_id}"' in source

    for intent in ["keep", "remove", "rework", "move", "hide", "merge", "investigate"]:
        assert f'<option value="{intent}">' in source

    assert 'data-mcel-element="element.refactor.element-annotation"' in source
    assert "Saving a check means it is required, not completed." in source
    assert "persistence comes next" not in source


def test_phase_five_script_loads_saves_deletes_and_restores_by_stable_selector() -> None:
    source = MCEL_LAB_JS.read_text(encoding="utf-8")
    required_functions = [
        "function mcelBlueprintShellLoadAnnotations",
        "function mcelBlueprintShellSaveAnnotation",
        "function mcelBlueprintShellDeleteAnnotation",
        "function mcelBlueprintShellRenderAnnotationEditor",
        "function mcelBlueprintShellAnnotationForRecord",
        "function mcelBlueprintShellAnnotationPayloadFromForm",
        "function mcelBlueprintShellRenderSavedAnnotationStatus",
    ]
    for function_name in required_functions:
        assert function_name in source

    assert '"/api/applications/mcel/annotations/read"' in source
    assert '"/api/applications/mcel/annotations/save"' in source
    assert '"/api/applications/mcel/annotations/delete"' in source
    assert "annotation?.targetSelector === record.selector" in source
    assert 'annotationPersistenceVersion: "app-json-v1"' in source
    assert 'new CustomEvent("mcel:annotation-saved"' in source
    assert 'new CustomEvent("mcel:annotation-deleted"' in source
    assert "removalOrReworkRequiresDependencyChecks" in source
    assert "requiredDependencyChecks" in source


def test_phase_five_routes_are_registered_on_the_viewport() -> None:
    dispatch = DISPATCH.read_text(encoding="utf-8")
    server = SERVER.read_text(encoding="utf-8")

    for action in ["read", "save", "delete"]:
        assert f'/api/applications/mcel/annotations/{action}' in dispatch
        assert f"self._handle_mcel_annotations_{action}()" in dispatch

    assert "from main_computer.viewport_routes_mcel import ViewportMcelRoutesMixin" in server
    assert "ViewportMcelRoutesMixin," in server


def test_phase_five_seed_files_are_app_specific_json_documents() -> None:
    for app_id in ["document-editor", "mcel-lab"]:
        path = ANNOTATIONS_ROOT / f"{app_id}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload == {
            "annotations": [],
            "appId": app_id,
            "format": 1,
            "updatedAt": "",
        }


def test_annotation_route_persists_updates_reads_and_deletes(tmp_path: Path) -> None:
    save = _FakeHandler(
        tmp_path,
        {"appId": "document-editor", "annotation": _annotation()},
    )
    save._handle_mcel_annotations_save()

    assert save.status == HTTPStatus.OK
    assert save.response is not None and save.response["ok"] is True
    assert save.response["annotation"]["intent"] == "rework"
    storage = (
        tmp_path
        / "main_computer"
        / "web"
        / "applications"
        / "mcel"
        / "annotations"
        / "document-editor.json"
    )
    assert storage.exists()

    updated = _annotation()
    updated["desiredBehavior"] = "Use concise, actionable copy."
    update = _FakeHandler(
        tmp_path,
        {"appId": "document-editor", "annotation": updated},
    )
    update._handle_mcel_annotations_save()
    assert update.status == HTTPStatus.OK
    assert len(update.response["annotations"]) == 1
    assert update.response["annotations"][0]["desiredBehavior"] == "Use concise, actionable copy."

    reload_handler = _FakeHandler(tmp_path, {"appId": "document-editor"})
    reload_handler._handle_mcel_annotations_read()
    assert reload_handler.status == HTTPStatus.OK
    assert reload_handler.response["annotations"][0]["targetSelector"] == "#document-title"
    assert reload_handler.response["path"].endswith(
        "main_computer/web/applications/mcel/annotations/document-editor.json"
    )

    delete = _FakeHandler(
        tmp_path,
        {
            "appId": "document-editor",
            "annotationId": "document-editor:#document-title",
        },
    )
    delete._handle_mcel_annotations_delete()
    assert delete.status == HTTPStatus.OK
    assert delete.response["deleted"] == 1
    assert delete.response["annotations"] == []


def test_remove_and_rework_cannot_omit_dependency_checks(tmp_path: Path) -> None:
    annotation = _annotation("remove")
    annotation["dependencyChecks"] = ["tests"]
    handler = _FakeHandler(
        tmp_path,
        {"appId": "document-editor", "annotation": annotation},
    )
    handler._handle_mcel_annotations_save()

    assert handler.status == HTTPStatus.BAD_REQUEST
    assert handler.response is not None and handler.response["ok"] is False
    assert "remove/rework annotations must require dependency checks" in handler.response["error"]


def test_annotation_paths_reject_traversal(tmp_path: Path) -> None:
    handler = _FakeHandler(tmp_path, {"appId": "../document-editor"})
    handler._handle_mcel_annotations_read()
    assert handler.status == HTTPStatus.BAD_REQUEST
    assert handler.response is not None and handler.response["ok"] is False


def test_phase_five_annotation_editor_styles_are_present() -> None:
    source = MCEL_LAB_CSS.read_text(encoding="utf-8")
    assert ".mcel-lab-annotation-editor" in source
    assert ".mcel-lab-annotation-checks" in source
    assert ".mcel-preview-inspect-annotated" in source
    assert '#mcel-blueprint-annotation-status[data-state="error"]' in source
