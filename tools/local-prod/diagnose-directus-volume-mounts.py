from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


DB_DEST = "/directus/database"
UPLOADS_DEST = "/directus/uploads"


def run(cmd: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        return proc.returncode, proc.stdout.strip()
    except FileNotFoundError:
        return 127, f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"command timed out: {' '.join(cmd)}"


def docker_json_lines(args: list[str]) -> list[dict[str, Any]]:
    code, out = run(["docker", *args])
    if code != 0:
        print(f"[FAIL] docker {' '.join(args)}")
        print(out)
        sys.exit(1)

    rows: list[dict[str, Any]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"[WARN] could not parse docker JSON line: {line}")
    return rows


def load_state(repo: Path) -> dict[str, Any]:
    path = repo / "runtime" / "coolify-local-docker" / "directus-blog-e2e-smoke.json"
    if not path.exists():
        print(f"[FAIL] state file not found: {path}")
        sys.exit(1)

    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[FAIL] state file is not JSON: {path}: {exc}")
        sys.exit(1)

    print(f"[INFO] state: {path}")
    for key in (
        "service_name",
        "service_uuid",
        "site_id",
        "directus_service_name",
        "website_service_name",
        "db_volume_name",
        "uploads_volume_name",
        "directus_port",
        "site_port",
    ):
        if state.get(key):
            print(f"       {key}: {state[key]}")
    print()
    return state


def inspect_container(container_id: str) -> dict[str, Any]:
    code, out = run(["docker", "inspect", container_id])
    if code != 0:
        print(f"[WARN] docker inspect failed for {container_id}: {out}")
        return {}
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError:
        print(f"[WARN] docker inspect output was not JSON for {container_id}")
        return {}
    if not parsed:
        return {}
    return parsed[0]


def name_of(inspected: dict[str, Any]) -> str:
    return str(inspected.get("Name") or "").lstrip("/")


def is_related(inspected: dict[str, Any], state: dict[str, Any]) -> bool:
    name = name_of(inspected)
    image = str(inspected.get("Config", {}).get("Image") or "")
    labels = inspected.get("Config", {}).get("Labels") or {}
    label_blob = json.dumps(labels, sort_keys=True)

    needles = [
        str(state.get("service_name") or ""),
        str(state.get("service_uuid") or ""),
        str(state.get("site_id") or ""),
        str(state.get("directus_service_name") or ""),
        str(state.get("website_service_name") or ""),
    ]
    needles = [needle for needle in needles if needle]

    haystack = " ".join([name, image, label_blob]).lower()
    return any(needle.lower() in haystack for needle in needles) or (
        "directus" in haystack and "blog" in haystack
    )


def summarize_mount(mount: dict[str, Any]) -> str:
    return (
        f"type={mount.get('Type')} "
        f"name={mount.get('Name') or '-'} "
        f"source={mount.get('Source') or '-'} "
        f"dest={mount.get('Destination') or '-'}"
    )


def volume_exists(name: str) -> bool:
    if not name:
        return False
    code, _out = run(["docker", "volume", "inspect", name])
    return code == 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose Directus blog smoke Docker volume mounts without redeploying anything."
    )
    parser.add_argument("--repo", default=".", help="Repo root. Default: current directory.")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    state = load_state(repo)

    code, out = run(["docker", "version", "--format", "{{.Server.Version}}"])
    if code != 0:
        print("[FAIL] Docker is not reachable")
        print(out)
        return 1
    print(f"[PASS] Docker is reachable: {out}")
    print()

    rows = docker_json_lines(["ps", "-a", "--format", "{{json .}}"])
    inspected = [inspect_container(str(row.get("ID") or "")) for row in rows]
    inspected = [item for item in inspected if item]

    related = [item for item in inspected if is_related(item, state)]

    print(f"[INFO] related containers found: {len(related)}")
    if not related:
        print("[WARN] No related containers found by name/label/image.")
        print("       Showing containers with Directus-looking names/images instead:")
        related = [
            item
            for item in inspected
            if "directus" in (name_of(item) + " " + str(item.get("Config", {}).get("Image") or "")).lower()
        ]

    actual_by_dest: dict[str, list[tuple[str, dict[str, Any]]]] = {
        DB_DEST: [],
        UPLOADS_DEST: [],
    }

    for item in related:
        name = name_of(item)
        image = item.get("Config", {}).get("Image")
        status = item.get("State", {}).get("Status")
        labels = item.get("Config", {}).get("Labels") or {}

        print()
        print(f"[CONTAINER] {name}")
        print(f"  id: {item.get('Id', '')[:12]}")
        print(f"  image: {image}")
        print(f"  status: {status}")
        if labels.get("com.docker.compose.project"):
            print(f"  compose.project: {labels.get('com.docker.compose.project')}")
        if labels.get("com.docker.compose.service"):
            print(f"  compose.service: {labels.get('com.docker.compose.service')}")
        if labels.get("coolify.name"):
            print(f"  coolify.name: {labels.get('coolify.name')}")

        mounts = item.get("Mounts") or []
        if not mounts:
            print("  mounts: none")
            continue

        print("  mounts:")
        for mount in mounts:
            print(f"    - {summarize_mount(mount)}")
            dest = str(mount.get("Destination") or "")
            if dest in actual_by_dest:
                actual_by_dest[dest].append((name, mount))

    print()
    print("[REQUESTED VOLUME NAMES FROM STATE]")
    requested_db = str(state.get("db_volume_name") or "")
    requested_uploads = str(state.get("uploads_volume_name") or "")

    for label, requested in (("database", requested_db), ("uploads", requested_uploads)):
        exists = volume_exists(requested)
        print(f"  {label}: {requested} exists={exists}")

    print()
    print("[ACTUAL DIRECTUS MOUNTS BY DESTINATION]")
    failures = 0

    for label, dest in (("database", DB_DEST), ("uploads", UPLOADS_DEST)):
        found = actual_by_dest[dest]
        if not found:
            print(f"[FAIL] no related container has a mount at {dest}")
            failures += 1
            continue

        print(f"[PASS] {label}: found {len(found)} mount(s) at {dest}")
        for container_name, mount in found:
            actual_name = str(mount.get("Name") or "")
            mount_type = str(mount.get("Type") or "")
            requested = requested_db if dest == DB_DEST else requested_uploads
            same_as_requested = actual_name == requested

            print(f"       container={container_name}")
            print(f"       {summarize_mount(mount)}")
            print(f"       same_as_requested={same_as_requested}")

            if mount_type != "volume":
                print("       WARNING: this is not a named Docker volume")
                failures += 1

    print()
    if failures:
        print("[RESULT] mount diagnostic found a real problem or no matching Directus mount.")
        return 1

    print("[RESULT] Directus is using named mounts for database/uploads.")
    print("         If same_as_requested=False, the big smoke should record resolved volume names instead of requiring requested names.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())