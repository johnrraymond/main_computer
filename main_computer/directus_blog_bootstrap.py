from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DIRECTUS_BLOG_COLLECTION = "posts"
DIRECTUS_PUBLIC_FIELDS = [
    "id",
    "status",
    "slug",
    "title",
    "excerpt",
    "body",
    "published_on",
    "read_time_minutes",
    "is_legacy",
    # Keep the legacy Blog fields public as compatibility-only fallbacks for
    # older local Directus installs and generated pages. The live Directus Blog
    # contract uses ``published_on`` as the public publish-date field.
    "featured_image",
]
DIRECTUS_FILE_FIELDS = ["id", "storage", "filename_download", "title", "type"]
DIRECTUS_FIELD_DEFINITIONS: list[dict[str, Any]] = [
    {
        "field": "status",
        "type": "string",
        "schema": {"type": "varchar", "max_length": 255, "is_nullable": False, "default_value": "draft"},
        "meta": {
            "interface": "select-dropdown",
            "display": "labels",
            "required": True,
            "options": {"choices": [{"text": "Draft", "value": "draft"}, {"text": "Published", "value": "published"}]},
        },
    },
    {
        "field": "slug",
        "type": "string",
        "schema": {"type": "varchar", "max_length": 255, "is_nullable": False, "is_unique": True},
        "meta": {"interface": "input", "required": True},
    },
    {
        "field": "title",
        "type": "string",
        "schema": {"type": "varchar", "max_length": 255, "is_nullable": False},
        "meta": {"interface": "input", "required": True},
    },
    {
        "field": "excerpt",
        "type": "text",
        "schema": {"type": "text", "is_nullable": True},
        "meta": {"interface": "input-multiline"},
    },
    {
        "field": "body",
        "type": "text",
        "schema": {"type": "text", "is_nullable": False},
        "meta": {"interface": "input-multiline", "required": True},
    },
    {
        "field": "published_on",
        "type": "date",
        "schema": {"type": "date", "is_nullable": True},
        "meta": {"interface": "datetime", "note": "Public Blog publication date. Use this instead of Directus date_created."},
    },
    {
        "field": "read_time_minutes",
        "type": "integer",
        "schema": {"type": "integer", "is_nullable": True},
        "meta": {"interface": "input", "note": "Optional public read-time display, for example 7 min read."},
    },
    {
        "field": "is_legacy",
        "type": "string",
        "schema": {"type": "varchar", "max_length": 32, "is_nullable": True},
        "meta": {"interface": "select-dropdown", "options": {"choices": [{"text": "Yes", "value": "yes"}, {"text": "No", "value": "no"}]}},
    },
    {
        "field": "featured_image",
        "type": "uuid",
        "schema": {"type": "char", "max_length": 36, "is_nullable": True},
        "meta": {"interface": "input", "note": "Directus file id used by public Blog rendering."},
    },
]


class DirectusBlogBootstrapError(RuntimeError):
    """Raised when the Directus Blog runtime contract cannot be applied."""


def ensure_directus_blog_runtime(
    public_url: str,
    *,
    admin_email: str,
    admin_password: str,
    collection: str = DIRECTUS_BLOG_COLLECTION,
    timeout_s: float = 8.0,
) -> dict[str, Any]:
    """Apply the deploy-time Directus Blog schema and anonymous public-read policy.

    Directus 11 grants anonymous reads through an access row whose role and user
    are both null. Creating a policy named "Public" is not sufficient; the
    permission must be attached to the policy referenced by that anonymous access
    row. This helper intentionally discovers that row instead of guessing.
    """

    base_url = str(public_url or "").rstrip("/")
    if not base_url:
        return {"ok": False, "error": "Directus public URL is missing.", "steps": []}
    clean_collection = _validate_collection_name(collection)
    steps: list[dict[str, Any]] = []

    try:
        token = _login(base_url, admin_email=admin_email, admin_password=admin_password, timeout_s=timeout_s)

        collection_result = _ensure_collection(base_url, token, clean_collection, timeout_s=timeout_s)
        steps.append(collection_result)
        if not collection_result["ok"]:
            return {"ok": False, "error": collection_result["message"], "steps": steps}

        fields_result = _ensure_fields(base_url, token, clean_collection, timeout_s=timeout_s)
        steps.append(fields_result)
        if not fields_result["ok"]:
            return {"ok": False, "error": fields_result["message"], "steps": steps}

        permissions_result = _ensure_public_read_permissions(base_url, token, clean_collection, timeout_s=timeout_s)
        steps.append(permissions_result)
        if not permissions_result["ok"]:
            return {"ok": False, "error": permissions_result["message"], "steps": steps}

        anonymous_result = _verify_anonymous_posts_read(base_url, clean_collection, timeout_s=timeout_s)
        steps.append(anonymous_result)
        if not anonymous_result["ok"]:
            return {"ok": False, "error": anonymous_result["message"], "steps": steps}

        return {
            "ok": True,
            "collection": clean_collection,
            "anonymous_policy": permissions_result.get("anonymous_policy", ""),
            "steps": steps,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "steps": steps}


