#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def api_json(
    base_url: str,
    token: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 20.0,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/api" + (path if path.startswith("/") else f"/{path}")
    body = None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
            try:
                parsed: Any = json.loads(text) if text.strip() else None
            except json.JSONDecodeError:
                parsed = text
            return {
                "ok": 200 <= int(response.status) < 300,
                "status": int(response.status),
                "url": url,
                "body": text[:6000],
                "json": parsed,
            }
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text) if text.strip() else None
        except json.JSONDecodeError:
            parsed = text
        return {
            "ok": False,
            "status": int(exc.code),
            "url": url,
            "body": text[:6000],
            "json": parsed,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": None,
            "url": url,
            "body": str(exc),
            "json": None,
        }


def http_probe(
    url: str,
    *,
    host_header: str = "",
    timeout: float = 10.0,
) -> dict[str, Any]:
    headers = {
        "Accept": "text/html,application/json,*/*",
        "User-Agent": "main-computer-route-twiddle/1",
    }
    if host_header:
        headers["Host"] = host_header

    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(20000).decode("utf-8", errors="replace")
            return {
                "ok": 200 <= int(response.status) < 300,
                "status": int(response.status),
                "url": url,
                "host_header": host_header,
                "body_prefix": body[:1200],
                "body_length_sampled": len(body),
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(8000).decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status": int(exc.code),
            "url": url,
            "host_header": host_header,
            "body_prefix": body[:1200],
            "body_length_sampled": len(body),
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": None,
            "url": url,
            "host_header": host_header,
            "error": str(exc),
            "body_prefix": "",
            "body_length_sampled": 0,
        }


def load_token_from_ref(repo_root: Path, token_ref: object) -> tuple[str, str]:
    ref = str(token_ref or "").strip()
    if not ref:
        return "", "missing_token_ref"

    import os

    env_value = os.environ.get(ref)
    if env_value:
        return env_value.strip(), f"environment:{ref}"

    if ref.lower().startswith("file:"):
        raw_path = ref.split(":", 1)[1].strip()
        token_path = Path(raw_path).expanduser()
        if not token_path.is_absolute():
            token_path = repo_root / token_path
        if not token_path.is_file():
            return "", f"missing_file:{token_path}"

        text = token_path.read_text(encoding="utf-8", errors="replace").strip()
        for line in text.splitlines():
            if line.startswith("token="):
                return line.split("=", 1)[1].strip(), f"file:{token_path}"
        return text.strip(), f"file:{token_path}"

    return ref, "literal_token_ref"


def host_from_url(value: object) -> tuple[str, int, str]:
    text = str(value or "").strip()
    if text and "://" not in text:
        text = f"http://{text}"

    try:
        parsed = urllib.parse.urlparse(text)
    except Exception:
        return text.replace("http://", "").replace("https://", "").strip("/"), 80, "http"

    scheme = parsed.scheme or "http"
    host = parsed.hostname or parsed.netloc or parsed.path
    default_port = 443 if scheme == "https" else 80
    port = int(parsed.port or default_port)
    return str(host or "").strip("/"), port, scheme


def socket_resolution(host: str, port: int) -> dict[str, Any]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except Exception as exc:
        return {
            "ok": False,
            "host": host,
            "port": port,
            "error": str(exc),
            "addresses": [],
        }

    addresses: list[str] = []
    for item in infos:
        sockaddr = item[4]
        if sockaddr:
            addresses.append(str(sockaddr[0]))

    return {
        "ok": bool(addresses),
        "host": host,
        "port": port,
        "addresses": sorted(set(addresses)),
    }


def run_command(command: list[str], *, timeout: float = 8.0) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "command": command,
            "stdout": completed.stdout[:12000],
            "stderr": completed.stderr[:6000],
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "returncode": None,
            "command": command,
            "stdout": "",
            "stderr": "command not found",
        }
    except PermissionError:
        return {
            "ok": False,
            "returncode": None,
            "command": command,
            "stdout": "",
            "stderr": "command is not executable",
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": None,
            "command": command,
            "stdout": (exc.stdout or "")[:12000] if isinstance(exc.stdout, str) else "",
            "stderr": "timed out",
        }
    except Exception as exc:
        return {
            "ok": False,
            "returncode": None,
            "command": command,
            "stdout": "",
            "stderr": str(exc),
        }


