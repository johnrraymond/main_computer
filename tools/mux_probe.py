import os
import subprocess
import textwrap

COOLIFY_CONTAINER = os.environ.get("MC_COOLIFY_CONTAINER", "mc-coolify-local")
SSH_HOST = os.environ.get("MC_SSH_HOST", "mc-coolify-local-ssh-target")
SSH_USER = os.environ.get("MC_SSH_USER", "root")
SSH_PORT = os.environ.get("MC_SSH_PORT", "22")

# Override if your target server UUID changes:
#   $env:MC_SERVER_UUID="otyaamlhgwwqmhekxsbfepuy"
SERVER_UUID = os.environ.get("MC_SERVER_UUID", "otyaamlhgwwqmhekxsbfepuy")

shell = r"""
set +e

echo "MC_PROBE begin"
echo "MC_PROBE container_id=$(id 2>&1)"
echo "MC_PROBE ssh_version=$(ssh -V 2>&1)"
echo "MC_PROBE host=__SSH_HOST__"
echo "MC_PROBE user=__SSH_USER__"
echo "MC_PROBE port=__SSH_PORT__"
echo "MC_PROBE server_uuid=__SERVER_UUID__"

echo "MC_PROBE key_candidates_start"
find /var/www/html/storage/app/ssh/keys \
  -maxdepth 1 \
  -type f \
  -name 'ssh_key@*' \
  ! -name '*.lock' \
  ! -name '*.pub' \
  -size +0c \
  -print \
  -exec ls -l {} \; 2>&1 || true
echo "MC_PROBE key_candidates_end"

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

echo "MC_PROBE key_path=$key_path"

if [ ! -s "$key_path" ]; then
  echo "MC_PROBE_RESULT fail no_non_lock_ssh_key_found"
  exit 2
fi

chmod 600 "$key_path" 2>/dev/null || true

ssh_base="-o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=8 -o ConnectionAttempts=1 -p __SSH_PORT__ -i $key_path"

echo "MC_PROBE direct_ssh_start"
ssh $ssh_base __SSH_USER__@__SSH_HOST__ "echo direct-ok; docker --version | head -n 1" 2>&1
echo "MC_PROBE direct_ssh_rc=$?"

echo "MC_PROBE mount_info_start"
mount | grep -E '/var/www/html|storage|/tmp' 2>/dev/null || true
echo "MC_PROBE mount_info_end"

run_case() {
  label="$1"
  control_path="$2"
  actual_path="$3"
  control_dir="$(dirname "$actual_path")"

  echo ""
  echo "MC_PROBE_CASE $label"
  echo "MC_PROBE_CASE control_path=$control_path"
  echo "MC_PROBE_CASE actual_path=$actual_path"

  mkdir -p "$control_dir" 2>/dev/null || true
  chmod 700 "$control_dir" 2>/dev/null || true

  ssh $ssh_base -S "$control_path" -O exit __SSH_USER__@__SSH_HOST__ >/dev/null 2>&1 || true
  rm -f "$actual_path" "$control_dir"/mux_* "$control_dir"/cm_socket* 2>/dev/null || true

  echo "MC_PROBE_CASE dir_before"
  ls -la "$control_dir" 2>&1 || true

  ssh $ssh_base \
    -o ControlMaster=yes \
    -o ControlPersist=120 \
    -o ControlPath="$control_path" \
    -MNf __SSH_USER__@__SSH_HOST__ >/tmp/mc_probe_master.out 2>&1

  master_rc=$?
  echo "MC_PROBE_CASE master_start_rc=$master_rc"
  cat /tmp/mc_probe_master.out 2>/dev/null || true

  echo "MC_PROBE_CASE dir_after_master"
  ls -la "$control_dir" 2>&1 || true

  if [ -S "$actual_path" ]; then
    echo "MC_PROBE_CASE socket_exists=yes"
    stat -c 'MC_PROBE_CASE socket_stat type=%F mode=%a user=%U uid=%u group=%G gid=%g path=%n' "$actual_path" 2>&1 || true
  else
    echo "MC_PROBE_CASE socket_exists=no"
  fi

  for delay in 0 1 3; do
    if [ "$delay" != "0" ]; then
      sleep "$delay"
    fi

    echo "MC_PROBE_CASE check_after_${delay}s_start"
    ssh $ssh_base -S "$control_path" -O check __SSH_USER__@__SSH_HOST__ >/tmp/mc_probe_check.out 2>&1
    check_rc=$?
    echo "MC_PROBE_CASE check_after_${delay}s_rc=$check_rc"
    cat /tmp/mc_probe_check.out 2>/dev/null || true

    echo "MC_PROBE_CASE auto_slave_after_${delay}s_start"
    ssh $ssh_base \
      -o ControlMaster=auto \
      -o ControlPath="$control_path" \
      __SSH_USER__@__SSH_HOST__ "echo auto-slave-ok" >/tmp/mc_probe_auto.out 2>&1
    auto_rc=$?
    echo "MC_PROBE_CASE auto_slave_after_${delay}s_rc=$auto_rc"
    cat /tmp/mc_probe_auto.out 2>/dev/null || true

    echo "MC_PROBE_CASE no_mux_direct_after_${delay}s_start"
    ssh $ssh_base \
      -o ControlMaster=no \
      -o ControlPath="$control_path" \
      __SSH_USER__@__SSH_HOST__ "echo no-mux-direct-ok" >/tmp/mc_probe_nomux.out 2>&1
    nomux_rc=$?
    echo "MC_PROBE_CASE no_mux_direct_after_${delay}s_rc=$nomux_rc"
    cat /tmp/mc_probe_nomux.out 2>/dev/null || true

    if [ -S "$actual_path" ]; then
      echo "MC_PROBE_CASE socket_after_${delay}s=yes"
      stat -c 'MC_PROBE_CASE socket_after_stat type=%F mode=%a user=%U uid=%u group=%G gid=%g path=%n' "$actual_path" 2>&1 || true
    else
      echo "MC_PROBE_CASE socket_after_${delay}s=no"
    fi

    echo "MC_PROBE_CASE dir_after_${delay}s"
    ls -la "$control_dir" 2>&1 || true
  done

  echo "MC_PROBE_CASE ssh_processes"
  ps aux 2>/dev/null | grep '[s]sh .*Control' || true

  ssh $ssh_base -S "$control_path" -O exit __SSH_USER__@__SSH_HOST__ >/dev/null 2>&1 || true
  rm -f "$actual_path" 2>/dev/null || true
}

run_case \
  "coolify_storage_server_uuid" \
  "/var/www/html/storage/app/ssh/mux/mux___SERVER_UUID__" \
  "/var/www/html/storage/app/ssh/mux/mux___SERVER_UUID__"

run_case \
  "coolify_storage_host_template" \
  "/var/www/html/storage/app/ssh/mux/mux_%h_%p_%r" \
  "/var/www/html/storage/app/ssh/mux/mux___SSH_HOST_____SSH_PORT_____SSH_USER__"

run_case \
  "tmp_server_uuid" \
  "/tmp/mc-mux-probe/mux___SERVER_UUID__" \
  "/tmp/mc-mux-probe/mux___SERVER_UUID__"

run_case \
  "tmp_host_template" \
  "/tmp/mc-mux-probe/mux_%h_%p_%r" \
  "/tmp/mc-mux-probe/mux___SSH_HOST_____SSH_PORT_____SSH_USER__"

echo ""
echo "MC_PROBE_RESULT done"
"""

shell = (
    textwrap.dedent(shell)
    .replace("__SSH_HOST__", SSH_HOST)
    .replace("__SSH_USER__", SSH_USER)
    .replace("__SSH_PORT__", SSH_PORT)
    .replace("__SERVER_UUID__", SERVER_UUID)
)

cmd = ["docker", "exec", COOLIFY_CONTAINER, "sh", "-lc", shell]
print("Running: docker exec", COOLIFY_CONTAINER, "sh -lc <probe>")
proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
print(proc.stdout)
raise SystemExit(proc.returncode)