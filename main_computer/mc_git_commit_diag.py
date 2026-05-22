#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def say(*parts: object) -> None:
    print(*parts, flush=True)


def banner(title: str) -> None:
    say("\n" + "=" * 90)
    say(title)
    say("=" * 90)


def run_git(repo: Path, args: list[str], timeout: float = 5.0, binary: bool = False) -> dict:
    cmd = ["git", *args]
    say(f"\n>>> BEFORE: {' '.join(cmd)}")
    say(f"    cwd={repo}")
    started = time.monotonic()

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(repo),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=not binary,
            encoding=None if binary else "utf-8",
            errors=None if binary else "replace",
        )
    except Exception as exc:
        say(f"<<< START FAILED: {type(exc).__name__}: {exc}")
        return {
            "ok": False,
            "returncode": None,
            "stdout": b"" if binary else "",
            "stderr": str(exc),
            "timed_out": False,
            "duration": round(time.monotonic() - started, 3),
        }

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        duration = round(time.monotonic() - started, 3)
        say(f"<<< AFTER: rc={proc.returncode} duration={duration}s")
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": stdout or (b"" if binary else ""),
            "stderr": stderr or "",
            "timed_out": False,
            "duration": duration,
        }
    except subprocess.TimeoutExpired:
        duration = round(time.monotonic() - started, 3)
        say(f"<<< TIMEOUT after {duration}s; killing process")
        try:
            proc.kill()
        except Exception as exc:
            say(f"    kill failed: {type(exc).__name__}: {exc}")
        try:
            stdout, stderr = proc.communicate(timeout=2)
        except Exception:
            stdout, stderr = (b"" if binary else ""), ""
        return {
            "ok": False,
            "returncode": proc.returncode,
            "stdout": stdout or (b"" if binary else ""),
            "stderr": stderr or "TIMEOUT",
            "timed_out": True,
            "duration": duration,
        }


def show_text_result(label: str, result: dict, limit: int = 2000) -> None:
    banner(label)
    say("ok:", result["ok"])
    say("returncode:", result["returncode"])
    say("timed_out:", result["timed_out"])
    say("duration:", result["duration"])

    stdout = result["stdout"]
    stderr = result["stderr"]

    if isinstance(stdout, bytes):
        say("stdout_bytes:", len(stdout))
        preview = stdout[:limit].replace(b"\0", b"\\0").decode("utf-8", "replace")
    else:
        say("stdout_bytes:", len(stdout.encode("utf-8", "replace")))
        preview = stdout[:limit].replace("\0", "\\0")

    if preview:
        say("stdout preview:")
        say(preview)

    if stderr:
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        say("stderr:")
        say(str(stderr)[:limit])


def split_z(raw: bytes) -> list[str]:
    return [
        item.decode("utf-8", "replace").replace("\\", "/")
        for item in raw.split(b"\0")
        if item
    ]


def check_python_environment() -> None:
    banner("Python environment")
    say("BEFORE sys.version")
    say("python:", sys.version.replace("\n", " "))
    say("AFTER sys.version")

    # Avoid platform.platform(); it can unexpectedly stall on some Windows setups.
    say("executable:", sys.executable)
    say("cwd:", os.getcwd())
    say("argv:", sys.argv)


def resolve_root(repo: Path) -> Path | None:
    result = run_git(repo, ["rev-parse", "--show-toplevel"], timeout=5)
    show_text_result("rev-parse --show-toplevel", result)
    if not result["ok"]:
        return None
    root_text = result["stdout"].strip() if isinstance(result["stdout"], str) else result["stdout"].decode("utf-8", "replace").strip()
    return Path(root_text).resolve()


def inspect_fast_git(root: Path) -> None:
    banner("Fast Git probes")

    probes = [
        ("git version", ["--version"]),
        ("branch", ["branch", "--show-current"]),
        ("HEAD", ["rev-parse", "--short", "HEAD"]),
        ("user.name", ["config", "--get", "user.name"]),
        ("user.email", ["config", "--get", "user.email"]),
    ]

    for label, args in probes:
        result = run_git(root, args, timeout=5)
        show_text_result(label, result)


