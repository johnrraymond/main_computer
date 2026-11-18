"""MCEL Icon: a semantic, renderer-independent icon vocabulary.

The JSON registry is the *only* icon geometry authority.  This Python adapter
and the browser adapter consume the same records.  Neither owns launcher
navigation, button behavior, theme selection, or frober execution status.
"""
from __future__ import annotations

from functools import lru_cache
from html import escape
import json
from pathlib import Path
import re

SCHEMA = "mcel.icon-registry.v1"
REGISTRY_PATH = Path(__file__).resolve().parent / "web/applications/icons/mcel-icons.v1.json"
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
ICON_FAMILIES = frozenset({"application", "frober", "action", "resource", "status", "navigation", "interface"})
_ALLOWED_SHAPES = {"path", "circle", "rect", "line", "ellipse", "polyline", "polygon"}
_ALLOWED_ATTRS = {
    "path": {"d"},
    "circle": {"cx", "cy", "r"},
    "rect": {"x", "y", "width", "height", "rx", "ry"},
    "line": {"x1", "y1", "x2", "y2"},
    "ellipse": {"cx", "cy", "rx", "ry"},
    "polyline": {"points"},
    "polygon": {"points"},
}
_ID = re.compile(r"[a-z][a-z0-9-]*\.[a-z][a-z0-9-]*\Z")
_CLASS = re.compile(r"[A-Za-z_-][A-Za-z0-9_-]*(?:\s+[A-Za-z_-][A-Za-z0-9_-]*)*\Z")
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)\Z")
_PATH = re.compile(r"[MmLlHhVvCcSsQqTtAaZz0-9+.,\-\s]+\Z")
_POINTS = re.compile(r"[0-9+.,\-\s]+\Z")
_COLOR = {"none", "currentColor"}


class IconRegistryError(ValueError):
    """An icon identity or its canonical drawing violates the contract."""


def validate_registry(registry: object) -> dict:
    """Validate the narrow SVG vocabulary; refuse unknown tags and attributes.

    The renderer never accepts arbitrary markup, CSS, handlers, hrefs, or SVG
    external references from the registry.
    """
    if not isinstance(registry, dict) or set(registry) != {"schema", "viewBox", "icons"}:
        raise IconRegistryError("MCEL Icon registry fields invalid")
    if registry["schema"] != SCHEMA or registry["viewBox"] != "0 0 24 24":
        raise IconRegistryError("MCEL Icon schema or coordinate system invalid")
    if not isinstance(registry["icons"], list) or not registry["icons"]:
        raise IconRegistryError("MCEL Icon registry is empty")
    seen: set[str] = set()
    for icon in registry["icons"]:
        if not isinstance(icon, dict) or set(icon) != {"id", "family", "label", "shapes"}:
            raise IconRegistryError("MCEL Icon entry fields invalid")
        key, family, label, shapes = (icon[field] for field in ("id", "family", "label", "shapes"))
        if not isinstance(key, str) or not _ID.fullmatch(key) or key in seen:
            raise IconRegistryError(f"MCEL Icon identity duplicate or invalid: {key!r}")
        if not isinstance(family, str) or family not in ICON_FAMILIES or not key.startswith(family + "."):
            raise IconRegistryError(f"MCEL Icon family mismatch: {key}")
        if not isinstance(label, str) or not label.strip() or len(label) > 96:
            raise IconRegistryError(f"MCEL Icon label invalid: {key}")
        if not isinstance(shapes, list) or not shapes or len(shapes) > 48:
            raise IconRegistryError(f"MCEL Icon shapes missing or excessive: {key}")
        for shape in shapes:
            if not isinstance(shape, dict) or set(shape) != {"tag", "attrs"}:
                raise IconRegistryError(f"MCEL Icon shape invalid: {key}")
            tag, attrs = shape["tag"], shape["attrs"]
            if tag not in _ALLOWED_SHAPES or not isinstance(attrs, dict) or not attrs:
                raise IconRegistryError(f"MCEL Icon shape tag/attributes invalid: {key}")
            if not set(attrs).issubset(_ALLOWED_ATTRS[tag] | {"fill", "stroke"}):
                raise IconRegistryError(f"MCEL Icon unsafe SVG attribute: {key}")
            for attr, value in attrs.items():
                if not isinstance(value, str) or not value or len(value) > 1024:
                    raise IconRegistryError(f"MCEL Icon shape attribute invalid: {key}")
                if attr == "d":
                    accepted = _PATH.fullmatch(value)
                elif attr == "points":
                    accepted = _POINTS.fullmatch(value)
                elif attr in ("fill", "stroke"):
                    accepted = value in _COLOR
                else:
                    accepted = _NUMBER.fullmatch(value)
                if not accepted:
                    raise IconRegistryError(f"MCEL Icon unsafe SVG value: {key}.{attr}")
        seen.add(key)
    return registry


@lru_cache(maxsize=1)
def load_registry() -> dict:
    return validate_registry(json.loads(REGISTRY_PATH.read_text(encoding="utf-8")))


def icon_ids(*, family: str | None = None) -> tuple[str, ...]:
    if family is not None and family not in ICON_FAMILIES:
        raise IconRegistryError(f"Unknown MCEL Icon family: {family}")
    return tuple(icon["id"] for icon in load_registry()["icons"] if family is None or icon["family"] == family)


def get_icon(icon_id: str) -> dict:
    """Return a detached metadata/drawing record so callers cannot mutate cache."""
    for icon in load_registry()["icons"]:
        if icon["id"] == icon_id:
            return json.loads(json.dumps(icon))
    raise IconRegistryError(f"Unknown MCEL Icon identity: {icon_id}")


def render_icon(icon_id: str, *, size: int = 24, aria_label: str | None = None,
                class_name: str = "") -> str:
    """Render one complete SVG; decorative unless expressly given an aria label.

    A semantic icon ID never carries click behavior, outcome, selected state,
    navigation targets, or the parent control's pressed/hover state.
    """
    if isinstance(size, bool) or not isinstance(size, int) or not 8 <= size <= 256:
        raise IconRegistryError("MCEL Icon size must be an integer from 8 to 256")
    if not isinstance(class_name, str) or (class_name and not _CLASS.fullmatch(class_name)):
        raise IconRegistryError("MCEL Icon CSS class invalid")
    if aria_label is not None and (not isinstance(aria_label, str) or not aria_label.strip()):
        raise IconRegistryError("MCEL Icon accessible label invalid")
    icon = get_icon(icon_id)
    attrs = [
        f'xmlns="{SVG_NAMESPACE}"',
        f'class="mcel-icon-svg{(" " + class_name) if class_name else ""}"',
        'viewBox="0 0 24 24"', f'width="{size}"', f'height="{size}"',
        'fill="none"', 'stroke="currentColor"', 'stroke-width="2.2"',
        'stroke-linecap="round"', 'stroke-linejoin="round"',
        f'data-mcel-icon-id="{icon_id}"',
        f'data-mcel-icon-family="{icon["family"]}"',
        'focusable="false"',
    ]
    if aria_label is None:
        attrs.append('aria-hidden="true"')
    else:
        attrs += ['role="img"', f'aria-label="{escape(aria_label, quote=True)}"']
    nodes = []
    for shape in icon["shapes"]:
        shape_attrs = " ".join(f'{k}="{escape(v, quote=True)}"' for k, v in shape["attrs"].items())
        nodes.append(f'<{shape["tag"]} {shape_attrs}/>')
    return f'<svg {" ".join(attrs)}> {"".join(nodes)}</svg>'
