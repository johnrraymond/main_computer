from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from main_computer.game_web_loader import load_game_manifest, read_game_bundle, selected_game_id
from main_computer.viewport_pages import APPLICATIONS_INDEX_HTML

_INCLUDE_RE = re.compile(r"^[ \t]*<!--\s*@include\s+([^>]+?)\s*-->", re.MULTILINE)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main() -> int:
    repo = REPO_ROOT
    game_id = selected_game_id()
    manifest = load_game_manifest(repo, game_id)
    bundles = manifest["web"]["bundles"]

    required = {
        "styles",
        "surface",
        "controls",
        "runtime-before-routing",
        "runtime-after-routing",
    }
    missing = sorted(required.difference(bundles))
    if missing:
        raise SystemExit(f"GAME_WEB_LOADER_SMOKE: FAIL missing bundles: {missing}")

    for slot in sorted(required):
        payload = read_game_bundle(repo, slot, game_id)
        if not payload:
            raise SystemExit(f"GAME_WEB_LOADER_SMOKE: FAIL empty bundle: {slot}")

    runtime_before = bundles["runtime-before-routing"]
    state_path = "web/scripts/dom-bindings/webgl-state.js"
    if state_path not in runtime_before:
        raise SystemExit(
            "GAME_WEB_LOADER_SMOKE: FAIL game-owned WebGL state is not supplied by the game manifest"
        )

    template = (repo / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    for slot in required:
        marker = f"@game-bundle {slot}"
        if marker not in template:
            raise SystemExit(f"GAME_WEB_LOADER_SMOKE: FAIL applications.html missing {marker}")

    relocation_path = repo / "game_projects" / game_id / "relocation-source-map.json"
    relocation = json.loads(relocation_path.read_text(encoding="utf-8"))
    game_owned_legacy_paths = {
        str(record.get("source") or "").replace("\\", "/")
        for record in relocation.get("files", [])
        if str(record.get("source") or "").startswith("main_computer/web/applications/")
    }

    legacy_include_refs: list[str] = []
    web_root = repo / "main_computer" / "web"
    for path in web_root.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for match in _INCLUDE_RE.finditer(text):
            include_name = match.group(1).strip().replace("\\", "/")
            full_source = f"main_computer/web/{include_name}"
            if full_source in game_owned_legacy_paths:
                legacy_include_refs.append(
                    f"{path.relative_to(repo).as_posix()} -> {full_source}"
                )
    if legacy_include_refs:
        raise SystemExit(
            "GAME_WEB_LOADER_SMOKE: FAIL Main Computer still includes game-owned legacy paths: "
            + repr(sorted(legacy_include_refs))
        )

    missing_destinations = []
    for record in relocation.get("files", []):
        destination = str(record.get("destination") or "")
        if not destination:
            continue
        destination_path = Path(destination.replace("\\", "/"))
        if destination_path.parts[:2] == ("games", game_id):
            destination_path = Path("game_projects", game_id, *destination_path.parts[2:])
        if not (repo / destination_path).is_file():
            missing_destinations.append(destination_path.as_posix())
    if missing_destinations:
        raise SystemExit(
            f"GAME_WEB_LOADER_SMOKE: FAIL relocated game files missing: {sorted(missing_destinations)}"
        )

    expected_rendered_markers = [
        'id="webgl-demo"',
        'id="webgl-gameplay-pack-selector"',
        "function initWebgl(",
        "window.MainComputerWebglSystemScenario",
        "PAX PRIORITY ONE",
    ]
    absent = [marker for marker in expected_rendered_markers if marker not in APPLICATIONS_INDEX_HTML]
    if absent:
        raise SystemExit(f"GAME_WEB_LOADER_SMOKE: FAIL rendered game markers missing: {absent}")

    print("GAME_WEB_LOADER_SMOKE: PASS")
    print(f"gameId={game_id}")
    print(f"manifest={repo / 'game_projects' / game_id / 'game.json'}")
    print(f"bundleSlots={len(bundles)}")
    print(f"gameOwnedLegacyIncludes=0")
    print(f"relocatedGameFiles={len(relocation.get('files', []))}")
    print(f"renderedBytes={len(APPLICATIONS_INDEX_HTML.encode('utf-8'))}")
    print(f"renderedSha256={sha256_text(APPLICATIONS_INDEX_HTML)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
