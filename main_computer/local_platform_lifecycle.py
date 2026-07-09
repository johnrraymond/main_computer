from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from main_computer.container_runtime import podman_command_cwd, resolve_container_runtime
from main_computer.local_platform_compose import (
    LocalPlatformComposeError,
    compose_project_name,
    generated_compose_path,
    safe_image_slug,
    site_compose_project_name,
    site_generated_compose_path,
    write_generated_site_compose,
    write_generated_websites_compose,
)
from main_computer.local_platform_registry import (
    LocalPlatformRegistry,
    LocalPlatformRegistryError,
    allocate_site_ports,
    load_local_platform_registry,
    normalize_registry_lane,
    registry_lane_to_publish_lane,
    save_local_platform_registry,
)
from main_computer.website_project_manifest import (
    WebsiteProjectError,
    _mark_lane_published,
    _wait_for_status_url,
    ensure_default_website_projects,
    load_website_project,
)


DEFAULT_DOCKER_TIMEOUT_SECONDS = 90.0
COMPOSE_SCOPE_AGGREGATE = "aggregate"
COMPOSE_SCOPE_SITE = "site"
DEFAULT_COMPOSE_SCOPE = COMPOSE_SCOPE_SITE
COMPOSE_SCOPE_ALIASES = {
    "": DEFAULT_COMPOSE_SCOPE,
    "aggregate": COMPOSE_SCOPE_AGGREGATE,
    "all": COMPOSE_SCOPE_AGGREGATE,
    "all-sites": COMPOSE_SCOPE_AGGREGATE,
    "websites": COMPOSE_SCOPE_AGGREGATE,
    "site": COMPOSE_SCOPE_SITE,
    "per-site": COMPOSE_SCOPE_SITE,
    "website": COMPOSE_SCOPE_SITE,
}


class WebsiteDockerLifecycleError(ValueError):
    """Raised when a local website Docker lifecycle request is invalid."""


@dataclass(frozen=True)
class WebsiteDockerPlan:
    action: str
    site_id: str
    lane: str
    registry_lane: str
    service: str
    port: int
    url: str
    status_url: str
    compose_path: Path
    compose_project: str
    compose_scope: str
    command: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "site_id": self.site_id,
            "lane": self.lane,
            "registry_lane": self.registry_lane,
            "service": self.service,
            "port": self.port,
            "url": self.url,
            "status_url": self.status_url,
            "compose_path": str(self.compose_path),
            "compose_project": self.compose_project,
            "compose_scope": self.compose_scope,
            "command": list(self.command),
        }


def normalize_compose_scope(value: object = DEFAULT_COMPOSE_SCOPE) -> str:
    scope = str(value or "").strip().lower()
    normalized = COMPOSE_SCOPE_ALIASES.get(scope)
    if normalized is None:
        raise WebsiteDockerLifecycleError(
            f"Unsupported website Docker compose scope: {value!r}. Use 'aggregate' or 'site'."
        )
    return normalized


def _compose_path_and_project(repo_root: Path, site_id: str, compose_scope: str) -> tuple[Path, str]:
    scope = normalize_compose_scope(compose_scope)
    if scope == COMPOSE_SCOPE_SITE:
        return site_generated_compose_path(repo_root, site_id), site_compose_project_name(site_id)
    return generated_compose_path(repo_root), compose_project_name()


def _docker_compose_base(repo_root: Path, site_id: str, compose_scope: str) -> list[str]:
    compose_path, project_name = _compose_path_and_project(repo_root, site_id, compose_scope)
    runtime = resolve_container_runtime(cwd=repo_root, probe=False)
    return runtime.compose_args(
        "-p",
        project_name,
        "-f",
        str(compose_path),
    )


def _docker_command_for_action(
    repo_root: Path,
    action: str,
    service: str,
    *,
    site_id: str,
    compose_scope: str = DEFAULT_COMPOSE_SCOPE,
    tail: int = 200,
) -> list[str]:
    base = _docker_compose_base(repo_root, site_id, compose_scope)
    if action in {"start", "publish"}:
        return [*base, "up", "-d", "--build", service]
    if action == "stop":
        return [*base, "stop", service]
    if action == "logs":
        return [*base, "logs", "--tail", str(max(1, int(tail))), service]
    return []


