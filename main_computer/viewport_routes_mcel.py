from __future__ import annotations

from datetime import datetime, timezone
from http import HTTPStatus
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit


_APP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_ALLOWED_INTENTS = {"keep", "remove", "rework", "move", "hide", "merge", "investigate"}
_REQUIRED_REMOVE_REWORK_CHECKS = {"handlers", "tests", "docs", "sourceOwners", "replacementPath"}
_MAX_ANNOTATIONS = 2000
_MAX_TEXT = 20000
_MAX_LIST_ITEMS = 200
_MAX_DIAGNOSTIC_EVENTS_RETURNED = 500
_MAX_DIAGNOSTIC_ISSUES = 80
_MAX_DIAGNOSTIC_TEXT = 4000
_MAX_DIAGNOSTIC_EVENT_BYTES = 200_000


class ViewportMcelRoutesMixin:

    def _mcel_diagnostics_root(self) -> Path:
        root = (self.server.debug_root / "runtime" / "mcel_diagnostics").resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _mcel_diagnostics_events_path(self) -> Path:
        root = self._mcel_diagnostics_root()
        target = (root / "events.jsonl").resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError("Diagnostic event path must stay inside MCEL diagnostic storage.") from exc
        return target

    def _mcel_diagnostic_text(self, value: object, field: str, *, max_len: int = _MAX_DIAGNOSTIC_TEXT) -> str:
        text = str(value or "").strip()
        if len(text) > max_len:
            return text[: max(0, max_len - 1)] + "…"
        return text

    def _mcel_diagnostic_app_id(self, value: object) -> str:
        normalized = self._mcel_diagnostic_text(value, "appId", max_len=96).lower()
        if not _APP_ID_RE.fullmatch(normalized):
            raise ValueError("appId must be a lowercase MCEL application identifier.")
        return normalized

    def _mcel_diagnostic_counts(self, value: object) -> dict[str, int]:
        source = value if isinstance(value, dict) else {}
        result: dict[str, int] = {}
        for key in ("errors", "warnings", "ok"):
            try:
                result[key] = max(0, int(source.get(key, 0)))
            except (TypeError, ValueError):
                result[key] = 0
        return result

    def _mcel_diagnostic_issue(self, value: object) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        issue = {
            "severity": self._mcel_diagnostic_text(value.get("severity"), "severity", max_len=80),
            "normalizedSeverity": self._mcel_diagnostic_text(value.get("normalizedSeverity"), "normalizedSeverity", max_len=80),
            "code": self._mcel_diagnostic_text(value.get("code"), "code", max_len=160),
            "finding": self._mcel_diagnostic_text(value.get("finding"), "finding"),
            "recommendedNextProbe": self._mcel_diagnostic_text(value.get("recommendedNextProbe"), "recommendedNextProbe", max_len=240),
        }
        return {key: text for key, text in issue.items() if text}

    def _mcel_diagnostic_issue_list(self, value: object) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        issues: list[dict[str, str]] = []
        for item in value[:_MAX_DIAGNOSTIC_ISSUES]:
            issue = self._mcel_diagnostic_issue(item)
            if issue:
                issues.append(issue)
        return issues

    def _mcel_diagnostic_primary_surface(self, value: object) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        result: dict[str, Any] = {
            "expected": self._mcel_diagnostic_text(value.get("expected"), "expected", max_len=240),
            "usable": bool(value.get("usable")),
            "exactlyOneAuthoritativeSurface": bool(value.get("exactlyOneAuthoritativeSurface")),
        }
        for name in ("host", "editor"):
            surface = value.get(name)
            if isinstance(surface, dict):
                result[name] = {
                    "exists": bool(surface.get("exists")),
                    "visible": bool(surface.get("visible")),
                    "selector": self._mcel_diagnostic_text(surface.get("selector"), f"{name}.selector", max_len=360),
                    "width": int(float(surface.get("width") or 0)),
                    "height": int(float(surface.get("height") or 0)),
                }
        return result

    def _mcel_diagnostic_measurements(self, value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, Any] = {}
        for key in ("visualIntegrityViolations", "layoutCollisions", "overlays", "forbiddenRegions"):
            if key in value:
                candidate = value.get(key)
                result[key] = candidate if isinstance(candidate, (list, dict)) else []
        return result

    def _mcel_normalize_diagnostic_event(self, body: object) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise ValueError("diagnostic event must be a JSON object.")
        source = body.get("event") if isinstance(body.get("event"), dict) else body
        app_id = self._mcel_diagnostic_app_id(source.get("appId"))
        received_at = datetime.now(timezone.utc).isoformat()
        timestamp = self._mcel_diagnostic_text(source.get("timestamp") or received_at, "timestamp", max_len=80)
        counts = self._mcel_diagnostic_counts(source.get("counts"))
        current = source.get("current") if isinstance(source.get("current"), dict) else {}
        issues = self._mcel_diagnostic_issue_list(
            current.get("issues") if isinstance(current, dict) and "issues" in current else source.get("issues")
        )
        history = source.get("history") if isinstance(source.get("history"), dict) else {}
        buckets = source.get("buckets") if isinstance(source.get("buckets"), dict) else {}
        primary_surface = self._mcel_diagnostic_primary_surface(source.get("primarySurface"))
        measurements = self._mcel_diagnostic_measurements(source.get("measurements"))

        event_seed = json.dumps(
            {"appId": app_id, "timestamp": timestamp, "route": source.get("route"), "counts": counts, "issues": issues},
            sort_keys=True,
            ensure_ascii=False,
        )
        event_id = hashlib.sha256(event_seed.encode("utf-8")).hexdigest()[:24]

        event: dict[str, Any] = {
            "schema": "mcel-diagnostic-event-v1",
            "eventId": event_id,
            "receivedAt": received_at,
            "sourceSchema": self._mcel_diagnostic_text(source.get("schema"), "schema", max_len=120),
            "widgetVersion": self._mcel_diagnostic_text(source.get("widgetVersion"), "widgetVersion", max_len=160),
            "appId": app_id,
            "contractId": self._mcel_diagnostic_text(source.get("contractId"), "contractId", max_len=240),
            "route": self._mcel_diagnostic_text(source.get("route"), "route", max_len=1000),
            "timestamp": timestamp,
            "verdict": self._mcel_diagnostic_text(source.get("verdict"), "verdict", max_len=80),
            "rawVerdict": self._mcel_diagnostic_text(source.get("rawVerdict"), "rawVerdict", max_len=80),
            "counts": counts,
            "issues": issues,
        }
        if isinstance(current, dict):
            event["current"] = {"counts": self._mcel_diagnostic_counts(current.get("counts")), "issues": issues}
        if isinstance(history, dict):
            event["history"] = {
                "counts": history.get("counts") if isinstance(history.get("counts"), dict) else {},
                "pageStartedAt": self._mcel_diagnostic_text(history.get("pageStartedAt"), "pageStartedAt", max_len=80),
                "lastUpdatedAt": self._mcel_diagnostic_text(history.get("lastUpdatedAt"), "lastUpdatedAt", max_len=80),
            }
        if buckets:
            event["buckets"] = {
                key: self._mcel_diagnostic_issue_list(value)
                for key, value in buckets.items()
                if isinstance(key, str) and isinstance(value, list)
            }
        if primary_surface is not None:
            event["primarySurface"] = primary_surface
        if measurements:
            event["measurements"] = measurements

        encoded = json.dumps(event, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(encoded) > _MAX_DIAGNOSTIC_EVENT_BYTES:
            raise ValueError("diagnostic event is too large.")
        return event

    def _mcel_append_diagnostic_event(self, event: dict[str, Any]) -> Path:
        path = self._mcel_diagnostics_events_path()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        return path

    def _mcel_read_diagnostic_events(self, *, app_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        path = self._mcel_diagnostics_events_path()
        if not path.exists():
            return []
        safe_limit = max(1, min(_MAX_DIAGNOSTIC_EVENTS_RETURNED, int(limit or 100)))
        normalized_app_id = self._mcel_diagnostic_app_id(app_id) if app_id else ""
        events: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if normalized_app_id and event.get("appId") != normalized_app_id:
                continue
            events.append(event)
        return events[-safe_limit:]

    def _mcel_diagnostics_summary(self) -> dict[str, Any]:
        events = self._mcel_read_diagnostic_events(limit=_MAX_DIAGNOSTIC_EVENTS_RETURNED)
        latest_by_app: dict[str, dict[str, Any]] = {}
        totals = {"events": len(events), "errors": 0, "warnings": 0, "ok": 0}
        for event in events:
            app_id = str(event.get("appId") or "")
            if app_id:
                latest_by_app[app_id] = event
            counts = event.get("counts") if isinstance(event.get("counts"), dict) else {}
            totals["errors"] += int(counts.get("errors") or 0)
            totals["warnings"] += int(counts.get("warnings") or 0)
            totals["ok"] += int(counts.get("ok") or 0)
        return {
            "schema": "mcel-diagnostics-summary-v1",
            "ok": True,
            "totals": totals,
            "apps": {
                app_id: {
                    "appId": app_id,
                    "timestamp": event.get("timestamp") or "",
                    "receivedAt": event.get("receivedAt") or "",
                    "verdict": event.get("verdict") or "",
                    "counts": event.get("counts") or {},
                    "issueCount": len(event.get("issues") if isinstance(event.get("issues"), list) else []),
                }
                for app_id, event in sorted(latest_by_app.items())
            },
        }

    def _handle_mcel_diagnostics_event_post(self) -> None:
        try:
            body = self._read_json()
            event = self._mcel_normalize_diagnostic_event(body)
            path = self._mcel_append_diagnostic_event(event)
            self.server.signal(
                "api-mcel-diagnostics-event",
                app_id=event.get("appId"),
                verdict=event.get("verdict"),
                errors=event.get("counts", {}).get("errors"),
                warnings=event.get("counts", {}).get("warnings"),
            )
            self._send_json({
                "ok": True,
                "schema": "mcel-diagnostic-event-ack-v1",
                "eventId": event["eventId"],
                "path": path.relative_to(self.server.debug_root).as_posix(),
                "event": event,
            })
        except Exception as exc:
            self.server.signal("api-mcel-diagnostics-event-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_mcel_diagnostics_events_get(self) -> None:
        try:
            query = parse_qs(urlsplit(self.path).query)
            app_id = str(query.get("appId", [""])[0] or "").strip()
            try:
                limit = int(query.get("limit", ["100"])[0])
            except (TypeError, ValueError):
                limit = 100
            events = self._mcel_read_diagnostic_events(app_id=app_id, limit=limit)
            self.server.signal("api-mcel-diagnostics-events", app_id=app_id, count=len(events))
            self._send_json({"ok": True, "schema": "mcel-diagnostic-events-v1", "appId": app_id, "count": len(events), "events": events})
        except Exception as exc:
            self.server.signal("api-mcel-diagnostics-events-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_mcel_diagnostics_summary_get(self) -> None:
        try:
            summary = self._mcel_diagnostics_summary()
            self.server.signal("api-mcel-diagnostics-summary", apps=len(summary.get("apps", {})))
            self._send_json(summary)
        except Exception as exc:
            self.server.signal("api-mcel-diagnostics-summary-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)


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
