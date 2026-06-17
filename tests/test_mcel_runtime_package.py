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
    assert result.version == "mcel-runtime.v0.1.9"
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
    assert "powerSite: mcelRuntimePowerSite" in text
    assert "report: mcelRuntimeReport" in text
    assert "diagnostics: mcelRuntimeDiagnostics" in text
    assert "twiddle: mcelRuntimeTwiddle" in text
    assert "vanity: mcelRuntimeVanity" in text
    assert "vanityDiagnostics: mcelRuntimeVanityDiagnostics" in text
    assert "vanityRemedies: mcelRuntimeVanityRemedies" in text
    assert "detectSources: mcelRuntimeDetectSources" in text
    assert "function mcelRuntimeSourceIslands" in text
    assert "function mcelRuntimeSourceElements" in text
    assert "function mcelRuntimeQueryOptions" in text
    assert "function mcelRuntimeReadQueryValue" in text
    assert '"mcel-theme"' in text
    assert '"mcel-chrome"' in text
    assert '"mcel-diagnostics"' in text
    assert '"mcel-twiddle"' in text
    assert '"mcel-vanity"' in text
    assert '"mcel-vanity-remedy"' in text
    assert '"theme"' in text
    assert '"chrome"' in text
    assert "data-mcel-runtime-hydrated" in text
    assert "data-mcel-runtime-powered" in text
    assert "data-mcel-runtime-site-style" in text
    assert "mcel-powered-site" in text
    assert "mcel-runtime-ready" in text
    assert "MCEL powered" in text
    assert 'section[data-mc-kind="hero"][data-mcel-runtime-hydrated="true"]' in text
    assert '[data-mc-component-kind="page"]' in text
    assert 'section[data-mc="command-row"][data-mcel-runtime-hydrated="true"]' in text
    hydrate_body = text.split("function mcelRuntimeHydrate", 1)[1].split("function mcelRuntimePowerSite", 1)[0]
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


def test_mcel_runtime_hydration_powers_site_mode_without_lab_replacing_everything() -> None:
    text = build_mcel_runtime_text(ROOT)

    assert 'reason: "no-mcel-source"' in text
    assert "mcelRuntimeMarkReady(root, emptyResult)" in text
    assert "const sources = mcelRuntimeSourceElements(target" in text
    assert "mcelRuntimeEnsureSiteChrome(doc)" in text
    assert "mcelRuntimeEnhanceElement(source, compiled" in text
    assert "mcelRuntimeReplaceElement(source, compiled.runtimeHtml ||" in text
    assert "mcelRuntimeShouldRender(source, opts)" in text
    assert 'element.dataset.mcelRuntimePowered = sourceCount > 0 ? "true" : "false"' in text
    assert 'element.dataset.mcelRuntimeMode = mode' in text
    assert 'body.classList.toggle("mcel-powered-site", sourceCount > 0 && mode !== "observe")' in text
    assert 'runtime.powerSite(window.document, {reason: "mcel-runtime:auto-hydrate"})' in text
    assert 'content: "MCEL " attr(data-mcel-runtime-version)' in text
    assert '--mcel-runtime-hero-badge: "MCEL powered"' in text
    assert 'element.dataset.mcelRuntimeChromeApplied = meta.mode === "render" ? "render" : "site"' in text
    assert 'body.mcel-powered-site :where(section[data-mc-kind="hero"][data-mcel-runtime-hydrated="true"]' in text
    assert ':root[data-mcel-runtime-theme="theme-saas"]' in text
    assert ':root[data-mcel-runtime-theme="theme-accessible"]' in text
    assert ':root[data-mcel-runtime-chrome="chrome-spotlight"]' in text
    assert ':root[data-mcel-runtime-chrome="chrome-cluster-grid"]' in text
    assert "section-max-width-padding-conflict" in text
    assert "MCEL diagnostics" in text
    assert "MCEL twiddle" in text
    assert "mcel-vanity-pass" in text
    assert "hard-inline-content-overflow" in text
    assert "font-size-step-down" in text
    assert "wrap-anywhere" in text
    assert "code-chip" in text
    assert "scroll-chip" in text
    assert "disclose" in text
    assert "mcelRuntimeScheduleVanity(root, opts)" in text
    assert 'vanity: true' in text
    assert 'vanityRemedy: "auto"' in text
    assert 'options.vanity === false' in text
    assert 'opts.vanityDetect === true || options.detect === true' in text
    assert "mcelRuntimeAmbientOptions()" in text
    assert 'mcelRuntimeScript = window.document?.currentScript || null' in text
    spotlight_body = text.split(':root[data-mcel-runtime-chrome="chrome-spotlight"] body.mcel-powered-site :where(section[data-mc-kind="proof"]', 1)[1].split('}', 1)[0]
    assert "max-width" not in spotlight_body
    assert "margin-inline" not in spotlight_body

    hydrate_body = text.split("function mcelRuntimeHydrate", 1)[1].split("function mcelRuntimePowerSite", 1)[0]
    assert "mcelRuntimeEnsureStyle(doc)" in hydrate_body
    assert "renderThisSource" in hydrate_body
    assert 'mode: "render"' in hydrate_body


def test_mcel_runtime_diagnostics_do_not_draw_page_outlines_without_debug_overlay() -> None:
    text = build_mcel_runtime_text(ROOT)

    assert 'html[data-mcel-runtime-diagnostics="true"] body.mcel-powered-site :where([data-mc][data-mcel-runtime-hydrated="true"])' not in text
    assert 'html[data-mcel-runtime-diagnostics="true"] body.mcel-powered-site :where([data-mcel-runtime-layout-issue])' not in text
    assert 'html[data-mcel-runtime-debug="true"][data-mcel-runtime-diagnostics="true"] body.mcel-powered-site :where([data-mc][data-mcel-runtime-hydrated="true"])' in text
    assert 'html[data-mcel-runtime-debug="true"][data-mcel-runtime-diagnostics="true"] body.mcel-powered-site :where([data-mcel-runtime-layout-issue])' in text


def test_mcel_runtime_vanity_defect_outlines_require_debug_overlay() -> None:
    text = build_mcel_runtime_text(ROOT)

    assert '\nbody.mcel-powered-site [${runtimeVanityDefectAttribute}] {' not in text
    assert '\nbody.mcel-powered-site [${runtimeVanityContainerDefectAttribute}] {' not in text
    assert 'html[data-mcel-runtime-debug="true"] body.mcel-powered-site [${runtimeVanityDefectAttribute}]' in text
    assert 'html[data-mcel-runtime-debug="true"] body.mcel-powered-site [${runtimeVanityContainerDefectAttribute}]' in text
    assert 'body.mcel-powered-site [${runtimeVanityFixAttribute}~="wrap-anywhere"]' in text
    assert 'body.mcel-powered-site [${runtimeVanityFixAttribute}~="code-chip"]' in text


def test_hub_site_runtime_copy_gates_mcel_vanity_outlines_without_debug() -> None:
    hub_runtime = ROOT / "runtime" / "websites" / "hub-site" / "runtime.js"
    text = hub_runtime.read_text(encoding="utf-8")

    assert '\nbody.mcel-powered-site [${runtimeVanityDefectAttribute}] {' not in text
    assert '\nbody.mcel-powered-site [${runtimeVanityContainerDefectAttribute}] {' not in text
    assert 'html[data-mcel-runtime-debug="true"] body.mcel-powered-site [${runtimeVanityDefectAttribute}]' in text
    assert 'html[data-mcel-runtime-debug="true"] body.mcel-powered-site [${runtimeVanityContainerDefectAttribute}]' in text
