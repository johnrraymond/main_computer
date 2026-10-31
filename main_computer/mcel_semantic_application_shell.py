"""Application-wide semantic shell contract for source-bound MCEL candidates.

The compiler already validates canonical IR. This additional validation covers
nested shell composition that is interpreted by McelSemanticApplicationShell.
No application-specific names, selectors, CSS, actions or backends appear here.
"""
from __future__ import annotations
import re
from typing import Any

_LOCAL = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_SEMANTIC = re.compile(r"^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._:@/-]*$")
_TYPES = {"summary", "form", "slot", "collection", "notice"}
_INPUTS = {"search", "text", "number", "checkbox"}

def _local(value: Any) -> bool:
    return isinstance(value, str) and bool(_LOCAL.fullmatch(value)) and value not in {
        "constructor", "prototype", "__proto__"
    }

def _path(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and all(_local(part) for part in value.split("."))

def validate_semantic_application_shell(ir: dict[str, Any]) -> dict[str, Any]:
    """Reject malformed, undeclared or potentially unsafe shell composition."""
    surfaces = [surface.get("semanticSurface", {}).get("applicationShell")
        for surface in ir.get("surfaces", [])
        if isinstance(surface.get("semanticSurface"), dict) and
        surface["semanticSurface"].get("applicationShell") is not None]
    if len(surfaces) != 1 or not isinstance(surfaces[0], dict):
        raise ValueError("MCEL_SHELL_EXACTLY_ONE_DECLARATION_REQUIRED")
    spec = surfaces[0]
    regions = spec.get("regions")
    if (spec.get("schema") != "mcel.semantic-application-shell.v1" or
        spec.get("layout") != "sidebar-workspace" or not _local(spec.get("id")) or
        not isinstance(regions, list) or not 2 <= len(regions) <= 24):
        raise ValueError("MCEL_SHELL_INVALID_STRUCTURE")
    ids: set[str] = set()
    slots: set[str] = set()
    fields: set[str] = set()
    actions: set[str] = set()
    intents = {item["id"]: item for item in ir.get("intents", [])}
    source_surface = next(surface["semanticSurface"] for surface in ir["surfaces"]
        if surface.get("semanticSurface", {}).get("applicationShell") is spec)
    declared_controls = {control.get("id"): control for control in source_surface.get("controls", [])}
    for region in regions:
        rid = region.get("id")
        if (not isinstance(rid, str) or not _SEMANTIC.fullmatch(rid) or rid in ids or
            region.get("zone") not in {"sidebar", "main"} or region.get("kind") not in _TYPES or
            not isinstance(region.get("title"), str) or len(region["title"]) > 100):
            raise ValueError("MCEL_SHELL_INVALID_REGION")
        ids.add(rid)
        if region["kind"] == "slot":
            slot = region.get("slot")
            if not _local(slot) or slot in slots:
                raise ValueError("MCEL_SHELL_INVALID_SLOT")
            slots.add(slot)
        if region["kind"] in {"summary", "notice"}:
            items = region.get("items", [])
            if (not isinstance(items, list) or len(items) > 24 or any(
                not _local(item.get("id")) or not isinstance(item.get("label"), str) or
                not item["label"] or not _path(item.get("path")) for item in items)):
                raise ValueError("MCEL_SHELL_INVALID_BINDING")
        if region["kind"] == "collection":
            columns = region.get("columns")
            if (not _path(region.get("path")) or not isinstance(columns, list) or
                not 1 <= len(columns) <= 12 or not all(_local(col) for col in columns) or
            ("rowKey" in region and not _local(region["rowKey"])) or
            ("selectControl" in region and
             (not _local(region["selectControl"]) or
              declared_controls.get(region["selectControl"], {}).get("role") != "capability-action"))):
                raise ValueError("MCEL_SHELL_INVALID_COLLECTION")
        if region["kind"] == "form":
            declared_fields = region.get("fields")
            if not isinstance(declared_fields, list) or len(declared_fields) > 20:
                raise ValueError("MCEL_SHELL_INVALID_FORM")
            for field in declared_fields:
                fid = field.get("id")
                if (not _local(fid) or fid in fields or field.get("type") not in _INPUTS or
                    not isinstance(field.get("label"), str) or not field["label"]):
                    raise ValueError("MCEL_SHELL_INVALID_FIELD")
                fields.add(fid)
                if field["type"] == "number":
                    lo, hi = field.get("min"), field.get("max")
                    if type(lo) not in {int, float} or type(hi) not in {int, float} or lo > hi:
                        raise ValueError("MCEL_SHELL_INVALID_NUMBER_RANGE")
        for action in region.get("actions", []):
            aid, intent_id = action.get("id"), action.get("intent")
            if (not _local(aid) or aid in actions or
                not isinstance(action.get("label"), str) or not action["label"] or
                action.get("authority") not in {"external-read", "not-admitted"}):
                raise ValueError("MCEL_SHELL_INVALID_ACTION")
            actions.add(aid)
            if action["authority"] == "not-admitted":
                if intent_id is not None:
                    raise ValueError("MCEL_SHELL_UNADMITTED_ACTION_REFERENCES_INTENT")
                continue
            control = declared_controls.get(action.get("control"))
            intent = intents.get(intent_id)
            if (not intent or not control or control.get("intent") != intent.get("sourceName") or
                intent.get("risk") != "external-read"):
                raise ValueError("MCEL_SHELL_UNDECLARED_OPERATION")
    if "notebook" not in slots and not any(r.get("kind") == "collection" and r.get("zone") == "main" for r in regions):
        raise ValueError("MCEL_SHELL_PRIMARY_REGION_REQUIRED")
    return spec