def lifecycle_plan(
    repo_root: Path,
    site_id: object,
    *,
    lane: object = "prod",
    action: str = "publish",
    compose_scope: object = DEFAULT_COMPOSE_SCOPE,
    tail: int = 200,
) -> WebsiteDockerPlan:
    action_name = str(action or "").strip().lower()
    if action_name not in {"start", "stop", "publish", "verify", "logs"}:
        raise WebsiteDockerLifecycleError(f"Unsupported website Docker action for a plan: {action!r}")

    registry = load_local_platform_registry(repo_root)
    clean_site_id = str(site_id or "").strip().lower()
    clean_compose_scope = normalize_compose_scope(compose_scope)
    try:
        registry_lane = normalize_registry_lane(lane)
        lane_data = registry.resolve(clean_site_id, registry_lane)
        compose_path, compose_project = _compose_path_and_project(repo_root, clean_site_id, clean_compose_scope)
    except (LocalPlatformRegistryError, LocalPlatformComposeError) as exc:
        raise WebsiteDockerLifecycleError(str(exc)) from exc

    publish_lane = registry_lane_to_publish_lane(registry_lane)
    command = _docker_command_for_action(
        repo_root,
        action_name,
        lane_data.service,
        site_id=clean_site_id,
        compose_scope=clean_compose_scope,
        tail=tail,
    )
    return WebsiteDockerPlan(
        action=action_name,
        site_id=clean_site_id,
        lane=publish_lane,
        registry_lane=registry_lane,
        service=lane_data.service,
        port=lane_data.port,
        url=lane_data.url,
        status_url=lane_data.status_url,
        compose_path=compose_path,
        compose_project=compose_project,
        compose_scope=clean_compose_scope,
        command=command,
    )


def _service_name_for_generated_site(site_id: str, registry_lane: str) -> str:
    suffix = "prod" if normalize_registry_lane(registry_lane) == "prod" else "dev"
    return f"{safe_image_slug(site_id)}-{suffix}"


def _site_registry_entry(repo_root: Path, registry: LocalPlatformRegistry, site_id: str) -> dict[str, Any]:
    project = load_website_project(repo_root, site_id)
    ports = allocate_site_ports(registry)
    repo_relative_path = project.path.relative_to(repo_root).as_posix()
    return {
        "id": project.id,
        "name": project.name,
        "kind": project.kind,
        "repo_relative_path": repo_relative_path,
        "lanes": {
            "prod": {
                "service": _service_name_for_generated_site(project.id, "prod"),
                "port": ports["prod"],
                "url": f"http://0.0.0.0:{ports['prod']}/",
                "status_url": f"http://0.0.0.0:{ports['prod']}/api/site/status",
            },
            "dev": {
                "service": _service_name_for_generated_site(project.id, "dev"),
                "port": ports["dev"],
                "url": f"http://0.0.0.0:{ports['dev']}/",
                "status_url": f"http://0.0.0.0:{ports['dev']}/api/site/status",
            },
        },
    }


def install_site(
    repo_root: Path,
    site_id: object,
    *,
    compose_scope: object = DEFAULT_COMPOSE_SCOPE,
) -> dict[str, Any]:
    """Ensure a website is registered for local Docker lanes and regenerate Compose."""

    ensure_default_website_projects(repo_root)
    clean_site_id = str(site_id or "").strip().lower()
    if not clean_site_id:
        raise WebsiteDockerLifecycleError("site_id is required.")

    clean_compose_scope = normalize_compose_scope(compose_scope)
    registry = load_local_platform_registry(repo_root)
    registered = False
    if clean_site_id not in registry.sites:
        data = registry.to_dict()
        data["sites"][clean_site_id] = _site_registry_entry(repo_root, registry, clean_site_id)
        registry = save_local_platform_registry(repo_root, data)
        registered = True

    if clean_compose_scope == COMPOSE_SCOPE_SITE:
        compose_result = write_generated_site_compose(repo_root, clean_site_id, registry)
    else:
        compose_result = write_generated_websites_compose(repo_root, registry)
    site = registry.sites[clean_site_id]
    lanes = {lane_name: lane.to_dict() for lane_name, lane in sorted(site.lanes.items())}
    return {
        "ok": True,
        "action": "install",
        "site_id": clean_site_id,
        "registered": registered,
        "site": site.to_dict(),
        "lanes": lanes,
        "compose_scope": clean_compose_scope,
        "generated_compose": compose_result,
    }


