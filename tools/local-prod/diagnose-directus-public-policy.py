from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


STATE_REL = Path("runtime") / "coolify-local-docker" / "directus-blog-e2e-smoke.json"
PUBLIC_FIELDS = [
    "id",
    "status",
    "slug",
    "title",
    "excerpt",
    "body",
    "featured_image",
    "published_at",
]
FILE_FIELDS = ["id", "storage", "filename_download", "title", "type"]


def compact(value: object, limit: int = 1200) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, sort_keys=True)
    else:
        text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def read_state(repo: Path) -> dict[str, Any]:
    path = repo / STATE_REL
    if not path.exists():
        raise SystemExit(f"[FAIL] state file not found: {path}")
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[FAIL] state file is not JSON: {path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit(f"[FAIL] state file did not contain a JSON object: {path}")
    return parsed


def data_of(parsed: object) -> object:
    if isinstance(parsed, dict) and "data" in parsed:
        return parsed["data"]
    return parsed


def http_json(
    base_url: str,
    path: str,
    *,
    token: str = "",
    method: str = "GET",
    payload: object | None = None,
    timeout: float = 15.0,
) -> tuple[bool, int, str, object | None]:
    url = base_url.rstrip("/") + path
    body = None
    headers = {
        "Accept": "application/json",
        "User-Agent": "main-computer-directus-policy-diagnose/1",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url, data=body, method=method, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(1024 * 1024).decode("utf-8", errors="replace")
            status = int(response.status)
    except HTTPError as exc:
        raw = exc.read(1024 * 1024).decode("utf-8", errors="replace")
        status = int(exc.code)
    except (URLError, TimeoutError, OSError) as exc:
        return False, 0, str(exc), None

    try:
        parsed = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        parsed = None

    return 200 <= status < 300, status, raw, parsed


def policy_id(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("id") or "")
    if value is None:
        return ""
    return str(value)


def role_or_user_is_empty(value: object) -> bool:
    if value is None:
        return True
    if value == "":
        return True
    if isinstance(value, dict) and not value.get("id"):
        return True
    return False


def list_policies(base_url: str, token: str) -> list[dict[str, Any]]:
    fields = "id,name,icon,app_access,admin_access"
    ok, status, raw, parsed = http_json(base_url, f"/policies?limit=-1&fields={fields}", token=token)
    if not ok:
        raise SystemExit(f"[FAIL] could not list policies: status={status}; body={compact(raw)}")
    data = data_of(parsed)
    if not isinstance(data, list):
        raise SystemExit(f"[FAIL] unexpected policies payload: {compact(parsed)}")
    return [item for item in data if isinstance(item, dict)]


def list_access(base_url: str, token: str) -> tuple[str, list[dict[str, Any]]]:
    attempts = [
        ("/access", "id,role,user,policy,policy.id,policy.name,policy.icon"),
        ("/access", "id,role,user,policy"),
        ("/items/directus_access", "id,role,user,policy"),
    ]
    errors: list[str] = []
    for endpoint, fields in attempts:
        ok, status, raw, parsed = http_json(base_url, f"{endpoint}?limit=-1&fields={fields}", token=token)
        if not ok:
            errors.append(f"{endpoint} fields={fields}: status={status}; body={compact(raw, 400)}")
            continue
        data = data_of(parsed)
        if isinstance(data, list):
            return endpoint, [item for item in data if isinstance(item, dict)]
        errors.append(f"{endpoint} fields={fields}: unexpected payload={compact(parsed, 400)}")
    raise SystemExit("[FAIL] could not list Directus access rows:\n  - " + "\n  - ".join(errors))


def list_permissions(base_url: str, token: str) -> list[dict[str, Any]]:
    fields = "id,collection,action,policy,fields,permissions"
    ok, status, raw, parsed = http_json(base_url, f"/permissions?limit=-1&fields={fields}", token=token)
    if not ok:
        raise SystemExit(f"[FAIL] could not list permissions: status={status}; body={compact(raw)}")
    data = data_of(parsed)
    if not isinstance(data, list):
        raise SystemExit(f"[FAIL] unexpected permissions payload: {compact(parsed)}")
    return [item for item in data if isinstance(item, dict)]


def anonymous_policy_ids(access_rows: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for row in access_rows:
        if role_or_user_is_empty(row.get("role")) and role_or_user_is_empty(row.get("user")):
            pid = policy_id(row.get("policy"))
            if pid:
                ids.add(pid)
    return ids


def test_anonymous_posts(base_url: str) -> tuple[bool, str, set[str]]:
    query = urlencode(
        [
            ("fields", "slug,status,title"),
            ("sort", "slug"),
        ]
    )
    ok, status, raw, parsed = http_json(base_url, f"/items/posts?{query}", token="")
    if not ok:
        return False, f"status={status}; body={compact(raw)}", set()
    data = data_of(parsed)
    if not isinstance(data, list):
        return False, f"unexpected payload={compact(parsed)}", set()
    slugs = {str(item.get("slug") or "") for item in data if isinstance(item, dict)}
    statuses = sorted(
        f"{item.get('slug')}:{item.get('status')}"
        for item in data
        if isinstance(item, dict)
    )
    return True, f"rows={statuses}", slugs


def find_permission(
    permissions: list[dict[str, Any]],
    *,
    collection: str,
    action: str,
    policy: str,
) -> dict[str, Any] | None:
    for item in permissions:
        if item.get("collection") != collection:
            continue
        if item.get("action") != action:
            continue
        if policy_id(item.get("policy")) == policy:
            return item
    return None


def upsert_permission(
    base_url: str,
    token: str,
    permissions: list[dict[str, Any]],
    *,
    collection: str,
    action: str,
    policy: str,
    fields: list[str],
    permissions_filter: dict[str, Any],
) -> None:
    payload = {
        "collection": collection,
        "action": action,
        "policy": policy,
        "permissions": permissions_filter,
        "validation": {},
        "presets": {},
        "fields": fields,
    }
    existing = find_permission(permissions, collection=collection, action=action, policy=policy)
    if existing and existing.get("id"):
        path = f"/permissions/{existing['id']}"
        method = "PATCH"
        label = "updated"
    else:
        path = "/permissions"
        method = "POST"
        label = "created"

    ok, status, raw, _parsed = http_json(base_url, path, token=token, method=method, payload=payload)
    if not ok:
        raise SystemExit(
            f"[FAIL] {label} permission failed for {collection}:{action} policy={policy}: "
            f"status={status}; body={compact(raw)}"
        )
    print(f"[FIX] {label} permission {collection}:{action} on anonymous policy {policy}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Small Directus public-policy diagnostic for the Directus blog smoke. "
            "It does not redeploy anything."
        )
    )
    parser.add_argument("--repo", default=".", help="Repository root. Default: current directory.")
    parser.add_argument("--directus-url", default="", help="Override Directus URL, e.g. http://127.0.0.1:28109")
    parser.add_argument("--admin-token", default="", help="Override Directus admin token. Defaults to smoke state admin_token.")
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Attach posts/files read permissions to the actual anonymous policy, then retest.",
    )
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    state = read_state(repo)
    base_url = args.directus_url or f"http://127.0.0.1:{state.get('directus_port')}"
    token = args.admin_token or str(state.get("admin_token") or "")

    print(f"[INFO] repo: {repo}")
    print(f"[INFO] state: {repo / STATE_REL}")
    print(f"[INFO] site_id: {state.get('site_id')}")
    print(f"[INFO] service_uuid: {state.get('service_uuid')}")
    print(f"[INFO] directus_url: {base_url}")
    print()

    if not token:
        raise SystemExit("[FAIL] no admin token found. Pass --admin-token or rerun after smoke state exists.")

    ok, status, raw, _parsed = http_json(base_url, "/server/ping", token="")
    if not ok or "pong" not in raw.lower():
        raise SystemExit(f"[FAIL] Directus ping failed: status={status}; body={compact(raw)}")
    print("[PASS] Directus ping returned pong")

    anon_ok, anon_detail, anon_slugs = test_anonymous_posts(base_url)
    if anon_ok:
        print(f"[PASS] anonymous /items/posts currently works: {anon_detail}")
    else:
        print(f"[FAIL] anonymous /items/posts currently fails: {anon_detail}")
    print()

    policies = list_policies(base_url, token)
    access_endpoint, access_rows = list_access(base_url, token)
    permissions = list_permissions(base_url, token)
    anon_policy_ids = anonymous_policy_ids(access_rows)

    print(f"[INFO] policies: {len(policies)}")
    for policy in policies:
        pid = str(policy.get("id") or "")
        marker = " <-- ANONYMOUS ACCESS" if pid in anon_policy_ids else ""
        print(
            f"  - id={pid} name={policy.get('name')!r} icon={policy.get('icon')!r} "
            f"app_access={policy.get('app_access')} admin_access={policy.get('admin_access')}{marker}"
        )

    print()
    print(f"[INFO] access rows from {access_endpoint}: {len(access_rows)}")
    for row in access_rows:
        pid = policy_id(row.get("policy"))
        is_anon = pid in anon_policy_ids and role_or_user_is_empty(row.get("role")) and role_or_user_is_empty(row.get("user"))
        marker = " <-- anonymous row" if is_anon else ""
        print(
            f"  - id={row.get('id')} policy={pid or row.get('policy')!r} "
            f"role={row.get('role')!r} user={row.get('user')!r}{marker}"
        )

    print()
    print(f"[INFO] anonymous policy ids: {sorted(anon_policy_ids) or '(none found)'}")

    interesting = [
        item
        for item in permissions
        if item.get("collection") in {"posts", "directus_files"} and item.get("action") == "read"
    ]
    print()
    print(f"[INFO] read permissions for posts/directus_files: {len(interesting)}")
    for item in interesting:
        pid = policy_id(item.get("policy"))
        marker = " <-- attached to anonymous policy" if pid in anon_policy_ids else " <-- NOT anonymous"
        print(
            f"  - id={item.get('id')} collection={item.get('collection')} action={item.get('action')} "
            f"policy={pid!r} fields={item.get('fields')!r} permissions={compact(item.get('permissions'))}{marker}"
        )

    posts_on_anon = [
        item for item in interesting
        if item.get("collection") == "posts" and policy_id(item.get("policy")) in anon_policy_ids
    ]
    files_on_anon = [
        item for item in interesting
        if item.get("collection") == "directus_files" and policy_id(item.get("policy")) in anon_policy_ids
    ]

    print()
    if not anon_policy_ids:
        print("[DIAGNOSIS] I could not find an access row for anonymous public access.")
        print("            That means merely creating a policy cannot make anonymous reads work.")
    elif not posts_on_anon:
        print("[DIAGNOSIS] There is no posts:read permission attached to the actual anonymous policy.")
    elif not files_on_anon:
        print("[DIAGNOSIS] There is no directus_files:read permission attached to the actual anonymous policy.")
    elif not anon_ok:
        print("[DIAGNOSIS] Permissions appear attached to anonymous policy, but anonymous read still fails.")
        print("            Check field list/filter syntax or Directus collection permission internals.")
    else:
        print("[DIAGNOSIS] Public policy wiring is working.")

    if args.fix:
        print()
        if not anon_policy_ids:
            raise SystemExit("[FAIL] --fix refused: no anonymous policy id was discovered.")
        chosen_policy = sorted(anon_policy_ids)[0]
        print(f"[FIX] using anonymous policy: {chosen_policy}")
        upsert_permission(
            base_url,
            token,
            permissions,
            collection="posts",
            action="read",
            policy=chosen_policy,
            fields=PUBLIC_FIELDS,
            permissions_filter={"status": {"_eq": "published"}},
        )
        permissions = list_permissions(base_url, token)
        upsert_permission(
            base_url,
            token,
            permissions,
            collection="directus_files",
            action="read",
            policy=chosen_policy,
            fields=FILE_FIELDS,
            permissions_filter={},
        )
        print()
        anon_ok, anon_detail, anon_slugs = test_anonymous_posts(base_url)
        if anon_ok:
            print(f"[PASS] anonymous /items/posts works after --fix: {anon_detail}")
        else:
            print(f"[FAIL] anonymous /items/posts still fails after --fix: {anon_detail}")

    if anon_ok and "hello-directus" in anon_slugs and "draft-directus" not in anon_slugs:
        print()
        print("[RESULT] published post is public and draft is excluded.")
        return 0

    if anon_ok:
        print()
        print(f"[RESULT] anonymous read works, but slug contract is not right: slugs={sorted(anon_slugs)}")
        return 2

    print()
    print("[RESULT] anonymous read is still broken.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())