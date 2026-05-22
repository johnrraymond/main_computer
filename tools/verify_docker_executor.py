#!/usr/bin/env python3
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(cmd, *, timeout=30):
    print(f"\n$ {' '.join(cmd)}")
    proc = subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if proc.stdout.strip():
        print(proc.stdout.rstrip())
    if proc.stderr.strip():
        print(proc.stderr.rstrip(), file=sys.stderr)
    print(f"[exit_code={proc.returncode}]")
    return proc


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--image",
        default="main-computer-executor:latest",
        help="Docker executor image to verify",
    )
    args = parser.parse_args()

    ok = True
    docker = shutil.which("docker")

    print("=== Docker executor quick verification ===")
    print(f"cwd: {Path.cwd()}")
    print(f"docker path: {docker}")

    if not docker:
        print("FAIL: docker CLI was not found on PATH.")
        return 2

    proc = run([docker, "--version"])
    ok = ok and proc.returncode == 0

    proc = run([docker, "info"], timeout=45)
    if proc.returncode != 0:
        print("FAIL: Docker CLI exists, but the Docker daemon is not reachable.")
        return 3

    proc = run([docker, "image", "inspect", args.image])
    image_exists = proc.returncode == 0
    ok = ok and image_exists

    if not image_exists:
        print(f"\nFAIL: required image is missing: {args.image}")
        print("\nLocal images containing 'main-computer' or 'executor':")
        run([docker, "images", "--format", "{{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}"])

        print(
            "\nNext step: build or tag the executor image as "
            f"{args.image}, then rerun this script."
        )
        return 4

    proc = run([docker, "run", "--rm", args.image, "python", "--version"])
    ok = ok and proc.returncode == 0

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        in_dir = tmp_path / "inputs"
        out_dir = tmp_path / "outputs"
        in_dir.mkdir()
        out_dir.mkdir()
        (in_dir / "hello.txt").write_text("hello from host\n", encoding="utf-8")

        proc = run(
            [
                docker,
                "run",
                "--rm",
                "-v",
                f"{in_dir.resolve()}:/inputs:ro",
                "-v",
                f"{out_dir.resolve()}:/outputs",
                args.image,
                "python",
                "-c",
                (
                    "from pathlib import Path; "
                    "text=Path('/inputs/hello.txt').read_text(); "
                    "Path('/outputs/ok.txt').write_text(text.upper()); "
                    "print(Path('/outputs/ok.txt').read_text())"
                ),
            ],
            timeout=45,
        )
        ok = ok and proc.returncode == 0

        out_file = out_dir / "ok.txt"
        if not out_file.exists():
            print("FAIL: container did not write mounted output file.")
            return 5

        print(f"mounted output: {out_file.read_text(encoding='utf-8').strip()}")

    if ok:
        print("\nPASS: Docker daemon, executor image, Python, and volume mounts all work.")
        return 0

    print("\nFAIL: one or more checks failed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())