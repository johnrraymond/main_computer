#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


REMOTE_TARGET_KEYS = ("remote_prod", "remote-prod", "publish", "remote")
UUID_KEYS = (
    "resource_uuid",
    "application_uuid",
    "coolify_resource_uuid",
    "coolify_application_uuid",
    "deploy_uuid",
    "uuid",
)


def die(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        die(f"Invalid JSON: {path}: {exc}")
    return payload if isinstance(payload, dict) else {}


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def repo_import(repo_root: Path) -> None:
    sys.path.insert(0, str(repo_root))


def load_controller(repo_root: Path, controller_id: str) -> Any:
    repo_import(repo_root)
    try:
        from main_computer.deployment_controllers import load_deployment_controller_registry
    except Exception as exc:
        die(f"Could not import deployment controller registry from {repo_root}: {exc}")

    registry = load_deployment_controller_registry(repo_root)
    controller = registry.get(controller_id)
    if not controller:
        known = ", ".join(item["id"] for item in registry.to_dict().get("controllers", []))
        die(f"Controller not found: {controller_id}. Known controllers: {known}")
    return controller


def load_default_remote_controller_id(repo_root: Path) -> str:
    repo_import(repo_root)
    try:
        from main_computer.deployment_controllers import load_deployment_controller_registry
    except Exception as exc:
        die(f"Could not import deployment controller registry from {repo_root}: {exc}")

    registry = load_deployment_controller_registry(repo_root)
    defaults = registry.defaults_for("remote-prod")
    if not defaults:
        die("No default remote-prod deployment controller is configured.")
    return defaults[0].id


def site_manifest_path(repo_root: Path, site_id: str) -> Path:
    return repo_root / "runtime" / "websites" / site_id / "site.json"


def load_site_target(repo_root: Path, site_id: str, lane: str) -> dict[str, Any]:
    manifest = read_json(site_manifest_path(repo_root, site_id))
    targets = manifest.get("publish_targets")
    if not isinstance(targets, dict):
        return {}

    lane_key = lane.replace("-", "_")
    target = targets.get(lane_key)
    if isinstance(target, dict):
        return dict(target)

    for key in REMOTE_TARGET_KEYS:
        target = targets.get(key)
        if isinstance(target, dict):
            return dict(target)

    return {}


def first_nonempty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def find_uuid(source: dict[str, Any]) -> str:
    for key in UUID_KEYS:
        value = str(source.get(key) or "").strip()
        if value:
            return value

    coolify = source.get("coolify")
    if isinstance(coolify, dict):
        for key in UUID_KEYS:
            value = str(coolify.get(key) or "").strip()
            if value:
                return value

    deploy = source.get("deploy")
    if isinstance(deploy, dict):
        for key in UUID_KEYS:
            value = str(deploy.get(key) or "").strip()
            if value:
                return value

    return ""


def resolve_config(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()

    if args.env_file:
        load_dotenv(Path(args.env_file).resolve())
    load_dotenv(repo_root / ".env")
    load_dotenv(repo_root / "runtime" / "deployment" / ".env")

    target = load_site_target(repo_root, args.site_id, args.lane) if args.site_id else {}

    controller_id = first_nonempty(
        args.controller,
        target.get("controller_id"),
        load_default_remote_controller_id(repo_root),
    )

    controller = load_controller(repo_root, controller_id)

    base_url = first_nonempty(
        args.base_url,
        target.get("coolify_base_url"),
        target.get("publishing_server_url"),
        target.get("server_url"),
        controller.base_url,
    ).rstrip("/")

    token_ref = first_nonempty(
        args.token_env,
        target.get("token_ref"),
        target.get("api_token"),
        target.get("api_token_ref"),
        controller.token_ref,
    )

    token = first_nonempty(args.token, os.environ.get(token_ref))

    if not token and args.token_file:
        token = Path(args.token_file).read_text(encoding="utf-8").strip()

    uuid = first_nonempty(args.uuid, find_uuid(target))

    return {
        "repo_root": str(repo_root),
        "site_id": args.site_id,
        "lane": args.lane,
        "target": target,
        "controller_id": controller_id,
        "controller_name": controller.name,
        "base_url": base_url,
        "token_ref": token_ref,
        "has_token": bool(token),
        "token": token,
        "uuid": uuid,
    }


def request_json(method: str, url: str, token: str = "", body: dict[str, Any] | None = None, timeout: float = 30.0) -> tuple[int, Any]:
    headers = {"Accept": "application/json"}
    data = None

    if token:
        headers["Authorization"] = f"Bearer {token}"

    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(text)
        except Exception:
            payload = text
        return exc.code, payload
    except urllib.error.URLError as exc:
        die(f"Request failed: {exc}")

    try:
        payload = json.loads(text) if text.strip() else {}
    except Exception:
        payload = text.strip()
    return status, payload


def coolify_url(config: dict[str, Any], path: str, query: dict[str, Any] | None = None) -> str:
    base_url = str(config["base_url"]).rstrip("/")
    clean = "/" + path.strip("/")
    if not clean.startswith("/api/"):
        clean = "/api/v1" + clean

    url = base_url + clean
    if query:
        url += "?" + urllib.parse.urlencode(query)
    return url


def print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def require_ready(config: dict[str, Any], *, need_uuid: bool = False) -> None:
    if not config["base_url"]:
        die("Missing Coolify base URL.")
    if not config["token_ref"]:
        die("Missing token env var name. Set controller.token_ref or pass --token-env.")
    if not config["has_token"]:
        die(f"Token env var is empty/missing: {config['token_ref']}")
    if need_uuid and not config["uuid"]:
        die(
            "Missing Coolify deploy UUID. Pass --uuid, or save one of these keys into "
            f"publish_targets.remote_prod: {', '.join(UUID_KEYS)}"
        )


def cmd_show(args: argparse.Namespace) -> None:
    config = resolve_config(args)
    safe = dict(config)
    safe.pop("token", None)
    safe["target"] = config["target"]
    print_json(safe)


def cmd_health(args: argparse.Namespace) -> None:
    config = resolve_config(args)
    url = str(config["base_url"]).rstrip("/") + "/api/health"

    if args.dry_run:
        print_json({"method": "GET", "url": url})
        return

    status, payload = request_json("GET", url, timeout=args.timeout)
    print_json({"status": status, "body": payload})
    if not (200 <= status < 300):
        raise SystemExit(1)


def cmd_list(args: argparse.Namespace) -> None:
    config = resolve_config(args)
    require_ready(config)

    url = coolify_url(config, f"/{args.what}")

    if args.dry_run:
        print_json({"method": "GET", "url": url, "token_ref": config["token_ref"]})
        return

    status, payload = request_json("GET", url, token=config["token"], timeout=args.timeout)
    print_json({"status": status, "body": payload})
    if not (200 <= status < 300):
        raise SystemExit(1)


def cmd_deploy(args: argparse.Namespace) -> None:
    config = resolve_config(args)
    require_ready(config, need_uuid=True)

    if args.method == "POST":
        url = coolify_url(config, "/deploy")
        body = {"uuid": config["uuid"], "force": args.force}
    else:
        url = coolify_url(config, "/deploy", {"uuid": config["uuid"], "force": str(args.force).lower()})
        body = None

    if args.dry_run:
        print_json({
            "method": args.method,
            "url": url,
            "body": body,
            "controller_id": config["controller_id"],
            "token_ref": config["token_ref"],
            "uuid": config["uuid"],
        })
        return

    status, payload = request_json(args.method, url, token=config["token"], body=body, timeout=args.timeout)
    print_json({"status": status, "body": payload})
    if not (200 <= status < 300):
        raise SystemExit(1)

    if args.wait:
        wait_for_deployments(config, payload, args)


def deployment_ids(payload: Any) -> list[str]:
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"deployment_uuid", "deployment_id", "uuid"} and isinstance(item, str):
                    found.append(item)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return list(dict.fromkeys(found))


def status_words(payload: Any) -> set[str]:
    words: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() in {"status", "state"} and isinstance(item, str):
                    words.add(item.lower())
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return words


def wait_for_deployments(config: dict[str, Any], payload: Any, args: argparse.Namespace) -> None:
    ids = deployment_ids(payload)
    if not ids:
        print("Deploy request returned no deployment UUID to wait on.", file=sys.stderr)
        return

    deadline = time.time() + args.wait_timeout
    for deploy_id in ids:
        url = coolify_url(config, f"/deployments/{deploy_id}")
        while time.time() < deadline:
            status, body = request_json("GET", url, token=config["token"], timeout=args.timeout)
            print_json({"deployment": deploy_id, "status": status, "body": body})

            if not (200 <= status < 300):
                raise SystemExit(1)

            words = status_words(body)
            if words & {"finished", "success", "successful", "succeeded", "completed"}:
                break
            if words & {"failed", "error", "cancelled", "canceled"}:
                raise SystemExit(1)

            time.sleep(args.poll_interval)
        else:
            die(f"Timed out waiting for deployment {deploy_id}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Coolify deploy twiddle that loads controller/token config from the repo.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--site-id", default="hub-site")
    parser.add_argument("--lane", default="remote-prod")
    parser.add_argument("--controller", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--token-env", default="")
    parser.add_argument("--token", default="")
    parser.add_argument("--token-file", default="")
    parser.add_argument("--uuid", default="")
    parser.add_argument("--env-file", default="")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--dry-run", action="store_true")

    sub = parser.add_subparsers(dest="cmd", required=True)

    show = sub.add_parser("show")
    show.set_defaults(func=cmd_show)

    health = sub.add_parser("health")
    health.set_defaults(func=cmd_health)

    list_cmd = sub.add_parser("list")
    list_cmd.add_argument("what", choices=["applications", "projects", "servers", "resources"])
    list_cmd.set_defaults(func=cmd_list)

    deploy = sub.add_parser("deploy")
    deploy.add_argument("--method", choices=["GET", "POST"], default="GET")
    deploy.add_argument("--force", action="store_true", default=True)
    deploy.add_argument("--no-force", action="store_false", dest="force")
    deploy.add_argument("--wait", action="store_true")
    deploy.add_argument("--wait-timeout", type=float, default=600.0)
    deploy.add_argument("--poll-interval", type=float, default=5.0)
    deploy.set_defaults(func=cmd_deploy)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()