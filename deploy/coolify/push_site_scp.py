#!/usr/bin/env python
from __future__ import annotations

import argparse
import getpass
import os
import posixpath
import re
import shlex
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import paramiko


SITE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
LOCAL_SECRET_FILENAMES = {"ssh_password.local"}


def fail(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def validate_site_id(site_id: str) -> None:
    if not SITE_ID_RE.fullmatch(site_id):
        fail(f"invalid site_id: {site_id!r}")


def split_host(value: str) -> tuple[str, str]:
    value = value.strip()
    if "@" in value:
        username, hostname = value.split("@", 1)
        return username.strip(), hostname.strip()
    return getpass.getuser(), value.strip()


def zip_site_contents(source_dir: Path, zip_path: Path) -> None:
    source_dir = source_dir.resolve()

    if not source_dir.is_dir():
        fail(f"source directory does not exist: {source_dir}")

    if not (source_dir / "index.html").is_file():
        fail(f"source directory must contain index.html at its root: {source_dir / 'index.html'}")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source_dir.rglob("*")):
            if path.is_dir():
                continue

            relative = path.relative_to(source_dir).as_posix()

            if relative.startswith("/") or relative.startswith("../") or "/../" in relative:
                fail(f"refusing unsafe zip path: {relative}")
            if path.name in LOCAL_SECRET_FILENAMES:
                print(f"skip local secret: {relative}")
                continue

            zf.write(path, relative)

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()

    if "index.html" not in names:
        fail("created zip does not contain root index.html")

    print(f"zip created: {zip_path}")
    print(f"files: {len(names)}")
    print(f"size: {zip_path.stat().st_size} bytes")


def sftp_mkdir_p(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    remote_dir = remote_dir.rstrip("/")
    if not remote_dir:
        return

    parts: list[str] = []
    current = remote_dir

    while current not in ("", "/"):
        parts.append(current)
        current = posixpath.dirname(current)

    for path in reversed(parts):
        try:
            sftp.stat(path)
        except FileNotFoundError:
            sftp.mkdir(path)


REMOTE_SCRIPT = r'''
set -eu

SITE_ID="$1"
REMOTE_ROOT="$2"
REMOTE_ZIP="$3"
BACKUP_ROOT="$4"
KEEP_BACKUPS="$5"

TARGET="${REMOTE_ROOT}/${SITE_ID}"
PARENT="$(dirname "$TARGET")"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK="/tmp/main-computer-publish/${SITE_ID}-${STAMP}-$$"
STAGING="${WORK}/staging"
BACKUP_DIR="${BACKUP_ROOT}/${SITE_ID}"
BACKUP_TGZ="${BACKUP_DIR}/${STAMP}.tar.gz"

echo "[remote] site_id=$SITE_ID"
echo "[remote] remote_zip=$REMOTE_ZIP"
echo "[remote] target=$TARGET"
echo "[remote] backup_tgz=$BACKUP_TGZ"

case "$SITE_ID" in
  *[!a-zA-Z0-9_-]*|"")
    echo "[remote] invalid site id: $SITE_ID" >&2
    exit 40
    ;;
esac

test -f "$REMOTE_ZIP" || {
  echo "[remote] uploaded zip missing: $REMOTE_ZIP" >&2
  exit 41
}

mkdir -p "$PARENT"
mkdir -p "$BACKUP_DIR"
mkdir -p "$STAGING"

python3 - "$REMOTE_ZIP" "$STAGING" <<'PY'
import sys
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

zip_path = Path(sys.argv[1])
staging = Path(sys.argv[2]).resolve()

def bad_name(name: str) -> bool:
    if not name or "\x00" in name:
        return True

    normalized = name.replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(name)

    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        return True

    return any(part in ("", ".", "..") for part in posix.parts)

try:
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()

        if not infos:
            raise SystemExit("zip is empty")

        names = [info.filename for info in infos]

        if "index.html" not in names:
            raise SystemExit("zip must contain index.html at root")

        for info in infos:
            if bad_name(info.filename):
                raise SystemExit(f"unsafe zip member: {info.filename}")

            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise SystemExit(f"zip symlink not allowed: {info.filename}")

        for info in infos:
            if info.is_dir():
                continue

            target = (staging / info.filename.replace("\\", "/")).resolve()

            if staging not in target.parents and target != staging:
                raise SystemExit(f"unsafe extraction target: {info.filename}")

            target.parent.mkdir(parents=True, exist_ok=True)

            with zf.open(info) as src, target.open("wb") as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)

except zipfile.BadZipFile:
    raise SystemExit("invalid zip")
PY

test -f "$STAGING/index.html" || {
  echo "[remote] staging index.html missing after extract" >&2
  exit 42
}

echo "[remote] staging contents:"
find "$STAGING" -maxdepth 3 -type f | sort | sed "s#^$STAGING/##"

if [ -d "$TARGET" ]; then
  echo "[remote] backing up existing target directory"
  tar -C "$PARENT" -czf "$BACKUP_TGZ" "$(basename "$TARGET")"

  echo "[remote] clearing existing target contents without moving target directory"
  find "$TARGET" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
else
  echo "[remote] no existing target directory; creating it"
  mkdir -p "$TARGET"
fi

rollback_from_backup() {
  echo "[remote] rollback requested" >&2
  find "$TARGET" -mindepth 1 -maxdepth 1 -exec rm -rf {} + || true

  if [ -f "$BACKUP_TGZ" ]; then
    echo "[remote] restoring backup from $BACKUP_TGZ" >&2
    tar -C "$PARENT" -xzf "$BACKUP_TGZ"
  else
    echo "[remote] no backup available to restore" >&2
  fi
}

echo "[remote] installing new site into existing target directory"
if ! cp -a "$STAGING"/. "$TARGET"/; then
  echo "[remote] copy failed" >&2
  rollback_from_backup
  exit 43
fi

if [ ! -f "$TARGET/index.html" ]; then
  echo "[remote] installed index.html missing" >&2
  rollback_from_backup
  exit 44
fi

echo "[remote] installed contents:"
find "$TARGET" -maxdepth 3 -type f | sort | sed "s#^$TARGET/##"

echo "[remote] cleaning uploaded zip and work dir"
rm -f "$REMOTE_ZIP"
rm -rf "$WORK"

if [ "$KEEP_BACKUPS" != "all" ]; then
  echo "[remote] pruning backups, keeping newest $KEEP_BACKUPS tarballs"
  find "$BACKUP_DIR" -maxdepth 1 -name "*.tar.gz" -type f -printf "%T@ %p\n" \
    | sort -nr \
    | awk "NR>$KEEP_BACKUPS {print \$2}" \
    | xargs -r rm -f
fi

echo "[remote] publish complete"
echo "[remote] final target=$TARGET"
echo "[remote] backup=$BACKUP_TGZ"
'''


def ssh_connect(
    host: str,
    port: int,
    username: str,
    password: str | None,
    key_filename: str | None,
) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    print(f"connecting: {username}@{host}:{port}")

    try:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            key_filename=key_filename,
            look_for_keys=key_filename is None and password is None,
            allow_agent=key_filename is None and password is None,
            timeout=30,
            banner_timeout=30,
            auth_timeout=30,
        )
    except Exception as exc:
        fail(f"ssh connection failed: {exc}")

    return client


