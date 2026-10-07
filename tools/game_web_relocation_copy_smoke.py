#!/usr/bin/env python3
"""Verify the additive webgl-demo relocation copy before loader cutover.

This smoke intentionally does not prove the new game tree is runnable yet. Its job is
narrower: while the legacy locations remain authoritative, every file copied into
``game_projects/webgl-demo`` is now the canonical merged game root. This smoke accepts
the post-merge state: each relocated destination must exist there and match the recorded
relocation hash; legacy source files are compared only when they still exist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

MAP_RELATIVE = Path("game_projects/webgl-demo/relocation-source-map.json")
GAME_ROOT = Path("game_projects/webgl-demo")
EXPECTED_SCHEMA = "main-computer-game-additive-relocation-map.v1"
EXPECTED_GAME_ID = "webgl-demo"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_repo_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    return Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", help="Repository root; defaults to the parent of tools/.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON only.")
    args = parser.parse_args()

    repo = resolve_repo_root(args.repo_root)
    map_path = repo / MAP_RELATIVE
    failures: list[dict[str, object]] = []

    if not map_path.is_file():
        failures.append({"check": "relocationMapExists", "path": str(map_path)})
        payload = {"ok": False, "schema": "game.webRelocationCopySmoke.v1", "failures": failures}
        print(json.dumps(payload, indent=2))
        return 1

    mapping = json.loads(map_path.read_text(encoding="utf-8"))
    if mapping.get("schema") != EXPECTED_SCHEMA:
        failures.append({"check": "relocationMapSchema", "actual": mapping.get("schema")})
    if mapping.get("gameId") != EXPECTED_GAME_ID:
        failures.append({"check": "gameId", "actual": mapping.get("gameId")})
    # The relocation map was created during the additive-copy phase. After the canonical
    # root merge, it remains provenance rather than a declaration that legacy sources
    # are still authoritative.

    files = mapping.get("files")
    if not isinstance(files, list) or not files:
        failures.append({"check": "relocationMapHasFiles"})
        files = []

    seen_destinations: set[str] = set()
    checked = 0
    bytes_checked = 0
    project_files = 0
    embedded_web_files = 0

    for item in files:
        if not isinstance(item, dict):
            failures.append({"check": "mappingEntryIsObject", "entry": repr(item)})
            continue
        source_text = item.get("source")
        destination_text = item.get("destination")
        expected_hash = item.get("sha256")
        expected_bytes = item.get("bytes")
        if not isinstance(source_text, str) or not isinstance(destination_text, str):
            failures.append({"check": "mappingPathsAreStrings", "entry": item})
            continue
        source_rel = Path(source_text)
        destination_rel = Path(destination_text)
        if source_rel.is_absolute() or destination_rel.is_absolute() or ".." in source_rel.parts or ".." in destination_rel.parts:
            failures.append({"check": "mappingPathsAreRepositoryRelative", "source": source_text, "destination": destination_text})
            continue
        canonical_destination_rel = destination_rel
        if destination_rel.parts[:2] == ("games", "webgl-demo"):
            canonical_destination_rel = Path("game_projects", "webgl-demo", *destination_rel.parts[2:])
        if canonical_destination_rel.parts[:2] != ("game_projects", "webgl-demo"):
            failures.append({"check": "destinationInsideCanonicalGameRoot", "destination": destination_text})
        if destination_text in seen_destinations:
            failures.append({"check": "destinationUnique", "destination": destination_text})
        seen_destinations.add(destination_text)

        source = repo / source_rel
        destination = repo / canonical_destination_rel
        if not destination.is_file():
            failures.append({"check": "relocatedCopyExists", "destination": destination_text})
            continue

        destination_size = destination.stat().st_size
        destination_hash = sha256_file(destination)
        if isinstance(expected_bytes, int) and destination_size != expected_bytes:
            failures.append({"check": "canonicalDestinationMatchesRelocationSnapshotBytes", "destination": canonical_destination_rel.as_posix(), "expectedBytes": expected_bytes, "actualBytes": destination_size})
        if isinstance(expected_hash, str) and destination_hash != expected_hash:
            failures.append({"check": "canonicalDestinationMatchesRelocationSnapshotSha256", "destination": canonical_destination_rel.as_posix(), "expectedSha256": expected_hash, "actualSha256": destination_hash})

        if source.is_file() and source.resolve() != destination.resolve():
            source_size = source.stat().st_size
            source_hash = sha256_file(source)
            if source_size != destination_size:
                failures.append({"check": "byteCountMatchesRemainingLegacySource", "source": source_text, "destination": canonical_destination_rel.as_posix(), "sourceBytes": source_size, "destinationBytes": destination_size})
            if source_hash != destination_hash:
                failures.append({"check": "sha256MatchesRemainingLegacySource", "source": source_text, "destination": canonical_destination_rel.as_posix(), "sourceSha256": source_hash, "destinationSha256": destination_hash})

        if source_text.startswith("game_projects/webgl-demo/"):
            project_files += 1
        if source_text.startswith("main_computer/web/applications/"):
            embedded_web_files += 1
        checked += 1
        bytes_checked += destination_size

    if project_files == 0:
        failures.append({"check": "existingGameProjectCopied"})
    if embedded_web_files == 0:
        failures.append({"check": "embeddedGameWebCopied"})

    payload = {
        "ok": not failures,
        "schema": "game.webRelocationCopySmoke.v1",
        "gameId": EXPECTED_GAME_ID,
        "mode": "canonical-root-merged",
        "canonicalGameRoot": "game_projects/webgl-demo",
        "checkedFiles": checked,
        "checkedBytes": bytes_checked,
        "existingGameProjectFiles": project_files,
        "embeddedWebFiles": embedded_web_files,
        "failures": failures,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        status = "PASS" if payload["ok"] else "FAIL"
        print(f"GAME_WEB_RELOCATION_COPY_SMOKE: {status}")
        print(f"gameId={EXPECTED_GAME_ID}")
        print(f"checkedFiles={checked}")
        print(f"checkedBytes={bytes_checked}")
        print(f"existingGameProjectFiles={project_files}")
        print(f"embeddedWebFiles={embedded_web_files}")
        print("canonicalGameRoot=game_projects/webgl-demo")
        for failure in failures:
            print("failure=" + json.dumps(failure, sort_keys=True))

    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
