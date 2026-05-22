from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(source: str, needle: str, label: str) -> None:
    if needle not in source:
        raise AssertionError(f"Missing {label}: {needle}")


def main() -> None:
    app = (ROOT / "main_computer" / "web" / "applications" / "apps" / "website-builder.html").read_text(encoding="utf-8")
    script = (ROOT / "main_computer" / "web" / "applications" / "scripts" / "website-builder.js").read_text(encoding="utf-8")
    bindings = (ROOT / "main_computer" / "web" / "applications" / "scripts" / "dom-bindings" / "websites.js").read_text(encoding="utf-8")

    checks = {
        "blog_button_opens_wizard": 'id="website-builder-blog-install-open"',
        "modal_wizard_exists": 'id="website-builder-blog-wizard"',
        "layer_stack_container_exists": 'id="website-builder-blog-layer-stack"',
        "assumption_container_exists": 'id="website-builder-blog-assumptions"',
        "api_hook_container_exists": 'id="website-builder-blog-api-hooks"',
        "assumption_endpoint_stubbed": "/api/sites/<site_id>/blog/install-assumptions",
        "plan_endpoint_stubbed": "/api/sites/<site_id>/blog/install-plan",
        "dry_run_endpoint_stubbed": "/api/sites/<site_id>/blog/install-dry-run",
        "apply_endpoint_stubbed": "/api/sites/<site_id>/blog/install-apply",
        "validate_endpoint_stubbed": "/api/sites/<site_id>/blog/install-validate",
        "commit_endpoint_stubbed": "/api/sites/<site_id>/blog/install-commit",
        "fixture_function_exists": "createWebsiteBuilderBlogInstallFixture",
        "golden_path_order_exists": 'install_order: ["database", "cms", "blog"]',
        "mutation_is_false": "mutation_allowed: false",
        "commit_is_false": "commit_allowed: false",
        "blog_binding_exists": "websiteBuilderBlogWizard",
    }

    app_checks = {
        "blog_button_opens_wizard",
        "modal_wizard_exists",
        "layer_stack_container_exists",
        "assumption_container_exists",
        "api_hook_container_exists",
    }
    binding_checks = {"blog_binding_exists"}

    for label, needle in checks.items():
        if label in app_checks:
            haystack = app
        elif label in binding_checks:
            haystack = bindings
        else:
            haystack = script
        require(haystack, needle, label)

    print(json.dumps({"ok": True, "smoke": "rag_blog_install_frontend_assumption_contract_smoke"}, indent=2))


if __name__ == "__main__":
    main()
