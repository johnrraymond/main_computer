from __future__ import annotations

from pathlib import Path
from typing import Any

from main_computer.sqlite_publish import (
    SQLitePublishError,
    configure_sqlite_database_resource,
    ensure_sqlite_publish_smoke_source,
    resolve_source_db_path,
    sqlite_database_connections,
    sqlite_publish_resources,
)
from main_computer.website_project_manifest import (
    WebsiteProject,
    WebsiteProjectError,
    load_website_project,
    save_website_directus_connection,
    utc_now,
    write_json,
)


BLOG_LAYER_INSTALL_ORDER = ["database", "cms", "blog"]

BLOG_LAYER_LABELS = {
    "database": "Database Layer",
    "cms": "CMS Provider",
    "blog": "Blog",
}

BLOG_LAYER_OPTIONS = {
    "database": "SQLite",
    "cms": "Directus",
    "blog": "Blog",
}

BLOG_LAYER_DESCRIPTIONS = {
    "database": "SQLite is the nested base dependency and installs before the CMS and Blog layers.",
    "cms": "Directus will be prepared and verified by deploy as the required CMS runtime.",
    "blog": "Blog records website runtime intent and waits for deployed Directus runtime verification.",
}

BLOG_PUBLIC_FIELDS = [
    "id",
    "status",
    "slug",
    "title",
    "excerpt",
    "body",
    "published_on",
    "read_time_minutes",
    "is_legacy",
]

BLOG_ROUTES = {
    "index": "/blog",
    "post": "/blog/:slug",
}

BLOG_SOURCE_FILES = [
    "src/content/runtime-config.js",
    "src/content/directus-client.js",
    "src/blog/list-posts.js",
    "src/blog/get-post-by-slug.js",
]

BLOG_RUNTIME_PLAN_GATES = [
    "sqlite_database",
    "directus_service",
    "directus_schema",
    "directus_permissions",
    "hub_runtime_wiring",
    "published_read_verification",
    "draft_protection_verification",
]

BLOG_RUNTIME_PLAN_MARKER_ALIASES = {
    "sqlite_database": ("sqlite_database", "database"),
    "directus_service": ("directus_service", "directus_running", "directus"),
    "directus_schema": ("directus_schema", "schema"),
    "directus_permissions": ("directus_permissions", "permissions"),
    "hub_runtime_wiring": ("hub_runtime_wiring", "site_wired", "runtime_wiring"),
    "published_read_verification": ("published_read_verification", "published_read"),
    "draft_protection_verification": ("draft_protection_verification", "draft_protection"),
}


class BlogInstallError(WebsiteProjectError):
    """Raised when a Blog layer cannot be installed safely."""


def blog_runtime_plan(repo_root: Path, site_id: object) -> dict[str, Any]:
    """Return a read-only Blog runtime preparation plan.

    This helper intentionally does not create SQLite files, configure Directus,
    write runtime env/config, apply schema/permissions, or promote Blog.  It is
    the readiness source of truth for explaining what still needs to happen
    before a selected Blog can become live.
    """

    project = load_website_project(repo_root, site_id)
    return _blog_runtime_plan(project)


def blog_install_assumptions(repo_root: Path, site_id: object) -> dict[str, Any]:
    """Return the durable Blog install contract consumed by the Website Builder UI."""

    project = load_website_project(repo_root, site_id)
    state = _blog_install_state(project)
    database_ready = bool(state["sqlite_connection"] and state["sqlite_source_exists"])

    cms_status = _stored_layer_status(project, "cms")
    blog_status = _stored_layer_status(project, "blog")
    cms_configured = _is_directus_configured(project)
    cms_layer_configured = cms_status in {"configured", "deploying", "ready", "blocked", "failed"}
    cms_ready = cms_status == "ready"
    blog_configured = _is_blog_feature_configured(project)
    blog_layer_configured = blog_status in {"configured", "pending_deploy", "ready"}
    blog_ready = blog_status == "ready"

    layers = [
        _layer_contract(
            project,
            "blog",
            blog_status if blog_layer_configured else "planned",
        ),
        _layer_contract(
            project,
            "cms",
            cms_status if cms_layer_configured else "planned",
        ),
        _layer_contract(
            project,
            "database",
            "already_installed" if database_ready else _stored_layer_status(project, "database"),
            existing_resource_detected=state["sqlite_source_exists"],
        ),
    ]

    ready = database_ready and cms_ready and blog_ready
    configured = database_ready and cms_layer_configured and blog_layer_configured
    return {
        "ok": True,
        "source": "backend",
        "site_id": project.id,
        "site_name": project.name,
        "feature": "blog",
        "golden_path": True,
        "provider_recommendation": "directus",
        "database_recommendation": "sqlite",
        "install_order": list(BLOG_LAYER_INSTALL_ORDER),
        "next_allowed_action": "commit_ready" if ready else ("pending_deploy_verification" if configured else "install_recommended_stack"),
        "mutation_allowed": True,
        "commit_allowed": ready,
        "sqlite": {
            "connection_configured": bool(state["sqlite_connection"]),
            "source_exists": state["sqlite_source_exists"],
            "source": _repo_relative_or_abs(repo_root, state["sqlite_source_path"]) if state["sqlite_source_path"] else "",
            "base_publish_policy": "seed_source_once_publish_unchanged_after_first_deploy",
        },
        "directus": {
            "provider": "directus",
            "configured": cms_layer_configured,
            "ready": cms_ready,
            "runtime": "deployed",
            "status": cms_status if cms_configured else "planned",
            "database_connection": "content",
            "deploy_verifies": [
                "service",
                "database_volume",
                "uploads_volume",
                "schema",
                "public_read_permissions",
                "draft_protection",
            ],
        },
        "blog_runtime_plan": _blog_runtime_plan(project),
        "runtime": _blog_runtime_contract(project),
        "blog": _blog_response_contract(project, ready=blog_ready),
        "actions": _blog_runtime_actions(project),
        "layers": layers,
        "assumptions": [
            {
                "id": "blog_feature_selected",
                "status": "pass",
                "severity": "required",
                "frontend_title": "Blog selected",
                "frontend_message": "The Blog feature uses the recommended layered stack.",
            },
            {
                "id": "directus_golden_path",
                "status": "configured" if cms_layer_configured else "planned",
                "severity": "required",
                "frontend_title": "Directus CMS",
                "frontend_message": "Directus will be used for this blog. Configure Blog Runtime prepares the local Directus runtime, schema, uploads, and public read permissions.",
            },
            {
                "id": "sqlite_nested_dependency",
                "status": "pass" if database_ready else "planned",
                "severity": "blocker",
                "frontend_title": "SQLite dependency",
                "frontend_message": (
                    "SQLite is configured and deploy can publish the base content DB artifact."
                    if database_ready
                    else "SQLite is the nested base dependency and must be configured before Directus."
                ),
            },
            {
                "id": "runtime_dependency_reuse",
                "status": "pass",
                "severity": "required",
                "frontend_title": "Runtime dependency reuse",
                "frontend_message": "Deploy will reuse existing runtime dependencies when safe. Direct database and CMS management tools will be added later.",
            },
            {
                "id": "first_deploy_database_publish",
                "status": "pass" if database_ready else "planned",
                "severity": "required",
                "frontend_title": "First deploy has database work",
                "frontend_message": (
                    "The site manifest contains a publishable SQLite connection, so first deploy can publish the base DB."
                    if database_ready
                    else "The database layer must write the SQLite connection and source DB before first deploy."
                ),
            },
            {
                "id": "commit_gate",
                "status": "pass" if ready else ("configured" if configured else "planned"),
                "severity": "blocker",
                "frontend_title": "Deploy verification gate",
                "frontend_message": (
                    "SQLite, Directus, and Blog runtime are deploy-verified."
                    if ready
                    else "Commit remains disabled until deploy verifies Directus runtime readiness."
                ),
            },
        ],
    }

