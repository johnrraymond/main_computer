from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "deploy" / "local-platform" / "site-server" / "app.py"


def load_site_server(monkeypatch, *, content_runtime: str = "live"):
    monkeypatch.setenv("BLOG_ENABLED", "true")
    monkeypatch.setenv("BLOG_PROVIDER", "directus")
    monkeypatch.setenv("BLOG_CONTENT_RUNTIME", content_runtime)
    monkeypatch.setenv("BLOG_COLLECTION", "posts")
    monkeypatch.setenv("DIRECTUS_URL", "http://directus-test:8055")
    monkeypatch.setenv("DIRECTUS_PUBLIC_URL", "http://127.0.0.1:28200")
    spec = importlib.util.spec_from_file_location("main_computer_site_server_app_test", APP)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_site_server_does_not_serve_packaged_runtime_files(monkeypatch, tmp_path: Path) -> None:
    content_root = tmp_path / "runtime" / "websites"
    site_dir = content_root / "runtime-site"
    runtime_dir = site_dir / ".main-computer" / "runtime"
    runtime_dir.mkdir(parents=True)
    (site_dir / "index.html").write_text("<h1>ok</h1>", encoding="utf-8")
    (runtime_dir / "app.py").write_text("print('secret runtime')\n", encoding="utf-8")
    monkeypatch.setenv("SITE_ID", "runtime-site")
    monkeypatch.setenv("CONTENT_ROOT", str(content_root))
    app = load_site_server(monkeypatch)

    assert app.safe_static_file("/index.html") == site_dir.resolve() / "index.html"
    assert app.safe_static_file("/.main-computer/runtime/app.py") is None
    assert app.safe_static_file("/.main-computer/runtime/runtime.json") is None
    assert app.safe_static_file("/anything.py") is None


def test_blog_runtime_queries_directus_through_published_only_boundary(monkeypatch) -> None:
    app = load_site_server(monkeypatch)
    calls: list[str] = []

    def fake_directus_json(path: str, timeout: float = 5.0) -> dict:
        calls.append(path)
        return {
            "data": [
                {
                    "id": 1,
                    "status": "published",
                    "slug": "hello-directus",
                    "title": "Hello Directus",
                    "excerpt": "Directus powered",
                    "body": "Post body",
                    "published_on": "2026-05-14",
                    "read_time_minutes": 7,
                    "is_legacy": "yes",
                }
            ]
        }

    monkeypatch.setattr(app, "directus_json", fake_directus_json)

    posts = app.list_published_posts()
    post = app.get_published_post_by_slug("hello-directus")

    assert posts[0]["slug"] == "hello-directus"
    assert post["title"] == "Hello Directus"
    assert all("filter%5Bstatus%5D%5B_eq%5D=published" in call for call in calls)
    assert all("sort=-published_on%2C-id" in call for call in calls)
    list_call = next(call for call in calls if "filter%5Bslug%5D%5B_eq%5D" not in call)
    detail_call = next(call for call in calls if "filter%5Bslug%5D%5B_eq%5D=hello-directus" in call)
    assert "published_on%2Cread_time_minutes%2Cis_legacy" in list_call
    assert "body" not in list_call
    assert "limit=-1" in list_call
    assert "body" in detail_call
    assert "limit=1" in detail_call


def test_blog_runtime_status_reports_ready_without_exposing_directus_internal_url(monkeypatch) -> None:
    app = load_site_server(monkeypatch)
    monkeypatch.setattr(app, "list_published_posts", lambda: [{"slug": "hello-directus"}])

    payload = app.blog_runtime_status_payload()

    assert payload["ok"] is True
    assert payload["blog"]["provider"] == "directus"
    assert payload["blog"]["published_read_ok"] is True
    assert payload["blog"]["draft_protected"] is True
    assert payload["blog"]["directus_public_url"] == "http://127.0.0.1:28200"
    assert "http://directus-test:8055" not in repr(payload)