def _login(base_url: str, *, admin_email: str, admin_password: str, timeout_s: float) -> str:
    ok, status, raw, parsed = _directus_request(
        base_url,
        "/auth/login",
        method="POST",
        payload={"email": admin_email, "password": admin_password},
        timeout_s=timeout_s,
    )
    if not ok:
        raise DirectusBlogBootstrapError(f"Directus admin login failed: status={status}; body={_compact(raw)}")
    data = _data_of(parsed)
    token = data.get("access_token") if isinstance(data, dict) else ""
    token = str(token or "")
    if not token:
        raise DirectusBlogBootstrapError(f"Directus admin login returned no access token: {_compact(parsed)}")
    return token


def _ensure_collection(base_url: str, token: str, collection: str, *, timeout_s: float) -> dict[str, Any]:
    ok, _status, _raw, _parsed = _directus_request(base_url, f"/collections/{collection}", token=token, timeout_s=timeout_s)
    if ok:
        return {"ok": True, "step": "schema.collection", "action": "reused", "collection": collection, "message": f"{collection} collection exists"}

    payload = {
        "collection": collection,
        "meta": {
            "collection": collection,
            "icon": "article",
            "note": "Main Computer Blog posts collection.",
            "display_template": "{{title}}",
        },
        "schema": {},
    }
    created_ok, status, raw, _created = _directus_request(
        base_url,
        "/collections",
        token=token,
        method="POST",
        payload=payload,
        timeout_s=timeout_s,
    )
    if created_ok or _looks_like_existing_resource(raw):
        return {"ok": True, "step": "schema.collection", "action": "created" if created_ok else "reused", "collection": collection, "message": f"{collection} collection ready"}
    return {"ok": False, "step": "schema.collection", "collection": collection, "message": f"failed to create {collection} collection: status={status}; body={_compact(raw)}"}


def _ensure_fields(base_url: str, token: str, collection: str, *, timeout_s: float) -> dict[str, Any]:
    list_ok, status, raw, parsed = _directus_request(base_url, f"/fields/{collection}", token=token, timeout_s=timeout_s)
    existing_names: set[str] = set()
    if list_ok:
        data = _data_of(parsed)
        if isinstance(data, list):
            existing_names = {
                str(item.get("field"))
                for item in data
                if isinstance(item, dict) and item.get("field")
            }

    created: list[str] = []
    reused: list[str] = []
    for definition in DIRECTUS_FIELD_DEFINITIONS:
        field = str(definition["field"])
        if field in existing_names:
            reused.append(field)
            continue
        created_ok, create_status, create_raw, _create = _directus_request(
            base_url,
            f"/fields/{collection}",
            token=token,
            method="POST",
            payload=definition,
            timeout_s=timeout_s,
        )
        if created_ok:
            created.append(field)
            continue
        if _looks_like_existing_resource(create_raw):
            reused.append(field)
            continue
        detail = _compact(raw) if not list_ok else _compact(create_raw)
        status_for_error = status if not list_ok else create_status
        return {
            "ok": False,
            "step": "schema.fields",
            "collection": collection,
            "field": field,
            "message": f"failed to ensure field {field!r}: status={status_for_error}; body={detail}",
        }

    return {
        "ok": True,
        "step": "schema.fields",
        "collection": collection,
        "created": created,
        "reused": reused,
        "message": f"ensured {collection} fields",
    }


def _ensure_public_read_permissions(base_url: str, token: str, collection: str, *, timeout_s: float) -> dict[str, Any]:
    policy_ok, policy, policy_message = _public_policy_id(base_url, token, timeout_s=timeout_s)
    if not policy_ok:
        return {"ok": False, "step": "permissions.public_policy", "message": policy_message}

    list_ok, permissions, list_message, policy_mode = _list_permissions(base_url, token, timeout_s=timeout_s)
    if not list_ok:
        return {"ok": False, "step": "permissions.list", "message": list_message}

    posts_payload = {
        "collection": collection,
        "action": "read",
        "policy": policy,
        "permissions": {"status": {"_eq": "published"}},
        "validation": {},
        "presets": {},
        "fields": DIRECTUS_PUBLIC_FIELDS,
    }
    posts_ok, posts_message = _upsert_permission(
        base_url,
        token,
        permissions,
        posts_payload,
        policy_mode=policy_mode,
        timeout_s=timeout_s,
    )
    if not posts_ok:
        return {"ok": False, "step": "permissions.posts_read", "anonymous_policy": policy, "message": f"{policy_message}; {posts_message}"}

    list_ok, permissions, _list_message, policy_mode = _list_permissions(base_url, token, timeout_s=timeout_s)
    if not list_ok:
        return {"ok": False, "step": "permissions.relist", "anonymous_policy": policy, "message": _list_message}

    files_payload = {
        "collection": "directus_files",
        "action": "read",
        "policy": policy,
        "permissions": {},
        "validation": {},
        "presets": {},
        "fields": DIRECTUS_FILE_FIELDS,
    }
    files_ok, files_message = _upsert_permission(
        base_url,
        token,
        permissions,
        files_payload,
        policy_mode=policy_mode,
        timeout_s=timeout_s,
    )
    if not files_ok:
        return {"ok": False, "step": "permissions.files_read", "anonymous_policy": policy, "message": f"{policy_message}; {posts_message}; {files_message}"}

    return {
        "ok": True,
        "step": "permissions.public_read",
        "anonymous_policy": policy,
        "message": f"{policy_message}; {posts_message}; {files_message}",
    }


