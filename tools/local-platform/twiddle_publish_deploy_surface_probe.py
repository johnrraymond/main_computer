import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlparse


def run(cmd, *, input_text=None, check=False):
    proc = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{proc.stderr}")
    return proc


def http_get(url):
    req = urllib.request.Request(url, headers={"Cache-Control": "no-store"})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            body = res.read().decode("utf-8", errors="replace")
            return {"ok": True, "status": res.status, "body": body[:2000]}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "status": exc.code, "body": body[:2000]}
    except Exception as exc:
        return {"ok": False, "status": None, "error": repr(exc), "body": ""}


def http_post(url, token):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            body = res.read().decode("utf-8", errors="replace")
            return {"ok": True, "status": res.status, "body": body[:4000]}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "status": exc.code, "body": body[:4000]}
    except Exception as exc:
        return {"ok": False, "status": None, "error": repr(exc), "body": ""}


def docker_inspect_all():
    ps = run(["docker", "ps", "-a", "--format", "{{.Names}}"], check=True)
    names = [line.strip() for line in ps.stdout.splitlines() if line.strip()]
    if not names:
        return []
    inspected = run(["docker", "inspect", *names], check=True)
    return json.loads(inspected.stdout)


def port_owner(containers, host_port):
    owners = []
    for c in containers:
        ports = (c.get("NetworkSettings") or {}).get("Ports") or {}
        for _container_port, bindings in ports.items():
            for binding in bindings or []:
                if str(binding.get("HostPort")) == str(host_port):
                    owners.append(c)
    return owners


def env_map(container):
    env = {}
    for item in ((container.get("Config") or {}).get("Env") or []):
        if "=" in item:
            k, v = item.split("=", 1)
            env[k] = v
    return env


def container_summary(container):
    env = env_map(container)
    return {
        "name": container.get("Name", "").lstrip("/"),
        "image": (container.get("Config") or {}).get("Image"),
        "id": container.get("Id", "")[:12],
        "status": (container.get("State") or {}).get("Status"),
        "started_at": (container.get("State") or {}).get("StartedAt"),
        "site_id": env.get("SITE_ID") or env.get("MC_SITE_ID"),
        "site_lane": env.get("SITE_LANE") or env.get("MC_RUNTIME_LANE"),
    }


def site_containers(containers, site_id):
    out = []
    for c in containers:
        name = c.get("Name", "").lstrip("/")
        env = env_map(c)
        if (
            env.get("SITE_ID") == site_id
            or env.get("MC_SITE_ID") == site_id
            or site_id in name
            or "hub-local" in name
            or "local-publish" in name
        ):
            out.append(c)
    return out


