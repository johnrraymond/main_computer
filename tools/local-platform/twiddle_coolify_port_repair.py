#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def out(value: dict) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def run(cmd: list[str], cwd: Path | None = None) -> dict:
    p = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=False)
    return {
        "ok": p.returncode == 0,
        "returncode": p.returncode,
        "stdout": p.stdout.strip(),
        "stderr": p.stderr.strip(),
        "cmd": cmd,
    }


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def free_port(start: int, end: int = 29999) -> int:
    for port in range(start, end + 1):
        if not port_open(port):
            return port
    raise RuntimeError(f"no free port found in {start}-{end}")


def health(port: int) -> dict:
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            body = r.read(1000).decode("utf-8", errors="replace")
            return {"ok": 200 <= r.status < 300, "status": r.status, "url": url, "body": body}
    except Exception as e:
        return {"ok": False, "status": None, "url": url, "error": str(e)}


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            values[k] = v.strip().strip('"').strip("'")
    return values


def write_env(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    seen = set()
    result: list[str] = []

    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            result.append(line)
            continue
        k, _ = line.split("=", 1)
        if k in updates:
            result.append(f"{k}={updates[k]}")
            seen.add(k)
        else:
            result.append(line)

    for k, v in updates.items():
        if k not in seen:
            result.append(f"{k}={v}")

    path.write_text("\n".join(result) + "\n", encoding="utf-8")


def docker_mapped_port(container: str) -> int | None:
    r = run(["docker", "port", container, "8080/tcp"])
    if not r["ok"] or not r["stdout"]:
        return None
    # Examples:
    # 127.0.0.1:17056
    # 0.0.0.0:17056
    last = r["stdout"].splitlines()[0].rsplit(":", 1)[-1]
    try:
        return int(last)
    except ValueError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--container", default="mc-applications-coolify")
    ap.add_argument("--port-base", type=int, default=27056)
    args = ap.parse_args()

    repo = Path(args.repo_root).resolve()
    sys.path.insert(0, str(repo))

    from main_computer.publishing.local_server_prepare import _load_coolify_local_docker

    helper = _load_coolify_local_docker(repo)
    env_path = helper.env_file(repo)
    values = parse_env(env_path)

    configured = int(values.get("APP_PORT", "17056"))
    mapped = docker_mapped_port(args.container)

    configured_health = health(configured)
    mapped_health = health(mapped) if mapped else {"ok": False, "skipped": True}

    needs_repair = not configured_health["ok"]

    replacement_app = free_port(args.port_base)
    replacement_soketi = free_port(replacement_app + 1)
    replacement_terminal = free_port(replacement_soketi + 1)

    result = {
        "ok": not needs_repair,
        "mode": "apply" if args.apply else "read_only",
        "env_path": str(env_path),
        "configured_app_port": configured,
        "docker_mapped_port": mapped,
        "configured_health": configured_health,
        "mapped_health": mapped_health,
        "needs_repair": needs_repair,
        "proposed": {
            "APP_PORT": str(replacement_app),
            "SOKETI_PORT": str(replacement_soketi),
            "SOKETI_TERMINAL_PORT": str(replacement_terminal),
        },
        "warning": "Without --apply this makes no changes.",
    }

    if not args.apply:
        out(result)
        return 0 if result["ok"] else 2

    if not needs_repair:
        result["applied"] = False
        result["message"] = "configured Coolify API is already reachable"
        out(result)
        return 0

    write_env(env_path, result["proposed"])

    down = run(["docker", "compose", "--env-file", str(env_path), "-f", str(helper.compose_file(repo)), "down"], cwd=repo)
    up = run(["docker", "compose", "--env-file", str(env_path), "-f", str(helper.compose_file(repo)), "up", "-d", "--force-recreate"], cwd=repo)

    time.sleep(6)
    after = health(replacement_app)

    result.update(
        {
            "applied": True,
            "docker_down": down,
            "docker_up": up,
            "after_health": after,
            "new_dashboard_url": f"http://127.0.0.1:{replacement_app}",
            "ok": after["ok"],
        }
    )
    out(result)
    return 0 if after["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())