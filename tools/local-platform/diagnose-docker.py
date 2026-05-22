from __future__ import annotations

import shutil
import subprocess
import sys


def main() -> int:
    if shutil.which("docker") is None:
        print("FAIL: docker executable was not found on PATH.")
        return 1
    completed = subprocess.run(["docker", "info"], text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        print("FAIL: docker is installed but not responding.")
        if completed.stderr:
            print(completed.stderr.strip())
        return completed.returncode or 1
    print("PASS: docker is installed and responding.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
