from __future__ import annotations

from typing import Any

from main_computer import directus_blog_bootstrap


def test_directus_blog_bootstrap_attaches_public_permissions_to_anonymous_policy(monkeypatch) -> None:
    state: dict[str, Any] = {
        "collections": set(),
        "fields": set(),
        "permissions": [],
        "policies": [
            {
                "id": "admin-policy",
                "name": "Administrator",
                "icon": "verified",
                "app_access": True,
                "admin_access": True,
            },
            {
                "id": "public-policy",
                "name": "$t:public_label",
                "icon": "public",
                "app_access": False,
                "admin_access": False,
            },
        ],
        "access": [
            {"id": "admin-access", "role": "admin-role", "user": None, "policy": "admin-policy"},
            {"id": "anon-access", "role": None, "user": None, "policy": "public-policy"},
        ],
        "anonymous_item_queries": [],
    }

    def fake_request(
        base_url: str,
        path: str,
        *,
        token: str = "",
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        timeout_s: float = 8.0,
    ) -> tuple[bool, int | None, str, object]:
        assert base_url == "http://127.0.0.1:28200"
        if path == "/auth/login" and method == "POST":
            return True, 200, '{"data":{"access_token":"token"}}', {"data": {"access_token": "token"}}

        if path == "/collections/posts" and method == "GET":
            if "posts" in state["collections"]:
                return True, 200, '{"data":{"collection":"posts"}}', {"data": {"collection": "posts"}}
            return False, 404, '{"errors":[{"message":"not found"}]}', {}

        if path == "/collections" and method == "POST":
            state["collections"].add(str(payload.get("collection")))
            return True, 200, '{"data":{"collection":"posts"}}', {"data": {"collection": "posts"}}

        if path == "/fields/posts" and method == "GET":
            return True, 200, '{"data":[]}', {"data": [{"field": field} for field in sorted(state["fields"])]}

        if path == "/fields/posts" and method == "POST":
            state["fields"].add(str(payload.get("field")))
            return True, 200, '{"data":{}}', {"data": {}}

        if path.startswith("/policies"):
            return True, 200, "{}", {"data": state["policies"]}

        if path.startswith("/access"):
            return True, 200, "{}", {"data": state["access"]}

        if path.startswith("/permissions?"):
            return True, 200, "{}", {"data": state["permissions"]}

        if path == "/permissions" and method == "POST":
            new_permission = dict(payload or {})
            new_permission["id"] = f"permission-{len(state['permissions']) + 1}"
            state["permissions"].append(new_permission)
            return True, 200, "{}", {"data": new_permission}

        if path.startswith("/items/posts?") and method == "GET" and not token:
            state["anonymous_item_queries"].append(path)
            has_public_posts = any(
                item.get("collection") == "posts"
                and item.get("action") == "read"
                and item.get("policy") == "public-policy"
                for item in state["permissions"]
            )
            if has_public_posts:
                return True, 200, '{"data":[]}', {"data": []}
            return False, 403, '{"errors":[{"message":"forbidden"}]}', {}

        raise AssertionError(f"unexpected Directus request: {method} {path} token={token!r} payload={payload!r}")

    monkeypatch.setattr(directus_blog_bootstrap, "_directus_request", fake_request)

    result = directus_blog_bootstrap.ensure_directus_blog_runtime(
        "http://127.0.0.1:28200",
        admin_email="admin@example.com",
        admin_password="Admin-password-1!",
    )

    assert result["ok"] is True
    assert result["anonymous_policy"] == "public-policy"
    assert {item["collection"] for item in state["permissions"]} == {"posts", "directus_files"}
    posts_permission = next(item for item in state["permissions"] if item["collection"] == "posts")
    assert posts_permission["permissions"] == {"status": {"_eq": "published"}}
    assert "published_on" in posts_permission["fields"]
    assert "read_time_minutes" in posts_permission["fields"]
    assert "is_legacy" in posts_permission["fields"]
    assert {"published_on", "read_time_minutes", "is_legacy"}.issubset(state["fields"])
    assert any("published_on%2Cread_time_minutes%2Cis_legacy" in path for path in state["anonymous_item_queries"])


def test_directus_blog_bootstrap_refuses_unassigned_public_policy(monkeypatch) -> None:
    def fake_request(
        base_url: str,
        path: str,
        *,
        token: str = "",
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        timeout_s: float = 8.0,
    ) -> tuple[bool, int | None, str, object]:
        if path == "/auth/login":
            return True, 200, "{}", {"data": {"access_token": "token"}}
        if path == "/collections/posts":
            return True, 200, "{}", {"data": {"collection": "posts"}}
        if path == "/fields/posts":
            return True, 200, "{}", {"data": [{"field": item["field"]} for item in directus_blog_bootstrap.DIRECTUS_FIELD_DEFINITIONS]}
        if path.startswith("/policies"):
            return True, 200, "{}", {"data": [{"id": "public-policy", "name": "$t:public_label", "icon": "public"}]}
        if path.startswith("/access"):
            return True, 200, "{}", {"data": []}
        raise AssertionError(f"unexpected Directus request: {method} {path}")

    monkeypatch.setattr(directus_blog_bootstrap, "_directus_request", fake_request)

    result = directus_blog_bootstrap.ensure_directus_blog_runtime(
        "http://127.0.0.1:28200",
        admin_email="admin@example.com",
        admin_password="Admin-password-1!",
    )

    assert result["ok"] is False
    assert "no Directus anonymous public access row was found" in result["error"]
