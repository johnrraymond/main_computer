#!/usr/bin/env python3
import argparse
import hashlib
import subprocess
from pathlib import Path


def run(cmd, *, input_bytes=None):
    p = subprocess.run(
        cmd,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def container_sha(container: str, path: str) -> str | None:
    rc, out, err = run([
        "docker", "exec", "--user", "root", container,
        "sh", "-lc", f"sha256sum {path!r} 2>/dev/null | awk '{{print $1}}'",
    ])
    if rc != 0:
        print(f"Could not hash container file {path}: {err.strip()}")
        return None
    value = out.strip()
    return value or None


def stream_file_to_container(container: str, src: Path, dest: str):
    data = src.read_bytes()
    rc, out, err = run([
        "docker", "exec", "-i", "--user", "root", container,
        "sh", "-lc", f"cat > {dest!r}",
    ], input_bytes=data)
    if rc != 0:
        raise SystemExit(f"stream write failed for {src} -> {dest}\nSTDERR:\n{err}")
    return sha256_bytes(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--container", default="mc-applications-coolify")
    ap.add_argument("--service-uuid", default="v11lha4aoh6sd1msc0z6i7dc")
    ap.add_argument("--source-dir", default="deploy/local-platform/site-server")
    ap.add_argument("--write", action="store_true",
                    help="Actually rewrite staged files using root byte-streaming.")
    args = ap.parse_args()

    source_dir = Path(args.source_dir)
    target_dir = f"/data/coolify/services/{args.service_uuid}/site-server"

    files = ["Dockerfile", "app.py"]

    print(f"Container:  {args.container}")
    print(f"Source dir: {source_dir.resolve()}")
    print(f"Target dir: {target_dir}")
    print()

    rc, out, err = run([
        "docker", "exec", "--user", "root", args.container,
        "sh", "-lc", f"mkdir -p {target_dir!r} && test -w {target_dir!r}",
    ])
    if rc != 0:
        raise SystemExit(f"target dir is not root-writable:\n{err}")

    for name in files:
        src = source_dir / name
        dest = f"{target_dir}/{name}"

        if not src.exists():
            raise SystemExit(f"missing local source file: {src}")

        local_sha = sha256_bytes(src.read_bytes())
        before_sha = container_sha(args.container, dest)

        print(f"{name}:")
        print(f"  local:          {local_sha}")
        print(f"  staged before:  {before_sha or '<missing>'}")

        if args.write:
            written_sha = stream_file_to_container(args.container, src, dest)
            after_sha = container_sha(args.container, dest)
            print(f"  streamed:       {written_sha}")
            print(f"  staged after:   {after_sha or '<missing>'}")
            print(f"  result:         {'OK' if after_sha == local_sha else 'MISMATCH'}")
        else:
            print(f"  current result: {'OK' if before_sha == local_sha else 'MISMATCH'}")

        print()

    if not args.write:
        print("Read-only check complete.")
        print("To test the proposed fix path, run again with --write.")
    else:
        print("Write check complete. If both files are OK, the byte-stream/root staging plan is valid.")


if __name__ == "__main__":
    main()