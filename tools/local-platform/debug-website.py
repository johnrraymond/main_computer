from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
from pathlib import Path
from typing import Any

SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_REPO_ROOT))

from main_computer.local_platform_compose import write_generated_websites_compose
from main_computer.local_platform_registry import (
    LocalPlatformRegistryError,
    allocate_site_ports,
    load_local_platform_registry,
    save_local_platform_registry,
)
from main_computer.website_project_manifest import create_website_project, default_manifest


TOOL_RELATIVE_PATH = "tools/local-platform/debug-website.py"
DEBUG_SITE_RE = re.compile(r"^debug-[a-z0-9][a-z0-9-]{0,77}[a-z0-9]$")
PURPOSE_RE = re.compile(r"[^a-z0-9-]+")
DEFAULT_PURPOSE = "bootstrap"


class DebugWebsiteError(ValueError):
    """Raised when a debug website deployment request is invalid."""


def repo_root_from_arg(value: str | None) -> Path:
    return (Path(value).resolve() if value else SCRIPT_REPO_ROOT)


def slugify_purpose(value: object) -> str:
    raw = str(value or DEFAULT_PURPOSE).strip().lower()
    slug = PURPOSE_RE.sub("-", raw).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        slug = DEFAULT_PURPOSE
    if slug.startswith("debug-"):
        slug = slug[len("debug-") :]
    slug = slug.strip("-") or DEFAULT_PURPOSE
    return slug[:48].strip("-") or DEFAULT_PURPOSE


def validate_debug_site_id(value: object) -> str:
    site_id = str(value or "").strip().lower()
    if not DEBUG_SITE_RE.fullmatch(site_id):
        raise DebugWebsiteError(
            "Debug website id must match debug-<slug>, use lowercase letters, numbers, and hyphens, "
            "and must not contain path separators or traversal."
        )
    return site_id


def generated_debug_site_id(purpose: object, *, unique: bool) -> str:
    site_id = f"debug-{slugify_purpose(purpose)}"
    if unique:
        site_id = f"{site_id}-{secrets.token_hex(3)}"
    return validate_debug_site_id(site_id)


def resolve_site_id(args: argparse.Namespace) -> str:
    if args.site:
        return validate_debug_site_id(args.site)
    return generated_debug_site_id(args.purpose, unique=bool(args.unique))


def title_from_site_id(site_id: str) -> str:
    return site_id.replace("-", " ").title()


def debug_manifest(site_id: str, *, purpose: str, bootstrap: bool, name: str | None = None) -> dict[str, Any]:
    display_name = name or title_from_site_id(site_id)
    manifest = default_manifest(site_id, display_name, "debug-site")
    manifest["description"] = f"Debug/workbench website for {purpose}."
    manifest["debug"] = {
        "purpose": purpose,
        "bootstrap": bool(bootstrap),
        "disposable": not bool(bootstrap),
        "managed_by": TOOL_RELATIVE_PATH,
    }
    manifest["deploy"]["target"] = "local-platform"
    return manifest


def ensure_registry_site(repo_root: Path, site_id: str, *, name: str) -> dict[str, Any]:
    registry = load_local_platform_registry(repo_root)
    data = registry.to_dict()
    existed = site_id in data["sites"]

    if existed:
        site = data["sites"][site_id]
        return {
            "created": False,
            "site": site,
            "ports": {
                lane: lane_data.get("port")
                for lane, lane_data in sorted(site.get("lanes", {}).items())
                if isinstance(lane_data, dict)
            },
        }

    ports = allocate_site_ports(registry)
    site = {
        "id": site_id,
        "name": name,
        "kind": "debug-site",
        "repo_relative_path": f"runtime/websites/{site_id}",
        "lanes": {
            "prod": {
                "service": f"{site_id}-prod",
                "port": ports["prod"],
                "url": f"http://localhost:{ports['prod']}/",
                "status_url": f"http://localhost:{ports['prod']}/api/site/status",
            },
            "dev": {
                "service": f"{site_id}-dev",
                "port": ports["dev"],
                "url": f"http://localhost:{ports['dev']}/",
                "status_url": f"http://localhost:{ports['dev']}/api/site/status",
            },
        },
    }
    data["sites"][site_id] = site
    save_local_platform_registry(repo_root, data)
    return {"created": True, "site": site, "ports": ports}


