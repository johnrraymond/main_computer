from __future__ import annotations

import json
from pathlib import Path

import main_computer.website_project_manifest as website_project_manifest
from main_computer.blog_install import blog_install_assumptions, blog_runtime_plan, install_blog_layer, persist_blog_intent
from main_computer.sqlite_publish import configure_sqlite_database_resource, ensure_sqlite_publish_smoke_source, publish_site_sqlite_databases, sqlite_database_connections
from main_computer.website_project_manifest import create_website_project, load_website_project, publish_website


def test_blog_intent_persists_to_site_json_without_installing_runtime(tmp_path: Path) -> None:
    project = create_website_project(tmp_path, "zzzzz", "Blog Site")
    manifest = dict(project.manifest)
    manifest["runtime"] = {"content_runtime": "deployed"}
    manifest["runtime_config"] = {
        "content": {
            "provider": "directus",
            "content_runtime": "deployed",
            "collection": "posts",
        }
    }
    manifest["backend"] = {
        "cms": {
            "provider": "directus",
            "required": True,
            "runtime": "deployed",
            "database_connection": "content",
            "schema": {"collection": "posts", "status": "pending_deploy"},
            "permissions": {"public_read_published_posts": True, "status": "pending_deploy"},
        },
        "databases": {
            "connections": {
                "content": {
                    "adapter": "sqlite",
                    "path": "./data/content.sqlite",
                    "artifact": "data/content.sqlite",
                    "publishable": True,
                }
            }
        },
    }
    manifest["blog_install"] = {"layers": {"blog": {"status": "failed"}}}
    (project.path / "site.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = persist_blog_intent(tmp_path, "zzzzz", {"runtime_lane": "local"})

    assert result["ok"] is True
    assert result["intent"] == {
        "selected": True,
        "enabled": False,
        "cms": "directus",
        "database": "sqlite",
        "runtime_lane": "local",
        "install_status": "pending_deploy",
        "install_order": ["database", "cms", "blog"],
    }

    project = load_website_project(tmp_path, "zzzzz")
    blog_feature = project.manifest["features"]["blog"]
    assert blog_feature == result["intent"]
    assert "content_runtime" not in blog_feature
    assert "routes" not in blog_feature
    assert "content" not in blog_feature
    assert "source_files" not in blog_feature
    assert "runtime_config" not in project.manifest
    assert "content_runtime" not in project.manifest.get("runtime", {})
    assert "blog_install" not in project.manifest
    assert project.manifest.get("backend") == {}
    assert not (project.path / "data" / "content.sqlite").exists()
    assert not (project.path / "src" / "content" / "runtime-config.js").exists()
    assert not sqlite_database_connections(project)
    publish_result = publish_website(tmp_path, "zzzzz", lane="dev", dry_run=True, verify=False)
    assert publish_result["database_publish"] == []
    assert publish_result["plan"]["cms_dependency_services"] == []
    assert "blog_runtime" not in json.dumps(publish_result).lower()




def test_blog_runtime_plan_for_intent_only_explains_missing_runtime(tmp_path: Path) -> None:
    create_website_project(tmp_path, "zzzzz", "Blog Site")
    persisted = persist_blog_intent(tmp_path, "zzzzz", {"runtime_lane": "local"})

    plan = persisted["contract"]["blog_runtime_plan"]

    assert plan["source"] == "blog_runtime_plan_v1"
    assert plan["selected"] is True
    assert plan["enabled"] is False
    assert plan["state"] == "intent_only"
    assert plan["ready_for_promotion"] is False
    assert plan["needs_database"] is True
    assert plan["needs_directus_service"] is True
    assert plan["needs_schema"] is True
    assert plan["needs_permissions"] is True
    assert plan["sqlite_ready"] is False
    assert plan["directus_configured"] is False
    assert plan["site_wired"] is False
    assert plan["missing"] == [
        "sqlite_database",
        "directus_service",
        "directus_schema",
        "directus_permissions",
        "hub_runtime_wiring",
        "published_read_verification",
        "draft_protection_verification",
    ]

    assumptions = blog_install_assumptions(tmp_path, "zzzzz")
    assert assumptions["blog_runtime_plan"] == blog_runtime_plan(tmp_path, "zzzzz")


def test_blog_runtime_plan_does_not_trust_stale_deployed_runtime_fields(tmp_path: Path) -> None:
    project = create_website_project(tmp_path, "zzzzz", "Blog Site")
    manifest = dict(project.manifest)
    manifest["features"] = {
        "blog": {
            "selected": True,
            "enabled": False,
            "cms": "directus",
            "database": "sqlite",
            "install_status": "pending_deploy",
        }
    }
    manifest["backend"] = {
        "cms": {
            "provider": "directus",
            "required": True,
            "runtime": "deployed",
            "database_connection": "content",
            "service": {
                "kind": "directus",
                "internal_url": "http://zzzzz-directus:8055",
                "public_url": "http://127.0.0.1:28200",
            },
            "schema": {"collection": "posts", "status": "pending_deploy"},
            "permissions": {"public_read_published_posts": True, "status": "pending_deploy"},
        },
    }
    manifest["runtime"] = {"content_runtime": "deployed"}
    manifest["runtime_config"] = {
        "content": {
            "provider": "directus",
            "content_runtime": "deployed",
            "collection": "posts",
        }
    }
    (project.path / "site.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    plan = blog_runtime_plan(tmp_path, "zzzzz")

    assert plan["selected"] is True
    assert plan["enabled"] is False
    assert plan["state"] == "intent_only"
    assert plan["ready_for_promotion"] is False
    assert plan["directus_configured"] is False
    assert plan["directus_running"] is False
    assert plan["schema_installed"] is False
    assert plan["permissions_installed"] is False
    assert plan["site_wired"] is False
    assert plan["stale_runtime_fields_trusted"] is False
    assert "directus_service" in plan["missing"]
    assert "hub_runtime_wiring" in plan["missing"]


def test_blog_runtime_plan_reports_partial_sqlite_prep_as_preparing_not_ready(tmp_path: Path) -> None:
    create_website_project(tmp_path, "zzzzz", "Blog Site")
    persist_blog_intent(tmp_path, "zzzzz", {"runtime_lane": "local"})
    configure_sqlite_database_resource(tmp_path, "zzzzz")
    ensure_sqlite_publish_smoke_source(tmp_path, "zzzzz")

    plan = blog_runtime_plan(tmp_path, "zzzzz")

    assert plan["selected"] is True
    assert plan["enabled"] is False
    assert plan["state"] == "preparing"
    assert plan["sqlite_ready"] is True
    assert plan["needs_database"] is False
    assert plan["ready_for_promotion"] is False
    assert plan["directus_configured"] is False
    assert plan["missing"] == [
        "directus_service",
        "directus_schema",
        "directus_permissions",
        "hub_runtime_wiring",
        "published_read_verification",
        "draft_protection_verification",
    ]


def test_blog_runtime_plan_ready_to_promote_requires_explicit_trusted_markers(tmp_path: Path) -> None:
    project = create_website_project(tmp_path, "zzzzz", "Blog Site")
    persist_blog_intent(tmp_path, "zzzzz", {"runtime_lane": "local"})
    configure_sqlite_database_resource(tmp_path, "zzzzz")
    ensure_sqlite_publish_smoke_source(tmp_path, "zzzzz")

    project = load_website_project(tmp_path, "zzzzz")
    manifest = dict(project.manifest)
    manifest["blog_install"] = {
        "runtime_preparation": {
            "directus_service": {"status": "ready", "verified": True},
            "directus_schema": {"status": "ready", "verified": True},
            "directus_permissions": {"status": "ready", "verified": True},
            "hub_runtime_wiring": {"status": "ready", "verified": True},
            "published_read_verification": {"status": "ready", "verified": True},
            "draft_protection_verification": {"status": "ready", "verified": True},
        }
    }
    (project.path / "site.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    plan = blog_runtime_plan(tmp_path, "zzzzz")

    assert plan["selected"] is True
    assert plan["enabled"] is False
    assert plan["state"] == "ready_to_promote"
    assert plan["ready_for_promotion"] is True
    assert plan["missing"] == []
    assert all(plan["trusted_gates"].values())



def test_blog_database_layer_prepares_sqlite_without_promoting_or_configuring_directus(tmp_path: Path) -> None:
    create_website_project(tmp_path, "zzzzz", "Blog Site")
    persist_blog_intent(tmp_path, "zzzzz", {"runtime_lane": "local"})

    result = install_blog_layer(tmp_path, "zzzzz", "database", {})

    assert result["ok"] is True
    assert result["layer_id"] == "database"
    assert result["action"] == "installed"
    assert result["base_seeded"] is True

    project = load_website_project(tmp_path, "zzzzz")
    blog_feature = project.manifest["features"]["blog"]
    assert blog_feature["selected"] is True
    assert blog_feature["enabled"] is False
    assert blog_feature["install_status"] == "pending_deploy"
    assert "content_runtime" not in blog_feature
    assert "routes" not in blog_feature
    assert "content" not in blog_feature
    assert "source_files" not in blog_feature
    assert "cms" not in project.manifest.get("backend", {})
    assert "runtime_config" not in project.manifest
    assert "content_runtime" not in project.manifest.get("runtime", {})
    assert (project.path / "data" / "content.sqlite").is_file()

    runtime_preparation = project.manifest["blog_install"]["runtime_preparation"]
    assert runtime_preparation["sqlite_database"]["status"] == "ready"
    assert runtime_preparation["sqlite_database"]["verified"] is True
    assert runtime_preparation["sqlite_database"]["source"] == "runtime/websites/zzzzz/data/content.sqlite"

    plan = blog_runtime_plan(tmp_path, "zzzzz")
    assert plan["state"] == "preparing"
    assert plan["sqlite_ready"] is True
    assert plan["needs_database"] is False
    assert plan["directus_configured"] is False
    assert plan["ready_for_promotion"] is False
    assert plan["missing"] == [
        "directus_service",
        "directus_schema",
        "directus_permissions",
        "hub_runtime_wiring",
        "published_read_verification",
        "draft_protection_verification",
    ]

    publish_result = publish_website(tmp_path, "zzzzz", lane="dev", dry_run=True, verify=False)
    assert publish_result["database_publish"]
    assert publish_result["plan"]["cms_dependency_services"] == []


def test_blog_install_leaves_sqlite_publishable_for_first_deploy(tmp_path: Path) -> None:
    create_website_project(tmp_path, "zzzzz", "Blog Site")

    database = install_blog_layer(tmp_path, "zzzzz", "database", {})
    assert database["ok"] is True
    assert database["action"] == "installed"
    assert database["base_seeded"] is True

    cms = install_blog_layer(tmp_path, "zzzzz", "cms", {})
    assert cms["ok"] is True

    blog = install_blog_layer(tmp_path, "zzzzz", "blog", {})
    assert blog["ok"] is True

    project = load_website_project(tmp_path, "zzzzz")
    blog_feature = project.manifest.get("features", {}).get("blog")
    assert blog_feature["enabled"] is True
    assert blog_feature["cms"] == "directus"
    assert blog_feature["database"] == "sqlite"
    assert blog_feature["content_runtime"] == "deployed"
    assert blog_feature["install_status"] == "pending_deploy"
    assert blog_feature["routes"] == {"index": "/blog", "post": "/blog/:slug"}
    assert blog_feature["content"]["provider"] == "directus"
    assert blog_feature["content"]["collection"] == "posts"
    assert blog_feature["content"]["published_filter"] == {"status": "published"}
    assert project.manifest["runtime_config"]["content"]["provider"] == "directus"
    assert project.manifest["runtime_config"]["content"]["cms_url_ref"] == "backend.cms.service.internal_url"
    assert (project.path / "src" / "content" / "runtime-config.js").is_file()
    assert (project.path / "src" / "content" / "directus-client.js").is_file()
    generated_client = (project.path / "src" / "content" / "directus-client.js").read_text(encoding="utf-8")
    assert "filter[status][_eq]" in generated_client
    assert "sqlite" not in generated_client.lower()

    connections = sqlite_database_connections(project)
    assert connections, "Blog install must configure at least one SQLite publishable connection"

    content = [db for db in connections if db.name == "content"]
    assert content, "Blog install must configure the content SQLite connection"

    db_path = project.path / "data" / "content.sqlite"
    assert db_path.exists(), "Blog install must create or seed runtime/websites/<site_id>/data/content.sqlite"

    result = publish_website(tmp_path, "zzzzz", lane="dev", dry_run=True, verify=False)
    assert result["database_publish"], json.dumps(result, indent=2)
    assert "source_exists" not in result["database_publish"][0]
    assert result["database_publish"][0]["resource_count"] == 1


def test_blog_sqlite_base_install_is_not_reseeded_without_explicit_choice(tmp_path: Path) -> None:
    create_website_project(tmp_path, "zzzzz", "Blog Site")

    first = install_blog_layer(tmp_path, "zzzzz", "database", {})
    assert first["ok"] is True

    second = install_blog_layer(tmp_path, "zzzzz", "database", {})
    assert second["ok"] is False
    assert second["code"] == "sqlite_reinstall_guard"
    assert second["recommended_action"] == "keep_existing"

    reused = install_blog_layer(tmp_path, "zzzzz", "database", {"keep_existing": True})
    assert reused["ok"] is True
    assert reused["action"] == "reused"
    assert reused["base_seeded"] is False

    first_publish = publish_site_sqlite_databases(tmp_path, "zzzzz", lane="dev")
    assert first_publish[0]["action"] == "created"

    second_publish = publish_site_sqlite_databases(tmp_path, "zzzzz", lane="dev")
    assert second_publish[0]["action"] == "unchanged"
    assert second_publish[0]["logical_changes"] is False


def test_blog_install_contract_reports_backend_routes_and_pending_deploy_state(tmp_path: Path) -> None:
    create_website_project(tmp_path, "zzzzz", "Blog Site")

    initial = blog_install_assumptions(tmp_path, "zzzzz")
    assert initial["source"] == "backend"
    assert initial["commit_allowed"] is False
    assert initial["sqlite"]["connection_configured"] is False

    install_blog_layer(tmp_path, "zzzzz", "database", {})
    install_blog_layer(tmp_path, "zzzzz", "cms", {})
    install_blog_layer(tmp_path, "zzzzz", "blog", {})

    ready = blog_install_assumptions(tmp_path, "zzzzz")
    assert ready["commit_allowed"] is False
    assert ready["sqlite"]["connection_configured"] is True
    assert ready["sqlite"]["source_exists"] is True
    assert ready["directus"]["configured"] is True
    assert ready["directus"]["ready"] is False
    assert ready["next_allowed_action"] == "pending_deploy_verification"
    layer_statuses = {layer["id"]: layer["status"] for layer in ready["layers"]}
    assert layer_statuses["cms"] == "configured"
    assert layer_statuses["blog"] == "pending_deploy"


def test_blog_cms_layer_runs_local_directus_setup_when_requested(tmp_path: Path, monkeypatch) -> None:
    create_website_project(tmp_path, "zzzzz", "Blog Site")
    install_blog_layer(tmp_path, "zzzzz", "database", {})

    calls: list[dict[str, object]] = []

    def fake_configure(repo_root: Path, site_id: object, **kwargs: object) -> dict[str, object]:
        calls.append({"repo_root": repo_root, "site_id": site_id, **kwargs})
        project = load_website_project(repo_root, site_id)
        return {
            "ok": True,
            "verified": True,
            "services": ["zzzzz-directus"],
            "site": project.to_dict(repo_root),
        }

    monkeypatch.setattr(website_project_manifest, "configure_website_directus_runtime", fake_configure)

    cms = install_blog_layer(
        tmp_path,
        "zzzzz",
        "cms",
        {
            "setup_local_directus": True,
            "directus_connection": {
                "mode": "create_new",
                "service_name": "zzzzz-directus",
                "database_volume": "zzzzz_directus_database_new",
                "uploads_volume": "zzzzz_directus_uploads_new",
                "public_port": 28210,
            },
        },
    )

    assert cms["ok"] is True
    assert cms["runtime"] == "local"
    assert cms["ready"] is True
    assert cms["directus_setup"]["services"] == ["zzzzz-directus"]
    assert calls == [{"repo_root": tmp_path, "site_id": "zzzzz", "verify": True, "timeout_s": 45.0}]
    assert cms["directus_connection"]["mode"] == "create_new"
    assert cms["directus_connection"]["database_volume"] == "zzzzz_directus_database_new"


def test_blog_configure_writes_directus_manifest_contract_without_claiming_ready(tmp_path: Path) -> None:
    create_website_project(tmp_path, "zzzzz", "Blog Site")
    install_blog_layer(tmp_path, "zzzzz", "database", {})
    cms = install_blog_layer(tmp_path, "zzzzz", "cms", {})

    assert cms["ok"] is True
    assert cms["provider"] == "directus"
    assert cms["runtime"] == "deployed"
    assert cms["ready"] is False

    project = load_website_project(tmp_path, "zzzzz")
    backend_cms = project.manifest["backend"]["cms"]
    assert backend_cms["provider"] == "directus"
    assert backend_cms["required"] is True
    assert backend_cms["runtime"] == "deployed"
    assert backend_cms["database_connection"] == "content"
    assert backend_cms["schema"]["collection"] == "posts"
    assert backend_cms["schema"]["status"] == "pending_deploy"
    assert backend_cms["permissions"]["public_read_published_posts"] is True
    assert backend_cms["permissions"]["public_read_files"] is True
    assert backend_cms["permissions"]["status"] == "pending_deploy"
    assert backend_cms["uploads_status"] == "pending_deploy"

    layer_statuses = project.manifest["blog_install"]["layers"]
    assert layer_statuses["cms"]["status"] == "configured"
    assert layer_statuses["cms"]["status"] != "ready"

    directus_marker = project.manifest["blog_install"]["runtime_preparation"]["directus_service"]
    assert directus_marker["status"] == "pending_deploy"
    assert directus_marker["requested"] is True
    assert directus_marker["verified"] is False

    plan = blog_runtime_plan(tmp_path, "zzzzz")
    assert plan["state"] == "preparing"
    assert plan["sqlite_ready"] is True
    assert plan["directus_configured"] is False
    assert plan["directus_running"] is False
    assert plan["ready_for_promotion"] is False
    assert "directus_service" in plan["missing"]


def test_directus_config_is_rejected_until_sqlite_dependency_exists(tmp_path: Path) -> None:
    create_website_project(tmp_path, "zzzzz", "Blog Site")

    try:
        install_blog_layer(tmp_path, "zzzzz", "cms", {})
    except Exception as exc:
        assert "SQLite database layer" in str(exc)
    else:  # pragma: no cover - asserts the intended exception path
        raise AssertionError("Directus CMS must not configure before SQLite is ready")


def test_directus_admin_secret_is_only_a_reference_in_manifest(tmp_path: Path) -> None:
    create_website_project(tmp_path, "zzzzz", "Blog Site")
    install_blog_layer(tmp_path, "zzzzz", "database", {})
    install_blog_layer(tmp_path, "zzzzz", "cms", {})

    project = load_website_project(tmp_path, "zzzzz")
    service = project.manifest["backend"]["cms"]["service"]
    assert service["admin_secret_ref"] == "directus_admin_token"
    assert "admin_token" not in project.path.joinpath("index.html").read_text(encoding="utf-8")
    assert "admin_token" not in project.path.joinpath("script.js").read_text(encoding="utf-8")


def test_blog_runtime_mark_ready_requires_published_read_and_draft_protection(tmp_path: Path) -> None:
    from main_computer.blog_install import mark_blog_runtime_from_deploy

    create_website_project(tmp_path, "zzzzz", "Blog Site")
    install_blog_layer(tmp_path, "zzzzz", "database", {})
    install_blog_layer(tmp_path, "zzzzz", "cms", {})
    install_blog_layer(tmp_path, "zzzzz", "blog", {})

    failed = mark_blog_runtime_from_deploy(
        tmp_path,
        "zzzzz",
        {"ok": True, "payload": {"ok": True, "blog": {"published_read_ok": False, "draft_protected": True}}},
    )
    assert failed["ok"] is False
    assert failed["install_status"] == "failed"

    ready = mark_blog_runtime_from_deploy(
        tmp_path,
        "zzzzz",
        {"ok": True, "payload": {"ok": True, "blog": {"published_read_ok": True, "draft_protected": True}}},
    )
    assert ready["ok"] is True
    assert ready["install_status"] == "ready"

    project = load_website_project(tmp_path, "zzzzz")
    assert project.manifest["features"]["blog"]["install_status"] == "ready"
    assert project.manifest["blog_install"]["layers"]["blog"]["status"] == "ready"
    assert project.manifest["blog_install"]["layers"]["cms"]["status"] == "ready"
