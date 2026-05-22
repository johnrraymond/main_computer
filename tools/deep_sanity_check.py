from __future__ import annotations

import argparse
import dataclasses
import datetime as _dt
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


TOOL_VERSION = "1.3"
DEFAULT_COMMAND_TIMEOUT_S = 8.0
DEFAULT_PROBE_TIMEOUT_S = 2.0
DEFAULT_WSL_FIND_TIMEOUT_S = 10.0
SECRET_KEY_RE = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|private[_-]?key|app[_-]?key|bearer|credential)",
    re.IGNORECASE,
)
SECRET_VALUE_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?:COOLIFY|DIRECTUS|OPENAI|ANTHROPIC|GITHUB|GH|AWS)[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|KEY)\s*[:=]", re.I),
)
STATUS_ORDER = {"PASS": 0, "INFO": 1, "WARN": 2, "FAIL": 3}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}
UNKNOWN_PROJECT_ROOT = "<unknown project root>"
UNATTRIBUTED_PROJECT_RELATION = "unattributed-project-containers"

LIVE_PROGRESS = False
PROGRESS_STARTED_AT = time.monotonic()


def set_live_progress(enabled: bool) -> None:
    global LIVE_PROGRESS
    LIVE_PROGRESS = bool(enabled)


def progress(message: str, evidence: dict[str, Any] | None = None) -> None:
    if not LIVE_PROGRESS:
        return
    elapsed = time.monotonic() - PROGRESS_STARTED_AT
    suffix = ""
    if evidence:
        try:
            suffix = " " + json.dumps(redact_mapping(evidence), sort_keys=True)
        except Exception:
            suffix = " " + str(evidence)
    print(f"[deep-sanity +{elapsed:07.2f}s] {message}{suffix}", file=sys.stderr, flush=True)


class FindingCollector(list):
    def append(self, finding: Finding) -> None:  # type: ignore[name-defined]
        super().append(finding)
        if isinstance(finding, Finding):
            evidence = finding.evidence if finding.evidence else None
            progress(f"{finding.status} {finding.area}: {finding.message}", evidence)

    def extend(self, findings: Any) -> None:
        for finding in findings:
            self.append(finding)


def format_command(command: list[str]) -> str:
    return " ".join(str(part) for part in command)


def bytes_human(value: int | float | None) -> str:
    if value is None:
        return ""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return ""
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    idx = 0
    while abs(n) >= 1024 and idx < len(units) - 1:
        n /= 1024
        idx += 1
    return f"{n:.1f} {units[idx]}" if idx else f"{int(n)} {units[idx]}"





@dataclasses.dataclass(frozen=True)
class Finding:
    status: str
    area: str
    message: str
    evidence: dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = {"status": self.status, "area": self.area, "message": self.message}
        if self.evidence:
            data["evidence"] = self.evidence
        return data


