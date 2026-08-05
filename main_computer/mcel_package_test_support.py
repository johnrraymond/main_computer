"""Read logical MCEL package files from the in-memory package authority."""
from __future__ import annotations
from pathlib import Path
from main_computer.mcel_application_packages import build_application_package_catalog


def logical_package_files(app_id: str, repo_root: Path | None = None) -> dict[str, bytes]:
    repo = (repo_root or Path(__file__).resolve().parents[1]).resolve()
    catalog = build_application_package_catalog(repo)
    matches = [record for record in catalog.packages if record.app_id == app_id]
    if len(matches) != 1 or not matches[0].valid:
        raise AssertionError(f"MCEL package {app_id!r} is not valid and unique.")
    return dict(matches[0].files)


def logical_package_text(app_id: str, relative: str, repo_root: Path | None = None) -> str:
    files = logical_package_files(app_id, repo_root)
    try:
        return files[relative].decode("utf-8")
    except KeyError as exc:
        raise AssertionError(f"Logical MCEL package {app_id!r} is missing {relative!r}.") from exc