def _ensure_generated_compose(
    repo_root: Path,
    site_id: object,
    *,
    compose_scope: object = DEFAULT_COMPOSE_SCOPE,
) -> dict[str, Any]:
    clean_compose_scope = normalize_compose_scope(compose_scope)
    if clean_compose_scope == COMPOSE_SCOPE_SITE:
        return write_generated_site_compose(repo_root, site_id)
    return write_generated_websites_compose(repo_root)


def _run_command(command: list[str], repo_root: Path, timeout_s: float) -> subprocess.CompletedProcess[str]:
    runtime = resolve_container_runtime(cwd=repo_root, probe=False)
    run_cwd = podman_command_cwd(repo_root) if runtime.runtime == "podman" else repo_root
    return subprocess.run(
        command,
        cwd=run_cwd or repo_root,
        text=True,
        capture_output=True,
        timeout=timeout_s,
        check=False,
    )


def verify_site(
    repo_root: Path,
    site_id: object,
    *,
    lane: object = "prod",
    compose_scope: object = DEFAULT_COMPOSE_SCOPE,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    plan = lifecycle_plan(repo_root, site_id, lane=lane, action="verify", compose_scope=compose_scope)
    verify_result = _wait_for_status_url(plan.status_url, max(1.0, timeout_s))
    return {
        "ok": bool(verify_result.get("ok")),
        "action": "verify",
        "plan": plan.to_dict(),
        "verified": bool(verify_result.get("ok")),
        "verify_status": verify_result.get("status"),
        "verify_body": verify_result.get("body", ""),
        "verify_payload": verify_result.get("payload", {}),
        "verify_attempts": verify_result.get("attempts", 0),
        "verify_error": verify_result.get("error", ""),
    }


def _planned_compose_path_for_install(
    repo_root: Path,
    clean_site_id: str,
    registry: LocalPlatformRegistry,
    compose_scope: str,
) -> Path:
    if compose_scope == COMPOSE_SCOPE_SITE:
        if clean_site_id in registry.sites:
            return site_generated_compose_path(repo_root, clean_site_id, registry)
        project = load_website_project(repo_root, clean_site_id)
        return project.path / ".main-computer" / "local-platform" / "docker-compose.yml"
    return generated_compose_path(repo_root)


def _planned_compose_project_for_install(clean_site_id: str, compose_scope: str) -> str:
    if compose_scope == COMPOSE_SCOPE_SITE:
        return site_compose_project_name(clean_site_id)
    return compose_project_name()


def website_docker_action(
    repo_root: Path,
    action: str,
    site_id: object,
    *,
    lane: object = "prod",
    dry_run: bool = False,
    verify: bool = True,
    compose_scope: object = DEFAULT_COMPOSE_SCOPE,
    tail: int = 200,
    timeout_s: float = DEFAULT_DOCKER_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    action_name = str(action or "").strip().lower()
    clean_compose_scope = normalize_compose_scope(compose_scope)
    if action_name == "install":
        if dry_run:
            ensure_default_website_projects(repo_root)
            clean_site_id = str(site_id or "").strip().lower()
            registry = load_local_platform_registry(repo_root)
            would_register = clean_site_id not in registry.sites
            return {
                "ok": True,
                "action": "install",
                "dry_run": True,
                "site_id": clean_site_id,
                "would_register": would_register,
                "compose_scope": clean_compose_scope,
                "compose_path": str(_planned_compose_path_for_install(repo_root, clean_site_id, registry, clean_compose_scope)),
                "compose_project": _planned_compose_project_for_install(clean_site_id, clean_compose_scope),
            }
        result = install_site(repo_root, site_id, compose_scope=clean_compose_scope)
        result["dry_run"] = False
        return result

    if action_name == "verify":
        if dry_run:
            plan = lifecycle_plan(repo_root, site_id, lane=lane, action="verify", compose_scope=clean_compose_scope)
            return {"ok": True, "action": "verify", "dry_run": True, "plan": plan.to_dict(), "verified": False}
        result = verify_site(
            repo_root,
            site_id,
            lane=lane,
            compose_scope=clean_compose_scope,
            timeout_s=min(timeout_s, 30.0),
        )
        result["dry_run"] = False
        return result

    if action_name not in {"start", "stop", "publish", "logs"}:
        raise WebsiteDockerLifecycleError(f"Unsupported website Docker action: {action!r}")

    _ensure_generated_compose(repo_root, site_id, compose_scope=clean_compose_scope)
    plan = lifecycle_plan(
        repo_root,
        site_id,
        lane=lane,
        action=action_name,
        compose_scope=clean_compose_scope,
        tail=tail,
    )
    if dry_run:
        return {
            "ok": True,
            "action": action_name,
            "dry_run": True,
            "plan": plan.to_dict(),
            "returncode": 0,
            "stdout": "",
            "stderr": "",
            "verified": False,
        }

    completed = _run_command(plan.command, repo_root, timeout_s)
    result: dict[str, Any] = {
        "ok": completed.returncode == 0,
        "action": action_name,
        "dry_run": False,
        "plan": plan.to_dict(),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "verified": False,
    }
    if completed.returncode != 0:
        return result

    if action_name == "publish" and verify:
        verify_result = _wait_for_status_url(plan.status_url, min(timeout_s, 30.0))
        result["verified"] = bool(verify_result.get("ok"))
        result["verify_status"] = verify_result.get("status")
        result["verify_body"] = verify_result.get("body", "")
        result["verify_payload"] = verify_result.get("payload", {})
        result["verify_attempts"] = verify_result.get("attempts", 0)
        if not result["verified"] and verify_result.get("error"):
            result["verify_error"] = verify_result.get("error")
        try:
            _mark_lane_published(load_website_project(repo_root, site_id), plan.lane, plan.to_dict(), bool(result["verified"]))
            result["site"] = load_website_project(repo_root, site_id).to_dict(repo_root)
        except WebsiteProjectError:
            raise
        except Exception as exc:
            result["publish_metadata_error"] = str(exc)
    elif action_name == "publish":
        result["verified"] = False
    elif action_name == "start":
        result["started"] = True
    elif action_name == "stop":
        result["stopped"] = True
    elif action_name == "logs":
        result["logs"] = completed.stdout
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Manage per-website Docker lifecycle commands.")
    parser.add_argument("action", choices=["install", "start", "stop", "publish", "verify", "logs"])
    parser.add_argument("site_id", help="Website id from runtime/websites/<site-id>/site.json")
    parser.add_argument("--lane", default="prod", help="Lane alias. local/prod/production/local-prod mean Local Server.")
    parser.add_argument("--repo-root", default=".", help="Repository root. Defaults to current directory.")
    parser.add_argument("--dry-run", action="store_true", help="Print the lifecycle plan without running Docker.")
    parser.add_argument("--no-verify", action="store_true", help="Do not probe the status URL after publish.")
    parser.add_argument(
        "--compose-scope",
        default=DEFAULT_COMPOSE_SCOPE,
        choices=[COMPOSE_SCOPE_SITE, COMPOSE_SCOPE_AGGREGATE],
        help=(
            "Compose file scope to use. Defaults to the site-local compose file. "
            "Use aggregate for the legacy all-websites file."
        ),
    )
    parser.add_argument("--tail", type=int, default=200, help="Number of log lines for logs action.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_DOCKER_TIMEOUT_SECONDS, help="Docker command timeout in seconds.")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    try:
        result = website_docker_action(
            repo_root,
            args.action,
            args.site_id,
            lane=args.lane,
            dry_run=args.dry_run,
            verify=not args.no_verify,
            compose_scope=args.compose_scope,
            tail=args.tail,
            timeout_s=args.timeout,
        )
    except (WebsiteDockerLifecycleError, WebsiteProjectError, LocalPlatformRegistryError, LocalPlatformComposeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
