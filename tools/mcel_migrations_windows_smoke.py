#!/usr/bin/env python3
"""Live Windows-only MCEL application backend smoke; read-only by default.

Run on the actual Windows host with the viewport listening locally:
  python -m tools.mcel_migrations_windows_smoke --base-url http://127.0.0.1:8765

Manifest inspection defaults to the bounded canonical MCEL Code Editor package,
not an expensive hash of the whole repository. Override it with --project-root.
Optional isolated process-termination probe (spawns/kills only its own child):
  python -m tools.mcel_migrations_windows_smoke --allow-process-probe

Never restarts or shuts down the Main Computer server. The browser/Monaco,
Aider write lane, and real server lifecycle still require operator acceptance.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import subprocess
import sys
import time
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "runtime/reports/mcel-migrations/windows-live-smoke.json"
DEFAULT_PROJECT_ROOT = "mcel_apps/code-editor"
MANIFEST_SCHEMA = "mcel-code-editor-project-manifest-v1"
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")


def inspect_manifest_response(data: dict, project_root: str) -> dict:
    """Prove the project manifest structure, hashes, and selected scope."""
    if data.get("schema") != MANIFEST_SCHEMA:
        raise AssertionError(f"Unexpected manifest schema: {data.get('schema')!r}")
    if data.get("project_root") != project_root:
        raise AssertionError(f"Unexpected project root: {data.get('project_root')!r}")
    files = data.get("files")
    if not isinstance(files, list) or not files:
        raise AssertionError("Project manifest contains no files")
    if data.get("file_count") != len(files):
        raise AssertionError("Project manifest file_count does not match files")
    digest = data.get("project_manifest_sha256")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise AssertionError("Missing or invalid project_manifest_sha256")
    paths = []
    for entry in files:
        if not isinstance(entry, dict):
            raise AssertionError("Project manifest file entry is not an object")
        path, sha, size = entry.get("path"), entry.get("sha256"), entry.get("size")
        if (not isinstance(path, str) or not path or path.startswith("/")
                or "\\" in path or any(part in {"", ".", ".."} for part in path.split("/"))):
            raise AssertionError(f"Invalid project manifest path: {path!r}")
        if not isinstance(sha, str) or not _SHA256.fullmatch(sha):
            raise AssertionError(f"Invalid SHA256 for project file: {path}")
        if type(size) is not int or size < 0:
            raise AssertionError(f"Invalid size for project file: {path}")
        paths.append(path)
    if len(set(paths)) != len(paths):
        raise AssertionError("Duplicate paths in project manifest")
    if project_root == DEFAULT_PROJECT_ROOT and not {"application.js", "mcel.app.json"}.issubset(paths):
        raise AssertionError("Canonical Code Editor source files missing from manifest")
    return {"schema": data["schema"], "project_root": project_root,
            "file_count": len(files), "project_manifest_sha256": digest,
            "canonical_source_present": "application.js" in paths}


def run(base_url: str = "http://127.0.0.1:8765", *, allow_process_probe: bool = False,
        project_root: str = DEFAULT_PROJECT_ROOT, output: Path = DEFAULT_OUTPUT,
        require_windows: bool = True) -> dict:
    checks: dict[str, dict] = {}
    def check(name: str, fn):
        try:
            detail = fn()
            checks[name] = {"status": "PASS", "evidence": detail}
            return detail
        except Exception as exc:
            checks[name] = {"status": "FAIL", "reason": str(exc)}
            return None

    if require_windows and sys.platform != "win32":
        return {"overall": "REFUSED_NON_WINDOWS", "host": sys.platform,
                "checks": {}, "message": "Run this acceptance script on the actual Windows host."}
    parsed = parse.urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Smoke test only permits loopback HTTP: never use an external host.")
    def post(path: str, body: dict) -> dict:
        req = request.Request(base_url.rstrip("/") + path,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with request.urlopen(req, timeout=30) as response:
                data = json.load(response)
                if not isinstance(data, dict) or data.get("ok") is False or data.get("error"):
                    raise AssertionError(f"Refused {path}: {data}")
                return data
        except error.HTTPError as exc:
            raise AssertionError(f"HTTP {exc.code} from {path}: {exc.read(2048).decode('utf8','replace')}") from exc

    snapshot = check("task.overview.all-processes", lambda: post(
        "/api/applications/task/overview", {"query": "", "limit": 100,
        "include_all": True, "include_connections": True}))
    if snapshot is not None:
        checks["task.overview.all-processes"]["evidence"] = {
            "process_rows": len(snapshot.get("processes") or []),
            "has_server": isinstance(snapshot.get("server"), dict),
            "has_hardware": isinstance(snapshot.get("hardware"), dict),
            "platform": sys.platform,
        }
    check("task.server-status", lambda: {
        "action": post("/api/applications/task/action", {"action": "server_status", "confirm": False}).get("action")})
    roots = check("files.roots", lambda: post("/api/applications/file-explorer/roots", {}))
    if roots:
        checks["files.roots"]["evidence"] = {"ids": [r.get("id") for r in roots.get("roots", [])]}
        root = next((r for r in roots.get("roots", []) if r.get("id") == "workspace"), None)
        if root:
            listing = check("files.workspace", lambda: post(
                "/api/applications/file-explorer/list", {"root_id": root["id"], "relative_path": ""}))
            if listing:
                checks["files.workspace"]["evidence"] = {"count": len(listing.get("entries", []))}
                directory = next((e for e in listing.get("entries", []) if e.get("kind") == "directory"), None)
                if directory:
                    nested = check("files.nested-directory", lambda: post(
                        "/api/applications/file-explorer/list", {
                            "root_id": root["id"], "relative_path": directory["relative_path"]}))
                    if nested:
                        checks["files.nested-directory"]["evidence"] = {
                            "path": nested.get("relative_path"), "count": len(nested.get("entries", []))}
                else:
                    checks["files.nested-directory"] = {"status": "UNVERIFIED", "reason": "No directory in workspace root"}
    files = check("editor.workspace", lambda: post("/api/applications/editor/files", {
        "repo_dir": ".", "path": "", "limit": 100}))
    if files:
        entries = files.get("entries") or files.get("files") or []
        checks["editor.workspace"]["evidence"] = {"entries": len(entries)}
        selected = next((e for e in entries if e.get("kind") not in {"directory", "dir"} and
                         str(e.get("path") or e.get("relative_path") or e.get("name") or "").endswith((".md", ".py", ".js"))), None)
        if selected:
            file_path = selected.get("path") or selected.get("relative_path") or selected.get("name")
            read = check("editor.read", lambda: post("/api/applications/editor/read", {
                "repo_dir": ".", "files": [file_path], "instruction": "Windows migration read-only proof"}))
            if read:
                checks["editor.read"]["evidence"] = {"files": len(read.get("files", [])), "path": file_path}
        else:
            checks["editor.read"] = {"status": "UNVERIFIED", "reason": "No readable root-level text file"}
    check("editor.project-manifest", lambda: inspect_manifest_response(
        post("/api/applications/editor/project/manifest", {
            "repo_dir": ".", "project_root": project_root}), project_root))

    if allow_process_probe:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            def kill_child():
                if child.poll() is not None:
                    raise AssertionError("Probe child exited before kill")
                data = post("/api/applications/task/action", {
                    "action": "terminate_pid", "pid": child.pid, "confirm": True})
                child.wait(timeout=10)
                if child.poll() is None:
                    raise AssertionError("Child did not terminate")
                return {"action": data.get("action"), "pid": child.pid, "exit_code": child.returncode}
            check("task.isolated-child-terminate", kill_child)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
    else:
        checks["task.isolated-child-terminate"] = {"status": "UNVERIFIED", "reason": "Requires --allow-process-probe"}
    checks["server.start-restart-shutdown"] = {"status": "UNVERIFIED", "reason": "Explicit live operator review required"}
    checks["aider.mutating-run"] = {"status": "UNVERIFIED", "reason": "Not permitted by non-mutating smoke"}
    checks["browser.monaco-and-tabs"] = {"status": "UNVERIFIED", "reason": "Requires browser interaction on Windows"}
    failed = [name for name,c in checks.items() if c["status"] == "FAIL"]
    unverified = [name for name,c in checks.items() if c["status"] == "UNVERIFIED"]
    result = {"schema": "mcel.migration-windows-live-smoke.v1",
              "generatedUtc": datetime.now(timezone.utc).isoformat(),
              "overall": "FAIL" if failed else "PASS_WITH_UNVERIFIED_GATES" if unverified else "PASS",
              "host": sys.platform, "target": parsed.netloc, "failed": failed,
              "unverified": unverified, "checks": checks}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("MCEL_WINDOWS_LIVE_SMOKE", result["overall"], "report:", output)
    for name, item in checks.items():
        print(" ", item["status"], name)
        if item["status"] == "FAIL":
            print("    reason:", item.get("reason", "unspecified")[:1200])
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url", default="http://127.0.0.1:8765")
    p.add_argument("--allow-process-probe", action="store_true")
    p.add_argument("--project-root", default=DEFAULT_PROJECT_ROOT,
                   help="Repository-relative project (default: canonical Code Editor package)")
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    a = p.parse_args()
    result = run(a.base_url, allow_process_probe=a.allow_process_probe,
                 project_root=a.project_root, output=a.output)
    sys.exit(0 if result["overall"].startswith("PASS") else 1)