def parse_json_lines(text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            items.append(parsed)
    return items


def docker_ps(service_name: str) -> dict[str, Any]:
    commands = [
        [
            "docker",
            "ps",
            "--format",
            "{{json .}}",
            "--filter",
            f"name={service_name}",
        ],
        [
            "docker",
            "ps",
            "--format",
            "{{json .}}",
            "--filter",
            f"label=com.docker.compose.service={service_name}",
        ],
    ]

    attempts: list[dict[str, Any]] = []
    containers: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for command in commands:
        result = run_command(command)
        attempts.append(result)
        for item in parse_json_lines(result.get("stdout", "")):
            container_id = str(item.get("ID") or item.get("Names") or "")
            if container_id and container_id not in seen_ids:
                seen_ids.add(container_id)
                containers.append(item)

    return {
        "ok": bool(containers),
        "containers": containers,
        "attempts": attempts,
    }


def docker_inspect_container_names(containers: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for item in containers:
        for key in ("Names", "Name", "ID"):
            value = str(item.get(key) or "").strip()
            if value:
                names.append(value.split(",")[0].strip())
                break
    return list(dict.fromkeys(names))


def docker_inspect(names: list[str]) -> dict[str, Any]:
    if not names:
        return {
            "ok": False,
            "skipped": True,
            "error": "no matching containers from docker ps",
            "containers": [],
        }

    result = run_command(["docker", "inspect", *names], timeout=12.0)
    parsed: Any = None
    if result["ok"]:
        try:
            parsed = json.loads(result["stdout"])
        except json.JSONDecodeError:
            parsed = None

    return {
        "ok": bool(result["ok"] and isinstance(parsed, list)),
        "command_result": result,
        "containers": parsed if isinstance(parsed, list) else [],
    }


def extract_published_ports_from_inspect(containers: list[dict[str, Any]]) -> list[int]:
    ports: set[int] = set()
    for container in containers:
        network = container.get("NetworkSettings")
        if not isinstance(network, dict):
            continue
        mapping = network.get("Ports")
        if not isinstance(mapping, dict):
            continue
        for bindings in mapping.values():
            if not isinstance(bindings, list):
                continue
            for binding in bindings:
                if not isinstance(binding, dict):
                    continue
                host_port = binding.get("HostPort")
                try:
                    port = int(str(host_port or ""))
                except ValueError:
                    continue
                if 0 < port < 65536:
                    ports.add(port)
    return sorted(ports)


def extract_network_names_from_inspect(containers: list[dict[str, Any]]) -> list[str]:
    names: set[str] = set()
    for container in containers:
        network = container.get("NetworkSettings")
        if not isinstance(network, dict):
            continue
        networks = network.get("Networks")
        if isinstance(networks, dict):
            names.update(str(name) for name in networks.keys())
    return sorted(names)


def has_host_rule(compose: str, host: str) -> bool:
    return (
        f"Host(`{host}`)" in compose
        or f"Host(\\`{host}\\`)" in compose
        or f"Host(`www.{host}`)" in compose
        or f"Host(\\`www.{host}\\`)" in compose
    )


def service_summary(service: Any, *, service_name: str, host: str) -> dict[str, Any]:
    if not isinstance(service, dict):
        return {
            "valid_service_object": False,
            "type": type(service).__name__,
        }

    raw = str(service.get("docker_compose_raw") or "")
    rendered = str(service.get("docker_compose") or "")
    applications = service.get("applications")
    app_count = len(applications) if isinstance(applications, list) else None

    return {
        "valid_service_object": True,
        "uuid": service.get("uuid"),
        "name": service.get("name"),
        "status": service.get("status"),
        "updated_at": service.get("updated_at"),
        "fqdn": service.get("fqdn"),
        "applications_count": app_count,
        "has_top_level_urls": "urls" in service,
        "has_top_level_fqdn": "fqdn" in service,
        "raw_contains_service": service_name in raw,
        "raw_contains_host": host in raw,
        "rendered_contains_service": service_name in rendered,
        "rendered_contains_host": host in rendered,
        "rendered_contains_host_rule": has_host_rule(rendered, host),
        "rendered_prefix": rendered[:2000],
    }


def candidate_route_urls(
    *,
    scheme: str,
    publish_host: str,
    publish_port: int,
    published_ports: list[int],
    extra_ports: list[int],
) -> list[str]:
    ports: list[int] = []
    ports.append(publish_port)
    ports.extend(published_ports)
    ports.extend(extra_ports)
    ports.extend([80, 8080, 8000, 17056])

    ordered_ports = list(dict.fromkeys(port for port in ports if 0 < int(port) < 65536))
    urls: list[str] = []
    for port in ordered_ports:
        if scheme == "https":
            url_scheme = "https"
        else:
            url_scheme = "http"
        suffix = "" if (url_scheme == "http" and port == 80) or (url_scheme == "https" and port == 443) else f":{port}"
        urls.append(f"{url_scheme}://127.0.0.1{suffix}/")
        urls.append(f"{url_scheme}://localhost{suffix}/")
    return list(dict.fromkeys(urls))


def parse_ports(value: str) -> list[int]:
    ports: list[int] = []
    for item in str(value or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            port = int(item)
        except ValueError:
            continue
        if 0 < port < 65536:
            ports.append(port)
    return list(dict.fromkeys(ports))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only twiddle for the post-/deploy route failure. "
            "Separates DNS resolution, Host-header route reachability, Docker container state, and Coolify service rendering."
        )
    )
    parser.add_argument("site_id", help="Website id, for example: hub-site")
    parser.add_argument("--lane", default="remote-prod")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument(
        "--extra-ports",
        default="",
        help="Comma-separated extra local ports to probe with Host: <publish-host>, for example 19080,3000.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    try:
        from main_computer.website_project_manifest import website_publish_plan
    except Exception as exc:
        print_json({"ok": False, "stage": "import_publish_plan", "repo_root": str(repo_root), "error": repr(exc)})
        return 1

    try:
        plan = website_publish_plan(repo_root, args.site_id, args.lane)
    except Exception as exc:
        print_json({"ok": False, "stage": "load_publish_plan", "repo_root": str(repo_root), "error": repr(exc)})
        return 1

    accepted_target = plan.get("accepted_publish_target") if isinstance(plan.get("accepted_publish_target"), dict) else {}
    controller = plan.get("controller") if isinstance(plan.get("controller"), dict) else {}

    base_url = str(controller.get("base_url") or "").rstrip("/")
    token_ref = str(controller.get("token_ref") or "").strip()
    token, token_source = load_token_from_ref(repo_root, token_ref)

    resource_uuid = str(
        plan.get("resource_uuid")
        or accepted_target.get("resource_uuid")
        or accepted_target.get("service_uuid")
        or accepted_target.get("uuid")
        or ""
    ).strip()
    service_uuid = str(accepted_target.get("service_uuid") or resource_uuid).strip()
    service_name = str(
        accepted_target.get("service_name")
        or plan.get("service_name")
        or f"main-computer-{args.site_id}-local-publish"
    ).strip()

    publish_url = str(plan.get("url") or accepted_target.get("domain") or "").strip()
    if publish_url and "://" not in publish_url:
        publish_url = f"http://{publish_url}"
    publish_host, publish_port, publish_scheme = host_from_url(publish_url)

    dns = {
        "publish_host": socket_resolution(publish_host, publish_port) if publish_host else {"ok": False, "error": "missing publish host"},
        "localhost": socket_resolution("localhost", publish_port),
        "localhost_test_url": publish_url,
    }

    direct_publish_probe = http_probe(publish_url, timeout=args.timeout) if publish_url else {
        "ok": False,
        "skipped": True,
        "error": "missing publish_url",
    }

    service_get: dict[str, Any] = {"ok": False, "skipped": True, "error": "missing base_url/token/service_uuid"}
    summary: dict[str, Any] = {}
    if base_url and token and service_uuid:
        service_get = api_json(base_url, token, "GET", f"/v1/services/{service_uuid}", timeout=args.timeout)
        summary = service_summary(service_get.get("json"), service_name=service_name, host=publish_host)

    ps = docker_ps(service_name)
    inspect_names = docker_inspect_container_names(ps.get("containers", []))
    inspected = docker_inspect(inspect_names)
    published_ports = extract_published_ports_from_inspect(inspected.get("containers", []))
    docker_networks = extract_network_names_from_inspect(inspected.get("containers", []))

    route_urls = candidate_route_urls(
        scheme=publish_scheme,
        publish_host=publish_host,
        publish_port=publish_port,
        published_ports=published_ports,
        extra_ports=parse_ports(args.extra_ports),
    )

    host_header_probes = [
        http_probe(url, host_header=publish_host, timeout=args.timeout)
        for url in route_urls
        if publish_host
    ]

    any_host_header_ok = any(item.get("ok") for item in host_header_probes)
    dns_ok = bool(dns["publish_host"].get("ok"))
    direct_url_ok = bool(direct_publish_probe.get("ok"))
    docker_container_seen = bool(ps.get("ok"))

    likely: list[str] = []
    if not dns_ok and any_host_header_ok:
        likely.append(
            "Windows name resolution for the publish host is missing, but Host-header routing works. "
            "This points to a local DNS/hosts entry issue for the *.localhost site host."
        )
    if not dns_ok and not any_host_header_ok and docker_container_seen:
        likely.append(
            "The site container exists, but neither direct DNS nor Host-header probes reached it. "
            "This points to a local route exposure / published-port / proxy-listener issue."
        )
    if not docker_container_seen:
        likely.append(
            "No matching deployed container was found by docker ps. "
            "This points to deployment still queued/failed, image pull/build failure, or a different generated container name."
        )
    if dns_ok and not direct_url_ok and any_host_header_ok:
        likely.append(
            "DNS resolves, and Host-header routing works, but the direct publish URL still fails. "
            "This suggests scheme/port mismatch in the published URL."
        )
    if direct_url_ok:
        likely.append("The published URL is reachable from this process.")
    if not likely:
        likely.append("No single cause identified; inspect service_get, docker_ps, docker_inspect, and host_header_probes.")

    result = {
        "ok": bool(direct_url_ok or any_host_header_ok),
        "mode": "read_only_route_reachability_probe",
        "warning": "This twiddle is read-only. It does not call /deploy and does not modify hosts, Docker, Coolify, or repo files.",
        "repo_root": str(repo_root),
        "site_id": args.site_id,
        "lane": args.lane,
        "plan": {
            "supported": plan.get("supported"),
            "mode": plan.get("mode"),
            "deployment_path": plan.get("deployment_path"),
            "uses_deploy_api": plan.get("uses_deploy_api"),
            "local_platform_used": plan.get("local_platform_used"),
            "deploy_endpoint": plan.get("deploy_endpoint"),
            "deploy_url": plan.get("deploy_url"),
            "publish_url": publish_url,
            "publish_host": publish_host,
            "publish_port": publish_port,
            "publish_scheme": publish_scheme,
            "resource_uuid": resource_uuid,
            "service_uuid": service_uuid,
            "service_name": service_name,
        },
        "controller": {
            "base_url": base_url,
            "token_ref": token_ref,
            "token_source": token_source,
            "has_token": bool(token),
        },
        "dns": dns,
        "direct_publish_url_probe": direct_publish_probe,
        "service_get": {
            "ok": service_get.get("ok"),
            "status": service_get.get("status"),
            "url": service_get.get("url"),
            "summary": summary,
            "body_if_failed": "" if service_get.get("ok") else service_get.get("body", ""),
        },
        "docker_ps": ps,
        "docker_inspect": {
            "ok": inspected.get("ok"),
            "names": inspect_names,
            "published_ports": published_ports,
            "networks": docker_networks,
            "containers_count": len(inspected.get("containers", [])) if isinstance(inspected.get("containers"), list) else 0,
            "command_result": inspected.get("command_result"),
        },
        "host_header_probes": host_header_probes,
        "acceptance": {
            "dns_resolves_publish_host": dns_ok,
            "direct_publish_url_ok": direct_url_ok,
            "host_header_route_ok": any_host_header_ok,
            "docker_container_seen": docker_container_seen,
            "service_get_succeeded": service_get.get("ok") is True,
            "coolify_rendered_has_host_rule": summary.get("rendered_contains_host_rule") is True,
        },
        "likely_findings": likely,
    }

    print_json(result)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())