import os
import subprocess
import textwrap

COOLIFY_CONTAINER = os.environ.get("MC_COOLIFY_CONTAINER", "mc-coolify-local")
SSH_HOST = os.environ.get("MC_SSH_HOST", "mc-coolify-local-ssh-target")
SSH_USER = os.environ.get("MC_SSH_USER", "root")
SSH_PORT = os.environ.get("MC_SSH_PORT", "22")
SERVER_UUID = os.environ.get("MC_SERVER_UUID", "otyaamlhgwwqmhekxsbfepuy")

shell = r"""
set +e

mux_path="/var/www/html/storage/app/ssh/mux"
tmp_mux="/tmp/mc-coolify-ssh-mux"
control_path="$mux_path/mux___SERVER_UUID__"
actual_tmp_path="$tmp_mux/mux___SERVER_UUID__"

echo "MC_SYMLINK_PROBE_V2 begin"
echo "MC_SYMLINK_PROBE_V2 container_id=$(id 2>&1)"
echo "MC_SYMLINK_PROBE_V2 ssh_version=$(ssh -V 2>&1)"
echo "MC_SYMLINK_PROBE_V2 target=__SSH_USER__@__SSH_HOST__:__SSH_PORT__"
echo "MC_SYMLINK_PROBE_V2 server_uuid=__SERVER_UUID__"
echo "MC_SYMLINK_PROBE_V2 mux_path=$mux_path"
echo "MC_SYMLINK_PROBE_V2 tmp_mux=$tmp_mux"
echo "MC_SYMLINK_PROBE_V2 control_path=$control_path"

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

echo "MC_SYMLINK_PROBE_V2 key_path=$key_path"

if [ ! -s "$key_path" ]; then
  echo "MC_SYMLINK_PROBE_V2_RESULT fail no_non_lock_ssh_key_found"
  exit 2
fi

chmod 600 "$key_path" 2>/dev/null || true

ssh_base="-o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=8 -o ConnectionAttempts=1 -p __SSH_PORT__ -i $key_path"

echo "MC_SYMLINK_PROBE_V2 direct_ssh_start"
ssh $ssh_base __SSH_USER__@__SSH_HOST__ "echo direct-ok; docker --version | head -n 1" 2>&1
direct_rc=$?
echo "MC_SYMLINK_PROBE_V2 direct_ssh_rc=$direct_rc"

if [ "$direct_rc" -ne 0 ]; then
  echo "MC_SYMLINK_PROBE_V2_RESULT fail direct_ssh_failed"
  exit 3
fi

echo "MC_SYMLINK_PROBE_V2 mount_info_start"
mount | grep -E '/var/www/html|storage|/tmp' 2>/dev/null || true
echo "MC_SYMLINK_PROBE_V2 mount_info_end"

echo "MC_SYMLINK_PROBE_V2 mux_before"
ls -ld "$mux_path" 2>&1 || true
ls -la "$mux_path" 2>&1 || true

mkdir -p "$tmp_mux" 2>/dev/null || true
chmod 700 "$tmp_mux" 2>/dev/null || true
rm -f "$tmp_mux"/mux_* "$tmp_mux"/cm_socket* 2>/dev/null || true

# Try polite exits only for known control paths. Do not grep/kill processes.
ssh $ssh_base -S "$mux_path/mux___SERVER_UUID__" -O exit __SSH_USER__@__SSH_HOST__ >/dev/null 2>&1 || true
ssh $ssh_base -S "$mux_path/mux_%h_%p_%r" -O exit __SSH_USER__@__SSH_HOST__ >/dev/null 2>&1 || true
ssh $ssh_base -S "$tmp_mux/mux___SERVER_UUID__" -O exit __SSH_USER__@__SSH_HOST__ >/dev/null 2>&1 || true
ssh $ssh_base -S "$tmp_mux/mux_%h_%p_%r" -O exit __SSH_USER__@__SSH_HOST__ >/dev/null 2>&1 || true

sleep 1

if [ -L "$mux_path" ]; then
  echo "MC_SYMLINK_PROBE_V2 existing_mux_is_symlink"
  rm -f "$mux_path" 2>/tmp/mc_symlink_rm.err
  rm_rc=$?
  echo "MC_SYMLINK_PROBE_V2 rm_existing_symlink_rc=$rm_rc"
  cat /tmp/mc_symlink_rm.err 2>/dev/null || true
elif [ -d "$mux_path" ]; then
  echo "MC_SYMLINK_PROBE_V2 existing_mux_is_directory"
  rm -f "$mux_path"/mux_* "$mux_path"/cm_socket* 2>/dev/null || true
  rmdir "$mux_path" 2>/tmp/mc_symlink_rmdir.err
  rmdir_rc=$?
  echo "MC_SYMLINK_PROBE_V2 rmdir_existing_mux_rc=$rmdir_rc"
  cat /tmp/mc_symlink_rmdir.err 2>/dev/null || true

  if [ "$rmdir_rc" -ne 0 ]; then
    echo "MC_SYMLINK_PROBE_V2 mux_dir_still_contains"
    ls -la "$mux_path" 2>&1 || true
    echo "MC_SYMLINK_PROBE_V2_RESULT fail could_not_replace_mux_directory"
    exit 4
  fi
else
  echo "MC_SYMLINK_PROBE_V2 existing_mux_missing_or_file"
  rm -f "$mux_path" 2>/dev/null || true
fi

ln -s "$tmp_mux" "$mux_path" 2>/tmp/mc_symlink_ln.err
ln_rc=$?
echo "MC_SYMLINK_PROBE_V2 ln_symlink_rc=$ln_rc"
cat /tmp/mc_symlink_ln.err 2>/dev/null || true

echo "MC_SYMLINK_PROBE_V2 mux_after_symlink"
ls -ld "$mux_path" "$tmp_mux" 2>&1 || true
ls -la "$mux_path" 2>&1 || true
ls -la "$tmp_mux" 2>&1 || true

if [ "$ln_rc" -ne 0 ] || [ ! -L "$mux_path" ]; then
  echo "MC_SYMLINK_PROBE_V2_RESULT fail symlink_not_created"
  exit 5
fi

ssh $ssh_base -S "$control_path" -O exit __SSH_USER__@__SSH_HOST__ >/dev/null 2>&1 || true
rm -f "$actual_tmp_path" 2>/dev/null || true

echo "MC_SYMLINK_PROBE_V2 master_start"
ssh $ssh_base \
  -o ControlMaster=yes \
  -o ControlPersist=120 \
  -o ControlPath="$control_path" \
  -MNf __SSH_USER__@__SSH_HOST__ >/tmp/mc_symlink_master.out 2>&1

master_rc=$?
echo "MC_SYMLINK_PROBE_V2 master_start_rc=$master_rc"
cat /tmp/mc_symlink_master.out 2>/dev/null || true

echo "MC_SYMLINK_PROBE_V2 mux_after_master"
ls -ld "$mux_path" "$tmp_mux" 2>&1 || true
ls -la "$mux_path" 2>&1 || true
ls -la "$tmp_mux" 2>&1 || true

if [ -S "$control_path" ]; then
  echo "MC_SYMLINK_PROBE_V2 socket_via_coolify_path=yes"
else
  echo "MC_SYMLINK_PROBE_V2 socket_via_coolify_path=no"
fi

if [ -S "$actual_tmp_path" ]; then
  echo "MC_SYMLINK_PROBE_V2 socket_via_tmp_path=yes"
  stat -c 'MC_SYMLINK_PROBE_V2 socket_stat type=%F mode=%a user=%U uid=%u group=%G gid=%g path=%n' "$actual_tmp_path" 2>&1 || true
else
  echo "MC_SYMLINK_PROBE_V2 socket_via_tmp_path=no"
fi

for delay in 0 1 3; do
  if [ "$delay" != "0" ]; then
    sleep "$delay"
  fi

  echo "MC_SYMLINK_PROBE_V2 check_after_${delay}s_start"
  ssh $ssh_base -S "$control_path" -O check __SSH_USER__@__SSH_HOST__ >/tmp/mc_symlink_check.out 2>&1
  check_rc=$?
  echo "MC_SYMLINK_PROBE_V2 check_after_${delay}s_rc=$check_rc"
  cat /tmp/mc_symlink_check.out 2>/dev/null || true

  echo "MC_SYMLINK_PROBE_V2 auto_slave_after_${delay}s_start"
  ssh $ssh_base \
    -o ControlMaster=auto \
    -o ControlPath="$control_path" \
    __SSH_USER__@__SSH_HOST__ "echo auto-slave-ok" >/tmp/mc_symlink_auto.out 2>&1
  auto_rc=$?
  echo "MC_SYMLINK_PROBE_V2 auto_slave_after_${delay}s_rc=$auto_rc"
  cat /tmp/mc_symlink_auto.out 2>/dev/null || true

  if [ -S "$actual_tmp_path" ]; then
    echo "MC_SYMLINK_PROBE_V2 socket_after_${delay}s=yes"
  else
    echo "MC_SYMLINK_PROBE_V2 socket_after_${delay}s=no"
  fi
done

echo "MC_SYMLINK_PROBE_V2 ssh_processes_relevant"
ps aux 2>/dev/null | grep '[s]sh ' | grep 'ControlPath=' | grep -E 'storage/app/ssh/mux|mc-coolify-ssh-mux|mc-mux-probe' 2>/dev/null || true

ssh $ssh_base -S "$control_path" -O check __SSH_USER__@__SSH_HOST__ >/tmp/mc_symlink_final_check.out 2>&1
final_rc=$?
echo "MC_SYMLINK_PROBE_V2 final_check_rc=$final_rc"
cat /tmp/mc_symlink_final_check.out 2>/dev/null || true

if [ "$master_rc" -eq 0 ] && [ "$final_rc" -eq 0 ] && [ -S "$actual_tmp_path" ]; then
  echo "MC_SYMLINK_PROBE_V2_RESULT pass symlinked_coolify_mux_path_is_live"
  exit 0
fi

echo "MC_SYMLINK_PROBE_V2_RESULT fail symlinked_coolify_mux_path_not_live"
exit 6
"""

shell = (
    textwrap.dedent(shell)
    .replace("__SSH_HOST__", SSH_HOST)
    .replace("__SSH_USER__", SSH_USER)
    .replace("__SSH_PORT__", SSH_PORT)
    .replace("__SERVER_UUID__", SERVER_UUID)
)

cmd = ["docker", "exec", COOLIFY_CONTAINER, "sh", "-lc", shell]
print("Running: docker exec", COOLIFY_CONTAINER, "sh -lc <symlink-probe-v2>")
proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
print(proc.stdout)
raise SystemExit(proc.returncode)