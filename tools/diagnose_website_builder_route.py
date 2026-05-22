#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import platform
import re
import socket
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


ROUTE_PATHS = [
    "/applications",
    "/applications/calculator",
    "/applications/website-builder",
    "/applications/website-builder/",
    "/applications/website-builder/hub-site",
    "/apps/website-builder/blog-site",
    "/applications/website-builder/UpperCase",
    "/applications/website-builder/not/a/site",
]

EXPECTED_TARGETS = {
    "/applications": "calculator",
    "/applications/calculator": "calculator",
    "/applications/website-builder": "website-builder",
    "/applications/website-builder/": "website-builder",
    "/applications/website-builder/hub-site": "website-builder",
    "/apps/website-builder/blog-site": "website-builder",
    "/applications/website-builder/UpperCase": None,
    "/applications/website-builder/not/a/site": None,
}

SERVER_SOURCE_MARKERS = {
    "main_computer/viewport_state.py": [
        'APPLICATION_WEBSITE_BUILDER_ROUTE = "website-builder"',
        "def _is_website_builder_application_route",
        "def _website_builder_route_site_id",
        'if candidate == APPLICATION_WEBSITE_BUILDER_ROUTE:',
    ],
    "main_computer/viewport_route_dispatch.py": [
        "if _is_website_builder_application_route(self.path):",
        '"route-applications-website-builder"',
        "site_id=_website_builder_route_site_id(self.path) or",
    ],
}

FRONTEND_PROJECT_URL_MARKERS = {
    "main_computer/web/applications/scripts/website-builder.js": [
        "history.replaceState",
        "history.pushState",
        "website-builder",
    ],
    "main_computer/web/applications/scripts/app-routing.js": [
        "website-builder",
    ],
    "main_computer/web/applications/scripts/dom-bindings/navigation.js": [
        "website-builder",
    ],
}


def line(title: str) -> None:
    print(f"\n=== {title} ===")


def find_repo_root(start: Path) -> Path:
    start = start.resolve()
    candidates = [start, *start.parents]
    for base in list(candidates):
        candidates.append(base / "main_computer_test")
    for candidate in candidates:
        if (candidate / "main_computer" / "viewport_state.py").exists():
            return candidate
    raise SystemExit(
        "Could not find repo root. Run from the checkout root, or pass "
        "--root /path/to/main_computer_test"
    )


def check_markers(root: Path, markers_by_file: dict[str, list[str]], *, title: str, required: bool) -> bool:
    line(title)
    all_ok = True
    for rel, markers in markers_by_file.items():
        path = root / rel
        if not path.exists():
            print(f"{'FAIL' if required else 'WARN'} missing {rel}")
            all_ok = False
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        missing = [marker for marker in markers if marker not in text]
        if missing:
            print(f"{'FAIL' if required else 'WARN'} {rel}")
            for marker in missing:
                print(f"  missing: {marker!r}")
            all_ok = False
        else:
            print(f"OK   {rel}")
    return all_ok


def import_route_helpers(root: Path) -> bool:
    line("Local Python route-helper check")
    sys.path.insert(0, str(root))
    try:
        state = importlib.import_module("main_computer.viewport_state")
        dispatch = importlib.import_module("main_computer.viewport_route_dispatch")
    except Exception as exc:
        print(f"FAIL could not import route modules: {type(exc).__name__}: {exc}")
        return False

    print(f"viewport_state imported from:          {getattr(state, '__file__', '<unknown>')}")
    print(f"viewport_route_dispatch imported from: {getattr(dispatch, '__file__', '<unknown>')}")

    for name in (
        "_application_route_target",
        "_is_website_builder_application_route",
        "_website_builder_route_site_id",
    ):
        if not callable(getattr(state, name, None)):
            print(f"FAIL viewport_state missing callable {name}")
            return False
        if not callable(getattr(dispatch, name, None)):
            print(f"FAIL viewport_route_dispatch missing wildcard-imported callable {name}")
            return False

    target_fn = state._application_route_target
    is_wb_fn = state._is_website_builder_application_route
    site_id_fn = state._website_builder_route_site_id

    all_ok = True
    for path, expected in EXPECTED_TARGETS.items():
        try:
            actual = target_fn(path)
            is_wb = is_wb_fn(path)
            site_id = site_id_fn(path)
        except Exception as exc:
            print(f"FAIL {path}: helper raised {type(exc).__name__}: {exc}")
            all_ok = False
            continue

        ok = actual == expected
        wb_note = f"is_wb={is_wb!r} site_id={site_id!r}"
        print(
            f"{'OK  ' if ok else 'FAIL'} {path:42} "
            f"target={actual!r:18} expected={expected!r:18} {wb_note}"
        )
        all_ok = all_ok and ok
    return all_ok