def inspect_locks(root: Path) -> None:
    banner("Git lock probe")

    git_dir_res = run_git(root, ["rev-parse", "--git-dir"], timeout=5)
    show_text_result("rev-parse --git-dir", git_dir_res)

    if not git_dir_res["ok"]:
        return

    git_dir_text = git_dir_res["stdout"].strip()
    git_dir = Path(git_dir_text)
    if not git_dir.is_absolute():
        git_dir = root / git_dir
    git_dir = git_dir.resolve()

    say("resolved git_dir:", git_dir)

    lock_names = [
        "index.lock",
        "HEAD.lock",
        "packed-refs.lock",
        "config.lock",
        "logs/HEAD.lock",
    ]

    any_lock = False
    for name in lock_names:
        path = git_dir / name
        say("checking lock:", path)
        if path.exists():
            any_lock = True
            stat = path.stat()
            say(f"LOCK FOUND: {path} size={stat.st_size} mtime={time.ctime(stat.st_mtime)}")

    refs = git_dir / "refs"
    say("checking refs locks under:", refs)
    if refs.exists():
        for path in refs.rglob("*.lock"):
            any_lock = True
            stat = path.stat()
            say(f"LOCK FOUND: {path} size={stat.st_size} mtime={time.ctime(stat.st_mtime)}")

    if not any_lock:
        say("No common Git lock files found.")


def inspect_runner_source(root: Path) -> None:
    banner("Runner source probe")

    path = root / "main_computer" / "git_commit.py"
    say("checking:", path)

    if not path.exists():
        say("main_computer/git_commit.py not found in this repo.")
        return

    say("BEFORE read_text")
    text = path.read_text(encoding="utf-8", errors="replace")
    say("AFTER read_text; chars:", len(text))

    old_poll_loop = "while process.poll() is None:" in text and "time.sleep(0.05)" in text
    communicate_loop = "communicate(timeout=0.2)" in text or "communicate(timeout = 0.2)" in text
    nul_command = "diff --cached --name-only -z" in text or '"-z"' in text

    say("old poll-before-drain pattern present:", old_poll_loop)
    say("communicate timeout drain pattern present:", communicate_loop)
    say("uses NUL-output Git commands:", nul_command)

    if old_poll_loop and not communicate_loop:
        say("DIAGNOSIS: pipe-buffer deadlock risk may still be present.")
    elif communicate_loop:
        say("DIAGNOSIS: pipe-drain fix appears installed.")
    else:
        say("DIAGNOSIS: subprocess pattern not recognized.")


def inspect_index(root: Path, selected: list[str]) -> None:
    banner("Index probe")

    staged_res = run_git(root, ["diff", "--cached", "--name-only", "-z", "--"], timeout=10, binary=True)
    show_text_result("diff --cached --name-only -z", staged_res)

    if staged_res["timed_out"]:
        say("DIAGNOSIS: `git diff --cached --name-only -z` itself timed out.")
        say("That suggests Git/index/lock/filesystem contention, not just UI formatting.")
        return

    raw = staged_res["stdout"]
    assert isinstance(raw, bytes)
    staged = split_z(raw)

    say("\nstaged_count:", len(staged))
    for item in staged[:100]:
        say("STAGED:", item)
    if len(staged) > 100:
        say(f"... {len(staged) - 100} more staged paths omitted")

    selected_norm = [p.replace("\\", "/").strip("/") for p in selected]
    staged_set = set(staged)
    selected_set = set(selected_norm)

    say("\nselected:", selected_norm)
    say("selected_already_staged:", sorted(staged_set & selected_set))
    say("staged_not_selected_count:", len(staged_set - selected_set))

    if staged_set:
        say("\nPRIMARY DIAGNOSIS:")
        say("The selected-file commit runner is refusing because the Git index already has staged files.")
        say("This is expected protection against accidentally committing unrelated staged files.")

    for path in selected_norm:
        res = run_git(root, ["status", "--porcelain=v1", "-z", "--", path], timeout=10, binary=True)
        raw_status = res["stdout"]
        assert isinstance(raw_status, bytes)
        say(f"\nselected_status for {path}:")
        say(raw_status.replace(b"\0", b"\\0").decode("utf-8", "replace") or "(clean/no status output)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo", nargs="?", default=".", help="Repo path")
    parser.add_argument("--selected", action="append", default=[], help="Selected file path. Repeatable.")
    args = parser.parse_args()

    check_python_environment()

    repo = Path(args.repo).resolve()
    selected = args.selected or [".dockerignore"]

    banner("Repo selection")
    say("input repo:", args.repo)
    say("resolved repo:", repo)
    say("selected:", selected)

    root = resolve_root(repo)
    if root is None:
        say("Could not resolve Git root.")
        return 2

    banner("Resolved Git root")
    say(root)

    inspect_fast_git(root)
    inspect_locks(root)
    inspect_runner_source(root)
    inspect_index(root, selected)

    banner("Suggested next step")
    say("If staged_count > 0, unstage before using selected-file commit mode:")
    say("  git restore --staged .")
    say("")
    say("Then rerun the selected-file commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())