def load_site_server_from_manifest(monkeypatch, tmp_path: Path):
    for name in [
        "BLOG_ENABLED",
        "BLOG_PROVIDER",
        "BLOG_CONTENT_RUNTIME",
        "BLOG_COLLECTION",
        "DIRECTUS_URL",
        "DIRECTUS_PUBLIC_URL",
    ]:
        monkeypatch.delenv(name, raising=False)
    content_root = tmp_path / "runtime" / "websites"
    site_dir = content_root / "legacy-blog"
    site_dir.mkdir(parents=True)
    (site_dir / "site.json").write_text(
        """{
          "id": "legacy-blog",
          "name": "Legacy Blog",
          "features": {
            "blog": {
              "enabled": true,
              "cms": "directus",
              "database": "sqlite",
              "content_runtime": "deployed",
              "routes": {"index": "/blog", "post": "/blog/:slug"},
              "content": {"provider": "directus", "collection": "posts"}
            }
          },
          "backend": {
            "cms": {
              "provider": "directus",
              "service": {
                "internal_url": "http://legacy-blog-directus:8055",
                "public_url": "http://127.0.0.1:28300"
              }
            }
          },
          "runtime_config": {
            "content": {
              "provider": "directus",
              "content_runtime": "deployed",
              "collection": "posts"
            }
          }
        }""",
        encoding="utf-8",
    )
    (site_dir / "data").mkdir(parents=True)
    (site_dir / "data" / "blog-posts.json").write_text(
        """{
          "site_id": "legacy-blog",
          "provider": "directus",
          "collection": "posts",
          "content_runtime": "deployed",
          "generated_at": "2026-05-20T00:00:00+00:00",
          "posts": [
            {
              "status": "published",
              "slug": "hello-directus",
              "title": "Hello Directus",
              "excerpt": "Directus powered",
              "body": "Post body",
              "published_on": "2026-05-14",
              "read_time_minutes": 7,
              "is_legacy": "yes"
            },
            {
              "status": "draft",
              "slug": "draft-directus",
              "title": "Draft Directus"
            }
          ]
        }""",
        encoding="utf-8",
    )
    (site_dir / "blog" / "hello-directus").mkdir(parents=True)
    (site_dir / "blog" / "index.html").write_text(
        '<html><body><article data-mc-blog-post="hello-directus"><a href="/blog/hello-directus/">Hello Directus</a></article></body></html>',
        encoding="utf-8",
    )
    (site_dir / "blog" / "hello-directus" / "index.html").write_text(
        "<html><body>Hello Directus detail</body></html>",
        encoding="utf-8",
    )
    monkeypatch.setenv("SITE_ID", "legacy-blog")
    monkeypatch.setenv("CONTENT_ROOT", str(content_root))
    spec = importlib.util.spec_from_file_location("main_computer_site_server_app_manifest_test", APP)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_blog_runtime_ignores_legacy_deployed_artifacts_and_queries_manifest_directus(monkeypatch, tmp_path: Path) -> None:
    app = load_site_server_from_manifest(monkeypatch, tmp_path)
    calls: list[str] = []

    def fake_directus_json(path: str, timeout: float = 5.0) -> dict:
        calls.append(path)
        if "filter%5Bslug%5D%5B_eq%5D=live-directus" in path:
            return {"data": [{"status": "published", "slug": "live-directus", "title": "Live Directus"}]}
        if "filter%5Bslug%5D%5B_eq%5D=hello-directus" in path:
            return {"data": []}
        return {"data": [{"status": "published", "slug": "live-directus", "title": "Live Directus"}]}

    monkeypatch.setattr(app, "directus_json", fake_directus_json)

    assert app.blog_is_enabled() is True
    payload = app.blog_runtime_status_payload()
    posts_payload, posts_status = app.blog_posts_payload()
    post_payload, post_status = app.blog_post_payload("live-directus")

    assert payload["ok"] is True
    assert payload["blog"]["provider"] == "directus"
    assert payload["blog"]["collection"] == "posts"
    assert payload["blog"]["content_runtime"] == "directus"
    assert payload["blog"]["published_read_ok"] is True
    assert payload["blog"]["post_slugs"] == ["live-directus"]
    assert payload["blog"]["directus_url_configured"] is True
    assert payload["blog"]["directus_public_url"] == "http://127.0.0.1:28300"
    assert posts_status == app.HTTPStatus.OK
    assert [post["slug"] for post in posts_payload["posts"]] == ["live-directus"]
    assert post_status == app.HTTPStatus.OK
    assert post_payload["post"]["title"] == "Live Directus"
    assert app.blog_post_payload("hello-directus")[1] == app.HTTPStatus.NOT_FOUND
    assert any("filter%5Bstatus%5D%5B_eq%5D=published" in call for call in calls)
    assert "legacy-blog-directus:8055" not in repr(payload)


