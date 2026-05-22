"""HTML page assets for :mod:`main_computer.viewport`.

The page bodies live in ``main_computer/web/*.html`` so that the main
viewport server module stays small enough to inspect in local-model context.
"""

from __future__ import annotations

import re
from pathlib import Path


_WEB_DIR = Path(__file__).with_name("web")
_INCLUDE_RE = re.compile(r"^[ \t]*<!--\s*@include\s+([^>]+?)\s*-->", re.MULTILINE)

_PAGE_FILES = {
    "TEXT_INDEX_HTML": "text.html",
    "GRAPHICAL_INDEX_HTML": "graphical.html",
    "DEBUG_TEXT_INDEX_HTML": "debug_text.html",
    "DEBUG_GRAPHICAL_INDEX_HTML": "debug_graphical.html",
    "REVISION_INDEX_HTML": "revision.html",
    "APPLICATIONS_INDEX_HTML": "applications.html",
    "ENERGY_INDEX_HTML": "energy.html",
}


def _expand_includes(text: str, base_dir: Path, seen: tuple[Path, ...] = ()) -> str:
    def replace(match: re.Match[str]) -> str:
        include_name = match.group(1).strip()
        include_path = (base_dir / include_name).resolve()
        web_root = _WEB_DIR.resolve()
        if web_root not in include_path.parents:
            raise ValueError(f"Viewport include escapes web asset directory: {include_name}")
        if include_path in seen:
            chain = " -> ".join(path.name for path in (*seen, include_path))
            raise ValueError(f"Recursive viewport include detected: {chain}")
        include_text = include_path.read_text(encoding="utf-8")
        return _expand_includes(include_text, base_dir, (*seen, include_path))

    return _INCLUDE_RE.sub(replace, text)


def _load_page(name: str) -> str:
    try:
        filename = _PAGE_FILES[name]
    except KeyError as exc:
        raise KeyError(f"Unknown viewport page asset: {name}") from exc
    return _expand_includes((_WEB_DIR / filename).read_text(encoding="utf-8"), _WEB_DIR)


TEXT_INDEX_HTML = _load_page("TEXT_INDEX_HTML")
GRAPHICAL_INDEX_HTML = _load_page("GRAPHICAL_INDEX_HTML")
DEBUG_TEXT_INDEX_HTML = _load_page("DEBUG_TEXT_INDEX_HTML")
DEBUG_GRAPHICAL_INDEX_HTML = _load_page("DEBUG_GRAPHICAL_INDEX_HTML")
REVISION_INDEX_HTML = _load_page("REVISION_INDEX_HTML")
APPLICATIONS_INDEX_HTML = _load_page("APPLICATIONS_INDEX_HTML")
ENERGY_INDEX_HTML = _load_page("ENERGY_INDEX_HTML")

__all__ = [
    "TEXT_INDEX_HTML",
    "GRAPHICAL_INDEX_HTML",
    "DEBUG_TEXT_INDEX_HTML",
    "DEBUG_GRAPHICAL_INDEX_HTML",
    "REVISION_INDEX_HTML",
    "APPLICATIONS_INDEX_HTML",
    "ENERGY_INDEX_HTML",
]
