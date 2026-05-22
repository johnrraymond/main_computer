import json
import os
import subprocess
from pathlib import Path

REPO = Path(".").resolve()
STATE = REPO / "runtime" / "coolify-local-docker" / "sqlite-deploy-smoke.json"
COOLIFY_CONTAINER = os.environ.get("MC_COOLIFY_CONTAINER", "mc-coolify-local")
SSH_HOST = os.environ.get("MC_SSH_HOST", "mc-coolify-local-ssh-target")
SSH_USER = os.environ.get("MC_SSH_USER", "root")
SSH_PORT = os.environ.get("MC_SSH_PORT", "22")

def run(cmd, *, timeout=30):
    p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    return p.returncode, p.stdout.strip()

def main():
    if not STATE.exists():
        print(f"MC_VOLUME_PROBE_RESULT fail missing_state_file {STATE}")
        return 2

    state = json.loads(STATE.read_text(encoding="utf-8"))
    service = state.get("service_name", "")
    expected_volume = state.get("volume_name", "")
    port = state.get("port", "")

    print(f"MC_VOLUME_PROBE state_file={STATE}")
    print(f"MC_VOLUME_PROBE service_name={service}")
    print(f"MC_VOLUME_PROBE expected_volume={expected_volume}")
    print(f"MC_VOLUME_PROBE port={port}")

    print("\nMC_VOLUME_PROBE host_docker_expected_volume_start")
    rc, out = run(["docker", "volume", "inspect", str(expected_volume), "--format", "{{json .}}"])
    print(f"MC_VOLUME_PROBE host_docker_expected_volume_rc={rc}")
    print(out)
    print("MC_VOLUME_PROBE host_docker_expected_volume_end")

    print("\nMC_VOLUME_PROBE host_docker_matching_containers_start")
    rc, out = run([
        "docker", "ps", "-a",
        "--filter", f"name={service}",
        "--format", "table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Ports}}",
    ])
    print(f"MC_VOLUME_PROBE host_docker_matching_containers_rc={rc}")
    print(out)
    print("MC_VOLUME_PROBE host_docker_matching_containers_end")

    remote_script = r"""
set +e
key_path="$(
  find /var/www/html/storage/app/ssh/keys \
    -maxdepth 1 \
    -type f \
    -name 'ssh_key@*' \
    ! -name '*.lock' \
    ! -name '*.pub' \
    -size +0c \
    -print 2>/dev/null | head -n 1
)"

echo "MC_VOLUME_PROBE remote_key_path=$key_path"
if [ ! -s "$key_path" ]; then
  echo "MC_VOLUME_PROBE_RESULT fail no_remote_key"
  exit 3
fi

chmod 600 "$key_path" 2>/dev/null || true

ssh_base="-o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=8 -o ConnectionAttempts=1 -p __SSH_PORT__ -i $key_path"

echo "MC_VOLUME_PROBE remote_docker_info_start"
ssh $ssh_base __SSH_USER__@__SSH_HOST__ "docker version --format '{{.Server.Version}}' 2>/dev/null || docker --version" 2>&1
echo "MC_VOLUME_PROBE remote_docker_info_end"

echo "MC_VOLUME_PROBE remote_expected_volume_start"
ssh $ssh_base __SSH_USER__@__SSH_HOST__ "docker volume inspect '__EXPECTED_VOLUME__' --format '{{json .}}'" 2>&1
echo "MC_VOLUME_PROBE remote_expected_volume_rc=$?"
echo "MC_VOLUME_PROBE remote_expected_volume_end"

echo "MC_VOLUME_PROBE remote_matching_volumes_start"
ssh $ssh_base __SSH_USER__@__SSH_HOST__ "docker volume ls --format '{{.Name}}' | grep -E '__EXPECTED_VOLUME__|sqlite|e2e|__SERVICE__' || true" 2>&1
echo "MC_VOLUME_PROBE remote_matching_volumes_end"

echo "MC_VOLUME_PROBE remote_matching_containers_start"
ssh $ssh_base __SSH_USER__@__SSH_HOST__ "docker ps -a --filter name='__SERVICE__' --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Ports}}'" 2>&1
echo "MC_VOLUME_PROBE remote_matching_containers_end"

echo "MC_VOLUME_PROBE remote_container_mounts_start"
container_id="$(ssh $ssh_base __SSH_USER__@__SSH_HOST__ "docker ps -a --filter name='__SERVICE__' --format '{{.ID}}' | head -n 1" 2>/dev/null)"
echo "MC_VOLUME_PROBE remote_container_id=$container_id"
if [ -n "$container_id" ]; then
  ssh $ssh_base __SSH_USER__@__SSH_HOST__ "docker inspect '$container_id' --format '{{json .Mounts}}'" 2>&1
fi
echo "MC_VOLUME_PROBE remote_container_mounts_end"
""".replace("__SSH_HOST__", SSH_HOST).replace("__SSH_USER__", SSH_USER).replace("__SSH_PORT__", str(SSH_PORT)).replace("__EXPECTED_VOLUME__", str(expected_volume)).replace("__SERVICE__", str(service))

    print("\nMC_VOLUME_PROBE remote_via_coolify_container_start")
    rc, out = run(["docker", "exec", COOLIFY_CONTAINER, "sh", "-lc", remote_script], timeout=60)
    print(f"MC_VOLUME_PROBE remote_via_coolify_container_rc={rc}")
    print(out)
    print("MC_VOLUME_PROBE remote_via_coolify_container_end")

    print("\nMC_VOLUME_PROBE_RESULT done")
    return 0

raise SystemExit(main())