def http_get(base_url: str, path: str, timeout: float) -> dict[str, object]:
    url = base_url.rstrip("/") + path
    req = Request(
        url,
        headers={
            "User-Agent": "website-builder-route-diagnostic/1",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    try:
        with urlopen(req, timeout=timeout) as response:
            body = response.read(700).decode("utf-8", errors="replace")
            return {
                "status": response.status,
                "url": url,
                "content_type": response.headers.get("content-type", ""),
                "server": response.headers.get("server", ""),
                "body": body,
                "error": "",
            }
    except HTTPError as exc:
        body = exc.read(700).decode("utf-8", errors="replace")
        return {
            "status": exc.code,
            "url": url,
            "content_type": exc.headers.get("content-type", ""),
            "server": exc.headers.get("server", ""),
            "body": body,
            "error": f"HTTPError {exc.code}",
        }
    except URLError as exc:
        return {
            "status": None,
            "url": url,
            "content_type": "",
            "server": "",
            "body": "",
            "error": f"URLError: {exc.reason}",
        }


def check_http(base_url: str, timeout: float) -> dict[str, dict[str, object]]:
    line(f"HTTP check against {base_url.rstrip('/')}")
    results = {}
    for path in ROUTE_PATHS:
        result = http_get(base_url, path, timeout)
        results[path] = result
        status = result["status"]
        body = re.sub(r"\s+", " ", str(result["body"])).strip()[:160]
        print(
            f"{str(status):>4} {path:42} "
            f"content-type={result['content_type']!r} server={result['server']!r}"
        )
        if result["error"] and status is None:
            print(f"     error: {result['error']}")
        if body:
            print(f"     body: {body!r}")
    return results


def port_probe(base_url: str, timeout: float) -> None:
    line("Port/process probe")
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        with socket.create_connection((host, port), timeout=timeout):
            print(f"OK   TCP connection to {host}:{port}")
    except OSError as exc:
        print(f"FAIL TCP connection to {host}:{port}: {exc}")

    if platform.system().lower().startswith("win"):
        commands = [f'netstat -ano | findstr ":{port}"']
    else:
        commands = [
            f"lsof -nP -iTCP:{port} -sTCP:LISTEN",
            f"ss -ltnp 'sport = :{port}'",
            f"netstat -anp 2>/dev/null | grep ':{port} '",
        ]

    for command in commands:
        try:
            completed = subprocess.run(
                command,
                shell=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=5,
            )
        except Exception as exc:
            print(f"$ {command}\n  could not run: {type(exc).__name__}: {exc}")
            continue
        output = completed.stdout.strip()
        print(f"$ {command}")
        print(output or f"  no output, exit {completed.returncode}")


def scan_sibling_checkouts(root: Path) -> None:
    line("Nearby checkout scan")
    parent = root.parent
    found = []
    for candidate in parent.glob("*"):
        state = candidate / "main_computer" / "viewport_state.py"
        dispatch = candidate / "main_computer" / "viewport_route_dispatch.py"
        if state.exists() and dispatch.exists():
            state_text = state.read_text(encoding="utf-8", errors="replace")
            dispatch_text = dispatch.read_text(encoding="utf-8", errors="replace")
            found.append(
                (
                    candidate,
                    "def _is_website_builder_application_route" in state_text,
                    "if _is_website_builder_application_route(self.path):" in dispatch_text,
                )
            )

    if not found:
        print(f"No sibling checkouts found under {parent}")
        return

    for candidate, has_state, has_dispatch in found:
        marker = "<-- selected root" if candidate.resolve() == root.resolve() else ""
        print(
            f"{candidate}  "
            f"state_marker={has_state} dispatch_marker={has_dispatch} {marker}"
        )


def print_diagnosis(server_source_ok: bool, helper_ok: bool, http_results: dict[str, dict[str, object]]) -> None:
    line("Likely diagnosis")
    wb_status = http_results.get("/applications/website-builder", {}).get("status")
    calc_status = http_results.get("/applications/calculator", {}).get("status")
    apps_status = http_results.get("/applications", {}).get("status")
    project_status = http_results.get("/applications/website-builder/hub-site", {}).get("status")

    if not server_source_ok:
        print("The selected local checkout does not contain the server-side Website Builder route patch.")
    elif not helper_ok:
        print("The selected local checkout contains route markers, but the helpers do not import/evaluate correctly.")
    elif wb_status == 200 and project_status == 200:
        print("The running HTTP server is serving Website Builder routes. Browser cache, a different URL, or a stale tab is more likely.")
    elif calc_status == 200 and wb_status == 404:
        print("Calculator routes work but Website Builder 404s. The process on this port is probably stale or started from a different checkout.")
    elif apps_status == 404 and calc_status == 404 and wb_status == 404:
        print("All application routes 404. Port 8765 is probably not the expected viewport server, or a proxy/static handler is intercepting routes.")
    elif wb_status is None:
        print("The diagnostic could not connect to the server. Check whether the viewport server is listening on this host/port.")
    else:
        print("Mixed result. Use the source markers, imported file paths, HTTP statuses, and port/process output above to identify the mismatch.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose Website Builder route 404s.")
    parser.add_argument("--root", default=".", help="Repo root containing main_computer/")
    parser.add_argument("--url", default="http://127.0.0.1:8765", help="Viewport server base URL")
    parser.add_argument("--timeout", type=float, default=4.0, help="HTTP/socket timeout in seconds")
    args = parser.parse_args()

    root = find_repo_root(Path(args.root))
    print(f"Selected repo root: {root}")

    server_source_ok = check_markers(
        root,
        SERVER_SOURCE_MARKERS,
        title="Server source marker check",
        required=True,
    )
    check_markers(
        root,
        FRONTEND_PROJECT_URL_MARKERS,
        title="Frontend project-URL marker check",
        required=False,
    )
    helper_ok = import_route_helpers(root)
    http_results = check_http(args.url, args.timeout)
    port_probe(args.url, args.timeout)
    scan_sibling_checkouts(root)
    print_diagnosis(server_source_ok, helper_ok, http_results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())