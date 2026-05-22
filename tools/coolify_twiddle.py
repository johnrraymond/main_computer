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


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def normalize_base_url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    if not value:
        raise SystemExit("Missing --base-url, or use --repo-root . --controller <id>.")
    return value


def load_controller_defaults(args: argparse.Namespace) -> None:
    if not args.controller:
        return

    repo_root = Path(args.repo_root or ".").resolve()
    sys.path.insert(0, str(repo_root))

    try:
        from main_computer.deployment_controllers import load_deployment_controller_registry
    except Exception as exc:
        raise SystemExit(f"Could not import deployment controller registry from {repo_root}: {exc}") from exc

    registry = load_deployment_controller_registry(repo_root)
    controller = registry.get(args.controller)
    if not controller:
        raise SystemExit(f"Controller not found: {args.controller}")

    if not args.base_url:
        args.base_url = controller.base_url
    if not args.token_env and controller.token_ref:
        args.token_env = controller.token_ref


def resolve_token(args: argparse.Namespace, required: bool = True) -> str:
    if args.token:
        return args.token.strip()

    if args.token_env:
        token = os.environ.get(args.token_env, "").strip()
        if token:
            return token
        if required:
            raise SystemExit(f"Token env var is empty or missing: {args.token_env}")

    if required:
        raise SystemExit("Missing token. Use --token-env ENV_NAME or --token TOKEN.")
    return ""


def make_url(args: argparse.Namespace, path: str, query: dict[str, Any] | None = None) -> str:
    base = normalize_base_url(args.base_url)
    api_prefix = "/" + str(args.api_prefix or "/api/v1").strip("/")
    clean_path = "/" + path.strip("/")

    if clean_path.startswith("/api/"):
        full_path = clean_path
    else:
        full_path = api_prefix + clean_path

    url = base + full_path
    if query:
        encoded = urllib.parse.urlencode(
            {k: str(v).lower() if isinstance(v, bool) else v for k, v in query.items() if v is not None}
        )
        url = f"{url}?{encoded}"
    return url


