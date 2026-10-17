"""Deterministic host-bound projection for the Code Editor DSL authority.

The profile projects canonical Code Editor Application IR into browser contract
modules without copying the existing Code Editor HTML, CSS, or runtime shell.
The stable host route, root selector, and canonical runtime facade remain the
durable boundary while the DSL becomes the package authority.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from main_computer.mcel_projection_profiles.host_bound_generic_v1 import project_host_bound_ir


PROFILE_ID = "mcel.code-editor.host-bound-projection.v1"
APP_ID = "code-editor"
NORMALIZED_DEFINITION = "generated/mcel.application.normalized.json"
RUNTIME_FACADE = "MainComputerCodeEditorRuntime"
HOST_ROUTE = "/applications/code-editor"
ROOT_SELECTOR = "#code-editor-app"
STATIC_SURFACE_ID = "code-editor.surface.monaco-selected-file-editor"
AUTHORING_CONTRACT_ID = "code-editor.contract.authoring.monaco-golden-path"
SURFACE_BUNDLE_SCHEMA = "mcel.application-surface-bundle.v1"
EXPECTED_INTENTS = {
    "applyReviewedPatch": "applyReviewedPatch",
    "closeFile": "closeFile",
    "discardDraft": "discardDraft",
    "editDraft": "editDraft",
    "inspectWorkspace": "inspectWorkspace",
    "openFile": "openFile",
    "previewAiderPlan": "previewAiderPlan",
    "saveFile": "saveFile",
}
EXPECTED_CAPABILITIES = {
    "capability:code-editor.aider-planning",
    "capability:code-editor.reviewed-patch-application",
    "capability:code-editor.source-workspace",
}


class CodeEditorProjectionProfileError(ValueError):
    """Raised when Code Editor IR cannot be projected without guessing."""


@dataclass(frozen=True)
class CodeEditorProjection:
    profile_id: str
    files: Mapping[str, bytes]
    file_hashes: Mapping[str, str]


def project_code_editor_ir(application_ir: Mapping[str, Any]) -> CodeEditorProjection:
    """Project one validated Code Editor IR document into deterministic files."""

    app = _mapping(application_ir.get("application"))
    if app.get("appId") != APP_ID:
        raise CodeEditorProjectionProfileError(
            f"Code Editor projection requires appId {APP_ID!r}; observed {app.get('appId')!r}."
        )

    intents = {
        str(item.get("sourceName")): item
        for item in application_ir.get("intents") or []
        if isinstance(item, Mapping) and isinstance(item.get("sourceName"), str)
    }
    observed_intents = set(intents)
    if observed_intents != set(EXPECTED_INTENTS):
        raise CodeEditorProjectionProfileError(
            "Code Editor projection requires the stable DSL-native runtime intents; "
            f"missing={sorted(set(EXPECTED_INTENTS) - observed_intents)!r}, "
            f"extra={sorted(observed_intents - set(EXPECTED_INTENTS))!r}."
        )
    for source_name, expected_method in sorted(EXPECTED_INTENTS.items()):
        intent = intents[source_name]
        if intent.get("runtimeMethod") != expected_method:
            raise CodeEditorProjectionProfileError(
                f"Intent {source_name!r} must bind runtime method {expected_method!r}."
            )
        if intent.get("writes"):
            raise CodeEditorProjectionProfileError(
                f"Code Editor host-bound intent {source_name!r} may not claim canonical writes."
            )
        if intent.get("transition"):
            raise CodeEditorProjectionProfileError(
                f"Code Editor host-bound intent {source_name!r} may not embed a canonical transition."
            )

    capability_ids = {
        str(item.get("id"))
        for item in application_ir.get("capabilities") or []
        if isinstance(item, Mapping)
    }
    if capability_ids != EXPECTED_CAPABILITIES:
        raise CodeEditorProjectionProfileError(
            "Code Editor capability set drifted from the source-workspace, Aider planning, and reviewed-patch lanes."
        )

    surfaces = [
        item for item in application_ir.get("surfaces") or []
        if isinstance(item, Mapping) and item.get("id") == "surface:code-editor.workspace"
    ]
    if len(surfaces) != 1:
        raise CodeEditorProjectionProfileError("Code Editor workspace surface is missing or duplicated.")
    surface = surfaces[0]
    if surface.get("root") != ROOT_SELECTOR or surface.get("route") != HOST_ROUTE:
        raise CodeEditorProjectionProfileError(
            "Code Editor projection must remain bound to /applications/code-editor and #code-editor-app."
        )
    if surface.get("presentationAuthority") != "existing-host-html":
        raise CodeEditorProjectionProfileError(
            "Code Editor HTML must remain the declared presentation authority for this host-bound projection."
        )
    if surface.get("runtimeFacade") != RUNTIME_FACADE:
        raise CodeEditorProjectionProfileError(
            f"Code Editor projection requires canonical runtime facade {RUNTIME_FACADE!r}."
        )

    projected = project_host_bound_ir(application_ir, profile_id=PROFILE_ID, export_prefix="CodeEditor", contract_id=AUTHORING_CONTRACT_ID)
    return CodeEditorProjection(projected.profile_id, projected.files, projected.file_hashes)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}

def _plain_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CodeEditorProjectionProfileError(f"{label} must be an object.")
    return json.loads(json.dumps(value, sort_keys=True))