@dataclasses.dataclass(frozen=True)
class CommandResult:
    command: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    unavailable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "unavailable": self.unavailable,
        }


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def decode_process_output(data: bytes) -> str:
    if not data:
        return ""
    if data.count(b"\x00") > max(2, len(data) // 8):
        try:
            return data.decode("utf-16le", errors="replace").replace("\x00", "")
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace").replace("\x00", "")


def run_command(
    command: list[str],
    *,
    timeout_s: float = DEFAULT_COMMAND_TIMEOUT_S,
    cwd: Path | None = None,
    purpose: str = "",
) -> CommandResult:
    exe = command[0] if command else ""
    command_text = format_command(command)
    label = purpose or command_text
    if not exe or shutil.which(exe) is None:
        progress("command unavailable", {"command": command_text, "purpose": purpose or None})
        return CommandResult(command, None, "", f"command not found: {exe}", unavailable=True)
    started = time.monotonic()
    progress("command start", {"purpose": label, "timeout_s": timeout_s, "cwd": str(cwd) if cwd else ""})
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(1.0, timeout_s),
            check=False,
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        stdout = decode_process_output(completed.stdout)
        stderr = decode_process_output(completed.stderr)
        progress(
            "command done",
            {
                "purpose": label,
                "returncode": completed.returncode,
                "elapsed_ms": elapsed_ms,
                "stdout_lines": len(stdout.splitlines()),
                "stderr_lines": len(stderr.splitlines()),
            },
        )
        return CommandResult(
            command=command,
            returncode=completed.returncode,
            stdout=stdout,
            stderr=stderr,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        progress("command timeout", {"purpose": label, "timeout_s": timeout_s, "elapsed_ms": elapsed_ms})
        return CommandResult(
            command=command,
            returncode=None,
            stdout=decode_process_output(exc.stdout or b""),
            stderr=decode_process_output(exc.stderr or b"") + f"\nTIMEOUT after {timeout_s:.1f}s",
            timed_out=True,
        )
    except OSError as exc:
        progress("command failed", {"purpose": label, "error": str(exc)})
        return CommandResult(command, None, "", str(exc), unavailable=True)


def redact_value(key: str, value: object) -> object:
    if value is None:
        return None
    text = str(value)
    if SECRET_KEY_RE.search(str(key)):
        return f"<redacted:{len(text)} chars>"
    return text if len(text) <= 500 else text[:500] + "...<truncated>"


def redact_mapping(values: dict[str, object]) -> dict[str, object]:
    return {str(key): redact_value(str(key), value) for key, value in sorted(values.items())}


def read_json_file(path: Path) -> tuple[dict[str, Any] | list[Any] | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "missing"
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"
    except OSError as exc:
        return None, str(exc)
    if isinstance(payload, (dict, list)):
        return payload, None
    return None, "top-level JSON is not object/list"


def safe_relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def find_repo_root(start: Path | None = None) -> Path:
    candidate = (start or Path.cwd()).resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for path in [candidate, *candidate.parents]:
        if (path / "new_patch.py").is_file() and (path / "main_computer").is_dir():
            return path
        if (path / "pyproject.toml").is_file() and (path / "main_computer").is_dir():
            return path
    return candidate


def parse_env_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return result
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip("'\"")
        if key:
            result[key] = value
    return result


def normalize_host(host: str) -> str:
    clean = str(host or "").strip().strip("\"'")
    return clean or "0.0.0.0"


def parse_port_mapping(value: object) -> dict[str, Any] | None:
    raw = str(value or "").strip().strip("\"'")
    if not raw:
        return None
    # Supports "127.0.0.1:8000:8080", "8000:8080", and "8000".
    proto = ""
    if "/" in raw:
        raw, proto = raw.rsplit("/", 1)
    pieces = raw.split(":")
    host_ip = "0.0.0.0"
    host_port = ""
    container_port = ""
    if len(pieces) >= 3:
        host_ip = ":".join(pieces[:-2]) or "0.0.0.0"
        host_port = pieces[-2]
        container_port = pieces[-1]
    elif len(pieces) == 2:
        host_port, container_port = pieces
    elif len(pieces) == 1:
        host_port = pieces[0]
        container_port = pieces[0]
    try:
        host_port_int = int(str(host_port).strip())
    except ValueError:
        return None
    try:
        container_port_int = int(str(container_port).strip()) if container_port else None
    except ValueError:
        container_port_int = None
    return {
        "raw": str(value),
        "host_ip": normalize_host(host_ip),
        "host_port": host_port_int,
        "container_port": container_port_int,
        "protocol": proto or "tcp",
    }


def _strip_yaml_value(value: str) -> str:
    clean = value.strip()
    if " #" in clean:
        clean = clean.split(" #", 1)[0].strip()
    return clean.strip("\"'")


def parse_compose_services(path: Path, repo_root: Path | None = None) -> dict[str, dict[str, Any]]:
    """Small Docker Compose reader for service names, env, ports, labels, and volumes.

    It intentionally avoids a YAML dependency. It is not a general YAML parser, but
    it covers the Compose shape generated and checked by this repository.
    """

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}

    services: dict[str, dict[str, Any]] = {}
    in_services = False
    current_service: str | None = None
    current_section: str | None = None

    def ensure_service(name: str) -> dict[str, Any]:
        return services.setdefault(
            name,
            {
                "service": name,
                "compose_path": safe_relative(path, repo_root) if repo_root else path.as_posix(),
                "ports": [],
                "environment": {},
                "labels": {},
                "volumes": [],
                "image": "",
                "build": "",
            },
        )

    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        if indent == 0:
            key = stripped.split(":", 1)[0]
            in_services = key == "services"
            current_service = None
            current_section = None
            continue
        if not in_services:
            continue
        if indent == 2 and stripped.endswith(":") and not stripped.startswith("-"):
            candidate = stripped[:-1].strip()
            if candidate and not candidate.startswith("x-"):
                current_service = candidate
                current_section = None
                ensure_service(candidate)
            continue
        if current_service is None:
            continue
        service = ensure_service(current_service)
        if indent == 4 and ":" in stripped and not stripped.startswith("-"):
            key, value = stripped.split(":", 1)
            key = key.strip()
            value = _strip_yaml_value(value)
            current_section = key
            if key == "image":
                service["image"] = value
            elif key == "build":
                service["build"] = value
            elif key in {"environment", "ports", "labels", "volumes"}:
                pass
            elif value:
                service[key] = value
            continue
        if current_section == "ports" and stripped.startswith("-"):
            item = stripped[1:].strip()
            parsed = parse_port_mapping(item)
            service["ports"].append(parsed or {"raw": item})
            continue
        if current_section == "volumes" and stripped.startswith("-"):
            service["volumes"].append(stripped[1:].strip().strip("\"'"))
            continue
        if current_section in {"environment", "labels"}:
            if stripped.startswith("-"):
                item = stripped[1:].strip().strip("\"'")
                if "=" in item:
                    key, value = item.split("=", 1)
                    service[current_section][key.strip()] = _strip_yaml_value(value)
                else:
                    service[current_section][item] = ""
            elif ":" in stripped:
                key, value = stripped.split(":", 1)
                service[current_section][key.strip()] = _strip_yaml_value(value)
    return services


def _port_from_url(url: object) -> int | None:
    text = str(url or "").strip()
    if not text:
        return None
    try:
        parsed = urlsplit(text.replace("0.0.0.0", "127.0.0.1"))
    except ValueError:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    try:
        return parsed.port
    except ValueError:
        return None


def lane_alias(value: object) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"prod", "production"}:
        return "local"
    return raw or "local"


def collect_registry_sites(repo_root: Path, findings: list[Finding]) -> list[dict[str, Any]]:
    path = repo_root / "runtime" / "local-platform" / "sites.json"
    payload, error = read_json_file(path)
    if error == "missing":
        findings.append(Finding("INFO", "websites", "Local platform registry is not present yet.", {"path": safe_relative(path, repo_root)}))
        return []
    if error or not isinstance(payload, dict):
        findings.append(Finding("FAIL", "websites", "Local platform registry cannot be read.", {"path": safe_relative(path, repo_root), "error": error}))
        return []

    sites_obj = payload.get("sites")
    if not isinstance(sites_obj, dict):
        findings.append(Finding("FAIL", "websites", "Local platform registry has no sites object.", {"path": safe_relative(path, repo_root)}))
        return []

    records: list[dict[str, Any]] = []
    for site_id, raw_site in sorted(sites_obj.items()):
        if not isinstance(raw_site, dict):
            findings.append(Finding("WARN", "websites", "Registry site entry is not an object.", {"site_id": site_id}))
            continue
        lanes = raw_site.get("lanes") if isinstance(raw_site.get("lanes"), dict) else {}
        for lane_name, raw_lane in sorted(lanes.items()):
            if not isinstance(raw_lane, dict):
                continue
            port = raw_lane.get("port") or _port_from_url(raw_lane.get("url"))
            records.append(
                {
                    "source": "registry",
                    "source_path": safe_relative(path, repo_root),
                    "site_id": str(raw_site.get("id") or site_id),
                    "name": str(raw_site.get("name") or site_id),
                    "kind": str(raw_site.get("kind") or ""),
                    "lane": lane_alias(lane_name),
                    "registry_lane": lane_name,
                    "service": str(raw_lane.get("service") or ""),
                    "port": int(port) if isinstance(port, int) or str(port).isdigit() else None,
                    "url": str(raw_lane.get("url") or ""),
                    "status_url": str(raw_lane.get("status_url") or ""),
                    "repo_relative_path": str(raw_site.get("repo_relative_path") or ""),
                    "archived": False,
                }
            )
    findings.append(Finding("PASS" if records else "INFO", "websites", f"Read {len(records)} local platform registry lane(s).", {"path": safe_relative(path, repo_root)}))
    return records


def _manifest_lanes(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    platform = payload.get("local_platform")
    if not isinstance(platform, dict):
        return {}
    lanes = platform.get("lanes")
    result: dict[str, dict[str, Any]] = {}
    if isinstance(lanes, dict):
        for lane_name, lane_data in lanes.items():
            if isinstance(lane_data, dict):
                result[lane_alias(lane_name)] = lane_data
    for legacy_lane, legacy_key in (("local", "local_url"), ("dev", "dev_url")):
        if legacy_lane not in result and platform.get(legacy_key):
            result[legacy_lane] = {"url": str(platform.get(legacy_key))}
    return result


def collect_manifest_sites(repo_root: Path, findings: list[Finding]) -> list[dict[str, Any]]:
    roots = [
        (repo_root / "runtime" / "websites", False),
        (repo_root / "runtime" / "websites-archive", True),
    ]
    records: list[dict[str, Any]] = []
    manifest_count = 0
    for base, archived in roots:
        if not base.exists():
            continue
        for manifest_path in sorted(base.glob("*/site.json")):
            manifest_count += 1
            payload, error = read_json_file(manifest_path)
            if error or not isinstance(payload, dict):
                findings.append(
                    Finding(
                        "FAIL",
                        "websites",
                        "Website manifest cannot be read.",
                        {"path": safe_relative(manifest_path, repo_root), "error": error},
                    )
                )
                continue
            site_id = str(payload.get("id") or manifest_path.parent.name)
            lanes = _manifest_lanes(payload)
            if not lanes:
                records.append(
                    {
                        "source": "manifest",
                        "source_path": safe_relative(manifest_path, repo_root),
                        "site_id": site_id,
                        "name": str(payload.get("name") or site_id),
                        "kind": str(payload.get("kind") or ""),
                        "lane": "",
                        "service": "",
                        "port": None,
                        "url": "",
                        "status_url": "",
                        "repo_relative_path": safe_relative(manifest_path.parent, repo_root),
                        "archived": archived,
                        "publish_targets": payload.get("publish_targets") if isinstance(payload.get("publish_targets"), dict) else {},
                    }
                )
                continue
            for lane_name, lane in sorted(lanes.items()):
                port = lane.get("port") or _port_from_url(lane.get("url"))
                records.append(
                    {
                        "source": "manifest",
                        "source_path": safe_relative(manifest_path, repo_root),
                        "site_id": site_id,
                        "name": str(payload.get("name") or site_id),
                        "kind": str(payload.get("kind") or ""),
                        "lane": lane_alias(lane_name),
                        "service": str(lane.get("service") or ""),
                        "port": int(port) if isinstance(port, int) or str(port).isdigit() else None,
                        "url": str(lane.get("url") or ""),
                        "status_url": str(lane.get("status_url") or ""),
                        "repo_relative_path": safe_relative(manifest_path.parent, repo_root),
                        "archived": archived,
                        "publish_targets": payload.get("publish_targets") if isinstance(payload.get("publish_targets"), dict) else {},
                    }
                )
            check_website_artifacts(repo_root, manifest_path.parent, payload, archived, findings)
            check_publish_targets(repo_root, manifest_path, payload, findings)
    findings.append(Finding("PASS" if manifest_count else "INFO", "websites", f"Found {manifest_count} website manifest(s)."))
    return records


def collect_compose_sites(repo_root: Path, findings: list[Finding]) -> list[dict[str, Any]]:
    compose_paths = [
        repo_root / "deploy" / "local-platform" / "generated" / "docker-compose.websites.yml",
        repo_root / "deploy" / "local-platform" / "docker-compose.yml",
    ]
    records: list[dict[str, Any]] = []
    for path in compose_paths:
        if not path.exists():
            continue
        services = parse_compose_services(path, repo_root)
        for service_name, service in sorted(services.items()):
            env = service.get("environment") if isinstance(service.get("environment"), dict) else {}
            site_id = str(env.get("SITE_ID") or env.get("MC_SITE_ID") or service_name)
            lane = lane_alias(env.get("SITE_LANE") or env.get("MC_RUNTIME_LANE") or ("dev" if service_name.endswith("-dev") else "local"))
            ports = [port for port in service.get("ports", []) if isinstance(port, dict) and isinstance(port.get("host_port"), int)]
            if not ports:
                records.append(
                    {
                        "source": "compose",
                        "source_path": safe_relative(path, repo_root),
                        "site_id": site_id,
                        "name": str(env.get("SITE_NAME") or site_id),
                        "kind": str(env.get("SITE_KIND") or ""),
                        "lane": lane,
                        "service": service_name,
                        "port": None,
                        "url": "",
                        "status_url": "",
                        "repo_relative_path": "",
                        "archived": False,
                        "image": service.get("image") or "",
                        "port_bind": "",
                    }
                )
            for port in ports:
                host_port = port.get("host_port")
                records.append(
                    {
                        "source": "compose",
                        "source_path": safe_relative(path, repo_root),
                        "site_id": site_id,
                        "name": str(env.get("SITE_NAME") or site_id),
                        "kind": str(env.get("SITE_KIND") or ""),
                        "lane": lane,
                        "service": service_name,
                        "port": host_port,
                        "url": f"http://localhost:{host_port}/" if isinstance(host_port, int) else "",
                        "status_url": f"http://localhost:{host_port}/api/site/status" if isinstance(host_port, int) else "",
                        "repo_relative_path": "",
                        "archived": False,
                        "image": service.get("image") or "",
                        "port_bind": port,
                    }
                )
        findings.append(
            Finding(
                "PASS" if services else "WARN",
                "websites",
                f"Read {len(services)} service(s) from Compose file.",
                {"path": safe_relative(path, repo_root)},
            )
        )
    return records


def check_website_artifacts(repo_root: Path, site_dir: Path, payload: dict[str, Any], archived: bool, findings: list[Finding]) -> None:
    if archived:
        return
    site_id = str(payload.get("id") or site_dir.name)
    builder = payload.get("builder") if isinstance(payload.get("builder"), dict) else {}
    expected = [
        builder.get("entry_html") or "index.html",
        builder.get("stylesheet") or "style.css",
        builder.get("script") or "script.js",
        builder.get("state_file") or "builder.json",
    ]
    missing = []
    for rel in expected:
        rel_text = str(rel or "").strip()
        if not rel_text or rel_text.startswith("/") or ".." in Path(rel_text).parts:
            findings.append(Finding("FAIL", "website-only", "Website manifest has an unsafe artifact path.", {"site_id": site_id, "artifact": rel_text}))
            continue
        if not (site_dir / rel_text).is_file():
            missing.append(rel_text)
    if missing:
        findings.append(
            Finding(
                "WARN",
                "website-only",
                "Website-only project is missing expected public/builder artifact files.",
                {"site_id": site_id, "site_dir": safe_relative(site_dir, repo_root), "missing": missing},
            )
        )


def check_publish_targets(repo_root: Path, manifest_path: Path, payload: dict[str, Any], findings: list[Finding]) -> None:
    site_id = str(payload.get("id") or manifest_path.parent.name)
    publish_targets = payload.get("publish_targets")
    if not isinstance(publish_targets, dict):
        return
    remote = publish_targets.get("remote_prod")
    if not isinstance(remote, dict):
        return
    accepted = bool(remote.get("accepted_at"))
    controller = str(remote.get("controller_id") or "")
    domain = str(remote.get("domain") or "").strip()
    project = str(remote.get("project") or "").strip()
    env = str(remote.get("environment") or "").strip()
    if accepted and (not controller or not project or not env):
        findings.append(
            Finding(
                "FAIL",
                "coolify",
                "Accepted remote website target is missing controller/project/environment data.",
                {"site_id": site_id, "path": safe_relative(manifest_path, repo_root), "target": redact_mapping(remote)},
            )
        )
    if accepted and not domain:
        findings.append(
            Finding(
                "WARN",
                "coolify",
                "Accepted remote website target has no domain recorded; website-only deploys can look healthy locally but route nowhere remotely.",
                {"site_id": site_id, "path": safe_relative(manifest_path, repo_root), "controller_id": controller, "project": project},
            )
        )
    if domain and any(part in domain for part in ("http://", "https://", "/")):
        findings.append(
            Finding(
                "WARN",
                "coolify",
                "Remote publish target domain should be a bare hostname, not a URL/path.",
                {"site_id": site_id, "domain": domain, "path": safe_relative(manifest_path, repo_root)},
            )
        )


def key_for_record(record: dict[str, Any]) -> tuple[str, str]:
    return (str(record.get("site_id") or ""), str(record.get("lane") or ""))


def analyze_website_port_space(records: list[dict[str, Any]], findings: list[Finding]) -> list[dict[str, Any]]:
    expected_by_key: dict[tuple[str, str], dict[str, list[Any]]] = {}
    port_claims: dict[int, list[dict[str, Any]]] = {}
    for record in records:
        port = record.get("port")
        key = key_for_record(record)
        bucket = expected_by_key.setdefault(key, {"records": [], "ports": [], "services": []})
        bucket["records"].append(record)
        if isinstance(port, int):
            bucket["ports"].append(port)
            port_claims.setdefault(port, []).append(record)
        if record.get("service"):
            bucket["services"].append(record.get("service"))

    for key, bucket in sorted(expected_by_key.items()):
        site_id, lane = key
        if not site_id:
            continue
        ports = sorted({p for p in bucket["ports"] if isinstance(p, int)})
        services = sorted({str(s) for s in bucket["services"] if s})
        sources = sorted({str(r.get("source")) for r in bucket["records"]})
        if len(ports) > 1:
            findings.append(
                Finding(
                    "FAIL",
                    "websites",
                    "Website lane has mismatched intended ports across registry/manifest/compose.",
                    {"site_id": site_id, "lane": lane, "ports": ports, "services": services, "sources": sources},
                )
            )
        if len(services) > 1:
            findings.append(
                Finding(
                    "WARN",
                    "websites",
                    "Website lane has multiple service names across sources.",
                    {"site_id": site_id, "lane": lane, "services": services, "sources": sources},
                )
            )
    for port, claims in sorted(port_claims.items()):
        active_claims = [c for c in claims if not c.get("archived")]
        unique_site_lanes = sorted({f"{c.get('site_id')}:{c.get('lane')}" for c in active_claims})
        if len(unique_site_lanes) > 1:
            findings.append(
                Finding(
                    "FAIL",
                    "websites",
                    "Multiple active website lanes claim the same host port.",
                    {"port": port, "claims": unique_site_lanes},
                )
            )
    matrix: list[dict[str, Any]] = []
    seen_rows: set[tuple[Any, ...]] = set()
    duplicate_rows = 0
    for record in sorted(records, key=lambda r: (str(r.get("site_id") or ""), str(r.get("lane") or ""), str(r.get("source") or ""), str(r.get("service") or ""), str(r.get("port") or ""))):
        row = {
            "site_id": record.get("site_id"),
            "lane": record.get("lane"),
            "source": record.get("source"),
            "service": record.get("service"),
            "port": record.get("port"),
            "url": record.get("url"),
            "status_url": record.get("status_url"),
            "archived": bool(record.get("archived")),
            "path": record.get("source_path") or record.get("repo_relative_path"),
        }
        key = (
            row.get("site_id"),
            row.get("lane"),
            row.get("source"),
            row.get("service"),
            row.get("port"),
            row.get("url"),
            row.get("status_url"),
            row.get("path"),
            row.get("archived"),
        )
        if key in seen_rows:
            duplicate_rows += 1
            continue
        seen_rows.add(key)
        matrix.append(row)
    if duplicate_rows:
        findings.append(Finding("INFO", "websites", "Collapsed duplicate website port-space row(s) in the rendered matrix.", {"duplicates": duplicate_rows}))
    return matrix


def url_is_local(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    host = parsed.hostname or ""
    return host in LOCAL_HOSTS


def probe_url(url: str, timeout_s: float) -> dict[str, Any]:
    if not url:
        return {"ok": False, "error": "empty URL"}
    probe = url.replace("0.0.0.0", "127.0.0.1")
    started = time.monotonic()
    try:
        request = Request(probe, headers={"User-Agent": f"main-computer-deep-sanity/{TOOL_VERSION}"})
        with urlopen(request, timeout=timeout_s) as response:
            body = response.read(4096).decode("utf-8", errors="replace")
            return {
                "ok": 200 <= int(response.status) < 300,
                "status": int(response.status),
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "body_prefix": body[:300],
            }
    except HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="replace")
        return {"ok": False, "status": int(exc.code), "elapsed_ms": int((time.monotonic() - started) * 1000), "error": body[:300] or str(exc)}
    except (OSError, URLError, ValueError) as exc:
        return {"ok": False, "elapsed_ms": int((time.monotonic() - started) * 1000), "error": str(exc)}


def probe_local_website_urls(records: list[dict[str, Any]], findings: list[Finding], *, timeout_s: float) -> list[dict[str, Any]]:
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for record in records:
        for key in ("status_url", "url"):
            url = str(record.get(key) or "")
            if not url or url in seen or not url_is_local(url):
                continue
            seen.add(url)
            progress("http probe start", {"url": url, "site_id": record.get("site_id"), "lane": record.get("lane"), "timeout_s": timeout_s})
            result = probe_url(url, timeout_s)
            progress("http probe done", {"url": url, "ok": result.get("ok"), "status": result.get("status"), "elapsed_ms": result.get("elapsed_ms"), "error": result.get("error")})
            row = {
                "url": url,
                "site_id": record.get("site_id"),
                "lane": record.get("lane"),
                "source": record.get("source"),
                **result,
            }
            results.append(row)
            if result.get("ok"):
                findings.append(Finding("PASS", "websites", "Local website endpoint responded.", {"url": url, "site_id": record.get("site_id"), "lane": record.get("lane")}))
            else:
                findings.append(Finding("WARN", "websites", "Local website endpoint did not respond.", {"url": url, "site_id": record.get("site_id"), "lane": record.get("lane"), "error": result.get("error"), "status": result.get("status")}))
    return results


def parse_netstat_ports(text: str) -> dict[int, list[dict[str, Any]]]:
    owners: dict[int, list[dict[str, Any]]] = {}
    seen: set[tuple[int, str, str, str]] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        pieces = line.split()
        if len(pieces) < 4:
            continue
        proto = pieces[0].lower()
        if not proto.startswith("tcp"):
            continue
        local = pieces[1]
        state = ""
        pid = ""
        if len(pieces) >= 5:
            state = pieces[-2].upper()
            pid = pieces[-1] if pieces[-1].isdigit() else ""
        elif len(pieces) >= 4:
            state = pieces[-1].upper()
        if state and not state.startswith("LISTEN"):
            continue
        if not state:
            continue
        if ":" not in local:
            continue
        port_text = local.rsplit(":", 1)[-1]
        if not port_text.isdigit():
            continue
        port = int(port_text)
        key = (port, proto, local, pid)
        if key in seen:
            continue
        seen.add(key)
        owners.setdefault(port, []).append({"source": "netstat", "proto": proto, "local": local, "pid": pid})
    return owners


def parse_ss_ports(text: str) -> dict[int, list[dict[str, Any]]]:
    owners: dict[int, list[dict[str, Any]]] = {}
    seen: set[tuple[int, str, str]] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("State "):
            continue
        pieces = line.split()
        if len(pieces) < 4:
            continue
        local = pieces[3]
        if ":" not in local:
            continue
        port_text = local.rsplit(":", 1)[-1]
        if not port_text.isdigit():
            continue
        process = ""
        if "users:" in line:
            process = line.split("users:", 1)[1].strip()
        port = int(port_text)
        key = (port, local, process)
        if key in seen:
            continue
        seen.add(key)
        owners.setdefault(port, []).append({"source": "ss", "local": local, "process": process})
    return owners


def collect_local_port_owners() -> dict[int, list[dict[str, Any]]]:
    if os.name == "nt":
        result = run_command(["netstat", "-ano", "-p", "tcp"], timeout_s=8, purpose="netstat TCP listener inventory")
        return parse_netstat_ports(result.stdout) if result.stdout else {}
    ss = run_command(["ss", "-ltnp"], timeout_s=6, purpose="ss TCP listener inventory")
    if ss.returncode == 0 and ss.stdout:
        return parse_ss_ports(ss.stdout)
    netstat = run_command(["netstat", "-ltnp"], timeout_s=6, purpose="netstat TCP listener inventory")
    return parse_netstat_ports(netstat.stdout) if netstat.stdout else {}



def docker_ps_size_rows(timeout_s: float = DEFAULT_COMMAND_TIMEOUT_S) -> tuple[list[dict[str, Any]], CommandResult | None]:
    result = run_command(
        ["docker", "ps", "-a", "--size", "--format", "{{json .}}"],
        timeout_s=max(1.0, timeout_s),
        purpose="docker ps -a with sizes",
    )
    if result.unavailable or result.returncode != 0:
        return [], result
    rows: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            rows.append({"raw": line, "parse_error": "invalid json"})
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows, result


def docker_size_lookup(size_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for row in size_rows:
        for key in ("ID", "Names", "Names"):
            value = str(row.get(key) or "").strip()
            if value:
                lookup[value.lstrip("/")] = row
    return lookup


def docker_system_df(timeout_s: float = DEFAULT_COMMAND_TIMEOUT_S) -> tuple[dict[str, Any], CommandResult]:
    result = run_command(
        ["docker", "system", "df", "-v"],
        timeout_s=max(10.0, timeout_s),
        purpose="docker system df -v storage inventory",
    )
    return {
        "available": not result.unavailable,
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "stdout": result.stdout,
        "stderr": result.stderr.strip(),
    }, result


def container_compose_project(labels: dict[str, Any]) -> str:
    return str(
        labels.get("com.docker.compose.project")
        or labels.get("com.docker.compose.project.working_dir")
        or labels.get("com.docker.compose.service")
        or ""
    )


def classify_docker_container(container: dict[str, Any], repo_root: Path, website_matrix: list[dict[str, Any]]) -> dict[str, Any]:
    summary_text_parts: list[str] = []
    config = container.get("Config") if isinstance(container.get("Config"), dict) else {}
    state = container.get("State") if isinstance(container.get("State"), dict) else {}
    labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
    mounts = container.get("Mounts") if isinstance(container.get("Mounts"), list) else []
    name = container_name(container)
    image = str(config.get("Image") or "")
    compose_project = container_compose_project(labels)
    service_names = {str(row.get("service") or "") for row in website_matrix if row.get("service")}
    site_ids = {str(row.get("site_id") or "") for row in website_matrix if row.get("site_id")}
    repo_name = repo_root.name.lower()
    repo_abs = str(repo_root).lower()
    joined = " ".join([name, image, compose_project, json.dumps(labels, sort_keys=True)]).lower()
    reasons: list[str] = []
    relation = "unknown"
    confidence = "low"
    is_project = False

    project_needles = [
        "main-computer",
        "main_computer",
        "maincomputer",
        repo_name,
        "main-computer-local-platform",
        "main-computer-site",
    ]
    if any(needle and needle in joined for needle in project_needles):
        is_project = True
        relation = "project"
        confidence = "high"
        reasons.append("name/image/labels contain Main Computer project markers")

    for service in sorted(service_names):
        if service and service.lower() in joined:
            is_project = True
            relation = "project"
            confidence = "high"
            reasons.append(f"matches website service {service}")
            break

    for site_id in sorted(site_ids):
        if site_id and len(site_id) >= 3 and site_id.lower() in joined:
            is_project = True
            relation = "project"
            confidence = "medium" if confidence == "low" else confidence
            reasons.append(f"mentions site id {site_id}")
            break

    for mount in mounts:
        source = str(mount.get("Source") or "").lower() if isinstance(mount, dict) else ""
        destination = str(mount.get("Destination") or "").lower() if isinstance(mount, dict) else ""
        if repo_abs and repo_abs in source:
            is_project = True
            relation = "project"
            confidence = "high"
            reasons.append("bind mount source is inside this repository")
            break
        if "main_computer" in source or "main-computer" in source or "main_computer" in destination or "main-computer" in destination:
            is_project = True
            relation = "project"
            confidence = "medium" if confidence == "low" else confidence
            reasons.append("mount mentions Main Computer")
            break

    if not is_project and ("coolify" in joined or "traefik" in joined):
        relation = "project-adjacent"
        confidence = "medium"
        reasons.append("Coolify/reverse-proxy container; may host project websites but is not itself a project service")

    if not is_project and ("docker-desktop" in joined or name in {"docker-desktop", "k8s", "kind"}):
        relation = "docker-system"
        confidence = "medium"
        reasons.append("Docker Desktop/system-looking container")

    if not reasons:
        relation = "not-project"
        confidence = "low"
        reasons.append("no Main Computer names, site ids, compose project labels, or repo mounts detected")

    return {
        "is_project": is_project,
        "relation": relation,
        "confidence": confidence,
        "reasons": reasons[:8],
        "compose_project": compose_project,
        "running": bool(state.get("Running")),
    }


def summarize_mounts(container: dict[str, Any], repo_root: Path) -> list[dict[str, Any]]:
    mounts = container.get("Mounts") if isinstance(container.get("Mounts"), list) else []
    rows: list[dict[str, Any]] = []
    repo_abs = str(repo_root).lower()
    for mount in mounts[:30]:
        if not isinstance(mount, dict):
            continue
        source = str(mount.get("Source") or "")
        destination = str(mount.get("Destination") or "")
        rows.append(
            {
                "type": mount.get("Type") or "",
                "source": source,
                "destination": destination,
                "inside_repo": bool(repo_abs and repo_abs in source.lower()),
            }
        )
    return rows


def docker_inspect_containers() -> tuple[list[dict[str, Any]], list[Finding]]:
    findings: list[Finding] = []
    ps = run_command(["docker", "ps", "-aq"], timeout_s=8, purpose="docker ps -aq container id inventory")
    if ps.unavailable:
        findings.append(Finding("INFO", "docker", "Docker CLI is not available.", {"error": ps.stderr}))
        return [], findings
    if ps.returncode != 0:
        findings.append(Finding("WARN", "docker", "Docker CLI is available but docker ps failed.", {"stderr": ps.stderr.strip()}))
        return [], findings
    ids = [line.strip() for line in ps.stdout.splitlines() if line.strip()]
    if not ids:
        findings.append(Finding("INFO", "docker", "Docker CLI is available; no containers were found."))
        return [], findings
    inspect = run_command(["docker", "inspect", *ids], timeout_s=20, purpose=f"docker inspect {len(ids)} container(s)")
    if inspect.returncode != 0:
        findings.append(Finding("WARN", "docker", "docker inspect failed.", {"stderr": inspect.stderr.strip()}))
        return [], findings
    try:
        payload = json.loads(inspect.stdout)
    except json.JSONDecodeError as exc:
        findings.append(Finding("WARN", "docker", "docker inspect output was not valid JSON.", {"error": str(exc)}))
        return [], findings
    containers = payload if isinstance(payload, list) else []
    findings.append(Finding("PASS", "docker", f"Inspected {len(containers)} Docker container(s)."))
    return containers, findings


def container_name(container: dict[str, Any]) -> str:
    raw = str(container.get("Name") or "")
    return raw.lstrip("/") or str(container.get("Id") or "")[:12]


def docker_container_summary(
    container: dict[str, Any],
    *,
    repo_root: Path | None = None,
    website_matrix: list[dict[str, Any]] | None = None,
    size_lookup: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    config = container.get("Config") if isinstance(container.get("Config"), dict) else {}
    state = container.get("State") if isinstance(container.get("State"), dict) else {}
    network = container.get("NetworkSettings") if isinstance(container.get("NetworkSettings"), dict) else {}
    labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
    ports = network.get("Ports") if isinstance(network.get("Ports"), dict) else {}
    name = container_name(container)
    short_id = str(container.get("Id") or "")[:12]
    host_ports: list[dict[str, Any]] = []
    for container_port, binds in sorted(ports.items()):
        if not isinstance(binds, list):
            continue
        for bind in binds:
            if not isinstance(bind, dict):
                continue
            host_port = str(bind.get("HostPort") or "")
            if host_port.isdigit():
                host_ports.append({"host_ip": bind.get("HostIp") or "", "host_port": int(host_port), "container_port": container_port})
    size_row: dict[str, Any] = {}
    if size_lookup:
        size_row = size_lookup.get(name) or size_lookup.get(short_id) or size_lookup.get(str(container.get("Id") or "")) or {}
    classification = (
        classify_docker_container(container, repo_root, website_matrix or [])
        if repo_root is not None
        else {"is_project": False, "relation": "unknown", "confidence": "low", "reasons": []}
    )
    summary = {
        "id": short_id,
        "name": name,
        "image": str(config.get("Image") or ""),
        "running": bool(state.get("Running")),
        "status": str(state.get("Status") or ""),
        "labels": redact_mapping(labels),
        "host_ports": host_ports,
        "size": str(size_row.get("Size") or ""),
        "created_at": str(size_row.get("CreatedAt") or ""),
        "project_classification": classification,
    }
    if repo_root is not None:
        summary["mounts"] = summarize_mounts(container, repo_root)
    return summary


def docker_port_owners(containers: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    owners: dict[int, list[dict[str, Any]]] = {}
    for container in containers:
        summary = docker_container_summary(container)
        for port in summary["host_ports"]:
            owners.setdefault(int(port["host_port"]), []).append(
                {
                    "source": "docker",
                    "container": summary["name"],
                    "image": summary["image"],
                    "running": summary["running"],
                    "container_port": port["container_port"],
                    "host_ip": port["host_ip"],
                }
            )
    return owners



def path_mentions_repo(path_text: object, repo_root: Path) -> bool:
    raw = str(path_text or "").strip()
    if not raw:
        return False
    normalized = raw.replace("\\", "/").rstrip("/").lower()
    repo = str(repo_root).replace("\\", "/").rstrip("/").lower()
    return bool(repo and (normalized == repo or normalized.startswith(repo + "/")))


def resolve_compose_port_token(value: object, env: dict[str, str] | None = None) -> int | None:
    raw = str(value or "").strip().strip("\"'")
    if not raw:
        return None
    env = env or os.environ
    interpolation = re.search(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(\d+))?\}", raw)
    if interpolation:
        name, default = interpolation.groups()
        env_value = str(env.get(name, "")).strip()
        if env_value.isdigit():
            return int(env_value)
        if default and default.isdigit():
            return int(default)
        return None
    pieces = raw.split(":")
    token = pieces[-2] if len(pieces) >= 2 else pieces[-1]
    token = token.strip()
    return int(token) if token.isdigit() else None


def compose_service_default_host_ports(service: dict[str, Any], env: dict[str, str] | None = None) -> list[dict[str, Any]]:
    ports: list[dict[str, Any]] = []
    for port in service.get("ports") or []:
        if not isinstance(port, dict):
            continue
        if isinstance(port.get("host_port"), int):
            ports.append(port)
            continue
        resolved = resolve_compose_port_token(port.get("raw"), env=env)
        if resolved is not None:
            raw = str(port.get("raw") or "")
            container_port = None
            parsed = raw.strip().strip("\"'").split(":")
            if parsed:
                try:
                    container_port = int(parsed[-1].split("/", 1)[0])
                except ValueError:
                    container_port = None
            ports.append(
                {
                    "raw": raw,
                    "host_ip": port.get("host_ip") or "127.0.0.1",
                    "host_port": resolved,
                    "container_port": container_port,
                    "protocol": port.get("protocol") or "tcp",
                    "resolved_from": "compose-default-or-env",
                }
            )
    return ports


def onlyoffice_container_reason(summary: dict[str, Any]) -> bool:
    text = " ".join(
        [
            str(summary.get("name") or ""),
            str(summary.get("image") or ""),
            json.dumps(summary.get("labels") or {}, sort_keys=True),
            json.dumps(summary.get("mounts") or {}, sort_keys=True),
        ]
    ).lower()
    return "onlyoffice" in text or "documentserver" in text


def collect_onlyoffice_state(
    repo_root: Path,
    docker_summaries: list[dict[str, Any]],
    local_port_owners: dict[int, list[dict[str, Any]]],
    docker_owners: dict[int, list[dict[str, Any]]],
    findings: list[Finding],
    *,
    probe: bool,
    probe_timeout_s: float,
) -> dict[str, Any]:
    compose_path = repo_root / "docker-compose.onlyoffice.yml"
    check_script = repo_root / "tools" / "onlyoffice" / "check-onlyoffice.py"
    control_script = repo_root / "tools" / "onlyoffice" / "onlyoffice-control.ps1"
    compose_services = parse_compose_services(compose_path, repo_root) if compose_path.exists() else {}
    onlyoffice_service = compose_services.get("onlyoffice") if isinstance(compose_services.get("onlyoffice"), dict) else {}
    expected_ports = compose_service_default_host_ports(onlyoffice_service, env=os.environ) if onlyoffice_service else []
    if not expected_ports:
        env_port = str(os.environ.get("MAIN_COMPUTER_ONLYOFFICE_PORT") or "").strip()
        fallback_port = int(env_port) if env_port.isdigit() else 18084
        expected_ports = [
            {
                "raw": "MAIN_COMPUTER_ONLYOFFICE_PORT or default",
                "host_ip": "127.0.0.1",
                "host_port": fallback_port,
                "container_port": 80,
                "protocol": "tcp",
                "resolved_from": "fallback",
            }
        ]

    onlyoffice_containers = [row for row in docker_summaries if onlyoffice_container_reason(row)]
    running = [row for row in onlyoffice_containers if row.get("running")]
    stopped = [row for row in onlyoffice_containers if not row.get("running")]
    compose_projects = sorted(
        {
            str((row.get("labels") or {}).get("com.docker.compose.project") or (row.get("project_classification") or {}).get("compose_project") or "")
            for row in onlyoffice_containers
            if str((row.get("labels") or {}).get("com.docker.compose.project") or (row.get("project_classification") or {}).get("compose_project") or "")
        }
    )

    foreign_repo_containers: list[dict[str, Any]] = []
    current_repo_containers: list[dict[str, Any]] = []
    for row in onlyoffice_containers:
        labels = row.get("labels") if isinstance(row.get("labels"), dict) else {}
        path_fields = {
            key: labels.get(key)
            for key in [
                "com.docker.compose.project.working_dir",
                "com.docker.compose.project.config_files",
                "com.docker.compose.project.environment_file",
            ]
            if labels.get(key)
        }
        if any(path_mentions_repo(value, repo_root) for value in path_fields.values()):
            current_repo_containers.append({"name": row.get("name"), "paths": path_fields})
        elif path_fields:
            foreign_repo_containers.append({"name": row.get("name"), "paths": path_fields})

    expected_port_numbers = {int(row["host_port"]) for row in expected_ports if isinstance(row.get("host_port"), int)}
    running_ports = {
        int(port.get("host_port"))
        for row in running
        for port in (row.get("host_ports") or [])
        if isinstance(port, dict) and isinstance(port.get("host_port"), int)
    }
    port_correlation: list[dict[str, Any]] = []
    for port in sorted(expected_port_numbers | running_ports):
        owners = [*docker_owners.get(port, []), *local_port_owners.get(port, [])]
        port_correlation.append({"port": port, "expected": port in expected_port_numbers, "running_onlyoffice": port in running_ports, "owners": owners})

    endpoint_probes: list[dict[str, Any]] = []
    if probe:
        probe_ports = sorted(expected_port_numbers | running_ports)
        for port in probe_ports:
            base = f"http://127.0.0.1:{port}"
            for suffix, required in [("/healthcheck", True), ("/web-apps/apps/api/documents/api.js", True), ("/", False)]:
                url = base + suffix
                progress("probe ONLYOFFICE endpoint", {"url": url, "timeout_s": probe_timeout_s})
                result = probe_url(url, probe_timeout_s)
                endpoint_probes.append({"port": port, "url": url, "required": required, **result})

    if compose_path.exists():
        findings.append(Finding("PASS", "onlyoffice", "ONLYOFFICE Docker Compose file exists.", {"path": safe_relative(compose_path, repo_root), "expected_ports": expected_ports}))
    else:
        findings.append(Finding("INFO", "onlyoffice", "ONLYOFFICE Docker Compose file is not present.", {"path": safe_relative(compose_path, repo_root)}))

    if check_script.exists():
        findings.append(Finding("PASS", "onlyoffice", "ONLYOFFICE checker script exists.", {"path": safe_relative(check_script, repo_root)}))
    else:
        findings.append(Finding("WARN", "onlyoffice", "ONLYOFFICE checker script is missing.", {"path": safe_relative(check_script, repo_root)}))

    if control_script.exists():
        findings.append(Finding("PASS", "onlyoffice", "ONLYOFFICE control script exists.", {"path": safe_relative(control_script, repo_root)}))

    if onlyoffice_containers:
        findings.append(
            Finding(
                "INFO",
                "onlyoffice",
                f"Found {len(onlyoffice_containers)} ONLYOFFICE/documentserver Docker container(s).",
                {"running": len(running), "stopped_or_created": len(stopped), "compose_projects": compose_projects},
            )
        )
    else:
        findings.append(Finding("INFO", "onlyoffice", "No ONLYOFFICE/documentserver Docker containers were found."))

    if running and not (running_ports & expected_port_numbers):
        findings.append(
            Finding(
                "WARN",
                "onlyoffice",
                "ONLYOFFICE is running, but not on the expected current-repo port.",
                {"expected_ports": sorted(expected_port_numbers), "running_ports": sorted(running_ports), "running_containers": [row.get("name") for row in running]},
            )
        )

    if stopped:
        findings.append(
            Finding(
                "WARN",
                "onlyoffice",
                "Stopped/created ONLYOFFICE container(s) exist and may be stale deploy leftovers.",
                {"containers": [{"name": row.get("name"), "status": row.get("status"), "size": row.get("size")} for row in stopped]},
            )
        )

    if len(compose_projects) > 1:
        findings.append(Finding("WARN", "onlyoffice", "Multiple ONLYOFFICE Compose projects exist.", {"compose_projects": compose_projects}))

    if foreign_repo_containers:
        findings.append(
            Finding(
                "WARN",
                "onlyoffice",
                "ONLYOFFICE container(s) point at a different repository path than this sanity-check repo.",
                {"repo_root": str(repo_root), "containers": foreign_repo_containers},
            )
        )
    elif current_repo_containers:
        findings.append(Finding("PASS", "onlyoffice", "ONLYOFFICE container path labels point at this repo.", {"containers": current_repo_containers}))

    for row in port_correlation:
        port = int(row["port"])
        owners = row.get("owners") or []
        if row.get("expected") and not owners:
            findings.append(Finding("INFO", "onlyoffice", "Expected ONLYOFFICE port is not currently owned.", {"port": port}))
        elif row.get("expected"):
            onlyoffice_owner_present = any(
                "onlyoffice" in json.dumps(owner, sort_keys=True).lower()
                or "documentserver" in json.dumps(owner, sort_keys=True).lower()
                for owner in owners
            )
            non_onlyoffice_owners = [
                owner
                for owner in owners
                if owner.get("source") != "netstat"
                and "onlyoffice" not in json.dumps(owner, sort_keys=True).lower()
                and "documentserver" not in json.dumps(owner, sort_keys=True).lower()
            ]
            if non_onlyoffice_owners:
                findings.append(Finding("WARN", "onlyoffice", "Expected ONLYOFFICE port has non-ONLYOFFICE owner(s).", {"port": port, "owners": non_onlyoffice_owners}))
            elif not onlyoffice_owner_present:
                findings.append(Finding("WARN", "onlyoffice", "Expected ONLYOFFICE port is occupied but no ONLYOFFICE Docker owner was found.", {"port": port, "owners": owners}))

    if probe:
        required_failures = [row for row in endpoint_probes if row.get("required") and not row.get("ok")]
        if required_failures and running:
            findings.append(
                Finding(
                    "WARN",
                    "onlyoffice",
                    "One or more required ONLYOFFICE HTTP probes failed.",
                    {"failures": [{"url": row.get("url"), "status": row.get("status"), "error": row.get("error")} for row in required_failures]},
                )
            )
        elif running and endpoint_probes:
            findings.append(Finding("PASS", "onlyoffice", "ONLYOFFICE required HTTP probes responded.", {"ports": sorted(running_ports | expected_port_numbers)}))

    return {
        "compose_path": safe_relative(compose_path, repo_root),
        "compose_services": compose_services,
        "check_script": safe_relative(check_script, repo_root),
        "control_script": safe_relative(control_script, repo_root),
        "expected_ports": expected_ports,
        "containers": onlyoffice_containers,
        "running_containers": [row.get("name") for row in running],
        "stopped_or_created_containers": [row.get("name") for row in stopped],
        "compose_projects": compose_projects,
        "current_repo_containers": current_repo_containers,
        "foreign_repo_containers": foreign_repo_containers,
        "port_correlation": port_correlation,
        "endpoint_probes": endpoint_probes,
    }


def parse_labels_for_routes(labels: dict[str, Any]) -> list[dict[str, str]]:
    routes: list[dict[str, str]] = []
    for key, value in sorted(labels.items()):
        key_text = str(key)
        value_text = str(value)
        if "traefik.http.routers." in key_text and key_text.endswith(".rule"):
            routes.append({"kind": "traefik_router", "label": key_text, "rule": value_text})
        elif "traefik.http.services." in key_text and key_text.endswith(".loadbalancer.server.port"):
            routes.append({"kind": "traefik_service_port", "label": key_text, "port": value_text})
        elif key_text.lower().startswith("caddy") or "coolify" in key_text.lower():
            routes.append({"kind": "coolify_or_proxy_label", "label": key_text, "value": value_text})
    return routes


def collect_coolify_state(repo_root: Path, containers: list[dict[str, Any]], findings: list[Finding]) -> dict[str, Any]:
    state_root = repo_root / "runtime" / "coolify-local-docker"
    compose_path = repo_root / "deploy" / "coolify" / "local-docker" / "docker-compose.yml"
    smoke_path = state_root / "deploy-smoke.json"
    env_paths = [state_root / ".env", state_root / "source" / ".env"]
    services = parse_compose_services(compose_path, repo_root) if compose_path.exists() else {}
    env_files = {
        safe_relative(path, repo_root): redact_mapping(parse_env_file(path))
        for path in env_paths
        if path.exists()
    }
    smoke_payload, smoke_error = read_json_file(smoke_path)
    coolify_containers: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    for container in containers:
        summary = docker_container_summary(container)
        labels = summary.get("labels") if isinstance(summary.get("labels"), dict) else {}
        text = json.dumps(summary, sort_keys=True).lower()
        if "coolify" in text or "mc-coolify" in text:
            coolify_containers.append(summary)
        parsed_routes = parse_labels_for_routes(labels)
        if parsed_routes:
            routes.append({"container": summary["name"], "image": summary["image"], "routes": parsed_routes})

    if compose_path.exists():
        findings.append(Finding("PASS", "coolify", "Coolify local-docker Compose file exists.", {"path": safe_relative(compose_path, repo_root), "services": sorted(services)}))
    else:
        findings.append(Finding("WARN", "coolify", "Coolify local-docker Compose file is missing.", {"path": safe_relative(compose_path, repo_root)}))

    if state_root.exists():
        findings.append(Finding("INFO", "coolify", "Coolify local-docker runtime state directory exists.", {"path": safe_relative(state_root, repo_root)}))
    else:
        findings.append(Finding("INFO", "coolify", "Coolify local-docker runtime state directory is not present yet.", {"path": safe_relative(state_root, repo_root)}))

    if smoke_error and smoke_error != "missing":
        findings.append(Finding("WARN", "coolify", "Coolify deploy-smoke state cannot be read.", {"path": safe_relative(smoke_path, repo_root), "error": smoke_error}))
    elif isinstance(smoke_payload, dict):
        findings.append(Finding("INFO", "coolify", "Coolify deploy-smoke state is present.", {"path": safe_relative(smoke_path, repo_root), "state": redact_mapping(smoke_payload)}))

    if coolify_containers:
        findings.append(Finding("INFO", "coolify", f"Found {len(coolify_containers)} Docker container(s) that look Coolify-related."))
    if routes:
        findings.append(Finding("INFO", "coolify", f"Found {len(routes)} container(s) with reverse-proxy/Coolify labels."))

    return {
        "compose_path": safe_relative(compose_path, repo_root),
        "compose_services": services,
        "state_root": safe_relative(state_root, repo_root),
        "env_files": env_files,
        "deploy_smoke": smoke_payload if isinstance(smoke_payload, dict) else None,
        "coolify_containers": coolify_containers,
        "proxy_routes": routes,
    }


def correlate_ports(
    website_matrix: list[dict[str, Any]],
    local_port_owners: dict[int, list[dict[str, Any]]],
    docker_owners: dict[int, list[dict[str, Any]]],
    findings: list[Finding],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in website_matrix:
        port = entry.get("port")
        if not isinstance(port, int):
            continue
        owners = [*docker_owners.get(port, []), *local_port_owners.get(port, [])]
        row = {**entry, "owners": owners}
        rows.append(row)
        if not owners:
            findings.append(
                Finding(
                    "INFO",
                    "port-space",
                    "Expected website port is not currently owned by a known Docker container/process.",
                    {"site_id": entry.get("site_id"), "lane": entry.get("lane"), "port": port, "source": entry.get("source")},
                )
            )
            continue
        matching_service = str(entry.get("service") or "")
        docker_matches = [
            owner for owner in owners
            if owner.get("source") == "docker" and matching_service and matching_service in str(owner.get("container") or "")
        ]
        if matching_service and not docker_matches and any(owner.get("source") == "docker" for owner in owners):
            findings.append(
                Finding(
                    "WARN",
                    "port-space",
                    "Website port is owned by a Docker container whose name does not match the expected service.",
                    {"site_id": entry.get("site_id"), "lane": entry.get("lane"), "port": port, "expected_service": matching_service, "owners": owners},
                )
            )
    return rows


def scan_public_site_secret_leaks(repo_root: Path, findings: list[Finding]) -> list[dict[str, Any]]:
    roots = [repo_root / "runtime" / "websites"]
    hits: list[dict[str, Any]] = []
    exts = {".html", ".js", ".json", ".css", ".txt", ".md", ".env"}
    for base in roots:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if path.name.lower() == ".env":
                hit = {"path": safe_relative(path, repo_root), "reason": "public .env file"}
                hits.append(hit)
                findings.append(Finding("FAIL", "website-only", "Public website tree contains a .env file.", hit))
                continue
            if path.suffix.lower() not in exts or path.stat().st_size > 1_000_000:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for pattern in SECRET_VALUE_PATTERNS:
                match = pattern.search(text)
                if match:
                    hit = {"path": safe_relative(path, repo_root), "pattern": pattern.pattern[:80]}
                    hits.append(hit)
                    findings.append(Finding("FAIL", "website-only", "Possible secret material found in public website artifact.", hit))
                    break
    if not hits:
        findings.append(Finding("PASS", "website-only", "No obvious public website secret leaks were found in runtime/websites."))
    return hits


def parse_wsl_list(text: str) -> list[dict[str, Any]]:
    distros: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.replace("\x00", "").strip()
        if not line or line.lower().startswith("name"):
            continue
        default = line.startswith("*")
        line = line.lstrip("*").strip()
        pieces = re.split(r"\s{2,}", line)
        if not pieces:
            continue
        name = pieces[0].strip()
        state = pieces[1].strip() if len(pieces) > 1 else ""
        version = pieces[2].strip() if len(pieces) > 2 else ""
        if name:
            distros.append({"name": name, "state": state, "version": version, "default": default})
    return distros


def build_wsl_probe_script(repo_name: str) -> str:
    safe_repo = re.sub(r"[^A-Za-z0-9_.-]", "", repo_name) or "main_computer_test"
    # The outer Python subprocess has its own timeout. These inner `timeout` wrappers
    # keep individual Linux commands from stalling the whole WSL probe when a mount,
    # Docker socket, or filesystem walk is unhealthy.
    #
    # Keep this script POSIX-sh friendly. WSL distros often map `sh` to dash, so avoid
    # Bash-only constructs such as arrays, brace expansion, and process substitution.
    return """set +e
if command -v timeout >/dev/null 2>&1; then
  MC_TIMEOUT="timeout"
else
  MC_TIMEOUT=""
fi
echo "__MC_SECTION:identity"
printf "pwd="; pwd
printf "hostname="; hostname 2>/dev/null
printf "uname="; uname -a 2>/dev/null
printf "whoami="; whoami 2>/dev/null
echo "__MC_SECTION:storage"
(df -h / /mnt/c 2>/dev/null || true) | head -20
echo "__MC_SECTION:project_paths"
for root in /mnt/c /mnt/d /home /opt /srv; do
  [ -d "$root" ] || continue
  if [ -n "$MC_TIMEOUT" ]; then
    timeout {find_timeout}s find "$root" -maxdepth 8 -type f \\( -name new_patch.py -o -name pyproject.toml -o -name docker-compose.yml \\) 2>/dev/null | grep -Ei '/({safe_repo}|main[_-]computer|main_computer_test|MainComputer)/' | head -120
  else
    find "$root" -maxdepth 6 -type f \\( -name new_patch.py -o -name pyproject.toml -o -name docker-compose.yml \\) 2>/dev/null | grep -Ei '/({safe_repo}|main[_-]computer|main_computer_test|MainComputer)/' | head -80
  fi
done
echo "__MC_SECTION:project_du"
for p in \\
  /mnt/c/Users/*/dsl/{safe_repo} \\
  /mnt/c/Users/*/{safe_repo} \\
  /mnt/c/*/{safe_repo} \\
  /mnt/c/*/*/{safe_repo} \\
  /home/*/{safe_repo} \\
  /opt/{safe_repo} \\
  /srv/{safe_repo} \\
  /mnt/c/Users/*/MainComputer \\
  /mnt/c/Users/*/dsl/MainComputer
do
  [ -d "$p" ] || continue
  if [ -n "$MC_TIMEOUT" ]; then timeout 8s du -sh "$p" 2>/dev/null; else du -sh "$p" 2>/dev/null; fi
done | head -40
echo "__MC_SECTION:processes"
(ps -eo pid,ppid,comm,args 2>/dev/null | grep -Ei 'main[_ -]?computer|coolify|docker compose|site-server|uvicorn|python.*viewport|new_patch' | grep -v grep | head -160) || true
echo "__MC_SECTION:listening_ports"
(ss -ltnp 2>/dev/null || netstat -ltnp 2>/dev/null || true) | head -260
echo "__MC_SECTION:docker_ps"
if command -v docker >/dev/null 2>&1; then
  if [ -n "$MC_TIMEOUT" ]; then timeout 8s docker ps -a --format '{{{{json .}}}}' 2>/dev/null | head -240; else docker ps -a --format '{{{{json .}}}}' 2>/dev/null | head -160; fi
else
  echo "docker unavailable"
fi
echo "__MC_SECTION:docker_df"
if command -v docker >/dev/null 2>&1; then
  if [ -n "$MC_TIMEOUT" ]; then timeout 10s docker system df -v 2>/dev/null | head -260; else docker system df -v 2>/dev/null | head -180; fi
else
  echo "docker unavailable"
fi
""".format(find_timeout=int(DEFAULT_WSL_FIND_TIMEOUT_S), safe_repo=safe_repo)


def parse_json_lines(lines: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in lines:
        raw = line.strip()
        if not raw or raw == "docker unavailable":
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            rows.append({"raw": raw, "parse_error": "invalid json"})
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def classify_wsl_docker_row(row: dict[str, Any], repo_root: Path, website_matrix: list[dict[str, Any]]) -> dict[str, Any]:
    text = json.dumps(row, sort_keys=True).lower()
    service_names = {str(site.get("service") or "").lower() for site in website_matrix if site.get("service")}
    site_ids = {str(site.get("site_id") or "").lower() for site in website_matrix if site.get("site_id")}
    reasons: list[str] = []
    relation = "not-project"
    confidence = "low"
    is_project = False
    for needle in ["main-computer", "main_computer", "maincomputer", repo_root.name.lower(), "main-computer-site"]:
        if needle and needle in text:
            is_project = True
            relation = "project"
            confidence = "high"
            reasons.append(f"matches project marker {needle}")
            break
    if not is_project:
        for service in sorted(service_names):
            if service and service in text:
                is_project = True
                relation = "project"
                confidence = "high"
                reasons.append(f"matches website service {service}")
                break
    if not is_project:
        for site_id in sorted(site_ids):
            if site_id and len(site_id) >= 3 and site_id in text:
                is_project = True
                relation = "project"
                confidence = "medium"
                reasons.append(f"mentions site id {site_id}")
                break
    if not is_project and ("coolify" in text or "traefik" in text):
        relation = "project-adjacent"
        confidence = "medium"
        reasons.append("Coolify/reverse-proxy container; may route project websites")
    if not reasons:
        reasons.append("no project markers detected in docker ps row")
    return {"is_project": is_project, "relation": relation, "confidence": confidence, "reasons": reasons[:6]}


def wsl_distro_uses_docker_desktop_shared_daemon(record: dict[str, Any]) -> bool:
    docker_rows = record.get("docker_containers") if isinstance(record.get("docker_containers"), list) else []
    if not docker_rows:
        return False
    sample = json.dumps(docker_rows[:40], sort_keys=True).lower()
    return (
        "desktop.docker.io/binds" in sample
        or "com.docker.compose.project.working_dir=c:" in sample
        or "com.docker.compose.project.config_files=c:" in sample
        or "\\users\\" in sample
        or "c:\\users\\" in sample
    )


def classify_wsl_distro(record: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    name = str(record.get("name") or "")
    reasons: list[str] = []
    is_project = False
    relation = "not-project"
    confidence = "low"
    lowered = name.lower()
    probe_incomplete = bool(record.get("probe_timed_out")) or record.get("probe_returncode") not in (0, None)

    if lowered in {"docker-desktop", "docker-desktop-data"}:
        reasons.append("Docker Desktop WSL distribution; project-looking processes/listeners here are Docker infrastructure")
        if probe_incomplete:
            reasons.append("probe did not complete cleanly, so live evidence may be incomplete")
        return {"is_project": False, "relation": "docker-system", "confidence": "high", "reasons": reasons[:8]}

    if probe_incomplete:
        reasons.append("probe did not complete cleanly, so live path/process/container evidence may be incomplete")

    docker_rows = record.get("docker_containers") if isinstance(record.get("docker_containers"), list) else []
    project_docker_count = sum(1 for row in docker_rows if isinstance(row, dict) and row.get("classification", {}).get("is_project"))
    adjacent_docker_count = sum(1 for row in docker_rows if isinstance(row, dict) and row.get("classification", {}).get("relation") == "project-adjacent")
    docker_desktop_shared = wsl_distro_uses_docker_desktop_shared_daemon(record)

    if record.get("project_paths"):
        is_project = True
        relation = "project"
        confidence = "high"
        reasons.append("contains project-looking paths")
    if record.get("processes"):
        is_project = True
        relation = "project"
        confidence = "high"
        reasons.append("has project-looking processes")

    if project_docker_count:
        if docker_desktop_shared and not record.get("project_paths") and not record.get("processes"):
            relation = "docker-client-project-access"
            confidence = "high"
            reasons.append(
                f"Docker CLI in this distro can see {project_docker_count} project-classified Docker Desktop container(s), "
                "but no distro-local project paths/processes were found"
            )
        else:
            is_project = True
            relation = "project"
            confidence = "high"
            reasons.append(f"has {project_docker_count} project-classified Docker container(s)")

    if not is_project and relation == "not-project" and adjacent_docker_count:
        relation = "project-adjacent"
        confidence = "medium"
        reasons.append(f"has {adjacent_docker_count} Coolify/reverse-proxy container(s)")
    if relation == "not-project" and any(needle in lowered for needle in ("maincomputer", "main-computer", "main_computer")):
        relation = "project-shell"
        confidence = "medium"
        reasons.append("distro name looks Main Computer-specific but no live project paths/processes/containers were found")
    if not reasons:
        reasons.append("no project paths, processes, or project-classified containers detected")
    return {"is_project": is_project, "relation": relation, "confidence": confidence, "reasons": reasons[:8]}

def collect_windows_wsl_registry_vhdx(timeout_s: float) -> tuple[list[dict[str, Any]], CommandResult | None]:
    if os.name != "nt":
        return [], None
    exe = "powershell.exe" if shutil.which("powershell.exe") else ("pwsh.exe" if shutil.which("pwsh.exe") else "powershell")
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$rows = @()
Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' | ForEach-Object {
  $p = Get-ItemProperty $_.PSPath
  $base = [string]$p.BasePath
  $vhd = if ($base) { Join-Path $base 'ext4.vhdx' } else { '' }
  $exists = if ($vhd) { Test-Path $vhd } else { $false }
  $size = if ($exists) { (Get-Item $vhd).Length } else { $null }
  $rows += [pscustomobject]@{
    distribution_name = [string]$p.DistributionName
    base_path = $base
    path = $vhd
    exists = [bool]$exists
    size_bytes = $size
  }
}
$rows | ConvertTo-Json -Compress
"""
    result = run_command(
        [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        timeout_s=max(4.0, timeout_s),
        purpose="PowerShell WSL registry VHDX inventory",
    )
    if result.unavailable or result.returncode != 0 or not result.stdout.strip():
        return [], result
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return [{"raw": result.stdout[:1000], "parse_error": "invalid json"}], result
    if isinstance(payload, dict):
        payload = [payload]
    rows: list[dict[str, Any]] = []
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            size = item.get("size_bytes")
            try:
                size_int = int(size) if size is not None else None
            except (TypeError, ValueError):
                size_int = None
            rows.append(
                {
                    "distribution_name": str(item.get("distribution_name") or ""),
                    "base_path": str(item.get("base_path") or ""),
                    "path": str(item.get("path") or ""),
                    "exists": bool(item.get("exists")),
                    "size_bytes": size_int,
                    "size_human": bytes_human(size_int),
                    "source": "wsl-registry",
                }
            )
    return rows, result


def collect_windows_wsl_vhdx() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    roots: list[Path] = []
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        roots.extend([
            Path(local_app) / "Packages",
            Path(local_app) / "Docker" / "wsl",
        ])
    program_data = os.environ.get("ProgramData")
    if program_data:
        roots.append(Path(program_data) / "DockerDesktop")
    seen: set[Path] = set()
    for base in roots:
        if not base.exists():
            continue
        patterns = ["*/LocalState/*.vhdx", "*.vhdx", "**/*.vhdx"]
        for pattern in patterns:
            try:
                candidates = list(base.glob(pattern))
            except OSError:
                continue
            for path in candidates[:200]:
                try:
                    resolved = path.resolve()
                    if resolved in seen or not path.is_file():
                        continue
                    seen.add(resolved)
                    size = path.stat().st_size
                except OSError:
                    continue
                rows.append(
                    {
                        "path": str(path),
                        "size_bytes": size,
                        "size_human": bytes_human(size),
                        "looks_docker": "docker" in str(path).lower(),
                        "looks_wsl": "localstate" in str(path).lower() or "wsl" in str(path).lower(),
                    }
                )
    rows.sort(key=lambda row: int(row.get("size_bytes") or 0), reverse=True)
    return rows


def split_wsl_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = "raw"
    for raw in text.splitlines():
        if raw.startswith("__MC_SECTION:"):
            current = raw.split(":", 1)[1].strip()
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(raw)
    return sections


def collect_wsl_inventory(
    repo_root: Path,
    findings: list[Finding],
    *,
    timeout_s: float,
    website_matrix: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    wsl_exe = "wsl.exe" if shutil.which("wsl.exe") else "wsl"
    list_result = run_command([wsl_exe, "--list", "--verbose"], timeout_s=timeout_s, purpose="wsl distro list")
    host_vhdx = collect_windows_wsl_vhdx() if os.name == "nt" else []
    registry_vhdx: list[dict[str, Any]] = []
    registry_result: CommandResult | None = None
    if os.name == "nt":
        registry_vhdx, registry_result = collect_windows_wsl_registry_vhdx(timeout_s)
        by_path = {str(row.get("path") or "").lower(): row for row in host_vhdx if row.get("path")}
        for row in registry_vhdx:
            key = str(row.get("path") or "").lower()
            if key and key not in by_path:
                host_vhdx.append(row)
                by_path[key] = row
            elif key and key in by_path:
                by_path[key].update({k: v for k, v in row.items() if v not in ("", None)})
    host_vhdx.sort(key=lambda row: int(row.get("size_bytes") or 0), reverse=True)
    if host_vhdx:
        findings.append(
            Finding(
                "INFO",
                "storage",
                f"Found {len(host_vhdx)} WSL/Docker VHDX file(s) in known Windows storage locations/registry.",
                {"largest": host_vhdx[:5]},
            )
        )
    elif registry_result and registry_result.timed_out:
        findings.append(Finding("WARN", "storage", "WSL registry VHDX inventory timed out.", {"timeout_s": timeout_s}))
    if list_result.unavailable:
        findings.append(Finding("INFO", "wsl", "WSL command is not available on this host.", {"command": wsl_exe}))
        return {"available": False, "distros": [], "command": wsl_exe, "host_vhdx": host_vhdx}
    if list_result.returncode != 0:
        findings.append(Finding("WARN", "wsl", "Could not list WSL distributions.", {"stderr": list_result.stderr.strip(), "command": wsl_exe}))
        return {"available": True, "distros": [], "list_error": list_result.stderr.strip(), "command": wsl_exe, "host_vhdx": host_vhdx}

    distros = parse_wsl_list(list_result.stdout)
    probe_script = build_wsl_probe_script(repo_root.name)
    detailed: list[dict[str, Any]] = []
    progress("wsl distros discovered", {"count": len(distros), "distros": [d.get("name") for d in distros]})
    for distro in distros:
        name = distro["name"]
        progress("wsl distro probe start", {"distro": name, "state": distro.get("state"), "timeout_s": max(timeout_s, DEFAULT_WSL_FIND_TIMEOUT_S + 12)})
        probe = run_command(
            [wsl_exe, "-d", name, "--", "sh", "-lc", probe_script],
            timeout_s=max(timeout_s, DEFAULT_WSL_FIND_TIMEOUT_S + 12),
            purpose=f"wsl probe {name}",
        )
        sections = split_wsl_sections(probe.stdout)
        project_paths = [line.strip() for line in sections.get("project_paths", []) if line.strip()]
        project_du = [line.strip() for line in sections.get("project_du", []) if line.strip()]
        processes = [line.strip() for line in sections.get("processes", []) if line.strip()]
        ports = parse_ss_ports("\n".join(sections.get("listening_ports", [])))
        docker_lines = [line.strip() for line in sections.get("docker_ps", []) if line.strip()]
        docker_rows = parse_json_lines(docker_lines)
        for row in docker_rows:
            row["classification"] = classify_wsl_docker_row(row, repo_root, website_matrix or [])
        docker_df = "\n".join(line.rstrip() for line in sections.get("docker_df", []) if line.rstrip())
        storage_lines = [line.strip() for line in sections.get("storage", []) if line.strip()]
        record = {
            **distro,
            "probe_returncode": probe.returncode,
            "probe_timed_out": probe.timed_out,
            "identity": [line.strip() for line in sections.get("identity", []) if line.strip()],
            "storage": storage_lines,
            "project_paths": project_paths,
            "project_du": project_du,
            "processes": processes,
            "listening_ports": ports,
            "docker_ps": docker_lines,
            "docker_containers": docker_rows,
            "docker_system_df": docker_df,
            "probe_stderr": probe.stderr.strip()[:2000],
        }
        classification = classify_wsl_distro(record, repo_root)
        record["project_classification"] = classification
        record["looks_project_related"] = bool(classification.get("is_project"))
        detailed.append(record)
        progress(
            "wsl distro classified",
            {
                "distro": name,
                "relation": classification.get("relation"),
                "confidence": classification.get("confidence"),
                "project_paths": len(project_paths),
                "processes": len(processes),
                "ports": len(ports),
                "docker_containers": len(docker_rows),
                "returncode": probe.returncode,
                "timed_out": probe.timed_out,
            },
        )
        if probe.timed_out:
            findings.append(
                Finding(
                    "WARN",
                    "wsl",
                    "WSL distro probe timed out; project relevance classification may be incomplete.",
                    {"distro": name, "timeout_s": max(timeout_s, DEFAULT_WSL_FIND_TIMEOUT_S + 12)},
                )
            )
        elif probe.returncode not in (0, None):
            findings.append(
                Finding(
                    "WARN",
                    "wsl",
                    "WSL distro probe exited non-zero; project relevance classification may be incomplete.",
                    {"distro": name, "returncode": probe.returncode, "stderr": probe.stderr.strip()[:500]},
                )
            )
        status = "INFO"
        if classification.get("is_project"):
            status = "WARN" if str(distro.get("state", "")).lower() != "running" else "INFO"
        findings.append(
            Finding(
                status,
                "wsl",
                "WSL distribution classified for project relevance.",
                {
                    "distro": name,
                    "state": distro.get("state"),
                    "relation": classification.get("relation"),
                    "confidence": classification.get("confidence"),
                    "reasons": classification.get("reasons"),
                    "project_path_count": len(project_paths),
                    "process_count": len(processes),
                    "listening_port_count": len(ports),
                    "docker_container_count": len(docker_rows),
                    "project_docker_container_count": sum(
                        1 for row in docker_rows if isinstance(row, dict) and row.get("classification", {}).get("is_project")
                    ),
                    "project_du": project_du[:10],
                    "probe_returncode": probe.returncode,
                    "probe_timed_out": probe.timed_out,
                },
            )
        )
    project_count = sum(1 for distro in detailed if distro.get("project_classification", {}).get("is_project"))
    adjacent_count = sum(1 for distro in detailed if distro.get("project_classification", {}).get("relation") == "project-adjacent")
    findings.append(
        Finding(
            "PASS" if distros else "INFO",
            "wsl",
            f"Enumerated {len(distros)} WSL distribution(s); {project_count} project, {adjacent_count} project-adjacent.",
        )
    )
    return {"available": True, "command": wsl_exe, "distros": detailed, "host_vhdx": host_vhdx}



MACHINE_PATH_RE = re.compile(
    # Negative lookbehind and the `(?!/)` guard avoid matching URL tails such as
    # `https://github.com/...` as fake Windows roots like `s:/github.com/...`.
    r"(?<![A-Za-z])(?:[A-Za-z]:[\\/](?!/)[^;\s,\n\"\'<>|]+|/mnt/[A-Za-z]/[^;\s,\n\"\'<>|]+|/(?:home|opt|srv|data|tmp)/[^;\s,\n\"\'<>|]+)"
)


def canonical_path_text(path_text: object) -> str:
    raw = str(path_text or "").strip().strip("\"'")
    if not raw:
        return ""
    raw = raw.replace("\\", "/")
    raw = re.sub(r"/+", "/", raw)
    raw = raw.rstrip("/")
    mnt = re.match(r"^/mnt/([A-Za-z])/(.*)$", raw)
    if mnt:
        raw = f"{mnt.group(1).upper()}:/{mnt.group(2)}"
    return raw


def path_key(path_text: object) -> str:
    return canonical_path_text(path_text).lower()


def root_looks_like_url_fragment(root: str) -> bool:
    lowered = path_key(root)
    # These roots are almost always artifacts of matching inside metadata URLs,
    # not local filesystem environments.
    if re.match(r"^[a-z]:/(?:github\.com|docs\.docker\.com|serversideup\.net|127\.0\.0\.1|localhost|host\.docker\.internal)(?:/|:|$)", lowered):
        return True
    if lowered.startswith(("http:/", "https:/")):
        return True
    return False


def root_is_docker_internal_linux_path(root: str) -> bool:
    lowered = path_key(root)
    return lowered.startswith("/data/coolify/services/")


def root_looks_like_machine_environment(root: str, repo_root: Path | None = None) -> bool:
    if not root or root_looks_like_url_fragment(root):
        return False
    lowered = path_key(root)
    if root.startswith("<"):
        return True
    if root_is_docker_internal_linux_path(root):
        return True
    if repo_root is not None and root_relation(root, repo_root) in {"current-repo", "other-checkout", "debug-checkout", "tool-instance", "runtime-state"}:
        return True
    if re.search(r"main[_-]?computer|maincomputer|coolify|onlyoffice|directus|gitea|foundry|report", lowered):
        return True
    return False


def env_root_from_path(path_text: object) -> str:
    path = canonical_path_text(path_text)
    if not path:
        return ""
    lowered = path.lower()
    parts = path.split("/")
    lowered_parts = [part.lower() for part in parts]

    # Collapse known Main Computer checkout descendants to the checkout root before
    # generic markers such as /runtime/ or /Scripts/. Otherwise virtualenv/script
    # paths become bogus machine "roots" like .proto-dev/venv or debug/runtime.
    if "dsl" in lowered_parts:
        try:
            dsl_idx = lowered_parts.index("dsl")
            if len(parts) > dsl_idx + 1 and re.search(r"main[_-]?computer|maincomputer", parts[dsl_idx + 1], re.I):
                return "/".join(parts[: dsl_idx + 2])
        except ValueError:
            pass

    if ".main-computer-tools" in lowered_parts and "instances" in lowered_parts:
        try:
            inst_idx = lowered_parts.index("instances")
            if len(parts) > inst_idx + 1:
                return "/".join(parts[: inst_idx + 2])
        except ValueError:
            pass

    for marker in (
        "/deploy/",
        "/runtime/",
        "/main_computer/",
        "/tools/",
        "/tests/",
        "/scripts/",
        "/game_projects/",
        "/contracts/",
    ):
        idx = lowered.find(marker)
        if idx > 2:
            return path[:idx]
    if parts:
        leaf = parts[-1].lower()
        if leaf in {"new_patch.py", "pyproject.toml", "docker-compose.yml", "docker-compose.onlyoffice.yml"} or leaf.endswith((".yml", ".yaml", ".json", ".env", ".py")):
            return "/".join(parts[:-1])
        # Standalone executable paths, such as C:/WINDOWS/system32/wsl.exe, are
        # commands rather than deploy/environment roots. Repo-owned executables
        # have already been collapsed by the dsl/.main-computer-tools rules above.
        if leaf.endswith((".exe", ".bat", ".cmd", ".ps1")):
            return ""
    return path



def extract_path_candidates(value: object) -> list[str]:
    text = str(value or "")
    candidates: list[str] = []
    for match in MACHINE_PATH_RE.finditer(text):
        raw = match.group(0).strip().strip("\"'")
        # Trim common PowerShell/JSON/YAML punctuation that path regex intentionally allows.
        raw = raw.rstrip(").],}")
        root = env_root_from_path(raw)
        if root and not root_looks_like_url_fragment(root) and root not in candidates:
            candidates.append(root)
    return candidates


def root_relation(root: str, repo_root: Path) -> str:
    key = path_key(root)
    current = path_key(str(repo_root))
    lower = key.lower()
    if lower == path_key(UNKNOWN_PROJECT_ROOT):
        return UNATTRIBUTED_PROJECT_RELATION
    if current and (key == current or key.startswith(current + "/")):
        return "current-repo"
    if ".main-computer-tools/instances/" in lower:
        return "tool-instance"
    if "docker" in lower and ("dockerdesktop" in lower or "/docker/" in lower):
        return "docker-storage"
    if "coolify" in lower and "runtime" in lower:
        return "runtime-state"
    if root_is_docker_internal_linux_path(root):
        return "docker-internal-root"
    if "debug" in lower and re.search(r"main[_-]?computer|maincomputer", lower):
        return "debug-checkout"
    if re.search(r"main[_-]?computer|maincomputer", lower):
        return "other-checkout"
    return "unknown-root"



def try_path_exists(path_text: str) -> bool | None:
    if not path_text:
        return None
    try:
        return Path(path_text).exists()
    except (OSError, RuntimeError, ValueError):
        return None


def safe_marker_exists(root: str, relative: str) -> bool:
    try:
        return (Path(root) / relative).exists()
    except (OSError, RuntimeError, ValueError):
        return False


def deploy_kind_from_container(row: dict[str, Any]) -> str:
    text = json.dumps(row, sort_keys=True).lower()
    name = str(row.get("name") or row.get("Names") or "")
    image = str(row.get("image") or row.get("Image") or "")
    if "onlyoffice" in text or "documentserver" in text:
        return "onlyoffice"
    if "coolify" in text:
        return "coolify"
    if "directus" in text:
        return "directus"
    if "main-computer-site" in text or "local-platform" in text:
        return "website"
    if "sqlite" in text:
        return "sqlite-smoke"
    if "nginx" in image.lower() or "nginx" in name.lower():
        return "nginx-smoke"
    if "redis" in image.lower():
        return "redis"
    if "postgres" in image.lower():
        return "postgres"
    if "soketi" in image.lower():
        return "realtime"
    return "container"


def summarize_container_for_environment(row: dict[str, Any]) -> dict[str, Any]:
    labels = row.get("labels") if isinstance(row.get("labels"), dict) else {}
    ports = row.get("host_ports") if isinstance(row.get("host_ports"), list) else []
    return {
        "name": row.get("name") or row.get("Names") or row.get("ID") or "",
        "image": row.get("image") or row.get("Image") or "",
        "running": bool(row.get("running") if "running" in row else str(row.get("State") or "").lower() == "running"),
        "status": row.get("status") or row.get("Status") or row.get("State") or "",
        "kind": deploy_kind_from_container(row),
        "ports": ports if ports else row.get("Ports", ""),
        "size": row.get("size") or row.get("Size") or "",
        "compose_project": labels.get("com.docker.compose.project") or row.get("compose_project") or "",
        "compose_service": labels.get("com.docker.compose.service") or "",
        "working_dir": labels.get("com.docker.compose.project.working_dir") or "",
        "config_files": labels.get("com.docker.compose.project.config_files") or "",
        "environment_file": labels.get("com.docker.compose.project.environment_file") or "",
    }


def add_machine_env(
    envs: dict[str, dict[str, Any]],
    root: str,
    *,
    source: str,
    detail: dict[str, Any] | None = None,
    repo_root: Path,
) -> dict[str, Any]:
    canonical = canonical_path_text(root)
    if root_relation(canonical, repo_root) == "current-repo":
        canonical = canonical_path_text(str(repo_root))
    key = path_key(canonical)
    if not key:
        key = f"unknown:{source}:{len(envs)}"
        canonical = ""
    env = envs.setdefault(
        key,
        {
            "root": canonical,
            "relation": root_relation(canonical, repo_root),
            "sources": [],
            "env_vars": [],
            "containers": [],
            "processes": [],
            "wsl_evidence": [],
            "static_state": {},
            "sanity": {"status": "INFO", "issues": []},
        },
    )
    if source not in env["sources"]:
        env["sources"].append(source)
    if detail:
        bucket = "env_vars" if source == "env" else "wsl_evidence" if source.startswith("wsl") else None
        if bucket:
            env[bucket].append(detail)
    return env



def collect_host_project_processes(timeout_s: float) -> tuple[list[dict[str, Any]], CommandResult | None]:
    pattern = r"main[_ -]?computer|maincomputer|coolify|onlyoffice|docker compose|new_patch|site-server|uvicorn"
    if os.name == "nt":
        exe = "powershell.exe" if shutil.which("powershell.exe") else ("pwsh.exe" if shutil.which("pwsh.exe") else "powershell")
        script = rf"""
$ErrorActionPreference = 'SilentlyContinue'
$rows = Get-CimInstance Win32_Process |
  Where-Object {{ ($_.CommandLine -match '{pattern}') -or ($_.Name -match '{pattern}') }} |
  Select-Object -First 120 ProcessId,Name,ExecutablePath,CommandLine
$rows | ConvertTo-Json -Compress
"""
        result = run_command([exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], timeout_s=max(4.0, timeout_s), purpose="host Main Computer process inventory")
        if result.unavailable or result.returncode != 0 or not result.stdout.strip():
            return [], result
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return [{"raw": result.stdout[:1000], "parse_error": "invalid json"}], result
        if isinstance(payload, dict):
            payload = [payload]
        rows = []
        if isinstance(payload, list):
            for item in payload[:120]:
                if isinstance(item, dict):
                    rows.append(redact_mapping({
                        "pid": item.get("ProcessId"),
                        "name": item.get("Name") or "",
                        "exe": item.get("ExecutablePath") or "",
                        "command": item.get("CommandLine") or "",
                    }))
        return rows, result
    result = run_command(["ps", "-eo", "pid,ppid,comm,args"], timeout_s=max(4.0, timeout_s), purpose="host Main Computer process inventory")
    if result.returncode != 0 or not result.stdout:
        return [], result
    rows: list[dict[str, Any]] = []
    rx = re.compile(pattern, re.I)
    for line in result.stdout.splitlines()[1:]:
        if rx.search(line) and "deep_sanity_check.py" not in line:
            rows.append({"raw": redact_value("raw", line.strip())})
        if len(rows) >= 120:
            break
    return rows, result


def collect_static_state_for_root(root: str, current_repo_root: Path) -> dict[str, Any]:
    docker_internal = root_is_docker_internal_linux_path(root)
    state: dict[str, Any] = {
        "exists": try_path_exists(root),
        "new_patch": False if docker_internal else safe_marker_exists(root, "new_patch.py"),
        "coolify_compose": False if docker_internal else safe_marker_exists(root, "deploy/coolify/local-docker/docker-compose.yml"),
        "onlyoffice_compose": False if docker_internal else safe_marker_exists(root, "docker-compose.onlyoffice.yml"),
    }
    if docker_internal:
        state["host_visibility"] = "docker-internal-linux-path"
        state["inspection_note"] = "Path is reported from Docker/Coolify metadata and may not be directly inspectable from the host filesystem."
    if not state["exists"] or not state["new_patch"]:
        return state
    path = Path(root)
    try:
        dummy: list[Finding] = []
        records = [
            *collect_registry_sites(path, dummy),
            *collect_manifest_sites(path, dummy),
            *collect_compose_sites(path, dummy),
        ]
        matrix = analyze_website_port_space(records, dummy)
        state["website_claim_count"] = len(matrix)
        state["website_ports"] = sorted({row.get("port") for row in matrix if isinstance(row.get("port"), int)})
        state["website_sites"] = sorted({str(row.get("site_id") or "") for row in matrix if row.get("site_id")})
    except Exception as exc:
        state["static_scan_error"] = str(exc)[:300]
    return state


def collect_machine_environments(
    repo_root: Path,
    environment: dict[str, Any],
    docker_summaries: list[dict[str, Any]],
    coolify_state: dict[str, Any],
    onlyoffice_state: dict[str, Any],
    wsl_inventory: dict[str, Any],
    findings: list[Finding],
    *,
    timeout_s: float,
) -> dict[str, Any]:
    envs: dict[str, dict[str, Any]] = {}
    add_machine_env(envs, str(repo_root), source="current-repo", repo_root=repo_root)

    # Environment variables are a common source of "current shell points at another checkout" drift.
    env_var_hits: list[dict[str, Any]] = []
    for key, value in os.environ.items():
        if key == "PATH":
            continue
        if not (key.startswith(("MAIN_COMPUTER", "COOLIFY", "COMPOSE", "DOCKER", "WSL", "VIRTUAL_ENV")) or key in {"PYTHONPATH"}):
            continue
        for candidate in extract_path_candidates(value):
            detail = {"key": key, "value": redact_value(key, value), "root": candidate}
            env_var_hits.append(detail)
            add_machine_env(envs, candidate, source="env", detail=detail, repo_root=repo_root)

    host_processes, host_process_result = collect_host_project_processes(timeout_s)
    if host_process_result and host_process_result.timed_out:
        findings.append(Finding("WARN", "machine-env", "Host process inventory timed out.", {"timeout_s": max(4.0, timeout_s)}))
    elif host_process_result and not host_process_result.unavailable and host_process_result.returncode == 0:
        findings.append(Finding("INFO", "machine-env", f"Collected {len(host_processes)} host process row(s) that look Main Computer/Coolify/ONLYOFFICE-related."))
    elif host_process_result and host_process_result.unavailable:
        findings.append(Finding("INFO", "machine-env", "Host process inventory command was unavailable.", {"error": host_process_result.stderr.strip()}))

    for proc in host_processes:
        process_roots = []
        for field in ("command", "exe", "raw"):
            process_roots.extend(extract_path_candidates(proc.get(field)))
        for root in process_roots:
            if root_relation(root, repo_root) == "unknown-root":
                continue
            env = add_machine_env(envs, root, source="host-process", repo_root=repo_root)
            env["processes"].append(proc)

    for row in docker_summaries:
        labels = row.get("labels") if isinstance(row.get("labels"), dict) else {}
        candidate_values = [
            labels.get("com.docker.compose.project.working_dir"),
            labels.get("com.docker.compose.project.config_files"),
            labels.get("com.docker.compose.project.environment_file"),
            row.get("name"),
            row.get("image"),
        ]
        roots: list[str] = []
        for value in candidate_values:
            for root in extract_path_candidates(value):
                if root not in roots:
                    roots.append(root)
        # If a project-looking container has no explicit path, keep it visible under an unknown bucket.
        if not roots and row.get("project_classification", {}).get("is_project"):
            roots = [UNKNOWN_PROJECT_ROOT]
        for root in roots:
            if not root_looks_like_machine_environment(root, repo_root):
                continue
            env = add_machine_env(envs, root, source="docker", repo_root=repo_root)
            env["containers"].append(summarize_container_for_environment(row))

    # WSL evidence can reveal checkouts that are not currently mounted or running on the Windows host.
    for distro in (wsl_inventory.get("distros") if isinstance(wsl_inventory.get("distros"), list) else []) or []:
        distro_name = str(distro.get("name") or "")
        for line in distro.get("project_paths") or []:
            for root in extract_path_candidates(line):
                add_machine_env(
                    envs,
                    root,
                    source="wsl-project-path",
                    detail={"distro": distro_name, "path": line},
                    repo_root=repo_root,
                )
        for line in distro.get("project_du") or []:
            pieces = str(line).split(maxsplit=1)
            if len(pieces) == 2:
                for root in extract_path_candidates(pieces[1]):
                    add_machine_env(
                        envs,
                        root,
                        source="wsl-project-du",
                        detail={"distro": distro_name, "du": line},
                        repo_root=repo_root,
                    )
        for line in distro.get("processes") or []:
            for root in extract_path_candidates(line):
                if root_relation(root, repo_root) == "unknown-root":
                    continue
                env = add_machine_env(envs, root, source="wsl-process", repo_root=repo_root)
                env["processes"].append({"distro": distro_name, "raw": redact_value("raw", line)})
        distro_relation = str((distro.get("project_classification") or {}).get("relation") or "")
        for crow in distro.get("docker_containers") or []:
            if not isinstance(crow, dict):
                continue
            # When WSL is only a Docker Desktop client, Docker rows duplicate the
            # Windows Docker inventory. Keep them under the WSL distro evidence,
            # but do not double-count them as separate machine environment deploys.
            if distro_relation in {"docker-client-project-access", "docker-system"}:
                continue
            roots: list[str] = []
            for value in (crow.get("Names"), crow.get("Image"), crow.get("Command"), crow.get("Mounts")):
                roots.extend(extract_path_candidates(value))
            if roots:
                for root in sorted(set(roots)):
                    if not root_looks_like_machine_environment(root, repo_root):
                        continue
                    env = add_machine_env(envs, root, source="wsl-docker", repo_root=repo_root)
                    env["containers"].append(summarize_container_for_environment(crow))

    # Add static state and evaluate each root.
    rows: list[dict[str, Any]] = []
    current_key = path_key(str(repo_root))
    active_non_current = 0
    active_coolify_roots: set[str] = set()
    active_onlyoffice_roots: set[str] = set()
    unattributed_project_containers: list[dict[str, Any]] = []
    env_var_drift: list[dict[str, Any]] = []

    for env in envs.values():
        root = str(env.get("root") or "")
        relation = root_relation(root, repo_root)
        env["relation"] = relation
        env["static_state"] = collect_static_state_for_root(root, repo_root) if root and not root.startswith("<") else {"exists": None, "new_patch": False}
        containers = env.get("containers") if isinstance(env.get("containers"), list) else []
        deduped_containers: list[dict[str, Any]] = []
        seen_container_keys: set[tuple[str, str, str]] = set()
        for row in containers:
            key = (
                str(row.get("name") or ""),
                str(row.get("kind") or ""),
                json.dumps(row.get("ports") or "", sort_keys=True),
            )
            if key in seen_container_keys:
                continue
            seen_container_keys.add(key)
            deduped_containers.append(row)
        env["containers"] = deduped_containers
        containers = deduped_containers
        running = [row for row in containers if row.get("running")]
        kinds = sorted({str(row.get("kind") or "container") for row in containers})
        env["deploy_kinds"] = kinds
        env["running_deploy_count"] = len(running)
        env["container_count"] = len(containers)
        env["process_count"] = len(env.get("processes") or [])
        issues: list[str] = []
        status = "PASS" if relation == "current-repo" else "INFO"
        if running and relation != "current-repo":
            active_non_current += 1
            status = "WARN"
            if relation == UNATTRIBUTED_PROJECT_RELATION:
                issues.append("project-looking Docker container(s) are missing path/root attribution labels")
                unattributed_project_containers.extend(running)
            else:
                issues.append("non-current environment has running deploy/container(s)")
        if env["process_count"] and relation != "current-repo":
            status = "WARN"
            issues.append("non-current environment has matching host/WSL process(es)")
        if env["static_state"].get("exists") is False and (running or env["process_count"]):
            status = "WARN"
            if root_is_docker_internal_linux_path(root):
                issues.append("active deploy/process uses a Docker-internal Linux path that the host cannot inspect directly")
            else:
                issues.append("active deploy/process points at a root path that does not exist on this host")
        if relation in {"other-checkout", "debug-checkout"} and env["static_state"].get("exists") and not env["static_state"].get("new_patch"):
            status = "WARN"
            issues.append("Main Computer-looking root exists but no new_patch.py marker was found")
        if any(kind == "coolify" for kind in kinds) and running:
            active_coolify_roots.add(path_key(root) or root)
        if any(kind == "onlyoffice" for kind in kinds) and running:
            active_onlyoffice_roots.add(path_key(root) or root)
        for detail in env.get("env_vars") or []:
            if relation != "current-repo" and re.search(r"main[_-]?computer|maincomputer", json.dumps(detail), re.I):
                env_var_drift.append(detail)
                if "environment variable points at non-current Main Computer root" not in issues:
                    issues.append("environment variable points at non-current Main Computer root")
                    status = "WARN"
        env["sanity"] = {"status": status, "issues": issues}
        rows.append(env)

    rows.sort(
        key=lambda row: (
            0 if row.get("relation") == "current-repo" else 1,
            -int(row.get("running_deploy_count") or 0),
            str(row.get("root") or ""),
        )
    )

    if active_non_current:
        findings.append(
            Finding(
                "WARN",
                "machine-env",
                "Running Main Computer-related deploys exist outside the current checkout.",
                {"environment_count": active_non_current},
            )
        )
    if unattributed_project_containers:
        examples: list[dict[str, Any]] = []
        for row in unattributed_project_containers[:12]:
            ports = row.get("ports")
            host_ports = [
                port.get("host_port")
                for port in ports
                if isinstance(port, dict) and port.get("host_port")
            ] if isinstance(ports, list) else []
            examples.append(
                {
                    "name": row.get("name") or "",
                    "kind": row.get("kind") or "",
                    "ports": host_ports,
                    "compose_project": row.get("compose_project") or "",
                    "compose_service": row.get("compose_service") or "",
                }
            )
        findings.append(
            Finding(
                "WARN",
                "machine-env",
                "Project-looking Docker container(s) could not be attributed to a checkout/root.",
                {"container_count": len(unattributed_project_containers), "examples": examples},
            )
        )
    if len(active_coolify_roots) > 1:
        findings.append(
            Finding(
                "WARN",
                "machine-env",
                "Multiple active Coolify roots/stacks were discovered on the machine.",
                {"root_count": len(active_coolify_roots), "roots": sorted(active_coolify_roots)[:12]},
            )
        )
    if len(active_onlyoffice_roots) > 1:
        findings.append(
            Finding(
                "WARN",
                "machine-env",
                "Multiple active ONLYOFFICE roots/stacks were discovered on the machine.",
                {"root_count": len(active_onlyoffice_roots), "roots": sorted(active_onlyoffice_roots)[:12]},
            )
        )
    if env_var_drift:
        findings.append(
            Finding(
                "WARN",
                "machine-env",
                "Environment variables reference Main Computer paths outside the current checkout.",
                {"count": len(env_var_drift), "examples": env_var_drift[:10]},
            )
        )

    status_counts: dict[str, int] = {"PASS": 0, "INFO": 0, "WARN": 0, "FAIL": 0}
    for row in rows:
        status = str(row.get("sanity", {}).get("status") or "INFO")
        status_counts[status] = status_counts.get(status, 0) + 1
    findings.append(
        Finding(
            "INFO" if not status_counts.get("WARN") and not status_counts.get("FAIL") else "WARN",
            "machine-env",
            f"Mapped {len(rows)} machine environment root(s) and their discovered deploy evidence.",
            {"status_counts": status_counts},
        )
    )

    return {
        "roots": rows,
        "host_processes": host_processes,
        "env_var_path_hits": env_var_hits,
        "active_non_current_environment_count": active_non_current,
        "active_coolify_root_count": len(active_coolify_roots),
        "active_onlyoffice_root_count": len(active_onlyoffice_roots),
        "unattributed_project_container_count": len(unattributed_project_containers),
    }


def collect_environment(repo_root: Path) -> dict[str, Any]:
    interesting_prefixes = ("MAIN_COMPUTER", "COOLIFY", "DOCKER", "WSL", "VIRTUAL_ENV", "PYTHON", "PATH")
    env = {
        key: value
        for key, value in os.environ.items()
        if key.startswith(interesting_prefixes) or key in {"COMPOSE_PROJECT_NAME", "COMPOSE_FILE", "COMPOSE_PROFILES"}
    }
    return {
        "tool_version": TOOL_VERSION,
        "time_utc": utc_now(),
        "repo_root": str(repo_root),
        "cwd": str(Path.cwd()),
        "platform": platform.platform(),
        "python": sys.version,
        "python_executable": sys.executable,
        "env": redact_mapping(env),
    }


def summarize_findings(findings: list[Finding]) -> dict[str, int]:
    summary = {"PASS": 0, "INFO": 0, "WARN": 0, "FAIL": 0}
    for finding in findings:
        summary[finding.status] = summary.get(finding.status, 0) + 1
    return summary


def highest_status(findings: list[Finding]) -> str:
    status = "PASS"
    for finding in findings:
        if STATUS_ORDER.get(finding.status, 0) > STATUS_ORDER.get(status, 0):
            status = finding.status
    return status


def render_text_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    summary = report.get("summary", {})
    lines.append("Main Computer deep sanity check")
    lines.append(f"repo_root: {report.get('environment', {}).get('repo_root', '')}")
    lines.append(f"overall: {report.get('overall_status')}  pass={summary.get('PASS', 0)} info={summary.get('INFO', 0)} warn={summary.get('WARN', 0)} fail={summary.get('FAIL', 0)}")
    lines.append("")
    lines.append("Machine environments and deploys:")
    machine = report.get("machine_environments") or {}
    roots = machine.get("roots") if isinstance(machine.get("roots"), list) else []
    if not roots:
        lines.append("  (no machine environment roots mapped)")
    else:
        lines.append("  root | relation | sanity | running/containers/processes | kinds | issues")
        for env in roots:
            sanity = env.get("sanity") if isinstance(env.get("sanity"), dict) else {}
            issues = "; ".join(str(issue) for issue in (sanity.get("issues") or [])[:4]) or "-"
            kinds = ",".join(str(kind) for kind in (env.get("deploy_kinds") or [])) or "-"
            lines.append(
                "  {root} | {relation} | {status} | {running}/{containers}/{processes} | {kinds} | {issues}".format(
                    root=env.get("root") or "<unknown>",
                    relation=env.get("relation") or "",
                    status=sanity.get("status") or "",
                    running=env.get("running_deploy_count") or 0,
                    containers=env.get("container_count") or 0,
                    processes=env.get("process_count") or 0,
                    kinds=kinds,
                    issues=issues,
                )
            )
            static = env.get("static_state") if isinstance(env.get("static_state"), dict) else {}
            if static:
                host_visibility = static.get("host_visibility")
                visibility_suffix = f" visibility={host_visibility}" if host_visibility else ""
                lines.append(
                    "    exists={exists} new_patch={new_patch} websites={websites} ports={ports}{visibility}".format(
                        exists=static.get("exists"),
                        new_patch=static.get("new_patch"),
                        websites=static.get("website_claim_count", ""),
                        ports=",".join(str(p) for p in (static.get("website_ports") or [])) or "-",
                        visibility=visibility_suffix,
                    )
                )
            if env.get("relation") == UNATTRIBUTED_PROJECT_RELATION:
                lines.append("    attribution=missing compose path/root labels; inspect container names, ports, and compose labels below")
            containers = env.get("containers") if isinstance(env.get("containers"), list) else []
            for container in containers[:8]:
                ports = container.get("ports")
                if isinstance(ports, list):
                    port_text = ",".join(str(p.get("host_port")) for p in ports if isinstance(p, dict) and p.get("host_port")) or "-"
                else:
                    port_text = str(ports or "-")
                lines.append(
                    "    deploy: {kind} | {name} | running={running} | ports={ports} | project={project}".format(
                        kind=container.get("kind") or "container",
                        name=container.get("name") or "",
                        running="yes" if container.get("running") else "no",
                        ports=port_text,
                        project=container.get("compose_project") or "",
                    )
                )
            if len(containers) > 8:
                lines.append(f"    ... truncated {len(containers) - 8} deploy/container row(s)")
    lines.append("")
    lines.append("Website port space:")
    matrix = report.get("website_port_matrix") or []
    if not matrix:
        lines.append("  (no website port claims found)")
    else:
        lines.append("  site_id | lane | source | path | service | port | url")
        for row in matrix:
            lines.append(
                "  {site_id} | {lane} | {source} | {path} | {service} | {port} | {url}".format(
                    site_id=row.get("site_id") or "",
                    lane=row.get("lane") or "",
                    source=row.get("source") or "",
                    path=row.get("path") or "",
                    service=row.get("service") or "",
                    port=row.get("port") or "",
                    url=row.get("url") or "",
                )
            )
    lines.append("")
    lines.append("Port ownership:")
    correlated = report.get("port_correlation") or []
    if not correlated:
        lines.append("  (no correlated website ports)")
    else:
        for row in correlated:
            owners = row.get("owners") or []
            owner_text = "; ".join(
                str(owner.get("container") or owner.get("process") or owner.get("pid") or owner.get("local") or owner.get("source"))
                for owner in owners
            ) or "unowned"
            lines.append(f"  {row.get('port')}: {row.get('site_id')}:{row.get('lane')} source={row.get('source') or ''}@{row.get('path') or ''} expected={row.get('service') or ''} owners={owner_text}")
    lines.append("")
    lines.append("ONLYOFFICE:")
    onlyoffice = report.get("onlyoffice") or {}
    if not onlyoffice:
        lines.append("  (no ONLYOFFICE state reported)")
    else:
        expected = ", ".join(str(row.get("host_port")) for row in onlyoffice.get("expected_ports", []) if isinstance(row, dict)) or "-"
        lines.append(f"  expected_ports: {expected}")
        lines.append(f"  compose_projects: {', '.join(onlyoffice.get('compose_projects') or []) or '-'}")
        containers = onlyoffice.get("containers") if isinstance(onlyoffice.get("containers"), list) else []
        if containers:
            lines.append("  containers:")
            for row in containers:
                ports = ",".join(str(p.get("host_port")) for p in row.get("host_ports", []) if isinstance(p, dict) and p.get("host_port")) or "-"
                labels = row.get("labels") if isinstance(row.get("labels"), dict) else {}
                working_dir = labels.get("com.docker.compose.project.working_dir") or ""
                config_files = labels.get("com.docker.compose.project.config_files") or ""
                lines.append(
                    "    {name} | running={running} | status={status} | ports={ports} | size={size} | image={image}".format(
                        name=row.get("name") or "",
                        running="yes" if row.get("running") else "no",
                        status=row.get("status") or "",
                        ports=ports,
                        size=row.get("size") or "",
                        image=row.get("image") or "",
                    )
                )
                if working_dir or config_files:
                    lines.append(f"      working_dir={working_dir} config={config_files}")
        else:
            lines.append("  containers: none")
        probes = onlyoffice.get("endpoint_probes") if isinstance(onlyoffice.get("endpoint_probes"), list) else []
        if probes:
            lines.append("  endpoint probes:")
            for probe in probes:
                state = "ok" if probe.get("ok") else "fail"
                lines.append(f"    {state} | {probe.get('url') or ''} | status={probe.get('status') or ''} | error={probe.get('error') or ''}")

    lines.append("")
    lines.append("Docker containers:")
    docker_containers = ((report.get("docker") or {}).get("containers") or [])
    if not docker_containers:
        lines.append("  (no Docker containers reported)")
    else:
        lines.append("  name | running | relation | confidence | ports | size | image | reasons")
        for row in docker_containers:
            cls = row.get("project_classification") if isinstance(row.get("project_classification"), dict) else {}
            ports = ",".join(str(p.get("host_port")) for p in row.get("host_ports", []) if isinstance(p, dict) and p.get("host_port")) or "-"
            reasons = "; ".join(str(r) for r in (cls.get("reasons") or [])[:3])
            lines.append(
                "  {name} | {running} | {relation} | {confidence} | {ports} | {size} | {image} | {reasons}".format(
                    name=row.get("name") or "",
                    running="yes" if row.get("running") else "no",
                    relation=cls.get("relation") or "",
                    confidence=cls.get("confidence") or "",
                    ports=ports,
                    size=row.get("size") or "",
                    image=row.get("image") or "",
                    reasons=reasons,
                )
            )

    lines.append("")
    lines.append("Storage:")
    docker_storage = ((report.get("docker") or {}).get("storage") or {})
    docker_df = str(docker_storage.get("stdout") or "").strip()
    if docker_df:
        lines.append("  Docker system df -v:")
        for line in docker_df.splitlines()[:120]:
            lines.append(f"    {line}")
        if len(docker_df.splitlines()) > 120:
            lines.append(f"    ... truncated {len(docker_df.splitlines()) - 120} line(s)")
    else:
        lines.append("  Docker system df -v: (not available)")
    host_vhdx = ((report.get("wsl") or {}).get("host_vhdx") or [])
    if host_vhdx:
        lines.append("  WSL/Docker VHDX files:")
        for row in host_vhdx[:30]:
            lines.append(f"    {row.get('size_human') or ''} | {row.get('path') or ''}")
        if len(host_vhdx) > 30:
            lines.append(f"    ... truncated {len(host_vhdx) - 30} VHDX file(s)")
    else:
        lines.append("  WSL/Docker VHDX files: (none found in known locations)")

    lines.append("")
    lines.append("WSL distributions and WSL Docker containers:")
    wsl = report.get("wsl") or {}
    distros = wsl.get("distros") if isinstance(wsl.get("distros"), list) else []
    if not distros:
        lines.append("  (no WSL distributions reported)")
    else:
        for distro in distros:
            cls = distro.get("project_classification") if isinstance(distro.get("project_classification"), dict) else {}
            lines.append(
                "  distro={name} state={state} relation={relation} confidence={confidence} paths={paths} procs={procs} listeners={listeners}".format(
                    name=distro.get("name") or "",
                    state=distro.get("state") or "",
                    relation=cls.get("relation") or "",
                    confidence=cls.get("confidence") or "",
                    paths=len(distro.get("project_paths") or []),
                    procs=len(distro.get("processes") or []),
                    listeners=len(distro.get("listening_ports") or {}),
                )
            )
            reasons = "; ".join(str(r) for r in (cls.get("reasons") or [])[:4])
            if reasons:
                lines.append(f"    reasons: {reasons}")
            for du in (distro.get("project_du") or [])[:8]:
                lines.append(f"    project_du: {du}")
            docker_rows = distro.get("docker_containers") if isinstance(distro.get("docker_containers"), list) else []
            if docker_rows:
                lines.append("    WSL docker containers:")
                for crow in docker_rows[:60]:
                    ccls = crow.get("classification") if isinstance(crow.get("classification"), dict) else {}
                    name = crow.get("Names") or crow.get("Name") or crow.get("ID") or crow.get("raw") or ""
                    ports = crow.get("Ports") or ""
                    status = crow.get("Status") or crow.get("State") or ""
                    image = crow.get("Image") or ""
                    lines.append(f"      {name} | {status} | {ccls.get('relation') or ''}/{ccls.get('confidence') or ''} | {ports} | {image}")
                if len(docker_rows) > 60:
                    lines.append(f"      ... truncated {len(docker_rows) - 60} WSL Docker container(s)")
    lines.append("")
    lines.append("Findings:")
    for finding in report.get("findings", []):
        evidence = finding.get("evidence")
        suffix = f" {json.dumps(evidence, sort_keys=True)}" if evidence else ""
        lines.append(f"  [{finding.get('status')}] {finding.get('area')}: {finding.get('message')}{suffix}")
    return "\n".join(lines) + "\n"


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = find_repo_root(Path(args.repo_root).resolve() if args.repo_root else Path.cwd())
    findings: list[Finding] = FindingCollector()
    progress("deep sanity check started", {"repo_root": str(repo_root)})
    if not (repo_root / "new_patch.py").exists():
        findings.append(Finding("WARN", "repo", "Repo root does not contain new_patch.py; pass --repo-root if this is wrong.", {"repo_root": str(repo_root)}))
    else:
        findings.append(Finding("PASS", "repo", "Repo root marker found.", {"path": safe_relative(repo_root / "new_patch.py", repo_root)}))

    progress("collect environment")
    environment = collect_environment(repo_root)

    progress("collect website registry/manifests/compose")
    registry_records = collect_registry_sites(repo_root, findings)
    manifest_records = collect_manifest_sites(repo_root, findings)
    compose_records = collect_compose_sites(repo_root, findings)
    all_records = [*registry_records, *manifest_records, *compose_records]
    progress(
        "website records collected",
        {"registry": len(registry_records), "manifest": len(manifest_records), "compose": len(compose_records), "total": len(all_records)},
    )
    website_matrix = analyze_website_port_space(all_records, findings)

    progress("scan public website artifacts for accidental secrets")
    secret_hits = scan_public_site_secret_leaks(repo_root, findings)

    progress("inspect Docker containers")
    containers, docker_findings = docker_inspect_containers()
    findings.extend(docker_findings)
    docker_size_rows, docker_ps_result = docker_ps_size_rows(timeout_s=float(getattr(args, "command_timeout", DEFAULT_COMMAND_TIMEOUT_S)))
    if docker_ps_result and docker_ps_result.returncode == 0:
        findings.append(Finding("INFO", "docker", f"Collected docker ps --size rows for {len(docker_size_rows)} container(s)."))
    elif docker_ps_result and not docker_ps_result.unavailable:
        findings.append(Finding("WARN", "docker", "docker ps --size failed.", {"stderr": docker_ps_result.stderr.strip()}))
    size_lookup = docker_size_lookup(docker_size_rows)
    docker_summaries = [
        docker_container_summary(container, repo_root=repo_root, website_matrix=website_matrix, size_lookup=size_lookup)
        for container in containers
    ]
    running_project = sum(1 for row in docker_summaries if row.get("running") and row.get("project_classification", {}).get("is_project"))
    running_non_project = sum(1 for row in docker_summaries if row.get("running") and not row.get("project_classification", {}).get("is_project"))
    findings.append(
        Finding(
            "INFO",
            "docker",
            "Classified Docker containers for project relevance.",
            {
                "total": len(docker_summaries),
                "running_project": running_project,
                "running_not_project_or_adjacent": running_non_project,
            },
        )
    )
    docker_storage, docker_df_result = docker_system_df(timeout_s=float(getattr(args, "command_timeout", DEFAULT_COMMAND_TIMEOUT_S)))
    if docker_df_result.unavailable:
        findings.append(Finding("INFO", "storage", "Docker storage inventory skipped because Docker CLI is unavailable."))
    elif docker_df_result.timed_out:
        findings.append(Finding("WARN", "storage", "Docker storage inventory timed out.", {"timeout_s": max(10.0, float(getattr(args, "command_timeout", DEFAULT_COMMAND_TIMEOUT_S)))}))
    elif docker_df_result.returncode == 0:
        findings.append(Finding("INFO", "storage", "Captured Docker storage usage from docker system df -v.", {"stdout_lines": len(docker_df_result.stdout.splitlines())}))
    else:
        findings.append(Finding("WARN", "storage", "Docker storage inventory failed.", {"stderr": docker_df_result.stderr.strip()}))
    docker_owners = docker_port_owners(containers)

    progress("collect Coolify/local-docker state")
    coolify_state = collect_coolify_state(repo_root, containers, findings)

    progress("collect local host port owners")
    local_port_owners = collect_local_port_owners()

    progress("collect ONLYOFFICE state")
    onlyoffice_state = collect_onlyoffice_state(
        repo_root,
        docker_summaries,
        local_port_owners,
        docker_owners,
        findings,
        probe=not getattr(args, "no_probe", False),
        probe_timeout_s=float(getattr(args, "probe_timeout", DEFAULT_PROBE_TIMEOUT_S)),
    )

    port_correlation = correlate_ports(website_matrix, local_port_owners, docker_owners, findings)

    probes: list[dict[str, Any]] = []
    if not getattr(args, "no_probe", False):
        progress("probe local website URLs")
        probes = probe_local_website_urls(all_records, findings, timeout_s=float(getattr(args, "probe_timeout", DEFAULT_PROBE_TIMEOUT_S)))
    else:
        progress("skip local website HTTP probes", {"reason": "--no-probe"})

    wsl_inventory: dict[str, Any] = {"skipped": True}
    if not getattr(args, "no_wsl", False):
        progress("collect WSL inventory")
        wsl_inventory = collect_wsl_inventory(
            repo_root,
            findings,
            timeout_s=float(getattr(args, "command_timeout", DEFAULT_COMMAND_TIMEOUT_S)),
            website_matrix=website_matrix,
        )
    else:
        progress("skip WSL inventory", {"reason": "--no-wsl"})

    progress("map machine environments and deploys")
    machine_environments = collect_machine_environments(
        repo_root,
        environment,
        docker_summaries,
        coolify_state,
        onlyoffice_state,
        wsl_inventory,
        findings,
        timeout_s=float(getattr(args, "command_timeout", DEFAULT_COMMAND_TIMEOUT_S)),
    )

    report = {
        "schema_version": 3,
        "environment": environment,
        "summary": summarize_findings(findings),
        "overall_status": highest_status(findings),
        "findings": [finding.to_dict() for finding in findings],
        "website_port_matrix": website_matrix,
        "port_correlation": port_correlation,
        "local_port_owners": {str(port): owners for port, owners in sorted(local_port_owners.items()) if any(row.get("port") == port for row in website_matrix)},
        "docker": {
            "containers": docker_summaries,
            "all_ps_size_rows": docker_size_rows,
            "storage": docker_storage,
            "port_owners": {str(port): owners for port, owners in sorted(docker_owners.items())},
        },
        "coolify": coolify_state,
        "onlyoffice": onlyoffice_state,
        "wsl": wsl_inventory,
        "machine_environments": machine_environments,
        "probes": probes,
        "secret_scan_hits": secret_hits,
    }
    progress("deep sanity report built", {"overall_status": report["overall_status"], "findings": len(report["findings"])})
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a deep sanity check for Main Computer Docker, WSL, Coolify, website, and port-space drift.")
    parser.add_argument("--repo-root", default=None, help="Repository root to inspect. Defaults to auto-detect from cwd.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a text report.")
    parser.add_argument("--json-out", default="", help="Also write the full report JSON to this path.")
    parser.add_argument("--no-probe", action="store_true", help="Do not make local HTTP probes against website URLs.")
    parser.add_argument("--probe-timeout", type=float, default=DEFAULT_PROBE_TIMEOUT_S, help="Timeout per local HTTP probe in seconds.")
    parser.add_argument("--no-wsl", action="store_true", help="Skip WSL distribution/process/path inventory.")
    parser.add_argument("--command-timeout", type=float, default=DEFAULT_COMMAND_TIMEOUT_S, help="Timeout for external commands in seconds.")
    parser.add_argument("--quiet", action="store_true", help="Disable live progress output. Final text/JSON report is still produced.")
    parser.add_argument("--no-fail-exit", action="store_true", help="Return exit code 0 even when FAIL findings are present.")
    return parser.parse_args(argv)


def write_json_report(path_text: str, report: dict[str, Any], repo_root: Path) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = repo_root / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    set_live_progress(not bool(getattr(args, "quiet", False)))
    report = build_report(args)
    repo_root = Path(report["environment"]["repo_root"]).resolve()
    if args.json_out:
        output_path = write_json_report(args.json_out, report, repo_root)
        report.setdefault("outputs", {})["json_out"] = str(output_path)
        # Rewrite so the output path is included in the file itself.
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text_report(report), end="")
        if args.json_out:
            print(f"json_report: {report.get('outputs', {}).get('json_out')}")
    if report.get("overall_status") == "FAIL" and not args.no_fail_exit:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