def install_blog_layer(repo_root: Path, site_id: object, layer_id: object, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Install one durable Blog layer.

    The database layer is intentionally conservative: it creates the authoring
    SQLite base only when it does not already exist. Existing SQLite data is
    reused only when explicitly requested, and overwrite is opt-in.
    """

    layer = _validate_layer_id(layer_id)
    body = payload if isinstance(payload, dict) else {}
    if layer == "database":
        return _install_database_layer(repo_root, site_id, body)
    if layer == "cms":
        return _install_cms_layer(repo_root, site_id, body)
    return _install_blog_feature_layer(repo_root, site_id, body)


def persist_blog_intent(repo_root: Path, site_id: object, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist the user's Blog runtime choice without installing or deploying it.

    This is the durable intent step for the Website Builder wizard. It writes the
    selected Blog feature into site.json, but it deliberately avoids the heavier
    layer-install path: no SQLite source DB is created, no Directus contract is
    hydrated, no runtime_config is written, and no Blog source files are generated.
    """

    project = load_website_project(repo_root, site_id)
    body = payload if isinstance(payload, dict) else {}
    runtime_lane = str(body.get("runtime_lane") or project.lane or "local").strip() or "local"

    manifest = _clear_blog_runtime_state_for_intent(dict(project.manifest))
    features = manifest.get("features")
    if not isinstance(features, dict):
        features = {}

    features["blog"] = _blog_intent_contract(runtime_lane=runtime_lane)
    manifest["features"] = features
    manifest["updated_at"] = utc_now()

    write_json(project.path / "site.json", manifest)
    project = load_website_project(repo_root, site_id)

    directus_connection = body.get("directus_connection")
    if isinstance(directus_connection, dict):
        project = save_website_directus_connection(repo_root, site_id, directus_connection)

    return {
        "ok": True,
        "source": "backend",
        "site_id": project.id,
        "feature": "blog",
        "intent": project.manifest.get("features", {}).get("blog", {}),
        "directus_connection": project.manifest.get("backend", {}).get("cms", {}).get("local_connection", {}),
        "site": project.to_dict(repo_root),
        "contract": blog_install_assumptions(repo_root, site_id),
    }


def _install_database_layer(repo_root: Path, site_id: object, payload: dict[str, Any]) -> dict[str, Any]:
    project = load_website_project(repo_root, site_id)
    state = _blog_install_state(project)
    source_path = state["sqlite_source_path"] or (project.path / "data" / "content.sqlite")
    existing_source = source_path.exists()
    overwrite = bool(payload.get("overwrite_sqlite"))
    keep_existing = bool(payload.get("keep_existing"))

    if source_path.exists() and not source_path.is_file():
        raise BlogInstallError(f"SQLite source path exists but is not a file: {source_path}")

    if existing_source and not overwrite and not keep_existing:
        return {
            "ok": False,
            "code": "sqlite_reinstall_guard",
            "layer_id": "database",
            "existing_resource_detected": True,
            "overwrite_required": False,
            "overwrite_default": False,
            "overwrite_allowed": True,
            "recommended_action": "keep_existing",
            "message": "SQLite already exists. Keeping existing runtime data is the default; overwrite requires explicit confirmation.",
            "contract": blog_install_assumptions(repo_root, site_id),
        }

    if overwrite and existing_source:
        source_path.unlink()
        existing_source = False

    configure_sqlite_database_resource(repo_root, site_id)
    project = load_website_project(repo_root, site_id)
    db = _sqlite_content_connection(project)
    source_path = resolve_source_db_path(project, db)

    if keep_existing and source_path.exists():
        resources = sqlite_publish_resources(source_path)
        seed_result = {
            "ok": True,
            "site_id": project.id,
            "connection": db.name,
            "source": _repo_relative_or_abs(repo_root, source_path),
            "resource_count": len(resources),
            "resources": resources,
            "reused_existing_source": True,
        }
        action = "reused"
    else:
        seed_result = ensure_sqlite_publish_smoke_source(repo_root, site_id)
        action = "overwritten" if overwrite else "installed"

    project = _mark_blog_layer(repo_root, site_id, "database", action=action)
    return {
        "ok": True,
        "source": "backend",
        "layer_id": "database",
        "action": action,
        "existing_resource_detected": existing_source,
        "overwrite_sqlite": overwrite,
        "keep_existing": keep_existing,
        "base_seeded": action in {"installed", "overwritten"},
        "base_deploy_policy": "publish once; subsequent unchanged deploys keep the existing artifact",
        "sqlite": seed_result,
        "site": project.to_dict(repo_root),
        "contract": blog_install_assumptions(repo_root, site_id),
    }


def _install_cms_layer(repo_root: Path, site_id: object, payload: dict[str, Any]) -> dict[str, Any]:
    project = load_website_project(repo_root, site_id)
    _require_database_ready(project)
    existing = _stored_layer_status(project, "cms") in {"configured", "ready"}
    project = _mark_blog_layer(repo_root, site_id, "cms", action="configured")
    directus_connection = payload.get("directus_connection")
    if isinstance(directus_connection, dict):
        project = save_website_directus_connection(repo_root, site_id, directus_connection)

    setup_result: dict[str, Any] | None = None
    if payload.get("setup_local_directus"):
        from main_computer.website_project_manifest import configure_website_directus_runtime

        setup_result = configure_website_directus_runtime(
            repo_root,
            site_id,
            verify=not bool(payload.get("skip_directus_verify")),
            timeout_s=float(payload.get("directus_timeout_s") or 45.0),
        )
        project = load_website_project(repo_root, site_id)
        if not setup_result.get("ok"):
            return {
                "ok": False,
                "source": "backend",
                "layer_id": "cms",
                "action": "failed",
                "provider": "directus",
                "runtime": "local",
                "ready": False,
                "directus_connection": project.manifest.get("backend", {}).get("cms", {}).get("local_connection", {}),
                "directus_setup": setup_result,
                "message": str(setup_result.get("error") or "Directus local setup failed during Blog Runtime configuration."),
                "site": project.to_dict(repo_root),
                "contract": blog_install_assumptions(repo_root, site_id),
            }

    local_connection = project.manifest.get("backend", {}).get("cms", {}).get("local_connection", {})
    connection_mode = str(local_connection.get("mode") or "").strip().lower()
    if connection_mode == "overwrite_existing":
        action = "overwritten"
    elif connection_mode == "create_new":
        action = "configured"
    elif existing or payload.get("keep_existing"):
        action = "reused"
    else:
        action = "configured"

    return {
        "ok": True,
        "source": "backend",
        "layer_id": "cms",
        "action": action,
        "provider": "directus",
        "runtime": "local" if setup_result else "deployed",
        "ready": bool(setup_result.get("verified")) if isinstance(setup_result, dict) else False,
        "directus_connection": local_connection,
        "directus_setup": setup_result,
        "site": project.to_dict(repo_root),
        "contract": blog_install_assumptions(repo_root, site_id),
    }


def _install_blog_feature_layer(repo_root: Path, site_id: object, payload: dict[str, Any]) -> dict[str, Any]:
    project = load_website_project(repo_root, site_id)
    _require_database_ready(project)
    _require_directus_configured(project)
    existing = _stored_layer_status(project, "blog") in {"configured", "pending_deploy", "hydrating", "ready"}
    project = _mark_blog_layer(repo_root, site_id, "blog", action="pending_deploy")
    hydration = _hydrate_blog_runtime_contract(repo_root, site_id, install_status="pending_deploy")
    project = load_website_project(repo_root, site_id)
    return {
        "ok": True,
        "source": "backend",
        "layer_id": "blog",
        "action": "reused" if existing or payload.get("keep_existing") else "pending_deploy",
        "feature": "blog",
        "runtime": "deployed",
        "ready": False,
        "blog": _blog_response_contract(project, ready=False),
        "actions": _blog_runtime_actions(project),
        "hydration": hydration,
        "site": project.to_dict(repo_root),
        "contract": blog_install_assumptions(repo_root, site_id),
    }



def _require_database_ready(project: WebsiteProject) -> None:
    state = _blog_install_state(project)
    if not state["sqlite_connection"]:
        raise BlogInstallError("Install the SQLite database layer before this Blog layer.")
    if not state["sqlite_source_exists"]:
        raise BlogInstallError("SQLite database layer is configured but runtime/websites/<site_id>/data/content.sqlite is missing.")


def _require_directus_configured(project: WebsiteProject) -> None:
    if not _is_directus_configured(project):
        raise BlogInstallError("Configure the Directus CMS layer before enabling the Blog runtime layer.")


def _merge_directus_manifest_contract(site_id: str, existing: dict[str, Any]) -> dict[str, Any]:
    defaults = _directus_manifest_contract(site_id)
    merged = {**defaults, **existing}
    for key in ("service", "storage", "schema", "permissions"):
        default_child = defaults.get(key) if isinstance(defaults.get(key), dict) else {}
        existing_child = existing.get(key) if isinstance(existing.get(key), dict) else {}
        merged[key] = {**default_child, **existing_child}
    return merged


def _mark_blog_layer(repo_root: Path, site_id: object, layer_id: str, *, action: str) -> WebsiteProject:
    project = load_website_project(repo_root, site_id)
    manifest = dict(project.manifest)

    install = manifest.get("blog_install")
    if not isinstance(install, dict):
        install = {}
    install["feature"] = "blog"
    install["selected_stack"] = {"blog": "blog", "cms": "directus", "database": "sqlite"}
    install["install_order"] = list(BLOG_LAYER_INSTALL_ORDER)
    install["base_deploy_policy"] = "sqlite_source_seeded_once; deploy publishes unchanged artifacts only once"
    install["runtime_dependency_policy"] = "deploy_reuses_existing_dependencies_when_safe"
    layers = install.get("layers")
    if not isinstance(layers, dict):
        layers = {}

    previous = layers.get(layer_id)
    previous_status = previous.get("status") if isinstance(previous, dict) else ""
    if layer_id == "cms":
        status = "ready" if previous_status == "ready" else "configured"
    elif layer_id == "blog":
        status = "ready" if previous_status == "ready" else "pending_deploy"
    else:
        status = "reused" if action == "reused" else "installed"

    layers[layer_id] = {
        "status": status,
        "action": action,
        "updated_at": utc_now(),
    }
    install["layers"] = layers

    if layer_id == "database":
        runtime_preparation = install.get("runtime_preparation")
        if not isinstance(runtime_preparation, dict):
            runtime_preparation = {}
        sqlite_state = _blog_install_state(project)
        sqlite_ready = bool(sqlite_state["sqlite_connection"] and sqlite_state["sqlite_source_exists"])
        sqlite_marker: dict[str, Any] = {
            "status": "ready" if sqlite_ready else "pending",
            "verified": sqlite_ready,
            "updated_at": utc_now(),
        }
        if sqlite_state["sqlite_source_path"] is not None:
            sqlite_marker["source"] = _repo_relative_or_abs(repo_root, sqlite_state["sqlite_source_path"])
        runtime_preparation["sqlite_database"] = sqlite_marker
        install["runtime_preparation"] = runtime_preparation

    if layer_id == "cms":
        runtime_preparation = install.get("runtime_preparation")
        if not isinstance(runtime_preparation, dict):
            runtime_preparation = {}
        existing_directus = runtime_preparation.get("directus_service")
        directus_marker = dict(existing_directus) if isinstance(existing_directus, dict) else {}
        directus_marker.update(
            {
                "status": "pending_deploy",
                "requested": True,
                "verified": False,
                "provider": "directus",
                "updated_at": utc_now(),
            }
        )
        runtime_preparation["directus_service"] = directus_marker
        install["runtime_preparation"] = runtime_preparation

    manifest["blog_install"] = install

    features = manifest.get("features")
    if not isinstance(features, dict):
        features = {}
    existing_blog_feature = features.get("blog")
    blog_feature = dict(existing_blog_feature) if isinstance(existing_blog_feature, dict) else {}
    previous_feature_status = str(blog_feature.get("install_status") or "").strip()
    feature_status = previous_feature_status if previous_feature_status else "pending_deploy"
    if layer_id == "blog":
        feature_status = "ready" if status == "ready" else "pending_deploy"
        blog_feature.update(_blog_feature_contract(install_status=feature_status))
    else:
        runtime_lane = str(blog_feature.get("runtime_lane") or project.lane or "local").strip() or "local"
        safe_feature = _blog_intent_contract(runtime_lane=runtime_lane)
        safe_feature["install_status"] = feature_status
        blog_feature.update(safe_feature)
        blog_feature["enabled"] = False
        blog_feature.pop("content_runtime", None)
        blog_feature.pop("routes", None)
        blog_feature.pop("content", None)
        blog_feature.pop("source_files", None)
    features["blog"] = blog_feature
    manifest["features"] = features

    if layer_id in {"cms", "blog"}:
        backend = manifest.get("backend")
        if not isinstance(backend, dict):
            backend = {}
        cms = backend.get("cms")
        if not isinstance(cms, dict):
            cms = {}
        cms = _merge_directus_manifest_contract(project.id, cms)
        backend["cms"] = cms
        manifest["backend"] = backend

    manifest["updated_at"] = utc_now()
    write_json(project.path / "site.json", manifest)
    return load_website_project(repo_root, site_id)



def _blog_runtime_plan(project: WebsiteProject) -> dict[str, Any]:
    feature = _blog_feature_value(project)
    selected = _blog_feature_selected(project)
    enabled = _is_blog_feature_configured(project)
    install_status = _blog_feature_install_status(project)

    state = _blog_install_state(project)
    sqlite_ready = bool(state["sqlite_connection"] and state["sqlite_source_exists"])
    gates = {
        "sqlite_database": sqlite_ready or _blog_runtime_gate_marker_ready(project, "sqlite_database"),
        "directus_service": _blog_runtime_gate_ready(project, "directus_service"),
        "directus_schema": _blog_runtime_gate_ready(project, "directus_schema"),
        "directus_permissions": _blog_runtime_gate_ready(project, "directus_permissions"),
        "hub_runtime_wiring": _blog_runtime_gate_ready(project, "hub_runtime_wiring"),
        "published_read_verification": _blog_runtime_gate_ready(project, "published_read_verification"),
        "draft_protection_verification": _blog_runtime_gate_ready(project, "draft_protection_verification"),
    }
    failed = selected and any(_blog_runtime_gate_failed(project, gate) for gate in BLOG_RUNTIME_PLAN_GATES)
    missing = [gate for gate in BLOG_RUNTIME_PLAN_GATES if selected and not gates[gate]]
    all_gates_ready = selected and not missing
    live_contract_ready = (
        enabled
        and install_status == "ready"
        and _trusted_blog_content_runtime(project) == "deployed"
    )

    if not selected and not enabled:
        plan_state = "not_selected"
    elif failed:
        plan_state = "failed"
    elif live_contract_ready and all_gates_ready:
        plan_state = "ready"
    elif not enabled and all_gates_ready:
        plan_state = "ready_to_promote"
    elif selected and not enabled and any(gates.values()):
        plan_state = "preparing"
    elif selected and not enabled:
        plan_state = "intent_only"
    else:
        plan_state = "preparing"

    return {
        "selected": selected,
        "enabled": enabled,
        "install_status": install_status,
        "state": plan_state,
        "needs_database": selected and not gates["sqlite_database"],
        "sqlite_ready": gates["sqlite_database"],
        "needs_directus_service": selected and not gates["directus_service"],
        "directus_configured": gates["directus_service"],
        "directus_running": gates["directus_service"],
        "needs_schema": selected and not gates["directus_schema"],
        "schema_installed": gates["directus_schema"],
        "needs_permissions": selected and not gates["directus_permissions"],
        "permissions_installed": gates["directus_permissions"],
        "site_wired": gates["hub_runtime_wiring"],
        "published_read_verified": gates["published_read_verification"],
        "draft_protection_verified": gates["draft_protection_verification"],
        "ready_for_promotion": bool(not enabled and all_gates_ready and not failed),
        "missing": missing,
        "trusted_gates": dict(gates),
        "source": "blog_runtime_plan_v1",
        "stale_runtime_fields_trusted": False,
        "feature_value_type": type(feature).__name__,
    }


def _blog_feature_value(project: WebsiteProject) -> object:
    features = project.manifest.get("features")
    if not isinstance(features, dict):
        return None
    return features.get("blog")


def _blog_feature_selected(project: WebsiteProject) -> bool:
    blog = _blog_feature_value(project)
    if isinstance(blog, dict):
        return blog.get("selected") is True or blog.get("enabled") is True
    return blog is True


def _blog_feature_install_status(project: WebsiteProject) -> str:
    blog = _blog_feature_value(project)
    if isinstance(blog, dict):
        status = str(blog.get("install_status") or "").strip()
        if status:
            return status
    return _stored_layer_status(project, "blog")


def _trusted_blog_content_runtime(project: WebsiteProject) -> str:
    if not _is_blog_feature_configured(project):
        return "disabled"

    runtime = project.manifest.get("runtime")
    runtime_state = str(runtime.get("content_runtime") or "").strip().lower() if isinstance(runtime, dict) else ""

    runtime_config = project.manifest.get("runtime_config")
    content = runtime_config.get("content") if isinstance(runtime_config, dict) else {}
    if not isinstance(content, dict):
        content = {}
    config_state = str(content.get("content_runtime") or "").strip().lower()
    provider = str(content.get("provider") or "").strip().lower()
    collection = str(content.get("collection") or "").strip().lower()

    if runtime_state == "deployed" or (config_state == "deployed" and provider == "directus" and collection == "posts"):
        return "deployed"
    return "disabled"


def _blog_runtime_gate_ready(project: WebsiteProject, gate: str) -> bool:
    if _blog_runtime_gate_marker_ready(project, gate):
        return True

    cms = _directus_cms_contract(project)
    if gate == "directus_service":
        return _status_value(cms.get("service_status")) == "ready"

    if gate == "directus_schema":
        schema = cms.get("schema") if isinstance(cms.get("schema"), dict) else {}
        return _status_value(cms.get("schema_status")) == "ready" or _status_value(schema.get("status")) == "ready"

    if gate == "directus_permissions":
        permissions = cms.get("permissions") if isinstance(cms.get("permissions"), dict) else {}
        return _status_value(cms.get("permissions_status")) == "ready" or _status_value(permissions.get("status")) == "ready"

    verification = _last_blog_runtime_verification(project)
    if gate == "published_read_verification":
        return bool(verification.get("ok") and verification.get("published_read_ok"))

    if gate == "draft_protection_verification":
        return bool(verification.get("ok") and verification.get("draft_protected"))

    if gate == "hub_runtime_wiring":
        return _blog_runtime_gate_marker_ready(project, gate) or (
            _blog_feature_install_status(project) == "ready"
            and _trusted_blog_content_runtime(project) == "deployed"
        )

    return False


def _blog_runtime_gate_failed(project: WebsiteProject, gate: str) -> bool:
    if _blog_runtime_gate_marker_failed(project, gate):
        return True

    if gate == "sqlite_database":
        return _stored_layer_status(project, "database") == "failed"

    if gate == "directus_service":
        return _status_value(_directus_cms_contract(project).get("service_status")) == "failed"

    if gate == "directus_schema":
        cms = _directus_cms_contract(project)
        schema = cms.get("schema") if isinstance(cms.get("schema"), dict) else {}
        return _status_value(cms.get("schema_status")) == "failed" or _status_value(schema.get("status")) == "failed"

    if gate == "directus_permissions":
        cms = _directus_cms_contract(project)
        permissions = cms.get("permissions") if isinstance(cms.get("permissions"), dict) else {}
        return _status_value(cms.get("permissions_status")) == "failed" or _status_value(permissions.get("status")) == "failed"

    verification = _last_blog_runtime_verification(project)
    if gate in {"published_read_verification", "draft_protection_verification"} and verification.get("ok") is False:
        return True

    return False


def _blog_runtime_gate_marker_ready(project: WebsiteProject, gate: str) -> bool:
    marker = _blog_runtime_gate_marker(project, gate)
    if marker is True:
        return True
    if not isinstance(marker, dict):
        return False
    if marker.get("verified") is True or marker.get("ready") is True or marker.get("ok") is True:
        return True
    return _status_value(marker.get("status")) in {"ready", "verified", "complete", "completed", "passed"}


def _blog_runtime_gate_marker_failed(project: WebsiteProject, gate: str) -> bool:
    marker = _blog_runtime_gate_marker(project, gate)
    if marker is False:
        return True
    if not isinstance(marker, dict):
        return False
    if marker.get("ok") is False:
        return True
    return _status_value(marker.get("status")) in {"failed", "error", "blocked"}


def _blog_runtime_gate_marker(project: WebsiteProject, gate: str) -> object:
    install = project.manifest.get("blog_install")
    if not isinstance(install, dict):
        return None

    aliases = BLOG_RUNTIME_PLAN_MARKER_ALIASES.get(gate, (gate,))
    sections = [
        install.get("runtime_preparation"),
        install.get("runtime_checks"),
        install.get("runtime_verification"),
        install.get("runtime_plan"),
    ]
    for section in sections:
        if not isinstance(section, dict):
            continue
        for alias in aliases:
            if alias in section:
                return section.get(alias)

    gates = install.get("gates")
    if isinstance(gates, dict):
        for alias in aliases:
            if alias in gates:
                return gates.get(alias)

    return None


def _last_blog_runtime_verification(project: WebsiteProject) -> dict[str, Any]:
    install = project.manifest.get("blog_install")
    if not isinstance(install, dict):
        return {}
    verification = install.get("last_runtime_verification")
    return verification if isinstance(verification, dict) else {}


def _directus_cms_contract(project: WebsiteProject) -> dict[str, Any]:
    backend = project.manifest.get("backend")
    if not isinstance(backend, dict):
        return {}
    cms = backend.get("cms")
    if not isinstance(cms, dict):
        return {}
    if str(cms.get("provider") or "").strip().lower() != "directus":
        return {}
    return cms


def _status_value(value: object) -> str:
    return str(value or "").strip().lower()


def _blog_install_state(project: WebsiteProject) -> dict[str, Any]:
    db = _maybe_sqlite_content_connection(project)
    source_path = resolve_source_db_path(project, db) if db is not None else project.path / "data" / "content.sqlite"
    return {
        "sqlite_connection": db,
        "sqlite_source_path": source_path,
        "sqlite_source_exists": source_path.is_file(),
    }


def _maybe_sqlite_content_connection(project: WebsiteProject):
    for db in sqlite_database_connections(project):
        if db.name == "content":
            return db
    return None


def _sqlite_content_connection(project: WebsiteProject):
    db = _maybe_sqlite_content_connection(project)
    if db is None:
        raise BlogInstallError("Website has no content SQLite connection.")
    return db


def _blog_intent_contract(*, runtime_lane: str = "local") -> dict[str, Any]:
    lane = str(runtime_lane or "local").strip() or "local"
    return {
        "selected": True,
        "enabled": False,
        "cms": "directus",
        "database": "sqlite",
        "runtime_lane": lane,
        "install_status": "pending_deploy",
        "install_order": list(BLOG_LAYER_INSTALL_ORDER),
    }


def _looks_like_blog_managed_directus_cms(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    provider = str(value.get("provider") or "").strip().lower()
    if provider != "directus":
        return False
    database_connection = str(value.get("database_connection") or "").strip().lower()
    schema = value.get("schema") if isinstance(value.get("schema"), dict) else {}
    permissions = value.get("permissions") if isinstance(value.get("permissions"), dict) else {}
    collection = str(schema.get("collection") or "").strip().lower()
    has_blog_permissions = (
        permissions.get("public_read_published_posts") is True
        or permissions.get("public_read_files") is True
    )
    return database_connection == "content" or collection == "posts" or has_blog_permissions


def _looks_like_blog_sqlite_content_connection(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    adapter = str(value.get("adapter") or "").strip().lower()
    artifact = str(value.get("artifact") or "").strip().replace("\\", "/")
    source_path = str(value.get("path") or "").strip().replace("\\", "/")
    return adapter == "sqlite" and (
        artifact in {"data/content.sqlite", "./data/content.sqlite"}
        or source_path in {"data/content.sqlite", "./data/content.sqlite"}
    )


def _clear_blog_runtime_state_for_intent(manifest: dict[str, Any]) -> dict[str, Any]:
    """Remove stale Blog runtime/deploy state when the user is only selecting Blog intent."""

    runtime_config = manifest.get("runtime_config")
    if isinstance(runtime_config, dict):
        content = runtime_config.get("content")
        if isinstance(content, dict):
            provider = str(content.get("provider") or "").strip().lower()
            runtime_state = str(content.get("content_runtime") or "").strip().lower()
            collection = str(content.get("collection") or "").strip().lower()
            if provider == "directus" or runtime_state == "deployed" or collection == "posts":
                runtime_config.pop("content", None)
        if not runtime_config:
            manifest.pop("runtime_config", None)
        else:
            manifest["runtime_config"] = runtime_config

    runtime = manifest.get("runtime")
    if isinstance(runtime, dict):
        runtime_state = str(runtime.get("content_runtime") or "").strip().lower()
        if runtime_state == "deployed":
            runtime.pop("content_runtime", None)
        if not runtime:
            manifest.pop("runtime", None)
        else:
            manifest["runtime"] = runtime

    backend = manifest.get("backend")
    if isinstance(backend, dict):
        if _looks_like_blog_managed_directus_cms(backend.get("cms")):
            backend.pop("cms", None)
        databases = backend.get("databases")
        if isinstance(databases, dict):
            connections = databases.get("connections")
            if isinstance(connections, dict):
                content = connections.get("content")
                if _looks_like_blog_sqlite_content_connection(content):
                    connections.pop("content", None)
                if not connections:
                    databases.pop("connections", None)
                else:
                    databases["connections"] = connections
            if not databases:
                backend.pop("databases", None)
            else:
                backend["databases"] = databases
        if not backend:
            manifest["backend"] = {}
        else:
            manifest["backend"] = backend

    install = manifest.get("blog_install")
    if isinstance(install, dict):
        manifest.pop("blog_install", None)
    return manifest


def _blog_feature_contract(*, install_status: str = "pending_deploy") -> dict[str, Any]:
    status = str(install_status or "pending_deploy").strip() or "pending_deploy"
    return {
        "enabled": True,
        "cms": "directus",
        "database": "sqlite",
        "content_runtime": "deployed",
        "install_status": status,
        "install_order": list(BLOG_LAYER_INSTALL_ORDER),
        "routes": dict(BLOG_ROUTES),
        "content": {
            "provider": "directus",
            "collection": "posts",
            "public_fields": list(BLOG_PUBLIC_FIELDS),
            "published_filter": {"status": "published"},
            "draft_safe": True,
        },
        "source_files": list(BLOG_SOURCE_FILES),
    }


def _directus_manifest_contract(site_id: str) -> dict[str, Any]:
    return {
        "provider": "directus",
        "required": True,
        "runtime": "deployed",
        "database_connection": "content",
        "service": {
            "kind": "directus",
            "image": "directus/directus:11.5.1",
            "internal_url": f"http://{site_id}-directus:8055",
            "public_url": "",
            "admin_secret_ref": "directus_admin_token",
        },
        "storage": {
            "database_volume": f"{site_id}_directus_database",
            "uploads_volume": f"{site_id}_directus_uploads",
        },
        "schema": {
            "collection": "posts",
            "status": "pending_deploy",
        },
        "permissions": {
            "public_read_published_posts": True,
            "public_read_files": True,
            "status": "pending_deploy",
        },
        "schema_status": "pending_deploy",
        "permissions_status": "pending_deploy",
        "uploads_status": "pending_deploy",
    }


def _blog_runtime_contract(project: WebsiteProject) -> dict[str, Any]:
    return {
        "content_runtime": "deployed",
        "provider": "directus",
        "site_id": project.id,
        "lane": project.lane or "local",
        "collection": "posts",
        "cms_url_ref": "backend.cms.service.internal_url",
        "cms_public_url_ref": "backend.cms.service.public_url",
        "published_filter": {"status": "published"},
    }


def _blog_response_contract(project: WebsiteProject, *, ready: bool) -> dict[str, Any]:
    feature = project.manifest.get("features", {}).get("blog") if isinstance(project.manifest.get("features"), dict) else {}
    feature = feature if isinstance(feature, dict) else {}
    status = str(feature.get("install_status") or _stored_layer_status(project, "blog") or "planned")
    return {
        "ready": bool(ready),
        "install_status": status,
        "routes": dict(feature.get("routes") if isinstance(feature.get("routes"), dict) else BLOG_ROUTES),
        "content": dict(feature.get("content") if isinstance(feature.get("content"), dict) else _blog_feature_contract()["content"]),
        "source_files": list(feature.get("source_files") if isinstance(feature.get("source_files"), list) else BLOG_SOURCE_FILES),
    }


def _blog_runtime_actions(project: WebsiteProject) -> dict[str, Any]:
    platform = project.manifest.get("local_platform")
    lanes = platform.get("lanes") if isinstance(platform, dict) else {}
    local_lane = lanes.get(project.lane) if isinstance(lanes, dict) else None
    if not isinstance(local_lane, dict):
        local_lane = lanes.get("local") if isinstance(lanes, dict) else None
    base_url = ""
    if isinstance(local_lane, dict):
        base_url = str(local_lane.get("url") or "")
    if not base_url and isinstance(platform, dict):
        base_url = str(platform.get("local_url") or "")
    base_url = base_url.rstrip("/")
    cms = project.manifest.get("backend", {}).get("cms") if isinstance(project.manifest.get("backend"), dict) else {}
    service = cms.get("service") if isinstance(cms, dict) else {}
    directus_url = str(service.get("public_url") or "") if isinstance(service, dict) else ""
    return {
        "open_blog": {
            "enabled": _is_blog_feature_configured(project),
            "path": "/blog",
            "url": f"{base_url}/blog" if base_url else "/blog",
        },
        "open_directus": {
            "enabled": _is_directus_configured(project),
            "url": directus_url,
            "credential_note": "Use the configured Directus admin credentials for this local runtime.",
        },
        "edit_blog_code": {
            "enabled": _is_blog_feature_configured(project),
            "files": list(BLOG_SOURCE_FILES),
        },
        "view_runtime_config": {
            "enabled": _is_blog_feature_configured(project),
            "manifest_path": "site.json#runtime_config.content",
        },
    }


def _write_blog_runtime_source_files(project: WebsiteProject) -> list[str]:
    runtime_config_js = """// Generated by Main Computer Blog install.
// No secrets belong in this client-visible file.
export const blogRuntimeConfig = {
  provider: "directus",
  contentRuntime: "deployed",
  collection: "posts",
  routes: {
    index: "/blog",
    post: "/blog/:slug"
  },
  publicFields: [
    "id",
    "status",
    "slug",
    "title",
    "excerpt",
    "body",
    "published_on",
    "read_time_minutes",
    "is_legacy"
  ],
  publishedFilter: {
    status: "published"
  }
};
"""

    directus_client_js = """// Generated by Main Computer Blog install.
// The deployed site server resolves DIRECTUS_URL from runtime env.
// Browser-side code should call the site /blog routes unless an explicit
// preview/session boundary is added later.
import { blogRuntimeConfig } from "./runtime-config.js";

export function directusPostsQuery(slug = "") {
  const params = new URLSearchParams();
  params.set("fields", blogRuntimeConfig.publicFields.join(","));
  params.set("sort", "-published_on,-id");
  params.set("filter[status][_eq]", "published");
  if (slug) params.set("filter[slug][_eq]", slug);
  return `/items/${blogRuntimeConfig.collection}?${params.toString()}`;
}

export async function listPublishedPosts(fetchJson) {
  const payload = await fetchJson(directusPostsQuery());
  return Array.isArray(payload?.data) ? payload.data : [];
}

export async function getPublishedPostBySlug(fetchJson, slug) {
  const payload = await fetchJson(directusPostsQuery(slug));
  const rows = Array.isArray(payload?.data) ? payload.data : [];
  return rows[0] || null;
}
"""

    list_posts_js = """// Generated by Main Computer Blog install.
import { listPublishedPosts } from "../content/directus-client.js";

export async function listBlogPosts(fetchJson) {
  return listPublishedPosts(fetchJson);
}
"""

    get_post_js = """// Generated by Main Computer Blog install.
import { getPublishedPostBySlug } from "../content/directus-client.js";

export async function getBlogPostBySlug(fetchJson, slug) {
  return getPublishedPostBySlug(fetchJson, slug);
}
"""
    files = {
        "src/content/runtime-config.js": runtime_config_js,
        "src/content/directus-client.js": directus_client_js,
        "src/blog/list-posts.js": list_posts_js,
        "src/blog/get-post-by-slug.js": get_post_js,
    }
    written: list[str] = []
    for relative, source in files.items():
        path = project.path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        written.append(relative)
    return written


def _hydrate_blog_runtime_contract(repo_root: Path, site_id: object, *, install_status: str) -> dict[str, Any]:
    project = load_website_project(repo_root, site_id)
    manifest = dict(project.manifest)

    features = manifest.get("features")
    if not isinstance(features, dict):
        features = {}
    blog_feature = dict(features.get("blog")) if isinstance(features.get("blog"), dict) else {}
    blog_feature.update(_blog_feature_contract(install_status=install_status))
    blog_feature["hydrated_at"] = utc_now()
    features["blog"] = blog_feature
    manifest["features"] = features

    runtime_config = manifest.get("runtime_config")
    if not isinstance(runtime_config, dict):
        runtime_config = {}
    runtime_config["content"] = _blog_runtime_contract(project)
    manifest["runtime_config"] = runtime_config

    install = manifest.get("blog_install")
    if not isinstance(install, dict):
        install = {}
    install["runtime_config_written_at"] = utc_now()
    install["blog_source_files"] = list(BLOG_SOURCE_FILES)
    manifest["blog_install"] = install
    manifest["updated_at"] = utc_now()

    write_json(project.path / "site.json", manifest)
    written = _write_blog_runtime_source_files(load_website_project(repo_root, site_id))
    return {
        "ok": True,
        "install_status": install_status,
        "runtime_config": "runtime_config.content",
        "source_files": written,
    }


def mark_blog_runtime_from_deploy(repo_root: Path, site_id: object, verification: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mark the Blog layer ready only after the deployed site runtime reports its Blog bridge works."""

    project = load_website_project(repo_root, site_id)
    if not _is_blog_feature_configured(project):
        return {"ok": True, "skipped": True, "reason": "blog_not_configured"}

    payload = verification if isinstance(verification, dict) else {}
    runtime_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
    blog_payload = runtime_payload.get("blog") if isinstance(runtime_payload.get("blog"), dict) else runtime_payload

    published_read_ok = bool(
        blog_payload.get("published_read_ok")
        or blog_payload.get("blogReadOk")
        or runtime_payload.get("blogReadOk")
    )
    draft_protected = blog_payload.get("draft_protected")
    if draft_protected is None:
        draft_protected = runtime_payload.get("draftProtected")
    draft_protected = bool(draft_protected)
    route_ok = bool(payload.get("ok") and runtime_payload.get("ok", True))
    ready = route_ok and published_read_ok and draft_protected

    status = "ready" if ready else "failed"
    hydration = _hydrate_blog_runtime_contract(repo_root, site_id, install_status=status)
    project = load_website_project(repo_root, site_id)
    manifest = dict(project.manifest)

    install = manifest.get("blog_install")
    if not isinstance(install, dict):
        install = {}
    layers = install.get("layers")
    if not isinstance(layers, dict):
        layers = {}
    now = utc_now()
    cms_layer = dict(layers.get("cms")) if isinstance(layers.get("cms"), dict) else {}
    cms_layer.update({"status": "ready" if ready else cms_layer.get("status", "configured"), "updated_at": now})
    layers["cms"] = cms_layer
    blog_layer = dict(layers.get("blog")) if isinstance(layers.get("blog"), dict) else {}
    blog_layer.update({"status": status, "updated_at": now, "verification": {
        "published_read_ok": published_read_ok,
        "draft_protected": draft_protected,
        "route_ok": route_ok,
    }})
    layers["blog"] = blog_layer
    install["layers"] = layers
    install["last_runtime_verification"] = {
        "ok": ready,
        "published_read_ok": published_read_ok,
        "draft_protected": draft_protected,
        "route_ok": route_ok,
        "updated_at": now,
    }
    manifest["blog_install"] = install

    features = manifest.get("features")
    if isinstance(features, dict) and isinstance(features.get("blog"), dict):
        features["blog"]["install_status"] = status
        features["blog"]["last_verified_at"] = now
        manifest["features"] = features

    backend = manifest.get("backend")
    cms = backend.get("cms") if isinstance(backend, dict) else {}
    if isinstance(cms, dict):
        if ready:
            cms["service_status"] = "ready"
            cms["schema_status"] = "ready"
            cms["permissions_status"] = "ready"
            if isinstance(cms.get("schema"), dict):
                cms["schema"]["status"] = "ready"
            if isinstance(cms.get("permissions"), dict):
                cms["permissions"]["status"] = "ready"
        backend["cms"] = cms
        manifest["backend"] = backend

    manifest["updated_at"] = now
    write_json(project.path / "site.json", manifest)
    return {
        "ok": ready,
        "install_status": status,
        "hydration": hydration,
        "verification": install["last_runtime_verification"],
    }


def _is_directus_configured(project: WebsiteProject) -> bool:
    backend = project.manifest.get("backend")
    if not isinstance(backend, dict):
        return False
    cms = backend.get("cms")
    if not isinstance(cms, dict):
        return False
    return (
        cms.get("provider") == "directus"
        and cms.get("required") is True
        and cms.get("runtime") == "deployed"
        and cms.get("database_connection") == "content"
    )


def _is_blog_feature_configured(project: WebsiteProject) -> bool:
    features = project.manifest.get("features")
    if not isinstance(features, dict):
        return False
    blog = features.get("blog")
    if isinstance(blog, dict):
        return blog.get("enabled") is True and blog.get("cms") == "directus" and blog.get("database") == "sqlite"
    return blog is True


def _stored_layer_status(project: WebsiteProject, layer_id: str) -> str:
    install = project.manifest.get("blog_install")
    if not isinstance(install, dict):
        return "planned"
    layers = install.get("layers")
    if not isinstance(layers, dict):
        return "planned"
    layer = layers.get(layer_id)
    if not isinstance(layer, dict):
        return "planned"
    return str(layer.get("status") or "planned")


def _layer_contract(
    project: WebsiteProject,
    layer_id: str,
    status: str,
    *,
    existing_resource_detected: bool = False,
) -> dict[str, Any]:
    status = status if status and status != "planned" else "planned"
    return {
        "id": layer_id,
        "label": BLOG_LAYER_LABELS[layer_id],
        "selected_option": "sqlite" if layer_id == "database" else "directus" if layer_id == "cms" else "blog",
        "option_label": BLOG_LAYER_OPTIONS[layer_id],
        "status": status,
        "locked_reason": (
            "SQLite is currently the default supported database layer."
            if layer_id == "database"
            else "Directus is currently the supported CMS provider."
            if layer_id == "cms"
            else "Blog uses the Directus over SQLite golden path."
        ),
        "description": BLOG_LAYER_DESCRIPTIONS[layer_id],
        "existing_resource_detected": bool(existing_resource_detected),
        "overwrite_default": False,
        "overwrite_allowed": False,
        "requires_user_confirmation": False,
        "recommended_action": "reuse_when_safe" if layer_id == "database" and existing_resource_detected else "configure",
        "options": [
            {
                "id": "sqlite" if layer_id == "database" else "directus" if layer_id == "cms" else "blog",
                "label": BLOG_LAYER_OPTIONS[layer_id],
                "available": True,
                "recommended": True,
                "default": True,
            }
        ],
    }


def _validate_layer_id(value: object) -> str:
    layer = str(value or "").strip().lower()
    if layer not in set(BLOG_LAYER_INSTALL_ORDER):
        raise BlogInstallError(f"Unsupported Blog install layer: {value!r}")
    return layer


def _repo_relative_or_abs(repo_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(path)