def _verify_anonymous_posts_read(base_url: str, collection: str, *, timeout_s: float) -> dict[str, Any]:
    query = urlencode(
        [
            ("fields", "slug,status,title,published_on,read_time_minutes,is_legacy"),
            ("sort", "slug"),
            ("filter[status][_eq]", "published"),
            ("limit", "5"),
        ]
    )
    ok, status, raw, parsed = _directus_request(
        base_url,
        f"/items/{collection}?{query}",
        timeout_s=timeout_s,
    )
    if not ok:
        return {"ok": False, "step": "verify.anonymous_posts", "message": f"anonymous {collection} read failed: status={status}; body={_compact(raw)}"}
    data = _data_of(parsed)
    if not isinstance(data, list):
        return {"ok": False, "step": "verify.anonymous_posts", "message": f"anonymous {collection} read returned unexpected payload: {_compact(parsed)}"}
    slugs = [str(item.get("slug") or "") for item in data if isinstance(item, dict) and item.get("slug")]
    return {"ok": True, "step": "verify.anonymous_posts", "post_slugs": slugs, "message": f"anonymous {collection} read succeeded"}


def _list_policies(base_url: str, token: str, *, timeout_s: float) -> tuple[bool, list[dict[str, Any]], str]:
    ok, status, raw, parsed = _directus_request(
        base_url,
        "/policies?limit=-1&fields=id,name,icon,app_access,admin_access",
        token=token,
        timeout_s=timeout_s,
    )
    if not ok:
        return False, [], f"policies API failed: status={status}; body={_compact(raw)}"
    data = _data_of(parsed)
    if not isinstance(data, list):
        return False, [], f"policies API returned unexpected payload: {_compact(parsed)}"
    return True, [item for item in data if isinstance(item, dict)], "listed policies"


def _list_access_rows(base_url: str, token: str, *, timeout_s: float) -> tuple[bool, list[dict[str, Any]], str]:
    attempts = [
        ("/access", "id,role,user,policy"),
        ("/items/directus_access", "id,role,user,policy"),
    ]
    errors: list[str] = []
    for endpoint, fields in attempts:
        ok, status, raw, parsed = _directus_request(
            base_url,
            f"{endpoint}?limit=-1&fields={fields}",
            token=token,
            timeout_s=timeout_s,
        )
        if not ok:
            errors.append(f"{endpoint} failed: status={status}; body={_compact(raw)}")
            continue
        data = _data_of(parsed)
        if isinstance(data, list):
            return True, [item for item in data if isinstance(item, dict)], f"listed Directus access rows from {endpoint}"
        errors.append(f"{endpoint} returned unexpected payload: {_compact(parsed)}")
    return False, [], "; ".join(errors)


def _public_policy_id(base_url: str, token: str, *, timeout_s: float) -> tuple[bool, str, str]:
    policies_ok, policies, policies_message = _list_policies(base_url, token, timeout_s=timeout_s)
    if not policies_ok:
        return False, "", policies_message
    access_ok, access_rows, access_message = _list_access_rows(base_url, token, timeout_s=timeout_s)
    if access_ok:
        anonymous = _anonymous_policy_ids(access_rows)
        if anonymous:
            def score(policy: dict[str, Any]) -> tuple[int, str]:
                policy_id = str(policy.get("id") or "")
                name = str(policy.get("name") or "").lower()
                icon = str(policy.get("icon") or "").lower()
                publicish = (
                    name in {"public", "$t:public_label"}
                    or "public_label" in name
                    or icon == "public"
                )
                return (0 if publicish else 1, policy_id)

            candidates = [policy for policy in policies if str(policy.get("id") or "") in anonymous]
            candidates.sort(key=score)
            if candidates:
                policy = str(candidates[0].get("id") or "")
                return True, policy, f"using Directus anonymous public policy {policy}; {access_message}"
            policy = sorted(anonymous)[0]
            return True, policy, f"using Directus anonymous public policy {policy}; {access_message}"
        return False, "", f"{access_message}; no Directus anonymous public access row was found"

    for item in policies:
        if str(item.get("name") or "").lower() == "public":
            policy = str(item.get("id") or "")
            if policy:
                return True, policy, f"using legacy Directus public policy {policy}; access lookup failed: {access_message}"
    return False, "", f"could not discover Directus anonymous public policy; {access_message}"


