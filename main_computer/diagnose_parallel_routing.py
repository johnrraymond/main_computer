#!/usr/bin/env python3
"""
verify_parallel_routing.py

Read-only diagnostics for Main Computer dev/prod parallel routing.

It verifies:
  - which local ports are reachable: 8765, 18765, 2080
  - whether raw upstreams respond like Main Computer
  - whether Caddy host-header routes point somewhere sensible
  - whether dev Compose appears to default to a prod-colliding port
  - whether repo-local Caddy config, Docker, WSL, and Caddy are present

It does not start/stop services, edit files, change hosts, or apply patches.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_PROD_PORT = 8765
DEFAULT_DEV_PORT = 18765
DEFAULT_CADDY_PORT = 2080
DEFAULT_PATHS = ["/api/workspace-timestamp", "/applications/document"]

PROD_HOST = "prod.main-computer.test"
DEV_HOST = "dev.main-computer.test"
HUB_HOST = "hub.dev.main-computer.test"
GIT_HOST = "git.dev.main-computer.test"


@dataclass
class Check:
    status: str
    name: str
    detail: str = ""


@dataclass
class HttpResult:
    ok: bool
    connect_host: str
    port: int
    host_header: str
    path: str
    status_code: Optional[int] = None
    reason: str = ""
    headers: Dict[str, str] = None
    body: bytes = b""
    error: str = ""

    def server(self) -> str:
        return (self.headers or {}).get("server", "")

    def body_hash(self) -> str:
        return hashlib.sha256(self.body).hexdigest()[:16] if self.body else ""

    def marker_summary(self) -> str:
        text = self.body.decode("utf-8", errors="replace")
        markers = []
        for marker in [
            "MAIN COMPUTER APPLICATIONS",
            "Document Editor",
            "Game Surface",
            "workspace",
            "pretty_docs",
            "main-computer",
        ]:
            if marker.lower() in text.lower():
                markers.append(marker)
        title = ""
        m = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.I | re.S)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()[:80]
        bits = []
        if title:
            bits.append(f"title={title!r}")
        if markers:
            bits.append("markers=" + ",".join(markers[:5]))
        if self.body_hash():
            bits.append("sha256[body]=" + self.body_hash())
        return "; ".join(bits)


class Reporter:
    def __init__(self) -> None:
        self.checks: List[Check] = []

    def section(self, title: str) -> None:
        print("\n" + "=" * 78)
        print(title)
        print("=" * 78)

    def add(self, status: str, name: str, detail: str = "") -> None:
        self.checks.append(Check(status, name, detail))
        icon = {
            "PASS": "[PASS]",
            "WARN": "[WARN]",
            "FAIL": "[FAIL]",
            "INFO": "[INFO]",
            "SKIP": "[SKIP]",
        }.get(status, "[????]")
        print(f"{icon} {name}")
        if detail:
            for line in detail.splitlines():
                print(f"       {line}")

    def info(self, name: str, detail: str = "") -> None:
        self.add("INFO", name, detail)

    def pass_(self, name: str, detail: str = "") -> None:
        self.add("PASS", name, detail)

    def warn(self, name: str, detail: str = "") -> None:
        self.add("WARN", name, detail)

    def fail(self, name: str, detail: str = "") -> None:
        self.add("FAIL", name, detail)

    def skip(self, name: str, detail: str = "") -> None:
        self.add("SKIP", name, detail)

    def summary(self) -> int:
        counts: Dict[str, int] = {}
        for c in self.checks:
            counts[c.status] = counts.get(c.status, 0) + 1

        self.section("Summary")
        print(json.dumps(counts, indent=2, sort_keys=True))

        if counts.get("FAIL"):
            print("\nResult: FAIL. At least one assumption is contradicted by live evidence.")
            return 2
        if counts.get("WARN"):
            print("\nResult: WARN. Nothing fatal was proven, but at least one assumption is unverified or risky.")
            return 1
        print("\nResult: PASS. The checked assumptions are consistent with the current machine state.")
        return 0


def sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def run_cmd(cmd: Sequence[str], timeout: float = 8.0, env: Optional[Dict[str, str]] = None) -> Tuple[int, str]:
    try:
        proc = subprocess.run(
            list(cmd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env=env,
        )
        return proc.returncode, proc.stdout.strip()
    except FileNotFoundError:
        return 127, f"not found: {cmd[0]}"
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        return 124, f"timeout after {timeout}s\n{out}".strip()
    except Exception as exc:
        return 125, f"{type(exc).__name__}: {exc}"


def find_repo_root(start: Path) -> Optional[Path]:
    for p in [start.resolve(), *start.resolve().parents]:
        if (p / "docker-compose.dev.yml").exists() and (p / "main_computer").exists():
            return p
    return None


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def tcp_connectable(host: str, port: int, timeout: float = 1.0) -> Tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, "connect ok"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def http_get(
    connect_host: str,
    port: int,
    path: str,
    host_header: Optional[str] = None,
    timeout: float = 2.5,
    max_bytes: int = 1024 * 1024,
) -> HttpResult:
    host_header = host_header or f"{connect_host}:{port}"
    result = HttpResult(
        ok=False,
        connect_host=connect_host,
        port=port,
        host_header=host_header,
        path=path,
        headers={},
    )

    try:
        with socket.create_connection((connect_host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            req = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host_header}\r\n"
                "User-Agent: main-computer-routing-verifier/1\r\n"
                "Accept: */*\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii", errors="replace")
            sock.sendall(req)

            chunks = []
            total = 0
            while total < max_bytes:
                try:
                    chunk = sock.recv(min(65536, max_bytes - total))
                except socket.timeout:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)

        raw = b"".join(chunks)
        if not raw:
            result.error = "empty response"
            return result

        header_blob, _, body = raw.partition(b"\r\n\r\n")
        header_text = header_blob.decode("iso-8859-1", errors="replace")
        lines = header_text.splitlines()
        if not lines:
            result.error = "missing status line"
            return result

        m = re.match(r"HTTP/\d(?:\.\d)?\s+(\d{3})(?:\s+(.*))?$", lines[0])
        if not m:
            result.error = f"bad status line: {lines[0]!r}"
            return result

        result.status_code = int(m.group(1))
        result.reason = (m.group(2) or "").strip()

        headers: Dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()

        result.headers = headers
        result.body = body
        result.ok = True
        return result

    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        return result


def print_http_result(prefix: str, r: HttpResult) -> None:
    if not r.ok:
        print(f"  {prefix}: ERROR {r.error}")
        return

    server = f"; server={r.server()!r}" if r.server() else ""
    marker = r.marker_summary()
    print(f"  {prefix}: HTTP {r.status_code} {r.reason}{server}; bytes={len(r.body)}")
    if marker:
        print(f"      {marker}")


def is_2xx_or_3xx(r: Optional[HttpResult]) -> bool:
    return bool(r and r.ok and r.status_code is not None and 200 <= r.status_code < 400)


def is_gateway_down(r: Optional[HttpResult]) -> bool:
    return bool(r and r.ok and r.status_code in {502, 503, 504})


def resolve_host(host: str) -> Tuple[bool, str]:
    try:
        infos = socket.getaddrinfo(host, None)
        addrs = sorted({i[4][0] for i in infos})
        return True, ", ".join(addrs)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def inspect_repo(rep: Reporter, repo: Optional[Path]) -> None:
    rep.section("Repository file assumptions")

    if not repo:
        rep.warn(
            "Repo root not found from current directory",
            "Run this script from the Main Computer repo root for compose/config checks. Live port checks will still run.",
        )
        return

    rep.info("Repo root", str(repo))

    compose = repo / "docker-compose.dev.yml"
    if not compose.exists():
        rep.fail("docker-compose.dev.yml missing", str(compose))
    else:
        text = read_text(compose)
        mapping_lines = [line.strip() for line in text.splitlines() if ":8765" in line or "8765:" in line]
        rep.info("Compose viewport-related lines", "\n".join(mapping_lines) or "(no lines containing 8765)")

        if "${MAIN_COMPUTER_HOST_PORT:-8765}:8765" in text or "MAIN_COMPUTER_HOST_PORT:-8765" in text:
            rep.fail(
                "Docker dev compose defaults to raw prod/current viewport port 8765",
                "This means Docker dev can collide with whatever is already on localhost:8765 unless an env var overrides it.",
            )
        elif "MAIN_COMPUTER_DOCKER_VIEWPORT_PORT" in text and "18765" in text:
            rep.pass_(
                "Docker dev compose appears to default to a separate dev port",
                "Found MAIN_COMPUTER_DOCKER_VIEWPORT_PORT / 18765 in docker-compose.dev.yml.",
            )
        elif ":-18765" in text:
            rep.pass_("Docker dev compose appears to default to 18765", "Found a 18765 default in docker-compose.dev.yml.")
        else:
            rep.warn(
                "Could not prove compose dev port default",
                "No obvious prod-colliding default was found, but no clear 18765 default was found either.",
            )

    dev_control = repo / "dev-control.ps1"
    if dev_control.exists():
        text = read_text(dev_control)
        has_docker_port = "DockerHostPort" in text and "18765" in text
        has_env = "MAIN_COMPUTER_DOCKER_VIEWPORT_PORT" in text
        if has_docker_port and has_env:
            rep.pass_(
                "dev-control.ps1 knows about a separate Docker dev port",
                "Found DockerHostPort/18765 and MAIN_COMPUTER_DOCKER_VIEWPORT_PORT.",
            )
        else:
            rep.warn(
                "dev-control.ps1 did not clearly advertise separate Docker dev port handling",
                "This may be fine, but the script did not find both DockerHostPort/18765 and MAIN_COMPUTER_DOCKER_VIEWPORT_PORT.",
            )
    else:
        rep.warn("dev-control.ps1 missing", str(dev_control))

    for rel in [
        "main_computer/executor_backend.py",
        "main_computer/docker_executor.py",
        "main_computer/wsl_executor.py",
        "docker/executor/Dockerfile",
        "docker-compose.executor.yml",
    ]:
        p = repo / rel
        if p.exists():
            rep.pass_(f"Executor-related file exists: {rel}")
        else:
            rep.warn(f"Executor-related file missing: {rel}")

    caddyfile = repo / "deploy" / "caddy" / "Caddyfile.dev-parallel"
    if not caddyfile.exists():
        rep.warn(
            "Repo-local Caddy dev-parallel config not found",
            "That is OK if you are running Caddy from an external config, but the repo does not currently contain the proposed Caddyfile.",
        )
    else:
        text = read_text(caddyfile)
        reverse_proxy_lines = [line.strip() for line in text.splitlines() if "reverse_proxy" in line]
        rep.info("Repo Caddy reverse_proxy lines", "\n".join(reverse_proxy_lines) or "(none)")

        prod_ok = re.search(
            r"prod\.main-computer\.test(?::2080)?[\s\S]*?reverse_proxy\s+127\.0\.0\.1:8765",
            text,
        ) is not None
        dev_ok = re.search(
            r"dev\.main-computer\.test(?::2080)?[\s\S]*?reverse_proxy\s+127\.0\.0\.1:18765",
            text,
        ) is not None

        if prod_ok and dev_ok:
            rep.pass_("Repo Caddyfile separates prod and dev upstreams", "prod -> 8765; dev -> 18765.")
        else:
            rep.fail(
                "Repo Caddyfile does not clearly separate prod/dev upstreams",
                "Expected prod.main-computer.test -> 127.0.0.1:8765 and dev.main-computer.test -> 127.0.0.1:18765.",
            )


def inspect_tools(rep: Reporter, repo: Optional[Path]) -> None:
    rep.section("Tool availability")
    rep.info("Platform", f"{platform.platform()} | python={sys.version.split()[0]}")

    for cmd in [["docker", "--version"], ["docker", "compose", "version"], ["caddy", "version"]]:
        rc, out = run_cmd(cmd, timeout=8)
        name = " ".join(cmd)
        if rc == 0:
            rep.pass_(name, out)
        elif rc == 127:
            rep.warn(name, out)
        else:
            rep.warn(name, out[:800])

    if platform.system().lower().startswith("win"):
        rc, out = run_cmd(["wsl.exe", "-l", "-v"], timeout=8)
        if rc == 0:
            rep.pass_("wsl.exe -l -v", out)
        else:
            rep.warn("wsl.exe -l -v", out[:800])

        rc, out = run_cmd(
            ["wsl.exe", "sh", "-lc", "command -v caddy >/dev/null && caddy version || true"],
            timeout=8,
        )
        if out.strip():
            rep.pass_("Caddy visible inside default WSL distro", out)
        else:
            rep.warn(
                "Caddy not visible inside default WSL distro",
                "wsl.exe sh -lc 'command -v caddy && caddy version' returned no caddy output",
            )
    else:
        rc, out = run_cmd(
            ["sh", "-lc", "test -n \"$WSL_DISTRO_NAME\" && echo WSL_DISTRO_NAME=$WSL_DISTRO_NAME || true"],
            timeout=3,
        )
        if out.strip():
            rep.pass_("Running inside WSL", out)
        else:
            rep.info("Not obviously running inside WSL", "No WSL_DISTRO_NAME detected.")

    if repo and (repo / "docker-compose.dev.yml").exists() and shutil.which("docker"):
        env = os.environ.copy()
        for k in ["MAIN_COMPUTER_HOST_PORT", "MAIN_COMPUTER_DOCKER_VIEWPORT_PORT"]:
            env.pop(k, None)

        rc, out = run_cmd(["docker", "compose", "-f", str(repo / "docker-compose.dev.yml"), "config"], timeout=20, env=env)
        if rc == 0:
            interesting = "\n".join(
                line for line in out.splitlines()
                if "published:" in line
                or "target:" in line
                or "MAIN_COMPUTER" in line
                or "8765" in line
                or "18765" in line
            )
            rep.info("docker compose config excerpt with default env", interesting[:4000] or "(no interesting lines)")

            if re.search(r"published:\s*['\"]?8765['\"]?", out):
                rep.fail("Effective compose config publishes host port 8765 by default")
            elif re.search(r"published:\s*['\"]?18765['\"]?", out):
                rep.pass_("Effective compose config publishes host port 18765 by default")
            else:
                rep.warn("Could not determine effective compose published viewport port")
        else:
            rep.warn("docker compose config failed", out[:1200])


def maybe_validate_caddyfile(rep: Reporter, repo: Optional[Path]) -> None:
    rep.section("Caddy config validation")

    caddyfile_candidates: List[Path] = []
    if repo:
        caddyfile_candidates.append(repo / "deploy" / "caddy" / "Caddyfile.dev-parallel")

    caddyfile_candidates.append(Path.home() / "main-computer-caddy" / "Caddyfile")

    existing = [p for p in caddyfile_candidates if p.exists()]
    if not existing:
        rep.skip("No Caddyfile candidate exists to validate", "\n".join(str(p) for p in caddyfile_candidates))
        return

    if shutil.which("caddy"):
        for p in existing:
            rc, out = run_cmd(["caddy", "validate", "--config", str(p)], timeout=10)
            if rc == 0:
                rep.pass_(f"caddy validate: {p}", out or "valid")
            else:
                rep.fail(f"caddy validate failed: {p}", out[:1200])
    elif platform.system().lower().startswith("win"):
        for p in existing:
            rc, out = run_cmd(["wsl.exe", "wslpath", "-a", str(p)], timeout=5)
            wsl_path = out.strip() if rc == 0 and out.strip() else str(p)
            rc, out = run_cmd(
                ["wsl.exe", "sh", "-lc", f"command -v caddy >/dev/null && caddy validate --config {sh_quote(wsl_path)}"],
                timeout=15,
            )
            if rc == 0:
                rep.pass_(f"WSL caddy validate: {p}", out or "valid")
            else:
                rep.warn(f"Could not validate Caddyfile via WSL: {p}", out[:1200])
    else:
        rep.warn("caddy binary not found; cannot validate Caddyfile syntax")


def inspect_process_ports(rep: Reporter, ports: Iterable[int]) -> None:
    rep.section("Listening-process evidence")
    port_re = "|".join(str(p) for p in ports)

    if platform.system().lower().startswith("win"):
        rc, out = run_cmd(["cmd.exe", "/c", f"netstat -ano | findstr /R \":{port_re}\""], timeout=8)
        if rc == 0 and out.strip():
            rep.info("Windows netstat lines", out)
        else:
            rep.warn("Windows netstat found no matching listening/connection lines", out or f"ports={port_re}")
    else:
        if shutil.which("ss"):
            rc, out = run_cmd(
                ["sh", "-lc", f"ss -ltnp 2>/dev/null | grep -E '(:{port_re})\\b' || true"],
                timeout=8,
            )
            if out.strip():
                rep.info("ss listening lines", out)
            else:
                rep.warn("ss found no matching listening lines", f"ports={port_re}")
        else:
            rep.skip("ss not available", "Cannot show Linux listening process lines.")


def inspect_dns(rep: Reporter) -> None:
    rep.section("Hostname resolution")

    for host in [PROD_HOST, DEV_HOST, HUB_HOST, GIT_HOST]:
        ok, detail = resolve_host(host)
        if not ok:
            rep.warn(f"{host} does not resolve", detail)
            continue

        localish = any(addr in detail for addr in ["127.0.0.1", "::1"])
        if localish:
            rep.pass_(f"{host} resolves to localhost", detail)
        else:
            rep.warn(f"{host} resolves, but not obviously to localhost", detail)


def inspect_live_http(rep: Reporter, prod_port: int, dev_port: int, caddy_port: int, paths: List[str]) -> None:
    rep.section("Raw upstream reachability")

    raw_up: Dict[int, bool] = {}
    raw_results: Dict[Tuple[int, str], HttpResult] = {}

    for port, label in [
        (prod_port, "prod/current raw upstream"),
        (dev_port, "dev raw upstream"),
        (caddy_port, "Caddy edge"),
    ]:
        ok, detail = tcp_connectable("127.0.0.1", port)
        raw_up[port] = ok
        if ok:
            rep.pass_(f"127.0.0.1:{port} is reachable ({label})", detail)
        else:
            rep.warn(f"127.0.0.1:{port} is not reachable ({label})", detail)

    for port, label in [(prod_port, "raw prod/current"), (dev_port, "raw dev")]:
        if not raw_up.get(port):
            continue

        print(f"\nHTTP probes for {label} on 127.0.0.1:{port}")
        for path in paths:
            r = http_get("127.0.0.1", port, path, host_header=f"localhost:{port}")
            raw_results[(port, path)] = r
            print_http_result(path, r)

    doc_prod = raw_results.get((prod_port, "/applications/document"))
    doc_dev = raw_results.get((dev_port, "/applications/document"))

    if is_2xx_or_3xx(doc_prod) and is_2xx_or_3xx(doc_dev):
        if doc_prod.body_hash() == doc_dev.body_hash():
            rep.warn(
                "Both raw prod and raw dev document pages return identical body hashes",
                "This does not prove a routing bug by itself; identical builds can render identical HTML. Use port/process evidence too.",
            )
        else:
            rep.pass_(
                "Raw prod and raw dev document pages are distinguishable",
                f"{prod_port} hash={doc_prod.body_hash()} | {dev_port} hash={doc_dev.body_hash()}",
            )

    rep.section("Caddy host-header route probes")

    if not raw_up.get(caddy_port):
        rep.warn("Caddy port is not reachable; skipping Caddy route proof", f"Expected 127.0.0.1:{caddy_port}")
        return

    caddy_results: Dict[Tuple[str, str], HttpResult] = {}

    for host, label in [
        (PROD_HOST, "prod Caddy route"),
        (DEV_HOST, "dev Caddy route"),
        (HUB_HOST, "hub Caddy route"),
        (GIT_HOST, "git Caddy route"),
    ]:
        print(f"\nHTTP probes for {label}: connect 127.0.0.1:{caddy_port}, Host: {host}:{caddy_port}")
        path_list = paths if host in [PROD_HOST, DEV_HOST] else ["/"]

        for path in path_list:
            r = http_get("127.0.0.1", caddy_port, path, host_header=f"{host}:{caddy_port}")
            caddy_results[(host, path)] = r
            print_http_result(path, r)

    prod_route = caddy_results.get((PROD_HOST, "/applications/document"))
    dev_route = caddy_results.get((DEV_HOST, "/applications/document"))

    raw_prod_up = raw_up.get(prod_port, False)
    raw_dev_up = raw_up.get(dev_port, False)

    if raw_prod_up and is_2xx_or_3xx(prod_route):
        rep.pass_("Prod Caddy route returns success while prod upstream is reachable")
    elif not raw_prod_up and is_gateway_down(prod_route):
        rep.pass_("Prod Caddy route fails like an unavailable upstream", "Raw prod/current upstream is down and Caddy returns gateway error.")
    elif prod_route and prod_route.ok:
        rep.warn("Prod Caddy route response is inconclusive", f"HTTP {prod_route.status_code}; raw prod up={raw_prod_up}")
    else:
        rep.warn("Prod Caddy route did not produce a usable HTTP response", prod_route.error if prod_route else "")

    if raw_dev_up and is_2xx_or_3xx(dev_route):
        rep.pass_("Dev Caddy route returns success while dev upstream is reachable")
    elif not raw_dev_up and is_gateway_down(dev_route):
        rep.pass_("Dev Caddy route fails like an unavailable dev upstream", "This is good evidence that dev is not silently falling through to prod.")
    elif (not raw_dev_up) and raw_prod_up and is_2xx_or_3xx(dev_route):
        rep.fail(
            "Dev Caddy route returns success even though dev upstream is down and prod upstream is up",
            "This strongly suggests dev.main-computer.test is pointing at prod/current, not 18765.",
        )
    elif dev_route and dev_route.ok:
        rep.warn("Dev Caddy route response is inconclusive", f"HTTP {dev_route.status_code}; raw dev up={raw_dev_up}; raw prod up={raw_prod_up}")
    else:
        rep.warn("Dev Caddy route did not produce a usable HTTP response", dev_route.error if dev_route else "")

    if is_2xx_or_3xx(prod_route) and is_2xx_or_3xx(dev_route):
        if prod_route.body_hash() == dev_route.body_hash():
            rep.warn(
                "Prod and dev Caddy document routes returned identical body hashes",
                "This alone cannot prove they are the same backend because the app UI may be identical in both environments.",
            )
        else:
            rep.pass_(
                "Prod and dev Caddy document routes returned different body hashes",
                f"prod hash={prod_route.body_hash()} | dev hash={dev_route.body_hash()}",
            )

    if prod_route and prod_route.server().lower().startswith("caddy"):
        rep.pass_("Prod route response has Caddy Server header", prod_route.server())
    elif prod_route and prod_route.ok:
        rep.warn("Prod route response does not advertise Caddy", f"Server={prod_route.server()!r}")

    if dev_route and dev_route.server().lower().startswith("caddy"):
        rep.pass_("Dev route response has Caddy Server header", dev_route.server())
    elif dev_route and dev_route.ok:
        rep.warn("Dev route response does not advertise Caddy", f"Server={dev_route.server()!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Main Computer dev/prod parallel routing assumptions.")
    parser.add_argument("--prod-port", type=int, default=DEFAULT_PROD_PORT)
    parser.add_argument("--dev-port", type=int, default=DEFAULT_DEV_PORT)
    parser.add_argument("--caddy-port", type=int, default=DEFAULT_CADDY_PORT)
    parser.add_argument("--path", action="append", dest="paths", help="Additional HTTP path to probe")
    args = parser.parse_args()

    paths = list(DEFAULT_PATHS)
    if args.paths:
        for p in args.paths:
            if not p.startswith("/"):
                p = "/" + p
            if p not in paths:
                paths.append(p)

    rep = Reporter()

    repo = find_repo_root(Path.cwd())
    inspect_repo(rep, repo)
    inspect_tools(rep, repo)
    maybe_validate_caddyfile(rep, repo)
    inspect_process_ports(rep, [args.prod_port, args.dev_port, args.caddy_port])
    inspect_dns(rep)
    inspect_live_http(rep, args.prod_port, args.dev_port, args.caddy_port, paths)

    return rep.summary()


if __name__ == "__main__":
    raise SystemExit(main())