def request_json(
    method: str,
    url: str,
    *,
    token: str = "",
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> tuple[int, Any, str]:
    headers = {
        "Accept": "application/json",
    }
    data = None

    if token:
        headers["Authorization"] = f"Bearer {token}"

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = text
        return exc.code, parsed, text
    except urllib.error.URLError as exc:
        raise SystemExit(f"Request failed: {exc}") from exc

    try:
        parsed = json.loads(text) if text.strip() else {}
    except Exception:
        parsed = text
    return status, parsed, text


def print_result(status: int, parsed: Any) -> None:
    print(json.dumps({"status": status, "body": parsed}, indent=2, sort_keys=True))


def fail_if_bad(status: int, parsed: Any) -> None:
    if status < 200 or status >= 300:
        print_result(status, parsed)
        raise SystemExit(1)


def cmd_health(args: argparse.Namespace) -> None:
    url = make_url(args, "/api/health")
    if args.dry_run:
        print(json.dumps({"method": "GET", "url": url}, indent=2))
        return

    status, parsed, text = request_json("GET", url, timeout=args.timeout)
    if isinstance(parsed, str):
        parsed = parsed.strip()
    print_result(status, parsed)
    fail_if_bad(status, parsed)


def cmd_list(args: argparse.Namespace) -> None:
    token = resolve_token(args)
    path_map = {
        "applications": "/applications",
        "projects": "/projects",
        "servers": "/servers",
        "resources": "/resources",
    }
    path = path_map[args.what]
    url = make_url(args, path)

    if args.dry_run:
        print(json.dumps({"method": "GET", "url": url}, indent=2))
        return

    status, parsed, _ = request_json("GET", url, token=token, timeout=args.timeout)
    print_result(status, parsed)
    fail_if_bad(status, parsed)


def cmd_server_validate(args: argparse.Namespace) -> None:
    token = resolve_token(args)
    url = make_url(args, f"/servers/{args.uuid}/validate")

    if args.dry_run:
        print(json.dumps({"method": "GET", "url": url}, indent=2))
        return

    status, parsed, _ = request_json("GET", url, token=token, timeout=args.timeout)
    print_result(status, parsed)
    fail_if_bad(status, parsed)


def find_deployment_uuids(value: Any) -> list[str]:
    found: list[str] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for key, item in obj.items():
                if key == "deployment_uuid" and isinstance(item, str) and item:
                    found.append(item)
                else:
                    walk(item)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(value)
    return list(dict.fromkeys(found))


def extract_status_words(value: Any) -> list[str]:
    words: list[str] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for key, item in obj.items():
                if key.lower() in {"status", "state"} and isinstance(item, str):
                    words.append(item.lower())
                else:
                    walk(item)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(value)
    return words


def wait_for_deployment(args: argparse.Namespace, deployment_uuid: str) -> bool:
    token = resolve_token(args)
    deadline = time.time() + args.wait_timeout
    url = make_url(args, f"/deployments/{deployment_uuid}")

    last_body: Any = None
    while time.time() < deadline:
        status, parsed, _ = request_json("GET", url, token=token, timeout=args.timeout)
        last_body = parsed
        print_result(status, parsed)

        if status < 200 or status >= 300:
            return False

        words = set(extract_status_words(parsed))
        if words & {"finished", "success", "successful", "succeeded", "completed"}:
            return True
        if words & {"failed", "error", "cancelled", "canceled"}:
            return False

        time.sleep(args.poll_interval)

    eprint("Timed out waiting for deployment.")
    if last_body is not None:
        eprint(json.dumps(last_body, indent=2, sort_keys=True))
    return False


def cmd_deploy(args: argparse.Namespace) -> None:
    token = resolve_token(args)

    if args.method.upper() == "POST":
        url = make_url(args, "/deploy")
        payload = {
            "uuid": args.uuid,
            "force": bool(args.force),
        }
    else:
        url = make_url(args, "/deploy", {"uuid": args.uuid, "force": bool(args.force)})
        payload = None

    if args.dry_run:
        print(json.dumps({"method": args.method.upper(), "url": url, "body": payload}, indent=2))
        return

    status, parsed, _ = request_json(
        args.method.upper(),
        url,
        token=token,
        payload=payload,
        timeout=args.timeout,
    )
    print_result(status, parsed)
    fail_if_bad(status, parsed)

    deployment_uuids = find_deployment_uuids(parsed)
    if args.wait:
        if not deployment_uuids:
            raise SystemExit("Deploy request succeeded, but no deployment_uuid was returned to wait on.")
        ok = True
        for deployment_uuid in deployment_uuids:
            ok = wait_for_deployment(args, deployment_uuid) and ok
        raise SystemExit(0 if ok else 1)


def cmd_start(args: argparse.Namespace) -> None:
    token = resolve_token(args)
    url = make_url(
        args,
        f"/applications/{args.uuid}/start",
        {
            "force": bool(args.force),
            "instant_deploy": bool(args.instant_deploy),
        },
    )

    if args.dry_run:
        print(json.dumps({"method": "GET", "url": url}, indent=2))
        return

    status, parsed, _ = request_json("GET", url, token=token, timeout=args.timeout)
    print_result(status, parsed)
    fail_if_bad(status, parsed)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Coolify API deploy twiddle.")
    parser.add_argument("--base-url", default="", help="Coolify base URL, e.g. https://coolify.example.com")
    parser.add_argument("--token-env", default="", help="Environment variable containing Coolify API token.")
    parser.add_argument("--token", default="", help="Raw Coolify API token. Avoid using this in shell history.")
    parser.add_argument("--repo-root", default=".", help="Repo root used with --controller.")
    parser.add_argument("--controller", default="", help="Read base_url/token_ref from repo controller, e.g. coolify-local.")
    parser.add_argument("--api-prefix", default="/api/v1", help="Coolify API prefix. Default: /api/v1")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--dry-run", action="store_true")

    sub = parser.add_subparsers(dest="cmd", required=True)

    health = sub.add_parser("health")
    health.set_defaults(func=cmd_health)

    list_cmd = sub.add_parser("list")
    list_cmd.add_argument("what", choices=["applications", "projects", "servers", "resources"])
    list_cmd.set_defaults(func=cmd_list)

    validate = sub.add_parser("server-validate")
    validate.add_argument("--uuid", required=True)
    validate.set_defaults(func=cmd_server_validate)

    deploy = sub.add_parser("deploy")
    deploy.add_argument("--uuid", required=True, help="Coolify resource/application UUID to deploy.")
    deploy.add_argument("--force", action="store_true", default=True)
    deploy.add_argument("--no-force", action="store_false", dest="force")
    deploy.add_argument("--method", choices=["GET", "POST"], default="GET")
    deploy.add_argument("--wait", action="store_true")
    deploy.add_argument("--wait-timeout", type=float, default=600.0)
    deploy.add_argument("--poll-interval", type=float, default=5.0)
    deploy.set_defaults(func=cmd_deploy)

    start = sub.add_parser("start-app")
    start.add_argument("--uuid", required=True, help="Coolify application UUID.")
    start.add_argument("--force", action="store_true", default=True)
    start.add_argument("--no-force", action="store_false", dest="force")
    start.add_argument("--instant-deploy", action="store_true")
    start.set_defaults(func=cmd_start)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    load_controller_defaults(args)
    args.func(args)


if __name__ == "__main__":
    main()