def exec_site_probe(container_name, site_id):
    code = f"""
from pathlib import Path
import os

site = {site_id!r}
p = Path("/app/runtime/websites") / site / "blog" / "index.html"
app_path = Path("/app/app.py")
app = app_path.read_text(errors="replace") if app_path.exists() else ""

print("SITE_ID=" + str(os.environ.get("SITE_ID")))
print("SITE_LANE=" + str(os.environ.get("SITE_LANE")))
print("CONTENT_ROOT=" + str(os.environ.get("CONTENT_ROOT")))
print("blog_exists=" + str(p.exists()))
print("blog_size=" + str(p.stat().st_size if p.exists() else None))
print("has_nested_index_fallback=" + str('candidates.append(root.joinpath(*parts, "index.html"))' in app))
print("has_parent_index_fallback=" + str('root.joinpath(*parts[:depth], "index.html")' in app))
"""
    proc = run(["docker", "exec", "-i", container_name, "python", "-c", code])
    return {
        "container": container_name,
        "ok": proc.returncode == 0,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def started_map(containers):
    return {
        c.get("Name", "").lstrip("/"): {
            "id": c.get("Id", "")[:12],
            "started_at": (c.get("State") or {}).get("StartedAt"),
            "image": (c.get("Config") or {}).get("Image"),
        }
        for c in containers
    }


def diff_started(before, after):
    changed = []
    for name, post in after.items():
        pre = before.get(name)
        if not pre:
            changed.append({"name": name, "change": "new", **post})
        elif pre.get("id") != post.get("id") or pre.get("started_at") != post.get("started_at"):
            changed.append({"name": name, "change": "restarted_or_recreated", "before": pre, "after": post})
    return changed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", default="hub-site")
    parser.add_argument("--published-url", default="http://127.0.0.1:18080/")
    parser.add_argument("--blog-route", default="/blog")
    parser.add_argument("--coolify-url", default="http://127.0.0.1:17056")
    parser.add_argument("--resource-uuid", default="v11lha4aoh6sd1msc0z6i7dc")
    parser.add_argument("--token-file", default=str(Path.home() / ".main-computer-tools/instances/main-computer-test/unleashed/coolify-local-docker/api-token.txt"))
    parser.add_argument("--trigger-deploy", action="store_true")
    parser.add_argument("--wait-seconds", type=int, default=20)
    args = parser.parse_args()

    parsed = urlparse(args.published_url)
    host_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    published_blog_url = urljoin(args.published_url.rstrip("/") + "/", args.blog_route.lstrip("/"))
    runtime_url = urljoin(args.published_url.rstrip("/") + "/", "api/site/blog/runtime")

    before_containers = docker_inspect_all()
    before_started = started_map(before_containers)

    owners = port_owner(before_containers, host_port)
    candidates = site_containers(before_containers, args.site)

    print("\n=== Published URL owner before deploy ===")
    print(json.dumps([container_summary(c) for c in owners], indent=2))

    print("\n=== Site-related containers before deploy ===")
    print(json.dumps([container_summary(c) for c in candidates], indent=2))

    print("\n=== Container code/file probes before deploy ===")
    for c in candidates:
        name = c.get("Name", "").lstrip("/")
        print(f"\n--- {name} ---")
        probe = exec_site_probe(name, args.site)
        print(probe["stdout"] or probe["stderr"])

    print("\n=== Published URL probes before deploy ===")
    print("/api/site/blog/runtime:", json.dumps(http_get(runtime_url), indent=2))
    print(args.blog_route + ":", json.dumps(http_get(published_blog_url), indent=2))

    if args.trigger_deploy:
        token = ""
        token_path = Path(args.token_file)
        if token_path.exists():
            token = token_path.read_text(encoding="utf-8", errors="replace").strip()

        deploy_url = args.coolify_url.rstrip("/") + f"/api/v1/deploy?uuid={args.resource_uuid}&force=true"
        print("\n=== Triggering Coolify /deploy ===")
        print(deploy_url)
        print(json.dumps(http_post(deploy_url, token), indent=2))

        print(f"\nWaiting {args.wait_seconds}s...")
        time.sleep(args.wait_seconds)

        after_containers = docker_inspect_all()
        after_started = started_map(after_containers)
        changed = diff_started(before_started, after_started)

        print("\n=== Containers changed by /deploy ===")
        print(json.dumps(changed, indent=2))

        owners_after = port_owner(after_containers, host_port)
        candidates_after = site_containers(after_containers, args.site)

        print("\n=== Published URL owner after deploy ===")
        print(json.dumps([container_summary(c) for c in owners_after], indent=2))

        print("\n=== Container code/file probes after deploy ===")
        for c in candidates_after:
            name = c.get("Name", "").lstrip("/")
            print(f"\n--- {name} ---")
            probe = exec_site_probe(name, args.site)
            print(probe["stdout"] or probe["stderr"])

        print("\n=== Published URL probes after deploy ===")
        print("/api/site/blog/runtime:", json.dumps(http_get(runtime_url), indent=2))
        print(args.blog_route + ":", json.dumps(http_get(published_blog_url), indent=2))

    print("\n=== How to read this ===")
    print("""
If /deploy changes a local-publish container but the published URL owner is hub-local:
  Fix target ownership. The accepted Publish URL is not served by the /deploy-controlled resource.

If /deploy changes hub-local but hub-local still lacks nested index fallback:
  Fix Coolify build contract. /deploy is restarting but not rebuilding current site-server code.

If hub-local has nested index fallback and sees blog/index.html but /blog is still 404:
  Fix site-server route handling.

If no relevant container changes after /deploy:
  Fix Coolify resource reconciliation. The /deploy hook is accepted but not tied to the expected service.
""")


if __name__ == "__main__":
    main()