def _list_permissions(base_url: str, token: str, *, timeout_s: float) -> tuple[bool, list[dict[str, Any]], str, bool]:
    attempts = [
        ("id,collection,action,policy,fields,permissions", True),
        ("id,collection,action,role,fields,permissions", False),
    ]
    errors: list[str] = []
    for fields, policy_mode in attempts:
        ok, status, raw, parsed = _directus_request(
            base_url,
            f"/permissions?limit=-1&fields={fields}",
            token=token,
            timeout_s=timeout_s,
        )
        if not ok:
            errors.append(f"permissions fields={fields}: status={status}; body={_compact(raw)}")
            continue
        data = _data_of(parsed)
        if isinstance(data, list):
            return True, [item for item in data if isinstance(item, dict)], f"listed permissions fields={fields}", policy_mode
        errors.append(f"permissions fields={fields}: unexpected payload={_compact(parsed)}")
    return False, [], "; ".join(errors), True


def _upsert_permission(
    base_url: str,
    token: str,
    permissions: list[dict[str, Any]],
    payload: dict[str, Any],
    *,
    policy_mode: bool,
    timeout_s: float,
) -> tuple[bool, str]:
    existing_id = ""
    for item in permissions:
        if item.get("collection") != payload.get("collection") or item.get("action") != payload.get("action"):
            continue
        if policy_mode and _directus_id(item.get("policy")) == str(payload.get("policy") or ""):
            existing_id = str(item.get("id") or "")
            break
        if not policy_mode and _empty_directus_identity(item.get("role")):
            existing_id = str(item.get("id") or "")
            break

    if existing_id:
        path = f"/permissions/{existing_id}"
        method = "PATCH"
        action = "updated"
    else:
        path = "/permissions"
        method = "POST"
        action = "created"

    ok, status, raw, _parsed = _directus_request(
        base_url,
        path,
        token=token,
        method=method,
        payload=payload,
        timeout_s=timeout_s,
    )
    if not ok:
        return False, f"failed to {action} permission {payload.get('collection')}:{payload.get('action')}: status={status}; body={_compact(raw)}"
    return True, f"{action} permission {payload.get('collection')}:{payload.get('action')}"


def _directus_request(
    base_url: str,
    path: str,
    *,
    token: str = "",
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout_s: float = 8.0,
) -> tuple[bool, int | None, str, object]:
    url = base_url.rstrip("/") + path
    body = None
    headers = {"Accept": "application/json", "User-Agent": "main-computer-directus-blog-bootstrap/1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method=method.upper())
    try:
        with urlopen(request, timeout=timeout_s) as response:
            raw = response.read(1024 * 1024).decode("utf-8", errors="replace")
            status = getattr(response, "status", 200)
    except HTTPError as exc:
        raw = exc.read(256 * 1024).decode("utf-8", errors="replace")
        return False, exc.code, raw, _parse_json(raw)
    except URLError as exc:
        return False, None, str(exc), {}
    return 200 <= int(status) < 300, int(status), raw, _parse_json(raw)


def _data_of(parsed: object) -> object:
    if isinstance(parsed, dict) and "data" in parsed:
        return parsed["data"]
    return parsed


def _directus_id(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("id") or "")
    if value is None:
        return ""
    return str(value)


def _empty_directus_identity(value: object) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, dict):
        return not bool(value.get("id"))
    return False


def _anonymous_policy_ids(access_rows: list[dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    for row in access_rows:
        if _empty_directus_identity(row.get("role")) and _empty_directus_identity(row.get("user")):
            policy = _directus_id(row.get("policy"))
            if policy:
                found.add(policy)
    return found


def _looks_like_existing_resource(raw: object) -> bool:
    text = _compact(raw).lower()
    return any(fragment in text for fragment in ("already exists", "already in use", "unique", "duplicate"))


def _parse_json(raw: str) -> object:
    try:
        return json.loads(raw) if str(raw or "").strip() else {}
    except json.JSONDecodeError:
        return {}


def _compact(value: object, limit: int = 500) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, sort_keys=True)
        except TypeError:
            text = str(value)
    text = " ".join(text.split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _validate_collection_name(value: object) -> str:
    clean = str(value or "").strip()
    if not clean or not clean.replace("_", "").isalnum():
        raise DirectusBlogBootstrapError(f"Unsafe Directus collection name: {value!r}")
    return clean