def write_debug_homepage(site_dir: Path, *, site_id: str, purpose: str, bootstrap: bool, overwrite: bool) -> bool:
    index_path = site_dir / "index.html"
    if index_path.exists() and not overwrite:
        return False
    bootstrap_text = "Bootstrap-managed" if bootstrap else "Disposable"
    index_path.write_text(
        f"""<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
    <title>{site_id}</title>
    <link rel=\"stylesheet\" href=\"/style.css\">
  </head>
  <body>
    <main class=\"debug-shell\">
      <p class=\"eyebrow\">Main Computer debug website</p>
      <h1>{site_id}</h1>
      <p>This {bootstrap_text.lower()} workbench can be used to generate, repair, and debug websites safely.</p>
      <dl>
        <dt>Purpose</dt><dd>{purpose}</dd>
        <dt>Managed by</dt><dd>{TOOL_RELATIVE_PATH}</dd>
      </dl>
    </main>
    <script src=\"/script.js\"></script>
  </body>
</html>
""",
        encoding="utf-8",
    )
    return True


def write_debug_styles(site_dir: Path, *, overwrite: bool) -> bool:
    style_path = site_dir / "style.css"
    if style_path.exists() and not overwrite:
        return False
    style_path.write_text(
        """body {
  margin: 0;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: #101828;
  color: #f9fafb;
}

.debug-shell {
  max-width: 760px;
  margin: 0 auto;
  padding: 12vh 24px;
}

.eyebrow {
  color: #93c5fd;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

h1 {
  font-size: clamp(2.5rem, 6vw, 5rem);
  margin: 0 0 1rem;
}

dl {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 0.5rem 1rem;
  margin-top: 2rem;
}

dt {
  color: #93c5fd;
  font-weight: 700;
}

dd {
  margin: 0;
}
""",
        encoding="utf-8",
    )
    return True


def write_debug_script(site_dir: Path, *, site_id: str, overwrite: bool) -> bool:
    script_path = site_dir / "script.js"
    if script_path.exists() and not overwrite:
        return False
    script_path.write_text(
        f"""console.log("Debug website ready:", {json.dumps(site_id)});
""",
        encoding="utf-8",
    )
    return True


