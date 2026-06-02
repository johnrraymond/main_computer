from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from main_computer.mcel_runtime_package import (
    MCEL_LAB_HELPER_FILE,
    MCEL_RUNTIME_MODULES,
    build_mcel_runtime_text,
    package_mcel_runtime,
)


ROOT = Path(__file__).resolve().parents[1]
MCEL_RUNTIME = ROOT / "deploy" / "local-platform" / "site-runtimes" / "mcel-runtime.js"


def test_mcel_runtime_packager_builds_single_frontend_runtime_without_lab_ui(tmp_path: Path) -> None:
    result = package_mcel_runtime(ROOT, tmp_path / "mcel-runtime.js")
    text = result.output_path.read_text(encoding="utf-8")

    assert result.size_bytes == len(text.encode("utf-8"))
    assert result.version == "mcel-runtime.v0.1.1"
    assert result.helper_functions == ("isolatedSiteCss",)
    assert MCEL_LAB_HELPER_FILE in result.source_files
    for source_file in MCEL_RUNTIME_MODULES:
        assert f"// BEGIN {source_file}" in text

    assert "function isolatedSiteCss()" in text
    assert 'Object.defineProperty(window, "MCELRuntime"' in text
    assert 'Object.defineProperty(window, "WebsiteBuilderRuntime"' in text
    assert "mountPreview" in text
    assert "renderDocument" in text
    assert "hydrate: mcelRuntimeHydrate" in text
    assert "detectSources: mcelRuntimeDetectSources" in text
    assert "function mcelRuntimeSourceIslands" in text
    assert "data-mcel-runtime-hydrated" in text
    assert "data-mcel-runtime-style" in text
    assert "mcel-runtime-ready" in text
    hydrate_body = text.split("function mcelRuntimeHydrate", 1)[1].split("function mcelRuntimeDetectSources", 1)[0]
    assert "target.innerHTML = compiled.runtimeHtml" not in hydrate_body

    assert "initMcelLabApp" not in text
    assert "mcelLabState" not in text
    assert "mcelSourceHtml" not in text
    assert "mcel-runtime-preview" in text

    node = shutil.which("node")
    if node:
        subprocess.run([node, "--check", str(result.output_path)], check=True)


def test_checked_in_mcel_runtime_asset_matches_packager_output() -> None:
    expected = build_mcel_runtime_text(ROOT)

    assert MCEL_RUNTIME.exists()
    assert MCEL_RUNTIME.read_text(encoding="utf-8") == expected


def test_mcel_runtime_hydration_is_passive_and_scoped_to_source_islands() -> None:
    text = build_mcel_runtime_text(ROOT)

    assert 'reason: "no-mcel-source"' in text
    assert "mcelRuntimeMarkReady(root, emptyResult)" in text
    assert "const islands = mcelRuntimeSourceIslands(target)" in text
    assert "mcelRuntimeReplaceElement(island, compiled.runtimeHtml ||" in text
    assert "mcelRuntimeEnsureStyle(doc)" in text
    assert 'element.dataset.mcelRuntimeReady = "true"' in text
    assert 'body.classList.toggle("mcel-runtime-active", changed)' in text
