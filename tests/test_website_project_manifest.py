from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

import main_computer.website_project_manifest as website_project_manifest


ROOT = Path(__file__).resolve().parents[1]
_LOCAL_PLATFORM_ENV_PREFIX = "MAIN_COMPUTER_LOCAL_PLATFORM_"


@pytest.fixture(autouse=True)
def _isolate_local_platform_and_docker_state(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.startswith(_LOCAL_PLATFORM_ENV_PREFIX):
            monkeypatch.delenv(name, raising=False)

    monkeypatch.setattr(
        website_project_manifest,
        "_inspect_running_container_env",
        lambda container_name, timeout_s=3.0: {"checked": True, "found": False, "env": {}},
    )
    monkeypatch.setattr(
        website_project_manifest,
        "_docker_containers_publishing_port",
        lambda port, timeout_s=4.0: {"checked": True, "owners": [], "error": ""},
    )
    monkeypatch.setattr(website_project_manifest, "_site_web_port_can_bind", lambda port: True)


def _find_free_generated_port_pair() -> tuple[int, int]:
    for prod_port in range(32000, 34000, 2):
        first = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        second = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            first.bind(("127.0.0.1", prod_port))
            second.bind(("127.0.0.1", prod_port + 1))
            return prod_port, prod_port + 1
        except OSError:
            continue
        finally:
            first.close()
            second.close()
    raise AssertionError("Could not find a free generated website port pair.")


from main_computer.website_project_manifest import (
    WebsiteProjectError,
    allocate_available_website_id,
    archive_website_project,
    archived_website_ids,
    client_reachable_url,
    create_local_platform_website_project,
    create_website_project,
    ensure_website_blog_page,
    list_website_projects,
    normalize_publish_lane,
    prepare_blog_deploy_setup,
    publish_website,
    read_website_project_files,
    save_website_project_files,
    save_website_publish_target,
    website_publish_plan,
)




def test_client_reachable_url_rewrites_bind_all_addresses_for_local_probes() -> None:
    assert client_reachable_url("http://0.0.0.0:18101/") == "http://localhost:18101/"
    assert client_reachable_url("http://0.0.0.0:18101/api/site/status") == "http://localhost:18101/api/site/status"
    assert client_reachable_url("http://localhost:18101/") == "http://localhost:18101/"
    assert client_reachable_url("") == ""

def test_repository_ships_hub_site_as_v2_artifact_set() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    site_dir = repo_root / "runtime" / "websites" / "hub-site"

    for artifact_name in ("site.json", "index.html", "style.css", "script.js", "builder.json", "runtime.js"):
        assert (site_dir / artifact_name).exists()

    manifest = json.loads((site_dir / "site.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "hub-site"
    assert manifest["schema_version"] == 2
    assert manifest["site_model"] == "2.0"
    assert manifest["source"] == {
        "kind": "host_runtime_site",
        "path": "runtime/websites/hub-site",
    }
    assert manifest["runtime"]["content_runtime"] == "deployed"
    assert manifest["artifacts"]["required_files"] == ["site.json", "index.html", "style.css", "script.js", "builder.json", "runtime.js"]


def test_website_manifest_loader_seeds_hub_and_blog_runtime_projects(tmp_path: Path) -> None:
    projects = list_website_projects(tmp_path)

    ids = [project.id for project in projects]
    assert ids == ["blog-site", "hub-site"] or ids == ["hub-site", "blog-site"]
    assert (tmp_path / "runtime" / "websites" / "hub-site" / "site.json").exists()
    assert (tmp_path / "runtime" / "websites" / "blog-site" / "site.json").exists()

    hub = next(project for project in projects if project.id == "hub-site")
    hub_data = hub.to_dict(tmp_path)
    assert hub_data["schema_version"] == 2
    assert hub_data["site_model"] == "2.0"
    assert hub_data["source"] == {
        "kind": "host_runtime_site",
        "path": "runtime/websites/hub-site",
    }
    assert hub_data["runtime"]["content_runtime"] == "deployed"
    assert hub_data["artifacts"]["required_files"] == ["site.json", "index.html", "style.css", "script.js", "builder.json", "runtime.js"]
    assert hub_data["content"] == {
        "index_html": True,
        "style_css": True,
        "builder_json": True,
        "script_js": True,
        "runtime_js": True,
    }
    assert hub_data["local_platform"]["lanes"]["local"]["service"] == "hub-local"
    assert hub_data["local_platform"]["lanes"]["dev"]["service"] == "hub-dev"


def test_website_manifest_supports_arbitrary_site_creation_and_safe_save(tmp_path: Path) -> None:
    project = create_website_project(tmp_path, "portfolio-site", "Portfolio Site")
    assert project.id == "portfolio-site"
    assert project.manifest["schema_version"] == 2
    assert project.manifest["site_model"] == "2.0"
    assert project.manifest["source"] == {
        "kind": "host_runtime_site",
        "path": "runtime/websites/portfolio-site",
    }
    for artifact_name in ("site.json", "index.html", "style.css", "script.js", "builder.json", "runtime.js"):
        assert (tmp_path / "runtime" / "websites" / "portfolio-site" / artifact_name).exists()

    saved = save_website_project_files(
        tmp_path,
        "portfolio-site",
        html="<h1>Portfolio</h1>\n",
        css="body { font-family: system-ui; }\n",
        builder='{"version": 1}\n',
    )
    assert saved.id == "portfolio-site"

    payload = read_website_project_files(tmp_path, "portfolio-site")
    assert payload["html"] == "<h1>Portfolio</h1>\n"
    assert "font-family" in payload["css"]
    builder_payload = json.loads(payload["builder"])
    assert builder_payload["version"] == 1
    assert builder_payload["page_runtime"]["id"] == "default"
    assert "WebsiteBuilderRuntime" in payload["runtime_js"]
    assert (tmp_path / "runtime" / "websites" / "portfolio-site" / "runtime.js").exists()

    with pytest.raises(WebsiteProjectError):
        create_website_project(tmp_path, "../escape", "Escape")


def test_ensure_website_blog_page_creates_combined_page_and_assets(tmp_path: Path) -> None:
    create_website_project(tmp_path, "writer-site", "Writer Site")
    site_dir = tmp_path / "runtime" / "websites" / "writer-site"

    result = ensure_website_blog_page(tmp_path, "writer-site")

    assert result["ok"] is True
    assert result["created"] is True
    assert result["repo_relative_path"] == "runtime/websites/writer-site/blog/index.html"
    blog_html = (site_dir / "blog" / "index.html").read_text(encoding="utf-8")
    assert "Main Computer generated blog page" in blog_html
    assert 'data-mc-widget="blog-list"' in blog_html
    assert 'data-mc-widget="blog-post-viewer"' in blog_html
    assert 'data-route-prefix="/blog/"' in blog_html
    assert 'data-mc-blog-route-mode="index"' in blog_html
    assert 'data-page-size="50"' in blog_html
    assert 'data-search-enabled="true"' in blog_html
    assert "Allowed Fuzz" in blog_html
    assert "Results per Page" in blog_html
    assert "All</option>" not in blog_html
    style_css = (site_dir / "style.css").read_text(encoding="utf-8")
    script_js = (site_dir / "script.js").read_text(encoding="utf-8")
    assert "Main Computer blog widget styles" in style_css
    assert 'body[data-mc-blog-route-mode="detail"] .mc-blog-widget' in style_css
    assert "mcBlogPostViewerSelector" in script_js
    assert "mcBlogWidgetApplyGeneratedPageMode" in script_js
    assert "mcBlogWidgetSanitizeRichHtml" in script_js
    assert "mcBlogWidgetRenderPagination" in script_js
    assert "mcBlogMaxAllowedFuzz" in script_js
    assert "listPath" in script_js
    assert 'slug === "index.html" ? "" : slug' in script_js
    assert 'replace(/\\/index\\.html$/i, "")' in script_js
    assert "mc-blog-article-presentation-v1" in style_css
    assert "mc-blog-index-grid-layout-v1" in style_css
    assert "mc-blog-search-pagination-controls-v1" in style_css
    assert '.mc-section.mc-blog-widget[data-mc-widget="blog-list"]' in style_css
    assert "width: min(1120px, calc(100vw - 48px));" in style_css
    assert "padding: clamp(3rem, 7vw, 6rem) 0;" in style_css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in style_css
    assert ".mc-blog-widget__controls" in style_css
    assert "flex-wrap: nowrap;" in style_css
    assert "overflow-x: auto;" in style_css
    assert '.mc-blog-widget__control input[type="number"]' in style_css
    assert "width: calc(100vw - 32px);" in style_css
    assert 'routeMode.mode === "detail"' in script_js
    assert 'routeMode.mode === "index"' in script_js

    second = ensure_website_blog_page(tmp_path, "writer-site")

    assert second["ok"] is True
    assert second["reused"] is True
    assert (site_dir / "style.css").read_text(encoding="utf-8").count("Main Computer blog widget styles") == 1
    assert (site_dir / "script.js").read_text(encoding="utf-8").count("const mcBlogWidgetSelector") == 1


def test_ensure_website_blog_page_upgrades_stale_blog_widget_assets(tmp_path: Path) -> None:
    create_website_project(tmp_path, "writer-site", "Writer Site")
    site_dir = tmp_path / "runtime" / "websites" / "writer-site"
    (site_dir / "style.css").write_text(
        "body { color: navy; }\n\n/* Main Computer blog widget styles */\n.mc-blog-widget { background: white; }\n",
        encoding="utf-8",
    )
    (site_dir / "script.js").write_text(
        "window.oldBlogHydrator = true;\nconst mcBlogWidgetSelector = \'[data-mc-widget=\\\"blog-list\\\"]\';\n",
        encoding="utf-8",
    )

    result = ensure_website_blog_page(tmp_path, "writer-site")

    assert result["ok"] is True
    assert result["updated_assets"] is True
    style_css = (site_dir / "style.css").read_text(encoding="utf-8")
    script_js = (site_dir / "script.js").read_text(encoding="utf-8")
    assert "body { color: navy; }" in style_css
    assert ".mc-blog-widget[hidden]" in style_css
    assert "mc-blog-article-presentation-v1" in style_css
    assert "mc-blog-index-grid-layout-v1" in style_css
    assert "mc-blog-search-pagination-controls-v1" in style_css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in style_css
    assert "window.oldBlogHydrator = true;" in script_js
    assert "mcBlogWidgetApplyRouteModeVisibility" in script_js
    assert "mcBlogWidgetApplyGeneratedPageMode(listWidgets, postViewers)" in script_js
    assert "mcBlogWidgetSanitizeRichHtml" in script_js
    assert "mcBlogWidgetRenderPagination" in script_js
    assert 'slug === "index.html" ? "" : slug' in script_js
    assert "flex-wrap: nowrap;" in style_css

    css_count = style_css.count("Main Computer blog widget styles")
    js_count = script_js.count("const mcBlogWidgetSelector")
    second = ensure_website_blog_page(tmp_path, "writer-site")

    assert second["ok"] is True
    assert second["reused"] is True
    assert (site_dir / "style.css").read_text(encoding="utf-8").count("Main Computer blog widget styles") == css_count
    assert (site_dir / "script.js").read_text(encoding="utf-8").count("const mcBlogWidgetSelector") == js_count


def test_website_project_save_preserves_managed_blog_assets_when_homepage_has_no_widget(tmp_path: Path) -> None:
    project = create_website_project(tmp_path, "writer-site", "Writer Site")
    ensure_website_blog_page(tmp_path, project.id)

    save_website_project_files(
        tmp_path,
        project.id,
        html="<main><h1>Edited homepage</h1></main>",
        css="body { color: rebeccapurple; }\n",
        js="window.editorSaved = true;\n",
        builder='{"engine": "grapesjs", "script": "script.js"}\n',
    )

    updated = read_website_project_files(tmp_path, project.id)
    assert "<h1>Edited homepage</h1>" in updated["html"]
    assert "body { color: rebeccapurple; }" in updated["css"]
    assert "window.editorSaved = true;" in updated["js"]
    assert "Main Computer blog widget styles" in updated["css"]
    assert "mc-blog-search-pagination-controls-v1" in updated["css"]
    assert "mcBlogWidgetRenderPagination" in updated["js"]


def test_ensure_website_blog_page_upgrades_article_assets_missing_grid_layout_marker(tmp_path: Path) -> None:
    create_website_project(tmp_path, "writer-site", "Writer Site")
    site_dir = tmp_path / "runtime" / "websites" / "writer-site"
    (site_dir / "style.css").write_text(
        "/* Main Computer blog widget styles */\n"
        "/* mc-blog-article-presentation-v1 */\n"
        ".mc-blog-widget[hidden] { display: none !important; }\n"
        ".mc-section.mc-blog-widget { width: min(100%, 1120px); }\n"
        ".mc-blog-widget__items { grid-template-columns: repeat(3, minmax(0, 1fr)); }\n",
        encoding="utf-8",
    )
    (site_dir / "script.js").write_text(
        "const mcBlogWidgetSelector = '[data-mc-widget=\\\"blog-list\\\"]';\n"
        "function mcBlogWidgetApplyRouteModeVisibility() {}\n"
        "function mcBlogWidgetSanitizeRichHtml() {}\n",
        encoding="utf-8",
    )

    result = ensure_website_blog_page(tmp_path, "writer-site")

    assert result["ok"] is True
    assert result["updated_assets"] is True
    style_css = (site_dir / "style.css").read_text(encoding="utf-8")
    assert "mc-blog-index-grid-layout-v1" in style_css
    assert '.mc-section.mc-blog-widget[data-mc-widget="blog-list"]' in style_css
    assert "padding: clamp(3rem, 7vw, 6rem) 0;" in style_css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in style_css
    assert "width: calc(100vw - 32px);" in style_css


def test_cleanup_deployed_blog_content_artifacts_removes_stale_snapshot_files_and_metadata(tmp_path: Path) -> None:
    project = create_website_project(tmp_path, "writer-site", "Writer Site")
    manifest = dict(project.manifest)
    manifest["blog_deployed_content"] = {"post_count": 1}
    manifest["features"] = {
        "blog": {
            "enabled": True,
            "selected": True,
            "cms": "directus",
            "database": "sqlite",
            "content_runtime": "deployed",
            "routes": {"index": "/blog", "post": "/blog/:slug"},
            "content": {
                "provider": "directus",
                "collection": "posts",
                "deployed_data_path": "data/blog-posts.json",
                "published_post_count": 1,
                "post_slugs": ["hello-directus"],
                "generated_at": "2026-05-20T00:00:00+00:00",
            },
        }
    }
    manifest["blog_install"] = {"runtime_preparation": {"deployed_content": {"status": "ready"}}}
    (project.path / "site.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    ensure_website_blog_page(tmp_path, "writer-site")

    data_dir = project.path / "data"
    posts_dir = data_dir / "blog-posts"
    posts_dir.mkdir(parents=True)
    (data_dir / "blog-posts.json").write_text('{"posts":[{"slug":"hello-directus"}]}\n', encoding="utf-8")
    (posts_dir / "hello-directus.json").write_text('{"slug":"hello-directus"}\n', encoding="utf-8")
    detail_dir = project.path / "blog" / "hello-directus"
    detail_dir.mkdir(parents=True)
    (detail_dir / "index.html").write_text(
        '<html><body data-mc-generated-blog-page="detail"><!-- Main Computer generated blog page: detail --></body></html>',
        encoding="utf-8",
    )

    result = website_project_manifest.cleanup_deployed_blog_content_artifacts(tmp_path, "writer-site")

    assert result["ok"] is True
    assert "runtime/websites/writer-site/data/blog-posts.json" in result["removed"]
    assert "runtime/websites/writer-site/data/blog-posts" in result["removed"]
    assert "runtime/websites/writer-site/blog/hello-directus/index.html" in result["removed"]
    assert not (data_dir / "blog-posts.json").exists()
    assert not posts_dir.exists()
    assert not detail_dir.exists()

    refreshed = json.loads((project.path / "site.json").read_text(encoding="utf-8"))
    assert "blog_deployed_content" not in refreshed
    assert refreshed["features"]["blog"]["content_runtime"] == "directus"
    assert "deployed_data_path" not in refreshed["features"]["blog"]["content"]
    assert "deployed_content" not in refreshed["blog_install"]["runtime_preparation"]


def test_write_deployed_blog_content_artifacts_is_retired_cleanup_shim(tmp_path: Path) -> None:
    project = create_website_project(tmp_path, "writer-site", "Writer Site")
    data_dir = project.path / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "blog-posts.json").write_text('{"posts":[{"slug":"making-first-post"}]}\n', encoding="utf-8")

    result = website_project_manifest.write_deployed_blog_content_artifacts(
        tmp_path,
        "writer-site",
        [
            {
                "id": 1,
                "status": "published",
                "slug": "Making first post",
                "title": "First Blog Post",
                "body": "Post body",
            },
        ],
    )

    assert result["ok"] is True
    assert result["retired"] is True
    assert result["skipped"] is True
    assert result["reason"] == "deployed_blog_post_snapshots_retired"
    assert result["post_count"] == 0
    assert result["post_slugs"] == []
    assert not (data_dir / "blog-posts.json").exists()



def test_ensure_website_blog_page_refuses_custom_page_without_overwrite(tmp_path: Path) -> None:
    create_website_project(tmp_path, "writer-site", "Writer Site")
    blog_page = tmp_path / "runtime" / "websites" / "writer-site" / "blog" / "index.html"
    blog_page.parent.mkdir(parents=True)
    blog_page.write_text("<main>My custom blog</main>", encoding="utf-8")

    result = ensure_website_blog_page(tmp_path, "writer-site")

    assert result["ok"] is False
    assert result["conflict"] is True
    assert result["code"] == "existing_blog_page_detected"
    assert blog_page.read_text(encoding="utf-8") == "<main>My custom blog</main>"

    overwritten = ensure_website_blog_page(tmp_path, "writer-site", overwrite=True)

    assert overwritten["ok"] is True
    assert overwritten["overwritten"] is True
    assert 'data-mc-widget="blog-list"' in blog_page.read_text(encoding="utf-8")


def test_prepare_blog_deploy_setup_runs_only_for_deploy_lane(tmp_path: Path) -> None:
    project = create_website_project(tmp_path, "writer-site", "Writer Site")
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
    (project.path / "site.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    local = prepare_blog_deploy_setup(tmp_path, "writer-site", lane="local")

    assert local["ok"] is True
    assert local["required"] is False
    assert local["reason"] == "not_deploy_lane"
    assert not (project.path / "blog" / "index.html").exists()

    deploy = prepare_blog_deploy_setup(tmp_path, "writer-site", lane="dev")

    assert deploy["ok"] is True
    assert deploy["required"] is True
    assert deploy["page"]["created"] is True
    assert (project.path / "blog" / "index.html").exists()



def test_website_archive_moves_non_hub_site_out_of_active_list_and_registry(tmp_path: Path) -> None:
    create_local_platform_website_project(tmp_path, "client-acme", "Client Acme", allocate_unique_id=True)

    result = archive_website_project(tmp_path, "client-acme")

    assert result["ok"] is True
    assert result["site_id"] == "client-acme"
    assert not (tmp_path / "runtime" / "websites" / "client-acme").exists()
    archived_manifest = tmp_path / "runtime" / "websites-archive" / "client-acme" / "site.json"
    assert archived_manifest.exists()
    manifest = json.loads(archived_manifest.read_text(encoding="utf-8"))
    assert manifest["id"] == "client-acme"
    assert manifest["archive"]["status"] == "archived"
    assert manifest["archive"]["original_repo_relative_path"] == "runtime/websites/client-acme"
    assert manifest["archive"]["archived_repo_relative_path"] == "runtime/websites-archive/client-acme"
    assert "client-acme" in archived_website_ids(tmp_path)

    active_ids = {project.id for project in list_website_projects(tmp_path)}
    assert "client-acme" not in active_ids
    registry = json.loads((tmp_path / "runtime" / "local-platform" / "sites.json").read_text(encoding="utf-8"))
    assert "client-acme" not in registry["sites"]
    compose = (tmp_path / "deploy" / "local-platform" / "generated" / "docker-compose.websites.yml").read_text(encoding="utf-8")
    assert "client-acme-local" not in compose
    assert "client-acme-dev" not in compose


def test_website_archive_allows_seed_blog_site_without_reseeding_it(tmp_path: Path) -> None:
    list_website_projects(tmp_path)

    result = archive_website_project(tmp_path, "blog-site")

    assert result["site_id"] == "blog-site"
    assert not (tmp_path / "runtime" / "websites" / "blog-site").exists()
    assert (tmp_path / "runtime" / "websites-archive" / "blog-site" / "site.json").exists()
    assert "blog-site" in archived_website_ids(tmp_path)
    ids_after_reload = {project.id for project in list_website_projects(tmp_path)}
    assert "hub-site" in ids_after_reload
    assert "blog-site" not in ids_after_reload


def test_website_archive_rejects_hub_site(tmp_path: Path) -> None:
    list_website_projects(tmp_path)

    with pytest.raises(WebsiteProjectError, match="Hub Site is protected"):
        archive_website_project(tmp_path, "hub-site")

    assert (tmp_path / "runtime" / "websites" / "hub-site" / "site.json").exists()


def test_local_platform_create_allocates_numbered_slug_for_archived_id(tmp_path: Path) -> None:
    create_local_platform_website_project(tmp_path, "client-acme", "Client Acme", allocate_unique_id=True)
    archive_website_project(tmp_path, "client-acme")

    assert allocate_available_website_id(tmp_path, "client-acme") == "client-acme-2"
    created, _result = create_local_platform_website_project(
        tmp_path,
        "client-acme",
        "Client Acme Again",
        allocate_unique_id=True,
    )

    assert created.id == "client-acme-2"
    assert (tmp_path / "runtime" / "websites" / "client-acme-2" / "site.json").exists()
    assert (tmp_path / "runtime" / "websites-archive" / "client-acme" / "site.json").exists()


def test_website_publish_plan_does_not_touch_manifest_when_local_platform_is_current(tmp_path: Path) -> None:
    create_local_platform_website_project(tmp_path, "client-acme", "Client Acme", regenerate_compose=False)
    manifest_path = tmp_path / "runtime" / "websites" / "client-acme" / "site.json"
    before = manifest_path.read_text(encoding="utf-8")

    plan = website_publish_plan(tmp_path, "client-acme", "dev")

    after = manifest_path.read_text(encoding="utf-8")
    assert plan["service"] == "client-acme-dev"
    assert after == before


def test_website_publish_plan_is_manifest_driven_and_dry_run_safe(monkeypatch, tmp_path: Path) -> None:
    generated_prod_port, generated_dev_port = _find_free_generated_port_pair()
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_SCAN_WSL_WEBSITES", "0")
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_START", str(generated_prod_port))
    monkeypatch.setenv("MAIN_COMPUTER_LOCAL_PLATFORM_GENERATED_PORT_END", str(generated_dev_port + 4))

    list_website_projects(tmp_path)
    (tmp_path / "deploy" / "local-platform").mkdir(parents=True)
    (tmp_path / "deploy" / "local-platform" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    plan = website_publish_plan(tmp_path, "hub-site", "local")
    assert plan["service"] == "hub-local"
    assert plan["url"] == "http://localhost:18080/"
    assert plan["compose_project"] == "main-computer-website-hub-site"
    assert plan["compose_path"].endswith("runtime/websites/hub-site/.main-computer/local-platform/docker-compose.yml")
    assert "-p" in plan["command"]
    assert "main-computer-website-hub-site" in plan["command"]
    assert "--force-recreate" not in plan["command"]
    assert plan["recreate_required"] is False
    assert plan["command"][-1] == "hub-local"

    for alias in ("local", "local-prod", "prod", "production"):
        assert normalize_publish_lane(alias) == "local"
        alias_plan = website_publish_plan(tmp_path, "hub-site", alias)
        assert alias_plan["lane"] == "local"
        assert alias_plan["service"] == "hub-local"
        assert alias_plan["url"] == "http://localhost:18080/"
    assert normalize_publish_lane("dev") == "dev"

    with pytest.raises(WebsiteProjectError, match="Accept publishing setup before publishing"):
        website_publish_plan(tmp_path, "hub-site", "remote_prod")

    publish_script = tmp_path / "deploy" / "coolify" / "push_site_scp.py"
    publish_script.parent.mkdir(parents=True, exist_ok=True)
    publish_script.write_text("print('publish stub')\n", encoding="utf-8")
    save_website_publish_target(
        tmp_path,
        "hub-site",
        "remote_prod",
        site_slug="johnrraymond",
        source_path="runtime/websites/hub-site",
        remote_host="root@publish.greatlibrary.io",
        remote_root="/srv/main-computer/sites",
        domain="https://johnrraymond.example",
    )
    publish_plan = website_publish_plan(tmp_path, "hub-site", "remote_prod")
    assert publish_plan["requested_lane"] == "remote-prod"
    assert publish_plan["lane"] == "remote-prod"
    assert publish_plan["deployment_path"] == "publish_command_template"
    assert publish_plan["uses_deploy_api"] is False
    assert publish_plan["local_platform_used"] is False
    assert publish_plan["service"] == ""
    assert publish_plan["site_slug"] == "johnrraymond"
    assert publish_plan["source_path"] == "runtime/websites/hub-site"
    assert publish_plan["remote_root"] == "/srv/main-computer/sites"
    assert publish_plan["command"] == [
        "python",
        "deploy\\coolify\\push_site_scp.py",
        "johnrraymond",
        "--source",
        "runtime/websites/hub-site",
        "--host",
        "root@publish.greatlibrary.io",
        "--remote-root",
        "/srv/main-computer/sites",
    ]
    assert publish_plan["accepted_publish_target"]["site_slug"] == "johnrraymond"
    assert publish_plan["accepted_publish_target"]["source_path"] == "runtime/websites/hub-site"
    assert "johnrraymond-site:" in publish_plan["remote_coolify_compose"]
    assert "image: 'python:3.12-slim'" in publish_plan["remote_coolify_compose"]
    assert "/srv/main-computer/sites/johnrraymond:/app/sites/johnrraymond:ro" in publish_plan["remote_coolify_compose"]
    assert "/app/sites/johnrraymond/.main-computer/runtime/app.py" in publish_plan["remote_coolify_compose"]
    assert "      - '8080'" in publish_plan["remote_coolify_compose"]
    assert publish_plan["supported"] is True

    result = publish_website(tmp_path, "hub-site", lane="local", dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["plan"]["service"] == "hub-local"

    custom = create_website_project(tmp_path, "client-acme", "Client Acme")
    assert custom.id == "client-acme"
    custom_dev = publish_website(tmp_path, "client-acme", lane="dev", dry_run=True)
    assert custom_dev["ok"] is True
    assert custom_dev["plan"]["service"] == "client-acme-dev"
    assert custom_dev["plan"]["url"] == f"http://localhost:{generated_dev_port}/"
    assert custom_dev["plan"]["status_url"] == f"http://localhost:{generated_dev_port}/api/site/status"
    client_manifest = json.loads((tmp_path / "runtime" / "websites" / "client-acme" / "site.json").read_text(encoding="utf-8"))
    assert client_manifest["local_platform"]["lanes"]["dev"]["service"] == "client-acme-dev"



def _write_publish_command_stub(repo_root: Path) -> None:
    publish_script = repo_root / "deploy" / "coolify" / "push_site_scp.py"
    publish_script.parent.mkdir(parents=True, exist_ok=True)
    publish_script.write_text("print('publish stub')\n", encoding="utf-8")


def _write_site_runtime_stub(repo_root: Path, body: str = "print('runtime')\n") -> None:
    runtime = repo_root / "deploy" / "local-platform" / "site-server" / "app.py"
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text(body, encoding="utf-8")


def test_site_page_runtime_bundle_copies_default_runtime_and_injects_full_document(tmp_path: Path) -> None:
    list_website_projects(tmp_path)
    html = """<!doctype html>
<html lang="en">
<head>
  <title>Runtime test</title>
  <script src="/script.js" defer></script>
</head>
<body><main>Runtime test</main></body>
</html>
"""
    save_website_project_files(
        tmp_path,
        "hub-site",
        html=html,
        builder='{"version": 2, "page_runtime": {"id": "default"}}\n',
    )

    site_root = tmp_path / "runtime" / "websites" / "hub-site"
    saved_html = (site_root / "index.html").read_text(encoding="utf-8")
    runtime_js = (site_root / "runtime.js").read_text(encoding="utf-8")
    metadata = json.loads((site_root / ".main-computer" / "runtime" / "page-runtime.json").read_text(encoding="utf-8"))

    assert '<script src="/runtime.js" defer></script>' in saved_html
    assert saved_html.index("/runtime.js") < saved_html.index("/script.js")
    assert "WebsiteBuilderRuntime" in runtime_js
    assert metadata["runtime_id"] == "default"
    assert metadata["entrypoint"] == "runtime.js"


def test_site_page_runtime_bundle_can_package_compiled_mcel_runtime(tmp_path: Path) -> None:
    source_runtime = ROOT / "deploy" / "local-platform" / "site-runtimes" / "mcel-runtime.js"
    target_runtime = tmp_path / "deploy" / "local-platform" / "site-runtimes" / "mcel-runtime.js"
    target_runtime.parent.mkdir(parents=True, exist_ok=True)
    target_runtime.write_text(source_runtime.read_text(encoding="utf-8"), encoding="utf-8")

    list_website_projects(tmp_path)
    save_website_project_files(
        tmp_path,
        "hub-site",
        html="""<!doctype html>
<html lang="en">
<head>
  <title>MCEL Runtime test</title>
  <script src="/script.js" defer></script>
</head>
<body><main data-mc="hero" data-mc-flow="feature">MCEL Runtime test</main></body>
</html>
""",
        builder='{"version": 2, "page_runtime": {"id": "mcel"}}\n',
    )

    site_root = tmp_path / "runtime" / "websites" / "hub-site"
    runtime_js = (site_root / "runtime.js").read_text(encoding="utf-8")
    metadata = json.loads((site_root / ".main-computer" / "runtime" / "page-runtime.json").read_text(encoding="utf-8"))

    assert "MCELRuntime" in runtime_js
    assert "WebsiteBuilderRuntime" in runtime_js
    assert "function isolatedSiteCss()" in runtime_js
    assert metadata["runtime_id"] == "mcel"
    assert metadata["entrypoint"] == "runtime.js"
    assert metadata["source"] == "deploy/local-platform/site-runtimes/mcel-runtime.js"
    assert metadata["source_exists"] is True


def test_site_runtime_bundle_copies_current_runtime_inside_site_directory(tmp_path: Path) -> None:
    list_website_projects(tmp_path)
    _write_site_runtime_stub(tmp_path, "print('runtime v1')\n")

    bundle = website_project_manifest.ensure_site_runtime_bundle(tmp_path, "hub-site")

    entrypoint = tmp_path / "runtime" / "websites" / "hub-site" / ".main-computer" / "runtime" / "app.py"
    metadata = tmp_path / "runtime" / "websites" / "hub-site" / ".main-computer" / "runtime" / "runtime.json"
    assert bundle["ok"] is True
    assert bundle["status"] == "created"
    assert entrypoint.read_text(encoding="utf-8") == "print('runtime v1')\n"
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    assert payload["runtime_id"] == "main-computer-site-runtime"
    assert payload["source"] == "deploy/local-platform/site-server/app.py"
    assert payload["entrypoint"] == ".main-computer/runtime/app.py"
    assert "/api/site/blog/posts" in payload["api_routes"]

    unchanged = website_project_manifest.ensure_site_runtime_bundle(tmp_path, "hub-site")
    assert unchanged["status"] == "unchanged"

    _write_site_runtime_stub(tmp_path, "print('runtime v2')\n")
    updated = website_project_manifest.ensure_site_runtime_bundle(tmp_path, "hub-site")
    assert updated["status"] == "updated"
    assert entrypoint.read_text(encoding="utf-8") == "print('runtime v2')\n"


def test_remote_publish_runs_saved_scp_command_and_never_generates_local_compose(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    list_website_projects(tmp_path)
    _write_publish_command_stub(tmp_path)
    _write_site_runtime_stub(tmp_path)
    save_website_publish_target(
        tmp_path,
        "hub-site",
        "remote_prod",
        site_slug="johnrraymond",
        source_path="runtime/websites/hub-site",
        remote_host="root@publish.greatlibrary.io",
        remote_root="/srv/main-computer/sites",
        ssh_password="secret-password",
    )

    def fail_generated_compose(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("remote Publish command must not generate or run Local Server compose")

    monkeypatch.setattr(website_project_manifest, "write_generated_websites_compose", fail_generated_compose)
    monkeypatch.setattr(
        website_project_manifest,
        "cleanup_deployed_blog_content_artifacts",
        lambda repo_root, site_id: {"ok": True, "removed": []},
    )

    captured: dict[str, object] = {}

    def fake_run(command, *, cwd=None, text=False, capture_output=False, timeout=None, check=False, env=None):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["text"] = text
        captured["capture_output"] = capture_output
        captured["timeout"] = timeout
        captured["check"] = check
        captured["env"] = env
        return subprocess.CompletedProcess(command, 0, stdout="uploaded\n", stderr="")

    monkeypatch.setattr(website_project_manifest.subprocess, "run", fake_run)

    result = publish_website(tmp_path, "hub-site", lane="remote_prod", verify=False)

    assert result["ok"] is True
    assert result["verified"] is True
    assert result["plan"]["lane"] == "remote-prod"
    assert result["plan"]["uses_deploy_api"] is False
    assert result["plan"]["local_platform_used"] is False
    assert result["plan"]["deployment_path"] == "publish_command_template"
    assert result["plan"]["command"] == [
        "python",
        "deploy\\coolify\\push_site_scp.py",
        "johnrraymond",
        "--source",
        "runtime/websites/hub-site",
        "--host",
        "root@publish.greatlibrary.io",
        "--remote-root",
        "/srv/main-computer/sites",
    ]
    assert captured["command"] == [
        sys.executable,
        "deploy/coolify/push_site_scp.py",
        "johnrraymond",
        "--source",
        "runtime/websites/hub-site",
        "--host",
        "root@publish.greatlibrary.io",
        "--remote-root",
        "/srv/main-computer/sites",
    ]
    assert captured["cwd"] == tmp_path
    assert captured["env"]["MAIN_COMPUTER_PUBLISH_SITE_SLUG"] == "johnrraymond"
    assert captured["env"]["MAIN_COMPUTER_PUBLISH_SOURCE"] == "runtime/websites/hub-site"
    assert captured["env"]["MAIN_COMPUTER_PUBLISH_HOST"] == "root@publish.greatlibrary.io"
    assert captured["env"]["MAIN_COMPUTER_PUBLISH_REMOTE_ROOT"] == "/srv/main-computer/sites"
    assert captured["env"]["MAIN_COMPUTER_PUBLISH_SSH_PASSWORD_FILE"] == "runtime/websites/hub-site/ssh_password.local"
    assert captured["env"]["MAIN_COMPUTER_SSH_PASSWORD"] == "secret-password"
    assert result["publish_command"]["env"]["MAIN_COMPUTER_PUBLISH_SSH_PASSWORD_FILE"] == "runtime/websites/hub-site/ssh_password.local"
    assert result["publish_command"]["env"]["MAIN_COMPUTER_SSH_PASSWORD"] == "<set>"
    assert result["publish_command"]["returncode"] == 0
    runtime_entrypoint = tmp_path / "runtime" / "websites" / "hub-site" / ".main-computer" / "runtime" / "app.py"
    runtime_metadata = tmp_path / "runtime" / "websites" / "hub-site" / ".main-computer" / "runtime" / "runtime.json"
    assert runtime_entrypoint.read_text(encoding="utf-8") == "print('runtime')\n"
    metadata = json.loads(runtime_metadata.read_text(encoding="utf-8"))
    assert metadata["runtime_id"] == "main-computer-site-runtime"
    assert metadata["entrypoint"] == ".main-computer/runtime/app.py"
    assert metadata["site_id"] == "hub-site"
    assert result["site_runtime_bundle"]["ok"] is True


def test_remote_publish_dry_run_keeps_publish_slug_and_source_site_separate(tmp_path: Path) -> None:
    list_website_projects(tmp_path)
    _write_publish_command_stub(tmp_path)
    save_website_publish_target(
        tmp_path,
        "hub-site",
        "remote_prod",
        site_slug="johnrraymond",
        source_path="runtime/websites/hub-site",
        remote_host="root@publish.greatlibrary.io",
        remote_root="/srv/main-computer/sites",
        ssh_password="secret-password",
    )

    result = publish_website(tmp_path, "hub-site", lane="remote_prod", dry_run=True)

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["plan"]["site_slug"] == "johnrraymond"
    assert result["plan"]["source_path"] == "runtime/websites/hub-site"
    assert result["plan"]["site_slug"] != Path(result["plan"]["source_path"]).name
    assert result["plan"]["command"] == [
        "python",
        "deploy\\coolify\\push_site_scp.py",
        "johnrraymond",
        "--source",
        "runtime/websites/hub-site",
        "--host",
        "root@publish.greatlibrary.io",
        "--remote-root",
        "/srv/main-computer/sites",
    ]
    assert result["plan"]["env"] == {
        "MAIN_COMPUTER_PUBLISH_MODE": "scp",
        "MAIN_COMPUTER_PUBLISH_SITE_SLUG": "johnrraymond",
        "MAIN_COMPUTER_PUBLISH_SOURCE": "runtime/websites/hub-site",
        "MAIN_COMPUTER_PUBLISH_REMOTE_ROOT": "/srv/main-computer/sites",
        "MAIN_COMPUTER_PUBLISH_HOST": "root@publish.greatlibrary.io",
        "MAIN_COMPUTER_PUBLISH_SSH_PASSWORD_FILE": "runtime/websites/hub-site/ssh_password.local",
        "MAIN_COMPUTER_SSH_PASSWORD": "<set>",
        "MAIN_COMPUTER_PUBLISH_SSH_PASSWORD": "<set>",
    }
    assert "johnrraymond-site:" in result["plan"]["remote_coolify_compose"]
    assert "image: 'python:3.12-slim'" in result["plan"]["remote_coolify_compose"]
    assert "/srv/main-computer/sites/johnrraymond:/app/sites/johnrraymond:ro" in result["plan"]["remote_coolify_compose"]
    assert "/app/sites/johnrraymond/.main-computer/runtime/app.py" in result["plan"]["remote_coolify_compose"]
    assert "      - '8080'" in result["plan"]["remote_coolify_compose"]
    assert result["publish_command"]["dry_run"] is True


def test_remote_publish_dry_run_reports_missing_scp_host_as_command_input(tmp_path: Path) -> None:
    list_website_projects(tmp_path)
    _write_publish_command_stub(tmp_path)
    save_website_publish_target(
        tmp_path,
        "hub-site",
        "remote_prod",
        site_slug="johnrraymond",
        source_path="runtime/websites/hub-site",
        remote_root="/srv/main-computer/sites",
    )

    result = publish_website(tmp_path, "hub-site", lane="remote_prod", dry_run=True)

    assert result["ok"] is False
    assert result["dry_run"] is True
    assert result["returncode"] == 1
    assert result["plan"]["deployment_path"] == "publish_command_template"
    assert result["plan"]["uses_deploy_api"] is False
    assert result["plan"]["missing"] == ["remote_host"]
    assert result["plan"]["command"] == [
        "python",
        "deploy\\coolify\\push_site_scp.py",
        "johnrraymond",
        "--source",
        "runtime/websites/hub-site",
        "--host",
        "",
        "--remote-root",
        "/srv/main-computer/sites",
    ]
    assert result["publish_command"]["skipped"] is True
    assert "command inputs" in result["error"]


def test_remote_publish_dry_run_supports_future_local_server_command_without_remote_host(tmp_path: Path) -> None:
    list_website_projects(tmp_path)
    save_website_publish_target(
        tmp_path,
        "hub-site",
        "remote_prod",
        publish_mode="local_server",
        use_local_server=True,
        site_slug="johnrraymond",
        source_path="runtime/websites/hub-site",
        remote_root="/srv/main-computer/sites",
    )

    result = publish_website(tmp_path, "hub-site", lane="remote_prod", dry_run=True)

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["plan"]["mode"] == "local_server"
    assert result["plan"]["accepted_publish_target"]["use_local_server"] is True
    assert result["plan"]["command"] == [
        "python",
        "deploy\\coolify\\push_site_local.py",
        "johnrraymond",
        "--source",
        "runtime/websites/hub-site",
        "--remote-root",
        "/srv/main-computer/sites",
    ]
    assert "MAIN_COMPUTER_PUBLISH_HOST" not in result["plan"]["env"]


def test_remote_publish_cleans_stale_blog_artifacts_before_running_publish_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    list_website_projects(tmp_path)
    _write_publish_command_stub(tmp_path)
    _write_site_runtime_stub(tmp_path)
    save_website_publish_target(
        tmp_path,
        "hub-site",
        "remote_prod",
        site_slug="johnrraymond",
        source_path="runtime/websites/hub-site",
        remote_host="root@publish.greatlibrary.io",
        remote_root="/srv/main-computer/sites",
    )

    cleanup_calls: list[str] = []

    def fake_cleanup(repo_root: Path, site_id: object, *, dry_run: bool = False) -> dict[str, object]:
        cleanup_calls.append(str(site_id))
        return {
            "ok": True,
            "retired": True,
            "content_runtime": "directus",
            "removed": ["runtime/websites/hub-site/data/blog-posts.json"],
            "missing": [],
            "manifest_changed": True,
        }

    run_calls: list[list[str]] = []

    def fake_run(command, *, cwd=None, text=False, capture_output=False, timeout=None, check=False, env=None):
        run_calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="uploaded\n", stderr="")

    monkeypatch.setattr(website_project_manifest, "cleanup_deployed_blog_content_artifacts", fake_cleanup)
    monkeypatch.setattr(website_project_manifest.subprocess, "run", fake_run)

    result = publish_website(tmp_path, "hub-site", lane="remote_prod", verify=False)

    assert result["ok"] is True
    assert cleanup_calls == ["hub-site"]
    assert run_calls == [[
        sys.executable,
        "deploy/coolify/push_site_scp.py",
        "johnrraymond",
        "--source",
        "runtime/websites/hub-site",
        "--host",
        "root@publish.greatlibrary.io",
        "--remote-root",
        "/srv/main-computer/sites",
    ]]
    assert result["blog_artifact_cleanup"]["removed"] == ["runtime/websites/hub-site/data/blog-posts.json"]


def test_remote_publish_repairs_managed_blog_widget_assets_before_upload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = create_website_project(tmp_path, "writer-site", "Writer Site")
    ensure_website_blog_page(tmp_path, project.id)
    _write_publish_command_stub(tmp_path)
    _write_site_runtime_stub(tmp_path)
    save_website_publish_target(
        tmp_path,
        project.id,
        "remote_prod",
        site_slug="writer-site",
        source_path="runtime/websites/writer-site",
        remote_host="root@publish.greatlibrary.io",
        remote_root="/srv/main-computer/sites",
    )

    site_dir = tmp_path / "runtime" / "websites" / "writer-site"
    (site_dir / "style.css").write_text("body { color: rebeccapurple; }\n", encoding="utf-8")
    (site_dir / "script.js").write_text("window.editorSaved = true;\n", encoding="utf-8")

    monkeypatch.setattr(
        website_project_manifest,
        "cleanup_deployed_blog_content_artifacts",
        lambda repo_root, site_id: {"ok": True, "removed": []},
    )

    captured_source_has_blog_css: list[bool] = []

    def fake_run(command, *, cwd=None, text=False, capture_output=False, timeout=None, check=False, env=None):
        captured_source_has_blog_css.append(
            "mc-blog-search-pagination-controls-v1" in (site_dir / "style.css").read_text(encoding="utf-8")
        )
        return subprocess.CompletedProcess(command, 0, stdout="uploaded\n", stderr="")

    monkeypatch.setattr(website_project_manifest.subprocess, "run", fake_run)

    result = publish_website(tmp_path, project.id, lane="remote_prod", verify=False)

    style_css = (site_dir / "style.css").read_text(encoding="utf-8")
    script_js = (site_dir / "script.js").read_text(encoding="utf-8")
    assert result["ok"] is True
    assert result["blog_widget_assets"]["required"] is True
    assert result["blog_widget_assets"]["updated_style_css"] is True
    assert result["blog_widget_assets"]["updated_script_js"] is True
    assert captured_source_has_blog_css == [True]
    assert "body { color: rebeccapurple; }" in style_css
    assert "Main Computer blog widget styles" in style_css
    assert "mc-blog-search-pagination-controls-v1" in style_css
    assert "window.editorSaved = true;" in script_js
    assert "mcBlogWidgetRenderPagination" in script_js


def test_remote_publish_command_failure_surfaces_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    list_website_projects(tmp_path)
    _write_publish_command_stub(tmp_path)
    _write_site_runtime_stub(tmp_path)
    save_website_publish_target(
        tmp_path,
        "hub-site",
        "remote_prod",
        site_slug="johnrraymond",
        source_path="runtime/websites/hub-site",
        remote_host="root@publish.greatlibrary.io",
        remote_root="/srv/main-computer/sites",
    )
    monkeypatch.setattr(
        website_project_manifest,
        "cleanup_deployed_blog_content_artifacts",
        lambda repo_root, site_id: {"ok": True, "removed": []},
    )
    monkeypatch.setattr(
        website_project_manifest.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 7, stdout="", stderr="scp failed\n"),
    )

    result = publish_website(tmp_path, "hub-site", lane="remote_prod", verify=False)

    assert result["ok"] is False
    assert result["returncode"] == 7
    assert result["publish_command"]["ok"] is False
    assert result["error"] == "scp failed"


def test_website_publish_plan_marks_expected_site_port_owner_reconcilable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    list_website_projects(tmp_path)
    monkeypatch.setattr(website_project_manifest, "_site_web_port_can_bind", lambda port: False)
    monkeypatch.setattr(
        website_project_manifest,
        "_docker_containers_publishing_port",
        lambda port, timeout_s=4.0: {
            "checked": True,
            "owners": [
                {
                    "id": "abc123",
                    "name": "main-computer-website-hub-site-hub-local-1",
                    "project": "main-computer-website-hub-site",
                    "service": "hub-local",
                    "status": "running",
                    "image": "main-computer-site-hub-site-prod:latest",
                    "ports": ["0.0.0.0:18080->8080/tcp"],
                }
            ],
            "error": "",
        },
    )

    plan = website_publish_plan(tmp_path, "hub-site", "local")

    preflight = plan["site_web_port_preflight"]
    assert preflight["ok"] is True
    assert preflight["status"] == "owned_by_expected_service"
    assert "docker compose up can reconcile it" in preflight["message"]


def test_publish_website_repairs_stale_local_platform_site_port_owner_before_compose(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    list_website_projects(tmp_path)
    state = {"removed": False}
    docker_commands: list[list[str]] = []

    def fake_site_web_port_can_bind(_port: object) -> bool:
        return bool(state["removed"])

    def fake_docker_containers_publishing_port(_port: object, timeout_s: float = 4.0) -> dict[str, object]:
        if state["removed"]:
            return {"checked": True, "owners": [], "error": ""}
        return {
            "checked": True,
            "owners": [
                {
                    "id": "def456",
                    "name": "main-computer-local-platform-hub-local-1",
                    "project": "main-computer-local-platform",
                    "service": "hub-local",
                    "status": "running",
                    "image": "main-computer-site-hub-site-prod:latest",
                    "ports": ["0.0.0.0:18080->8080/tcp"],
                }
            ],
            "error": "",
        }

    def fake_run_docker_mutation(command: list[str], timeout_s: float = 15.0) -> dict[str, object]:
        docker_commands.append(command)
        assert command == ["docker", "rm", "-f", "main-computer-local-platform-hub-local-1"]
        state["removed"] = True
        return {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(website_project_manifest, "_site_web_port_can_bind", fake_site_web_port_can_bind)
    monkeypatch.setattr(website_project_manifest, "_docker_containers_publishing_port", fake_docker_containers_publishing_port)
    monkeypatch.setattr(website_project_manifest, "_run_docker_mutation", fake_run_docker_mutation)
    monkeypatch.setattr(website_project_manifest.subprocess, "run", lambda *args, **kwargs: Completed())

    result = publish_website(tmp_path, "hub-site", lane="local", verify=False)

    assert result["ok"] is True
    assert result["returncode"] == 0
    assert result["site_web_port_repair"]["removed_containers"] == [
        "main-computer-local-platform-hub-local-1"
    ]
    assert docker_commands == [["docker", "rm", "-f", "main-computer-local-platform-hub-local-1"]]
    assert result["plan"]["site_web_port_preflight"]["status"] == "available"




def test_website_publish_plan_includes_directus_dependency_service(tmp_path: Path) -> None:
    create_local_platform_website_project(tmp_path, "zzzzz", "zzzzz")
    site_path = tmp_path / "runtime" / "websites" / "zzzzz" / "site.json"
    manifest = json.loads(site_path.read_text(encoding="utf-8"))
    manifest.setdefault("backend", {})["cms"] = {
        "provider": "directus",
        "required": True,
        "runtime": "deployed",
        "service": {
            "kind": "directus",
            "image": "directus/directus:11.5.1",
            "internal_url": "http://zzzzz-directus:8055",
            "public_url": "",
            "admin_secret_ref": "directus_admin_token",
        },
        "storage": {
            "database_volume": "zzzzz_directus_database",
            "uploads_volume": "zzzzz_directus_uploads",
        },
        "schema": {"collection": "posts", "status": "pending_deploy"},
        "permissions": {"public_read_published_posts": True, "public_read_files": True, "status": "pending_deploy"},
    }
    site_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    plan = website_publish_plan(tmp_path, "zzzzz", "dev")

    assert plan["service"] == "zzzzz-dev"
    assert plan["cms_dependency_services"] == ["zzzzz-directus"]
    assert "--force-recreate" not in plan["command"]
    assert plan["recreate_required"] is False
    assert plan["command"][-2:] == ["zzzzz-directus", "zzzzz-dev"]



def test_website_publish_plan_includes_directus_for_blog_cms_prep_without_enabling_blog(tmp_path: Path) -> None:
    create_local_platform_website_project(tmp_path, "zzzzz", "zzzzz")
    site_path = tmp_path / "runtime" / "websites" / "zzzzz" / "site.json"
    manifest = json.loads(site_path.read_text(encoding="utf-8"))
    manifest["features"] = {
        "blog": {
            "selected": True,
            "enabled": False,
            "cms": "directus",
            "database": "sqlite",
            "install_status": "pending_deploy",
        }
    }
    manifest["blog_install"] = {
        "layers": {"cms": {"status": "configured"}},
        "runtime_preparation": {
            "directus_service": {
                "status": "pending_deploy",
                "requested": True,
                "verified": False,
            }
        },
    }
    manifest.setdefault("backend", {})["cms"] = {
        "provider": "directus",
        "required": True,
        "runtime": "deployed",
        "database_connection": "content",
        "service": {
            "kind": "directus",
            "image": "directus/directus:11.5.1",
            "internal_url": "http://zzzzz-directus:8055",
            "public_url": "",
            "admin_secret_ref": "directus_admin_token",
        },
        "storage": {
            "database_volume": "zzzzz_directus_database",
            "uploads_volume": "zzzzz_directus_uploads",
        },
        "schema": {"collection": "posts", "status": "pending_deploy"},
        "permissions": {"public_read_published_posts": True, "public_read_files": True, "status": "pending_deploy"},
    }
    site_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    plan = website_publish_plan(tmp_path, "zzzzz", "dev")

    assert plan["service"] == "zzzzz-dev"
    assert plan["cms_dependency_services"] == ["zzzzz-directus"]
    assert plan["command"][-2:] == ["zzzzz-directus", "zzzzz-dev"]

    refreshed = json.loads(site_path.read_text(encoding="utf-8"))
    assert refreshed["features"]["blog"]["enabled"] is False
    assert refreshed["features"]["blog"]["install_status"] == "pending_deploy"


def test_directus_service_verification_marks_only_the_directus_runtime_gate(tmp_path: Path) -> None:
    create_local_platform_website_project(tmp_path, "zzzzz", "zzzzz")
    site_path = tmp_path / "runtime" / "websites" / "zzzzz" / "site.json"
    manifest = json.loads(site_path.read_text(encoding="utf-8"))
    manifest["features"] = {
        "blog": {
            "selected": True,
            "enabled": False,
            "cms": "directus",
            "database": "sqlite",
            "install_status": "pending_deploy",
        }
    }
    manifest["blog_install"] = {
        "runtime_preparation": {
            "sqlite_database": {"status": "ready", "verified": True},
            "directus_service": {"status": "pending_deploy", "requested": True, "verified": False},
        }
    }
    manifest.setdefault("backend", {})["cms"] = {
        "provider": "directus",
        "required": True,
        "runtime": "deployed",
        "database_connection": "content",
        "service": {
            "kind": "directus",
            "image": "directus/directus:11.5.1",
            "internal_url": "http://zzzzz-directus:8055",
            "public_url": "",
        },
        "schema": {"collection": "posts", "status": "pending_deploy"},
        "permissions": {"public_read_published_posts": True, "public_read_files": True, "status": "pending_deploy"},
    }
    site_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    project = website_project_manifest.load_website_project(tmp_path, "zzzzz")
    website_project_manifest._mark_directus_service_verified(
        project,
        [
            {
                "provider": "directus",
                "service": "zzzzz-directus",
                "internal_url": "http://zzzzz-directus:8055",
                "public_url": "http://127.0.0.1:28200",
                "ok": True,
            }
        ],
    )

    refreshed = json.loads(site_path.read_text(encoding="utf-8"))
    marker = refreshed["blog_install"]["runtime_preparation"]["directus_service"]
    assert marker["status"] == "ready"
    assert marker["verified"] is True
    assert marker["service"] == "zzzzz-directus"
    assert marker["public_url"] == "http://127.0.0.1:28200"
    assert refreshed["backend"]["cms"]["service_status"] == "ready"
    assert refreshed["backend"]["cms"]["schema_status"] == "pending_deploy"
    assert refreshed["backend"]["cms"]["permissions_status"] == "pending_deploy"
    assert refreshed["features"]["blog"]["enabled"] is False
    assert refreshed["features"]["blog"]["install_status"] == "pending_deploy"



def test_directus_blog_bootstrap_marks_schema_and_permissions_ready(tmp_path: Path) -> None:
    create_local_platform_website_project(tmp_path, "zzzzz", "zzzzz")
    site_path = tmp_path / "runtime" / "websites" / "zzzzz" / "site.json"
    manifest = json.loads(site_path.read_text(encoding="utf-8"))
    manifest["features"] = {
        "blog": {
            "selected": True,
            "enabled": True,
            "cms": "directus",
            "database": "sqlite",
            "install_status": "pending_deploy",
        }
    }
    manifest["blog_install"] = {
        "runtime_preparation": {
            "sqlite_database": {"status": "ready", "verified": True},
            "directus_service": {"status": "ready", "requested": True, "verified": True},
        }
    }
    manifest.setdefault("backend", {})["cms"] = {
        "provider": "directus",
        "required": True,
        "runtime": "deployed",
        "database_connection": "content",
        "service": {
            "kind": "directus",
            "image": "directus/directus:11.5.1",
            "internal_url": "http://zzzzz-directus:8055",
            "public_url": "http://127.0.0.1:28200",
        },
        "schema": {"collection": "posts", "status": "pending_deploy"},
        "permissions": {"public_read_published_posts": True, "public_read_files": True, "status": "pending_deploy"},
        "schema_status": "pending_deploy",
        "permissions_status": "pending_deploy",
    }
    site_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    project = website_project_manifest.load_website_project(tmp_path, "zzzzz")
    website_project_manifest._mark_directus_blog_bootstrap_verified(
        project,
        [
            {
                "ok": True,
                "provider": "directus",
                "service": "zzzzz-directus",
                "public_url": "http://127.0.0.1:28200",
                "anonymous_policy": "public-policy",
            }
        ],
    )

    refreshed = json.loads(site_path.read_text(encoding="utf-8"))
    cms = refreshed["backend"]["cms"]
    assert cms["schema_status"] == "ready"
    assert cms["permissions_status"] == "ready"
    assert cms["schema"]["status"] == "ready"
    assert cms["permissions"]["status"] == "ready"
    marker = refreshed["blog_install"]["runtime_preparation"]["directus_service"]
    assert marker["schema_status"] == "ready"
    assert marker["permissions_status"] == "ready"
    assert refreshed["blog_install"]["directus_bootstrap"]["ok"] is True

def test_website_publish_plan_force_recreates_only_for_stale_blog_container_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    create_local_platform_website_project(tmp_path, "zzzzz", "zzzzz")
    site_path = tmp_path / "runtime" / "websites" / "zzzzz" / "site.json"
    manifest = json.loads(site_path.read_text(encoding="utf-8"))
    manifest["features"] = {
        "blog": {
            "selected": True,
            "enabled": False,
            "cms": "directus",
            "database": "sqlite",
            "install_status": "pending_deploy",
        }
    }
    site_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    monkeypatch.setattr(
        website_project_manifest,
        "_inspect_running_container_env",
        lambda container_name, timeout_s=3.0: {
            "checked": True,
            "found": True,
            "env": {
                "SITE_ID": "zzzzz",
                "BLOG_ENABLED": "true",
                "BLOG_CONTENT_RUNTIME": "deployed",
                "DIRECTUS_URL": "http://zzzzz-directus:8055",
            },
        },
    )

    plan = website_publish_plan(tmp_path, "zzzzz", "dev")

    assert plan["service"] == "zzzzz-dev"
    assert plan["recreate_required"] is True
    assert "--force-recreate" in plan["command"]
    assert plan["command"][-1] == "zzzzz-dev"
    assert plan["container_recreate"]["stale_env_keys"] == [
        "BLOG_CONTENT_RUNTIME",
        "BLOG_ENABLED",
        "DIRECTUS_URL",
    ]
    assert plan["recreate_reasons"] == [
        "running site container has stale Blog/Directus env while manifest features.blog.enabled=false"
    ]


def test_publish_website_regenerates_compose_before_building_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    list_website_projects(tmp_path)
    calls: list[str] = []

    def fake_write_generated_site_compose(
        repo_root: Path,
        site_id: object,
        **_kwargs: object,
    ) -> dict[str, object]:
        assert repo_root == tmp_path
        assert site_id == "hub-site"
        assert _kwargs.get("register_missing") is True
        calls.append("compose")
        return {
            "ok": True,
            "path": str(tmp_path / "runtime" / "websites" / "hub-site" / ".main-computer" / "local-platform" / "docker-compose.yml"),
            "repo_relative_path": "runtime/websites/hub-site/.main-computer/local-platform/docker-compose.yml",
            "site_id": "hub-site",
            "service_count": 1,
            "services": ["hub-local"],
            "cms_services": [],
        }

    def fake_website_publish_plan(repo_root: Path, site_id: object, lane: object = "local") -> dict[str, object]:
        assert repo_root == tmp_path
        assert site_id == "hub-site"
        assert calls == ["compose"]
        calls.append("plan")
        return {
            "site": {"id": "hub-site"},
            "requested_lane": "local",
            "lane": "local",
            "accepted_publish_target": None,
            "service": "hub-local",
            "url": "http://localhost:18080/",
            "status_url": "http://localhost:18080/api/site/status",
            "port": 18080,
            "cms_dependency_services": [],
            "compose_path": str(tmp_path / "runtime" / "websites" / "hub-site" / ".main-computer" / "local-platform" / "docker-compose.yml"),
            "compose_project": "main-computer-website-hub-site",
            "command": ["docker", "compose", "up", "-d", "--build", "--force-recreate", "hub-local"],
            "supported": True,
        }

    monkeypatch.setattr(
        website_project_manifest,
        "write_generated_site_compose",
        fake_write_generated_site_compose,
    )
    monkeypatch.setattr(website_project_manifest, "website_publish_plan", fake_website_publish_plan)

    result = publish_website(tmp_path, "hub-site", lane="local", dry_run=True, verify=False)

    assert result["ok"] is True
    assert calls == ["compose", "plan"]
    assert result["generated_compose"]["ok"] is True


def test_publish_website_cli_prints_dry_run_plan(tmp_path: Path) -> None:
    list_website_projects(tmp_path)
    (tmp_path / "deploy" / "local-platform").mkdir(parents=True)
    (tmp_path / "deploy" / "local-platform" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "tools" / "local-platform" / "publish-website.py"),
            "hub-site",
            "--lane",
            "dev",
            "--repo-root",
            str(tmp_path),
            "--dry-run",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["service"] == "hub-dev"

    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "tools" / "local-platform" / "publish-website.py"),
            "hub-site",
            "--lane",
            "local-prod",
            "--repo-root",
            str(tmp_path),
            "--dry-run",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["lane"] == "local"
    assert payload["service"] == "hub-local"



def test_directus_ping_verifier_accepts_plain_pong_response(monkeypatch) -> None:
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self, _limit):
            return b"pong"

    def fake_urlopen(url, timeout=4.0):
        assert str(url).endswith("/server/ping")
        return FakeResponse()

    
    monkeypatch.setattr(website_project_manifest, "urlopen", fake_urlopen)
    result = website_project_manifest._wait_for_directus_ping("http://127.0.0.1:28105", 1.0)

    assert result["ok"] is True
    assert result["status"] == 200
    assert result["body"] == "pong"
    assert result["attempts"] == 1




def test_directus_use_existing_adopts_running_directus_owner_by_public_port(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    create_local_platform_website_project(tmp_path, "johnrraymond", "John R. Raymond")
    site_path = tmp_path / "runtime" / "websites" / "johnrraymond" / "site.json"
    manifest = json.loads(site_path.read_text(encoding="utf-8"))
    manifest["backend"] = {
        "cms": {
            "provider": "directus",
            "required": True,
            "runtime": "deployed",
            "service": {"kind": "directus"},
            "storage": {},
        }
    }
    site_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        website_project_manifest,
        "_docker_containers_publishing_port",
        lambda port, timeout_s=4.0: {
            "checked": True,
            "owners": [
                {
                    "name": "main-computer-local-platform-hub-site-directus-1",
                    "project": "main-computer-local-platform",
                    "service": "hub-site-directus",
                    "status": "running",
                    "image": "directus/directus:11.5.1",
                    "ports": ["127.0.0.1:28200->8055/tcp"],
                }
            ],
            "error": "",
        },
    )

    project = website_project_manifest.save_website_directus_connection(
        tmp_path,
        "johnrraymond",
        {
            "mode": "use_existing",
            "service_name": "johnrraymond-directus",
            "database_volume": "johnrraymond_directus_database",
            "uploads_volume": "johnrraymond_directus_uploads",
            "public_port": 28200,
        },
    )

    connection = project.manifest["backend"]["cms"]["local_connection"]
    service = project.manifest["backend"]["cms"]["service"]
    assert connection["service_name"] == "hub-site-directus"
    assert connection["managed"] is False
    assert connection["external"] is True
    assert connection["internal_url"] == "http://hub-site-directus:8055"
    assert service["internal_url"] == "http://hub-site-directus:8055"
    assert service["public_url"] == "http://127.0.0.1:28200"

    plan = website_project_manifest._directus_runtime_action_plan(
        project,
        "main-computer-local-platform",
    )
    assert plan["required"] is False
    assert "existing shared service" in plan["message"]


def _configure_directus_connection_for_publish(tmp_path: Path, *, mode: str = "use_existing") -> None:
    create_local_platform_website_project(tmp_path, "zzzzz", "zzzzz")
    site_path = tmp_path / "runtime" / "websites" / "zzzzz" / "site.json"
    manifest = json.loads(site_path.read_text(encoding="utf-8"))
    manifest["backend"] = {
        "cms": {
            "provider": "directus",
            "required": True,
            "runtime": "deployed",
            "service": {"kind": "directus"},
            "storage": {},
        }
    }
    site_path.write_text(json.dumps(manifest), encoding="utf-8")
    payload = {
        "mode": mode,
        "service_name": "zzzzz-directus",
        "database_volume": "zzzzz_directus_database",
        "uploads_volume": "zzzzz_directus_uploads",
        "public_port": 28200,
    }
    if mode == "overwrite_existing":
        payload["destructive_confirmation"] = True
    website_project_manifest.save_website_directus_connection(tmp_path, "zzzzz", payload)


def test_directus_reuse_removes_stale_container_but_keeps_volumes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_directus_connection_for_publish(tmp_path, mode="use_existing")
    monkeypatch.setattr(
        website_project_manifest,
        "_docker_containers_for_compose_service",
        lambda service, timeout_s=6.0: {
            "checked": True,
            "owners": [
                {
                    "name": "main-computer-local-platform-zzzzz-directus-1",
                    "project": "main-computer-local-platform",
                    "service": "zzzzz-directus",
                    "status": "running",
                }
            ],
            "error": "",
        },
    )
    commands: list[list[str]] = []

    def fake_run(args: list[str], timeout_s: float = 20.0) -> dict[str, object]:
        commands.append(args)
        return {"ok": True, "args": args, "returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(website_project_manifest, "_run_docker_mutation", fake_run)

    result = website_project_manifest._apply_directus_runtime_action(
        tmp_path,
        "zzzzz",
        "main-computer-local-platform-unleashed",
    )

    assert result["ok"] is True
    assert result["removed_containers"] == ["main-computer-local-platform-zzzzz-directus-1"]
    assert result["removed_volumes"] == []
    assert commands == [["docker", "rm", "-f", "main-computer-local-platform-zzzzz-directus-1"]]


def test_directus_overwrite_removes_matching_containers_and_selected_volumes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_directus_connection_for_publish(tmp_path, mode="overwrite_existing")
    monkeypatch.setattr(
        website_project_manifest,
        "_docker_containers_for_compose_service",
        lambda service, timeout_s=6.0: {
            "checked": True,
            "owners": [
                {
                    "name": "main-computer-local-platform-zzzzz-directus-1",
                    "project": "main-computer-local-platform",
                    "service": "zzzzz-directus",
                    "status": "running",
                },
                {
                    "name": "main-computer-local-platform-unleashed-zzzzz-directus-1",
                    "project": "main-computer-local-platform-unleashed",
                    "service": "zzzzz-directus",
                    "status": "running",
                },
            ],
            "error": "",
        },
    )
    commands: list[list[str]] = []

    def fake_run(args: list[str], timeout_s: float = 20.0) -> dict[str, object]:
        commands.append(args)
        return {"ok": True, "args": args, "returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(website_project_manifest, "_run_docker_mutation", fake_run)

    result = website_project_manifest._apply_directus_runtime_action(
        tmp_path,
        "zzzzz",
        "main-computer-local-platform-unleashed",
    )

    assert result["ok"] is True
    assert result["removed_containers"] == [
        "main-computer-local-platform-zzzzz-directus-1",
        "main-computer-local-platform-unleashed-zzzzz-directus-1",
    ]
    assert result["removed_volumes"] == ["zzzzz_directus_database", "zzzzz_directus_uploads"]
    assert commands == [
        ["docker", "rm", "-f", "main-computer-local-platform-zzzzz-directus-1"],
        ["docker", "rm", "-f", "main-computer-local-platform-unleashed-zzzzz-directus-1"],
        ["docker", "volume", "rm", "zzzzz_directus_database"],
        ["docker", "volume", "rm", "zzzzz_directus_uploads"],
    ]
    refreshed = website_project_manifest.load_website_project(tmp_path, "zzzzz")
    connection = refreshed.manifest["backend"]["cms"]["local_connection"]
    assert connection["reset_requested"] is False
    assert connection["reset_applied_at"]