def _load_remote_prod_blog_site(monkeypatch, tmp_path: Path, *, publish_config: dict | None = None):
    import json

    for name in [
        "BLOG_ENABLED",
        "BLOG_PROVIDER",
        "BLOG_CONTENT_RUNTIME",
        "BLOG_COLLECTION",
        "DIRECTUS_URL",
        "DIRECTUS_PUBLIC_URL",
        "SITE_LANE",
    ]:
        monkeypatch.delenv(name, raising=False)
    content_root = tmp_path / "runtime" / "websites"
    site_dir = content_root / "hub-site"
    site_dir.mkdir(parents=True)
    cms = {
        "provider": "directus",
        "service": {
            "internal_url": "http://hub-site-directus:8055",
            "public_url": "http://127.0.0.1:28200",
        },
    }
    if publish_config is not None:
        cms["publish"] = publish_config
    manifest = {
        "id": "hub-site",
        "name": "Hub Site",
        "features": {
            "blog": {
                "enabled": True,
                "selected": True,
                "cms": "directus",
                "content_runtime": "directus",
                "routes": {"index": "/blog", "post": "/blog/:slug"},
                "content": {"provider": "directus", "collection": "posts"},
            }
        },
        "backend": {"cms": cms},
    }
    (site_dir / "site.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    monkeypatch.setenv("SITE_ID", "hub-site")
    monkeypatch.setenv("CONTENT_ROOT", str(content_root))
    monkeypatch.setenv("MC_RUNTIME_LANE", "remote-prod")
    spec = importlib.util.spec_from_file_location("main_computer_site_server_app_remote_prod_test", APP)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_remote_prod_blog_runtime_requires_explicit_publish_directus_config(monkeypatch, tmp_path: Path) -> None:
    app = _load_remote_prod_blog_site(monkeypatch, tmp_path)

    config = app.blog_runtime_config(include_internal=True)
    payload = app.blog_runtime_status_payload()

    assert config["directus_scope"] == "publish"
    assert config["directus_url_configured"] is False
    assert config["publish_directus_url_configured"] is False
    assert payload["ok"] is False
    assert payload["blog"]["state"] == "error"
    assert "Publish Directus URL is not configured" in payload["blog"]["error"]
    assert "hub-site-directus:8055" not in repr(payload)


def test_remote_prod_blog_runtime_uses_explicit_publish_directus_config(monkeypatch, tmp_path: Path) -> None:
    app = _load_remote_prod_blog_site(
        monkeypatch,
        tmp_path,
        publish_config={"internal_url": "https://cms.example.test", "public_url": "https://cms.example.test"},
    )

    config = app.blog_runtime_config(include_internal=True)

    assert config["directus_scope"] == "publish"
    assert config["directus_url"] == "https://cms.example.test"
    assert config["directus_public_url"] == "https://cms.example.test"
    assert config["directus_url_configured"] is True
    assert config["publish_directus_url_configured"] is True


def test_blog_disabled_manifest_overrides_stale_runtime_state_and_env_flag(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BLOG_ENABLED", "true")
    monkeypatch.setenv("BLOG_PROVIDER", "directus")
    monkeypatch.setenv("BLOG_CONTENT_RUNTIME", "deployed")
    monkeypatch.setenv("DIRECTUS_URL", "http://stale-directus:8055")
    monkeypatch.setenv("DIRECTUS_PUBLIC_URL", "http://127.0.0.1:28200")
    content_root = tmp_path / "runtime" / "websites"
    site_dir = content_root / "intent-only-blog"
    site_dir.mkdir(parents=True)
    (site_dir / "site.json").write_text(
        """{
          "id": "intent-only-blog",
          "name": "Intent Only Blog",
          "features": {
            "blog": {
              "selected": true,
              "enabled": false,
              "cms": "directus",
              "database": "sqlite",
              "install_status": "pending_deploy"
            }
          },
          "backend": {
            "cms": {
              "provider": "directus",
              "required": true,
              "runtime": "deployed",
              "service": {
                "internal_url": "http://intent-only-blog-directus:8055",
                "public_url": "http://127.0.0.1:28300"
              }
            }
          },
          "runtime": {"content_runtime": "deployed"},
          "runtime_config": {
            "content": {
              "provider": "directus",
              "content_runtime": "deployed",
              "collection": "posts"
            }
          }
        }""",
        encoding="utf-8",
    )
    monkeypatch.setenv("SITE_ID", "intent-only-blog")
    monkeypatch.setenv("CONTENT_ROOT", str(content_root))

    spec = importlib.util.spec_from_file_location("main_computer_site_server_app_disabled_manifest_test", APP)
    assert spec is not None
    assert spec.loader is not None
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)

    assert app.blog_is_enabled() is False
    status = app.status_payload()
    assert status["blog"]["selected"] is True
    assert status["blog"]["enabled"] is False
    assert status["blog"]["content_runtime"] == "disabled"
    assert status["blog"]["install_status"] == "pending_deploy"

    payload = app.blog_runtime_status_payload()
    assert payload["ok"] is True
    assert payload["blog"]["selected"] is True
    assert payload["blog"]["enabled"] is False
    assert payload["blog"]["content_runtime"] == "disabled"
    assert payload["blog"]["install_status"] == "pending_deploy"
    assert payload["blog"]["ready"] is False
    assert payload["blog"]["state"] == "not_configured"
    assert payload["blog"]["error"] == ""

    posts_payload, posts_status = app.blog_posts_payload()
    assert posts_status == app.HTTPStatus.OK
    assert posts_payload["ok"] is True
    assert posts_payload["posts"] == []

    post_payload, post_status = app.blog_post_payload("draft-safe")
    assert post_status == app.HTTPStatus.NOT_FOUND
    assert post_payload["post"] is None


def test_blog_runtime_status_preserves_directus_http_error_body(monkeypatch) -> None:
    app = load_site_server(monkeypatch)

    def fake_directus_json(path: str, timeout: float = 5.0) -> dict:
        raise RuntimeError('Directus request failed with HTTP 403: {"errors":[{"message":"You do not have permission to access collection posts"}]}')

    monkeypatch.setattr(app, "directus_json", fake_directus_json)

    payload = app.blog_runtime_status_payload()

    assert payload["ok"] is False
    assert "permission" in payload["blog"]["error"]
    assert "posts" in payload["blog"]["error"]


def test_blog_posts_api_returns_published_posts_only(monkeypatch) -> None:
    app = load_site_server(monkeypatch)
    calls: list[str] = []

    def fake_directus_json(path: str, timeout: float = 5.0) -> dict:
        calls.append(path)
        assert "body" not in path
        assert "limit=-1" in path
        return {
            "data": [
                {
                    "id": 7,
                    "status": "published",
                    "slug": "hello-directus",
                    "title": "Hello Directus",
                    "excerpt": "Directus powered",
                    "published_on": "2026-05-14",
                    "read_time_minutes": 7,
                    "is_legacy": "yes",
                }
            ]
        }

    monkeypatch.setattr(app, "directus_json", fake_directus_json)

    payload, status = app.blog_posts_payload()

    assert status == app.HTTPStatus.OK
    assert payload["ok"] is True
    assert payload["blog"]["ready"] is True
    assert payload["blog"]["state"] == "ready"
    assert payload["posts"][0]["slug"] == "hello-directus"
    assert payload["posts"][0]["published_on"] == "2026-05-14"
    assert payload["posts"][0]["read_time_minutes"] == 7
    assert all("filter%5Bstatus%5D%5B_eq%5D=published" in call for call in calls)
    assert all("sort=-published_on%2C-id" in call for call in calls)
    assert all("limit=-1" in call for call in calls)


def test_blog_posts_api_searches_strictly_fuzzes_opt_in_and_paginates(monkeypatch) -> None:
    app = load_site_server(monkeypatch)
    calls: list[str] = []
    posts = [
        {
            "id": 1,
            "status": "published",
            "slug": "library-notes",
            "title": "Library Notes",
            "excerpt": "Catalog updates",
            "body": "A long entry about archives.",
            "published_on": "2026-05-15",
        },
        {
            "id": 2,
            "status": "published",
            "slug": "garden",
            "title": "Garden",
            "excerpt": "Soil and flowers",
            "body": "A long entry about tomatoes.",
            "published_on": "2026-05-14",
        },
        {
            "id": 3,
            "status": "published",
            "slug": "zebra",
            "title": "Zebra",
            "excerpt": "Alphabet end",
            "body": "A long entry about stripes.",
            "published_on": "2026-05-13",
        },
    ]

    def fake_directus_json(path: str, timeout: float = 5.0) -> dict:
        calls.append(path)
        return {"data": posts}

    monkeypatch.setattr(app, "directus_json", fake_directus_json)

    strict_payload, strict_status = app.blog_posts_payload("q=libary&fuzz=0")
    fuzzy_payload, fuzzy_status = app.blog_posts_payload("q=libary&fuzz=1")
    page_payload, page_status = app.blog_posts_payload("per_page=2&page=2")

    assert strict_status == app.HTTPStatus.OK
    assert strict_payload["posts"] == []
    assert strict_payload["pagination"]["total"] == 0
    assert fuzzy_status == app.HTTPStatus.OK
    assert [post["slug"] for post in fuzzy_payload["posts"]] == ["library-notes"]
    assert fuzzy_payload["search"] == {"query": "libary", "fuzz": 1}
    assert page_status == app.HTTPStatus.OK
    assert [post["slug"] for post in page_payload["posts"]] == ["zebra"]
    assert page_payload["pagination"] == {
        "page": 2,
        "per_page": 2,
        "total": 3,
        "total_pages": 2,
        "has_previous": True,
        "has_next": False,
        "default_per_page": 50,
        "max_allowed_fuzz": 5,
    }
    assert any("body" in call for call in calls)
    assert any("body" not in call for call in calls)
    assert all("limit=-1" in call for call in calls)



def test_blog_post_api_returns_one_published_post_and_404s_missing_or_draft(monkeypatch) -> None:
    app = load_site_server(monkeypatch)
    calls: list[str] = []

    def fake_directus_json(path: str, timeout: float = 5.0) -> dict:
        calls.append(path)
        if "filter%5Bslug%5D%5B_eq%5D=hello-directus" in path:
            return {"data": [{"slug": "hello-directus", "title": "Hello Directus", "status": "published"}]}
        return {"data": []}

    monkeypatch.setattr(app, "directus_json", fake_directus_json)

    payload, status = app.blog_post_payload("hello-directus")
    missing_payload, missing_status = app.blog_post_payload("draft-post")

    assert status == app.HTTPStatus.OK
    assert payload["ok"] is True
    assert payload["post"]["title"] == "Hello Directus"
    assert missing_status == app.HTTPStatus.NOT_FOUND
    assert missing_payload["post"] is None
    assert all("filter%5Bstatus%5D%5B_eq%5D=published" in call for call in calls)
    assert all("body" in call for call in calls)
    assert all("limit=1" in call for call in calls)


def test_blog_api_routes_exist_but_blog_pages_are_not_site_server_owned(monkeypatch) -> None:
    app = load_site_server(monkeypatch)
    monkeypatch.setattr(app, "safe_static_file", lambda path: None)
    monkeypatch.setattr(app, "blog_posts_payload", lambda raw_query="": ({"ok": True, "posts": []}, app.HTTPStatus.OK))

    calls: list[tuple[str, object, object]] = []

    def send_json(payload, status=app.HTTPStatus.OK):
        calls.append(("json", payload, status))

    def send_bytes(data, content_type, status=app.HTTPStatus.OK):
        calls.append(("bytes", content_type, status))

    handler = app.SiteServerHandler.__new__(app.SiteServerHandler)
    handler._send_json = send_json
    handler._send_bytes = send_bytes

    handler.path = "/api/site/blog/posts"
    handler.do_GET()
    handler.path = "/blog"
    handler.do_GET()
    handler.path = "/blog/hello-directus"
    handler.do_GET()

    assert calls[0] == ("json", {"ok": True, "posts": []}, app.HTTPStatus.OK)
    assert calls[1][0] == "json"
    assert calls[1][2] == app.HTTPStatus.NOT_FOUND
    assert calls[1][1]["path"] == "/blog"
    assert calls[2][0] == "json"
    assert calls[2][2] == app.HTTPStatus.NOT_FOUND
    assert calls[2][1]["path"] == "/blog/hello-directus"


def test_user_owned_directory_index_can_handle_nested_blog_viewer_routes(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SITE_ID", "route-site")
    content_root = tmp_path / "runtime" / "websites"
    site_dir = content_root / "route-site"
    (site_dir / "blog").mkdir(parents=True)
    (site_dir / "blog" / "index.html").write_text("<html><body>Blog viewer</body></html>", encoding="utf-8")
    monkeypatch.setenv("CONTENT_ROOT", str(content_root))

    spec = importlib.util.spec_from_file_location("main_computer_site_server_app_route_test", APP)
    assert spec is not None
    assert spec.loader is not None
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)

    assert app.safe_static_file("/blog") == site_dir / "blog" / "index.html"
    assert app.safe_static_file("/blog/hello-directus") == site_dir / "blog" / "index.html"
    assert app.safe_static_file("/missing/hello-directus") is None