def run_remote_script(
    client: paramiko.SSHClient,
    site_id: str,
    remote_root: str,
    remote_zip: str,
    backup_root: str,
    keep_backups: str,
) -> None:
    args = [
        shlex.quote(site_id),
        shlex.quote(remote_root),
        shlex.quote(remote_zip),
        shlex.quote(backup_root),
        shlex.quote(keep_backups),
    ]
    command = "sh -s -- " + " ".join(args)

    print(f"+ ssh remote: {command}")

    stdin, stdout, stderr = client.exec_command(command, get_pty=False)
    stdin.write(REMOTE_SCRIPT)
    stdin.channel.shutdown_write()

    for line in stdout:
        print(line, end="")

    err = stderr.read().decode("utf-8", errors="replace")
    if err:
        print(err, end="", file=sys.stderr)

    exit_code = stdout.channel.recv_exit_status()
    if exit_code != 0:
        fail(f"remote publish failed with exit code {exit_code}", code=exit_code)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish a generated static site over SSH/SFTP by uploading a zip and replacing the remote site contents."
    )
    parser.add_argument("site_id", help="Site id, for example: johnrraymond")
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Local generated site directory. Must contain index.html at its root.",
    )
    parser.add_argument(
        "--host",
        default="root@coolify",
        help="SSH target, for example root@publish.greatlibrary.io or root@1.2.3.4.",
    )
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument(
        "--ssh-password",
        default=os.environ.get("MAIN_COMPUTER_SSH_PASSWORD"),
        help="SSH password. Defaults to MAIN_COMPUTER_SSH_PASSWORD.",
    )
    parser.add_argument(
        "--ask-password",
        action="store_true",
        help="Prompt for SSH password.",
    )
    parser.add_argument(
        "--ssh-key",
        default=None,
        help="Optional SSH private key path.",
    )
    parser.add_argument(
        "--remote-root",
        default="/srv/main-computer/sites",
        help="Remote parent directory containing site folders.",
    )
    parser.add_argument(
        "--backup-root",
        default="/srv/main-computer/site-backups",
        help="Remote backup directory.",
    )
    parser.add_argument(
        "--keep-backups",
        default="10",
        help='Number of .tar.gz backups to keep per site, or "all".',
    )
    parser.add_argument(
        "--remote-upload-dir",
        default="/tmp/main-computer-publish/uploads",
        help="Remote temporary upload directory.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Create the zip locally but do not upload or modify the remote server.",
    )

    args = parser.parse_args()

    site_id = args.site_id.strip()
    validate_site_id(site_id)

    if args.keep_backups != "all":
        try:
            keep = int(args.keep_backups)
        except ValueError:
            fail("--keep-backups must be an integer or 'all'")
        if keep < 1:
            fail("--keep-backups must be at least 1")

    username, hostname = split_host(args.host)

    password = args.ssh_password
    if args.ask_password:
        password = getpass.getpass(f"SSH password for {username}@{hostname}: ")

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    with tempfile.TemporaryDirectory(prefix="main-computer-publish-") as tmp:
        local_zip = Path(tmp) / f"{site_id}-{stamp}.zip"
        zip_site_contents(args.source, local_zip)

        if args.dry_run:
            print("dry-run: upload skipped")
            return

        remote_zip = posixpath.join(
            args.remote_upload_dir.rstrip("/"),
            f"{site_id}-{stamp}.zip",
        )

        client = ssh_connect(
            host=hostname,
            port=args.port,
            username=username,
            password=password,
            key_filename=args.ssh_key,
        )

        try:
            print(f"+ sftp mkdir -p {args.remote_upload_dir}")
            sftp = client.open_sftp()
            try:
                sftp_mkdir_p(sftp, args.remote_upload_dir)
                print(f"+ sftp put {local_zip} -> {remote_zip}")
                sftp.put(str(local_zip), remote_zip)
            finally:
                sftp.close()

            run_remote_script(
                client=client,
                site_id=site_id,
                remote_root=args.remote_root,
                remote_zip=remote_zip,
                backup_root=args.backup_root,
                keep_backups=args.keep_backups,
            )

        finally:
            client.close()

    print("publish succeeded")


if __name__ == "__main__":
    main()
