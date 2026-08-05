"""Ephemeral MCEL browser build materialization.

The browser package tree and package catalog are deterministic build outputs under
``runtime/build``. They are generated on demand and may be deleted at any time.
"""
from __future__ import annotations
from pathlib import Path


def ensure_mcel_browser_build(repo_root: Path) -> tuple[Path, Path]:
    from main_computer.mcel_application_runtime_projection import write_runtime_projections
    from main_computer.mcel_application_package_browser_catalog import write_browser_catalog

    runtime_root, _projection_set, _runtime_changed = write_runtime_projections(repo_root)
    catalog_path, _payload, _catalog_changed = write_browser_catalog(repo_root)
    return runtime_root, catalog_path