def write_debug_builder_state(site_dir: Path, *, site_id: str, purpose: str, bootstrap: bool, overwrite: bool) -> bool:
    builder_path = site_dir / "builder.json"
    if builder_path.exists() and not overwrite:
        return False
    builder_path.write_text(
        json.dumps(
            {
                "version": 2,
                "engine": "grapesjs",
                "site_model": "2.0",
                "entry_html": "index.html",
                "stylesheet": "style.css",
                "script": "script.js",
                "debug": {
                    "site_id": site_id,
                    "purpose": purpose,
                    "bootstrap": bool(bootstrap),
                    "managed_by": TOOL_RELATIVE_PATH,
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return True


def ensure_debug_website(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = repo_root_from_arg(args.repo_root)
    site_id = resolve_site_id(args)
    purpose = slugify_purpose(args.purpose or site_id.removeprefix("debug-"))
    name = args.name or title_from_site_id(site_id)

    manifest = debug_manifest(site_id, purpose=purpose, bootstrap=bool(args.bootstrap), name=name)
    expected_site_dir = repo_root / "runtime" / "websites" / site_id
    site_existed = expected_site_dir.exists()
    project = create_website_project(
        repo_root,
        site_id,
        name,
        kind="debug-site",
        manifest=manifest,
        overwrite=bool(args.overwrite),
    )
    site_dir = project.path
    should_write_managed_starters = bool(args.overwrite) or not site_existed

    changed_files = {
        "index_html": write_debug_homepage(
            site_dir,
            site_id=site_id,
            purpose=purpose,
            bootstrap=bool(args.bootstrap),
            overwrite=should_write_managed_starters,
        ),
        "style_css": write_debug_styles(site_dir, overwrite=should_write_managed_starters),
        "script_js": write_debug_script(site_dir, site_id=site_id, overwrite=should_write_managed_starters),
        "builder_json": write_debug_builder_state(
            site_dir,
            site_id=site_id,
            purpose=purpose,
            bootstrap=bool(args.bootstrap),
            overwrite=should_write_managed_starters,
        ),
    }

    registry_result = ensure_registry_site(repo_root, site_id, name=name)
    compose_result = None
    if not args.no_compose:
        compose_result = write_generated_websites_compose(repo_root)

    return {
        "ok": True,
        "action": "ensure",
        "site_id": site_id,
        "name": name,
        "purpose": purpose,
        "bootstrap": bool(args.bootstrap),
        "repo_root": str(repo_root),
        "site_path": str(site_dir),
        "repo_relative_path": project.to_dict(repo_root)["repo_relative_path"],
        "manifest_path": str(site_dir / "site.json"),
        "files": {
            "site_json": str(site_dir / "site.json"),
            "index_html": str(site_dir / "index.html"),
            "style_css": str(site_dir / "style.css"),
            "script_js": str(site_dir / "script.js"),
        },
        "changed_files": changed_files,
        "registry": registry_result,
        "compose": compose_result,
    }


def status_debug_website(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = repo_root_from_arg(args.repo_root)
    site_id = resolve_site_id(args)
    site_dir = repo_root / "runtime" / "websites" / site_id
    registry = load_local_platform_registry(repo_root)
    registry_site = registry.sites.get(site_id)
    manifest_path = site_dir / "site.json"
    manifest: dict[str, Any] | None = None
    if manifest_path.exists():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                manifest = payload
        except json.JSONDecodeError:
            manifest = None
    return {
        "ok": True,
        "action": "status",
        "site_id": site_id,
        "site_exists": site_dir.exists(),
        "site_path": str(site_dir),
        "manifest_exists": manifest_path.exists(),
        "registered": registry_site is not None,
        "registry_site": registry_site.to_dict() if registry_site else None,
        "manifest": manifest,
    }


def list_debug_websites(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = repo_root_from_arg(args.repo_root)
    registry = load_local_platform_registry(repo_root)
    sites = [
        site.to_dict()
        for site in registry.list_sites()
        if site.id.startswith("debug-") or site.kind == "debug-site"
    ]
    return {
        "ok": True,
        "action": "list",
        "repo_root": str(repo_root),
        "count": len(sites),
        "sites": sites,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create and manage debug-* website workbenches.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--repo-root", default=None)
        sub.add_argument("--site", default=None, help="Explicit debug-* site id, e.g. debug-bootstrap.")
        sub.add_argument("--purpose", default=DEFAULT_PURPOSE, help="Purpose slug used when --site is omitted.")
        sub.add_argument("--unique", action="store_true", help="Append a short random suffix when --site is omitted.")

    ensure = subparsers.add_parser("ensure", help="Create or refresh a debug website and registry entry.")
    add_common(ensure)
    ensure.add_argument("--name", default=None)
    ensure.add_argument("--bootstrap", action="store_true", help="Mark this as the stable bootstrap debug website.")
    ensure.add_argument("--overwrite", action="store_true", help="Rewrite managed starter files.")
    ensure.add_argument("--no-compose", action="store_true", help="Skip regenerating generated website compose.")

    status = subparsers.add_parser("status", help="Inspect one debug website.")
    add_common(status)

    list_cmd = subparsers.add_parser("list", help="List registered debug websites.")
    list_cmd.add_argument("--repo-root", default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "ensure":
            result = ensure_debug_website(args)
        elif args.command == "status":
            result = status_debug_website(args)
        elif args.command == "list":
            result = list_debug_websites(args)
        else:  # pragma: no cover - argparse enforces this.
            raise DebugWebsiteError(f"Unsupported command: {args.command}")
    except (DebugWebsiteError, LocalPlatformRegistryError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 2

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
