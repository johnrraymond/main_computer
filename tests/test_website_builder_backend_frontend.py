from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_website_builder_backend_frontend_view_is_first_class() -> None:
    app = (ROOT / "main_computer" / "web" / "applications" / "apps" / "website-builder.html").read_text(encoding="utf-8")
    css = (ROOT / "main_computer" / "web" / "applications" / "styles" / "website-builder.css").read_text(encoding="utf-8")
    script = (ROOT / "main_computer" / "web" / "applications" / "scripts" / "website-builder.js").read_text(encoding="utf-8")
    bindings = (ROOT / "main_computer" / "web" / "applications" / "scripts" / "dom-bindings" / "websites.js").read_text(encoding="utf-8")

    assert 'data-website-builder-tab="backend"' in app
    assert 'data-website-builder-panel="backend"' in app
    assert "website-builder-backend-panel" in app
    assert "website-builder-backend-runtime-grid" in app
    assert "website-builder-backend-products" in app
    assert 'data-website-builder-backend-runtime="none"' in app
    assert 'data-website-builder-backend-runtime="fastapi"' in app
    assert 'data-website-builder-backend-runtime="node-express"' in app
    assert 'data-website-builder-backend-runtime="worker"' in app
    assert 'data-website-builder-backend-product="api"' in app
    assert 'data-website-builder-backend-product="forms"' in app
    assert 'data-website-builder-backend-product="database"' in app
    assert 'data-website-builder-backend-product="auth"' in app
    assert "Edit API" in app
    assert "Save / Preview" in app
    assert "Promote the known-good backend and frontend together" in app
    assert ".website-builder-backend-panel.active" in css
    assert ".website-builder-backend-runtime-card.active" in css
    assert "websiteBuilderBackendRuntimeLabels" in script
    assert "normalizeWebsiteBuilderBackendConfig" in script
    assert "seedWebsiteBuilderBackendRoutes" in script
    assert "buildWebsiteBuilderBackendSourcePreview" in script
    assert '"backend"' in script
    assert "websiteBuilderBackendRoutes" in bindings
    assert "websiteBuilderBackendSourcePreview" in bindings
