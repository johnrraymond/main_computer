from __future__ import annotations

from datetime import datetime, timezone
from http import HTTPStatus
import json
from pathlib import Path
import re
from typing import Any


_APP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_ALLOWED_INTENTS = {"keep", "remove", "rework", "move", "hide", "merge", "investigate"}
_REQUIRED_REMOVE_REWORK_CHECKS = {"handlers", "tests", "docs", "sourceOwners", "replacementPath"}
_MAX_ANNOTATIONS = 2000
_MAX_TEXT = 20000
_MAX_LIST_ITEMS = 200


class ViewportMcelRoutesMixin:
    def _mcel_annotations_root(self) -> Path:
        root = (
            self.server.debug_root
            / "main_computer"
            / "web"
            / "applications"
            / "mcel"
            / "annotations"
        ).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _mcel_annotation_path(self, app_id: object) -> tuple[Path, str]:
        normalized = str(app_id or "").strip().lower()
        if not _APP_ID_RE.fullmatch(normalized):
            raise ValueError("appId must be a lowercase MCEL application identifier.")
        root = self._mcel_annotations_root()
        target = (root / f"{normalized}.json").resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError("Annotation path must stay inside MCEL annotation storage.") from exc
        return target, normalized

    def _mcel_read_annotation_document(self, path: Path, app_id: str) -> dict[str, Any]:
        if not path.exists():
            return {"format": 1, "appId": app_id, "updatedAt": "", "annotations": []}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("MCEL annotation storage is not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("MCEL annotation storage must contain an object.")
        annotations = payload.get("annotations")
        if not isinstance(annotations, list):
            annotations = []
        return {
            "format": 1,
            "appId": app_id,
            "updatedAt": str(payload.get("updatedAt") or ""),
            "annotations": [item for item in annotations if isinstance(item, dict)],
        }

    def _mcel_annotation_text(self, value: object, field: str, *, required: bool = False) -> str:
        text = str(value or "").strip()
        if required and not text:
            raise ValueError(f"{field} is required.")
        if len(text) > _MAX_TEXT:
            raise ValueError(f"{field} is too long.")
        return text

    def _mcel_annotation_list(self, value: object, field: str) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError(f"{field} must be an array.")
        if len(value) > _MAX_LIST_ITEMS:
            raise ValueError(f"{field} contains too many entries.")
        result: list[str] = []
        for item in value:
            text = self._mcel_annotation_text(item, field)
            if text and text not in result:
                result.append(text)
        return result

    def _mcel_normalize_annotation(self, app_id: str, value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("annotation must be an object.")
        intent = self._mcel_annotation_text(value.get("intent"), "intent", required=True).lower()
        if intent not in _ALLOWED_INTENTS:
            raise ValueError("intent is not supported.")
        target_selector = self._mcel_annotation_text(
            value.get("targetSelector") or value.get("selector"),
            "targetSelector",
            required=True,
        )
        dependency_checks = self._mcel_annotation_list(
            value.get("dependencyChecks") or value.get("requiredDependencyChecks"),
            "dependencyChecks",
        )
        if intent in {"remove", "rework"}:
            missing = sorted(_REQUIRED_REMOVE_REWORK_CHECKS.difference(dependency_checks))
            if missing:
                raise ValueError(
                    "remove/rework annotations must require dependency checks: "
                    + ", ".join(missing)
                )
        annotation_id = self._mcel_annotation_text(
            value.get("annotationId") or value.get("recordId") or f"{app_id}:{target_selector}",
            "annotationId",
            required=True,
        )
        now = datetime.now(timezone.utc).isoformat()
        return {
            "annotationId": annotation_id,
            "appId": app_id,
            "recordId": self._mcel_annotation_text(value.get("recordId"), "recordId"),
            "targetSelector": target_selector,
            "visibleText": self._mcel_annotation_text(value.get("visibleText"), "visibleText"),
            "mcelRole": self._mcel_annotation_text(value.get("mcelRole"), "mcelRole"),
            "layoutZone": self._mcel_annotation_text(value.get("layoutZone"), "layoutZone"),
            "parentRegion": value.get("parentRegion") if isinstance(value.get("parentRegion"), dict) else None,
            "intent": intent,
            "purpose": self._mcel_annotation_text(value.get("purpose"), "purpose"),
            "currentProblem": self._mcel_annotation_text(value.get("currentProblem"), "currentProblem"),
            "desiredBehavior": self._mcel_annotation_text(value.get("desiredBehavior"), "desiredBehavior"),
            "layoutRole": self._mcel_annotation_text(value.get("layoutRole"), "layoutRole"),
            "workflowRole": self._mcel_annotation_text(value.get("workflowRole"), "workflowRole"),
            "riskPolicy": self._mcel_annotation_text(value.get("riskPolicy"), "riskPolicy"),
            "doNotChange": self._mcel_annotation_list(value.get("doNotChange"), "doNotChange"),
            "allowedFixes": self._mcel_annotation_list(
                value.get("allowedFixes") or value.get("allowedOutcomes"),
                "allowedFixes",
            ),
            "forbiddenFixes": self._mcel_annotation_list(
                value.get("forbiddenFixes") or value.get("forbiddenOutcomes"),
                "forbiddenFixes",
            ),
            "dependencyChecks": dependency_checks,
            "dependencyCheckNotes": self._mcel_annotation_text(
                value.get("dependencyCheckNotes"), "dependencyCheckNotes"
            ),
            "sourceHints": self._mcel_annotation_list(value.get("sourceHints"), "sourceHints"),
            "testExpectations": self._mcel_annotation_list(
                value.get("testExpectations"), "testExpectations"
            ),
            "priority": self._mcel_annotation_text(value.get("priority") or "normal", "priority"),
            "userReasoning": self._mcel_annotation_text(
                value.get("userReasoning")
                or value.get("currentProblem")
                or value.get("desiredBehavior"),
                "userReasoning",
            ),
            "createdAt": self._mcel_annotation_text(value.get("createdAt"), "createdAt") or now,
            "updatedAt": now,
        }

    def _mcel_write_annotation_document(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    def _handle_mcel_annotations_read(self) -> None:
        try:
            body = self._read_json()
            path, app_id = self._mcel_annotation_path(body.get("appId"))
            payload = self._mcel_read_annotation_document(path, app_id)
            self.server.signal(
                "api-mcel-annotations-read",
                app_id=app_id,
                count=len(payload["annotations"]),
            )
            self._send_json(
                {
                    "ok": True,
                    "path": path.relative_to(self.server.debug_root).as_posix(),
                    **payload,
                }
            )
        except Exception as exc:
            self.server.signal("api-mcel-annotations-error", route="read", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_mcel_annotations_save(self) -> None:
        try:
            body = self._read_json()
            path, app_id = self._mcel_annotation_path(body.get("appId"))
            annotation = self._mcel_normalize_annotation(app_id, body.get("annotation"))
            payload = self._mcel_read_annotation_document(path, app_id)
            annotations = payload["annotations"]
            existing_index = next(
                (
                    index
                    for index, item in enumerate(annotations)
                    if item.get("annotationId") == annotation["annotationId"]
                    or item.get("targetSelector") == annotation["targetSelector"]
                ),
                -1,
            )
            if existing_index >= 0:
                previous = annotations[existing_index]
                annotation["createdAt"] = str(previous.get("createdAt") or annotation["createdAt"])
                annotations[existing_index] = annotation
            else:
                if len(annotations) >= _MAX_ANNOTATIONS:
                    raise ValueError("MCEL annotation storage is full.")
                annotations.append(annotation)
            payload["updatedAt"] = annotation["updatedAt"]
            payload["annotations"] = annotations
            self._mcel_write_annotation_document(path, payload)
            self.server.signal(
                "api-mcel-annotations-save",
                app_id=app_id,
                annotation_id=annotation["annotationId"],
                count=len(annotations),
            )
            self._send_json(
                {
                    "ok": True,
                    "path": path.relative_to(self.server.debug_root).as_posix(),
                    "annotation": annotation,
                    **payload,
                }
            )
        except Exception as exc:
            self.server.signal("api-mcel-annotations-error", route="save", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_mcel_annotations_delete(self) -> None:
        try:
            body = self._read_json()
            path, app_id = self._mcel_annotation_path(body.get("appId"))
            annotation_id = self._mcel_annotation_text(
                body.get("annotationId"), "annotationId"
            )
            target_selector = self._mcel_annotation_text(
                body.get("targetSelector"), "targetSelector"
            )
            if not annotation_id and not target_selector:
                raise ValueError("annotationId or targetSelector is required.")
            payload = self._mcel_read_annotation_document(path, app_id)
            before = len(payload["annotations"])
            payload["annotations"] = [
                item
                for item in payload["annotations"]
                if not (
                    (annotation_id and item.get("annotationId") == annotation_id)
                    or (target_selector and item.get("targetSelector") == target_selector)
                )
            ]
            deleted = before - len(payload["annotations"])
            payload["updatedAt"] = datetime.now(timezone.utc).isoformat()
            self._mcel_write_annotation_document(path, payload)
            self.server.signal(
                "api-mcel-annotations-delete",
                app_id=app_id,
                deleted=deleted,
                count=len(payload["annotations"]),
            )
            self._send_json(
                {
                    "ok": True,
                    "path": path.relative_to(self.server.debug_root).as_posix(),
                    "deleted": deleted,
                    **payload,
                }
            )
        except Exception as exc:
            self.server.signal("api-mcel-annotations-error", route="delete", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
