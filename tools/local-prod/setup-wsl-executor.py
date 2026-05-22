from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any


def _json_default(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _run(command: list[str | Path], *, cwd: Path, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(part) for part in command],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )


def _truncate(value: str, limit: int = 2000) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


def _command_path(command: str) -> str | None:
    found = shutil.which(command)
    if found:
        return found
    candidate = Path(command)
    if candidate.exists():
        return str(candidate)
    return None


def _parse_wsl_names(stdout: str) -> list[str]:
    cleaned = (stdout or "").replace("\x00", "")
    names: list[str] = []
    for raw in cleaned.replace("\r", "\n").splitlines():
        item = raw.strip().lstrip("*").strip()
        if not item:
            continue
        if item.lower().startswith("windows subsystem for linux"):
            continue
        names.append(item)
    return names


def _host_path_to_wsl(path: Path) -> str:
    raw = str(path.resolve())
    normalized = raw.replace("\\", "/")
    match = re.match(r"^([A-Za-z]):/(.*)$", normalized)
    if match:
        return f"/mnt/{match.group(1).lower()}/{match.group(2)}"
    if normalized.startswith("//"):
        raise ValueError(f"UNC paths are not supported for WSL executor paths: {raw}")
    return normalized


def _shell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _runtime_image_file_name(distribution_name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", str(distribution_name or "").strip())
    name = re.sub(r"\s+", "-", name).strip("-. ")
    if not name:
        name = "main-computer-executor"
    return f"{name}-rootfs.tar"


def _default_wsl_runtime_root(profile: str, root: Path) -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        base = Path(local_appdata) / "MainComputer"
        return base / ("wsl" if profile == "prod" else "wsl-test")
    return root.parent / ".main-computer-runtime" / ("wsl" if profile == "prod" else "wsl-test")


def _runtime_image_for_profile(root: Path, profile: str, distribution_name: str, wsl_runtime_root: str = "", override: str = "") -> Path:
    if override:
        return Path(override).expanduser().resolve()
    if wsl_runtime_root:
        runtime_root = Path(wsl_runtime_root).expanduser().resolve()
    else:
        runtime_root = _default_wsl_runtime_root(profile, root)
    return runtime_root / "images" / _runtime_image_file_name(distribution_name)


def _status(
    *,
    ok: bool,
    state: str,
    message: str,
    **extra: Any,
) -> dict[str, Any]:
    payload = {"ok": bool(ok), "state": state, "message": message}
    payload.update(extra)
    return payload


def _list_distributions(*, root: Path, wsl_command: str) -> tuple[list[str], dict[str, Any] | None]:
    result = _run([wsl_command, "--list", "--quiet"], cwd=root, timeout_seconds=15)
    names = _parse_wsl_names(result.stdout or "")
    if result.returncode != 0:
        return names, _status(
            ok=False,
            state="wsl-list-failed",
            message="wsl.exe exists but did not list distributions",
            error=_truncate(result.stderr or result.stdout or ""),
            returncode=result.returncode,
        )
    return names, None


def _build_runtime_image_if_needed(args: argparse.Namespace, *, root: Path, image_path: Path) -> dict[str, Any] | None:
    if image_path.exists():
        return None
    if not args.build_if_missing:
        return _status(
            ok=False,
            state="missing-runtime-image",
            message=(
                "WSL runtime image is missing. Build it first or rerun setup with "
                "--build-if-missing."
            ),
            runtime_image=str(image_path),
            runtime_profile=args.runtime_profile,
        )

    builder = root / "scripts" / "windows" / "build-main-computer-runtime.ps1"
    if not builder.exists():
        return _status(
            ok=False,
            state="missing-runtime-builder",
            message="WSL runtime image is missing and the runtime builder script was not found",
            runtime_image=str(image_path),
            builder=str(builder),
            runtime_profile=args.runtime_profile,
        )

    result = _run(
        [
            args.powershell_command,
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            builder,
            "-Profile",
            args.runtime_profile,
            "-OutputPath",
            image_path,
            "-DistributionName",
            args.wsl_distribution,
        ],
        cwd=root,
        timeout_seconds=900,
    )
    if result.returncode != 0 or not image_path.exists():
        return _status(
            ok=False,
            state="runtime-build-failed",
            message="could not build missing WSL runtime image",
            runtime_image=str(image_path),
            builder=str(builder),
            runtime_profile=args.runtime_profile,
            stdout=_truncate(result.stdout or ""),
            error=_truncate(result.stderr or result.stdout or ""),
            returncode=result.returncode,
        )
    return None


def _install_distribution(args: argparse.Namespace, *, root: Path, image_path: Path) -> dict[str, Any]:
    installer = root / "scripts" / "windows" / "install-main-computer-runtime.ps1"
    if not installer.exists():
        return _status(
            ok=False,
            state="missing-runtime-installer",
            message="WSL runtime installer script was not found",
            installer=str(installer),
        )

    command: list[str | Path] = [
        args.powershell_command,
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        installer,
        "-Profile",
        args.runtime_profile,
        "-RuntimeImagePath",
        image_path,
        "-DistributionName",
        args.wsl_distribution,
        "-WslCommand",
        args.wsl_command,
    ]
    if args.wsl_runtime_root:
        command.extend(["-RuntimeRoot", args.wsl_runtime_root])
    if args.reset:
        command.append("-Reset")

    result = _run(command, cwd=root, timeout_seconds=300)
    if result.returncode != 0:
        return _status(
            ok=False,
            state="runtime-install-failed",
            message="could not install missing WSL runtime distro",
            distribution=args.wsl_distribution,
            runtime_profile=args.runtime_profile,
            runtime_image=str(image_path),
            stdout=_truncate(result.stdout or ""),
            error=_truncate(result.stderr or result.stdout or ""),
            returncode=result.returncode,
        )
    return _status(
        ok=True,
        state="installed",
        message="missing WSL runtime distro was installed",
        distribution=args.wsl_distribution,
        runtime_profile=args.runtime_profile,
        runtime_image=str(image_path),
        stdout=_truncate(result.stdout or ""),
    )


def _verify_or_repair_executor(args: argparse.Namespace, *, root: Path) -> dict[str, Any]:
    source_entrypoint = root / "docker" / "executor" / "main-computer-exec"
    contract_script = "\n".join(
        [
            "set -e",
            "rm -rf /tmp/main-computer-executor-setup",
            "mkdir -p /tmp/main-computer-executor-setup/workspace /tmp/main-computer-executor-setup/outputs",
            "rm -rf /workspace /outputs",
            "ln -s /tmp/main-computer-executor-setup/workspace /workspace",
            "ln -s /tmp/main-computer-executor-setup/outputs /outputs",
            "test -x /usr/local/bin/main-computer-exec",
            "/usr/local/bin/main-computer-exec run --cwd /workspace --timeout-ms 5000 --artifact-dir /outputs -- 'echo main-computer-exec-ready' | grep -q main-computer-exec-ready",
            "echo main-computer-exec-contract-ok",
        ]
    )

    verify = _run(
        [
            args.wsl_command,
            "--distribution",
            args.wsl_distribution,
            "--user",
            "root",
            "--exec",
            "/bin/sh",
            "-lc",
            contract_script,
        ],
        cwd=root,
        timeout_seconds=20,
    )
    if verify.returncode == 0 and "main-computer-exec-contract-ok" in (verify.stdout or ""):
        return _status(
            ok=True,
            state="ready",
            message="WSL executor entrypoint contract passed",
            distribution=args.wsl_distribution,
            verified=True,
        )

    if not source_entrypoint.exists():
        return _status(
            ok=False,
            state="contract-failed",
            message="WSL executor contract failed and repo-owned entrypoint was not found",
            distribution=args.wsl_distribution,
            repo_entrypoint=str(source_entrypoint),
            error=_truncate(verify.stderr or verify.stdout or ""),
        )

    source_wsl = _host_path_to_wsl(source_entrypoint)
    quoted_source_wsl = _shell_single_quote(source_wsl)
    repair_script = "\n".join(
        [
            "set -e",
            f"test -r {quoted_source_wsl}",
            f"cp {quoted_source_wsl} /usr/local/bin/main-computer-exec",
            "sed -i 's/\\r$//' /usr/local/bin/main-computer-exec",
            "chmod 0755 /usr/local/bin/main-computer-exec",
        ]
    )
    repair = _run(
        [
            args.wsl_command,
            "--distribution",
            args.wsl_distribution,
            "--user",
            "root",
            "--exec",
            "/bin/sh",
            "-lc",
            repair_script,
        ],
        cwd=root,
        timeout_seconds=20,
    )
    if repair.returncode != 0:
        return _status(
            ok=False,
            state="repair-failed",
            message="could not refresh WSL executor entrypoint after contract failure",
            distribution=args.wsl_distribution,
            repo_entrypoint=str(source_entrypoint),
            original_error=_truncate(verify.stderr or verify.stdout or ""),
            error=_truncate(repair.stderr or repair.stdout or ""),
        )

    verify_after = _run(
        [
            args.wsl_command,
            "--distribution",
            args.wsl_distribution,
            "--user",
            "root",
            "--exec",
            "/bin/sh",
            "-lc",
            contract_script,
        ],
        cwd=root,
        timeout_seconds=20,
    )
    ok = verify_after.returncode == 0 and "main-computer-exec-contract-ok" in (verify_after.stdout or "")
    return _status(
        ok=ok,
        state="ready" if ok else "contract-failed",
        message="WSL executor entrypoint was repaired and verified" if ok else "WSL executor contract still failed after repair",
        distribution=args.wsl_distribution,
        repo_entrypoint=str(source_entrypoint),
        repaired=True,
        error="" if ok else _truncate(verify_after.stderr or verify_after.stdout or ""),
    )


def ensure_wsl_executor(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.root).expanduser().resolve()
    wsl_path = _command_path(args.wsl_command)
    if not wsl_path:
        return _status(
            ok=False,
            state="missing-wsl-command",
            message=f"{args.wsl_command} was not found",
            wsl_command=args.wsl_command,
        )
    args.wsl_command = wsl_path

    names, list_error = _list_distributions(root=root, wsl_command=args.wsl_command)
    if list_error is not None:
        return list_error

    installed_already = args.wsl_distribution in names
    if not installed_already:
        if args.skip_install:
            return _status(
                ok=False,
                state="missing-distro",
                message="WSL distro is missing and install was skipped",
                distribution=args.wsl_distribution,
                installed_distributions=names,
            )

        image_path = _runtime_image_for_profile(
            root,
            args.runtime_profile,
            args.wsl_distribution,
            args.wsl_runtime_root,
            args.runtime_image,
        )
        image_error = _build_runtime_image_if_needed(args, root=root, image_path=image_path)
        if image_error is not None:
            return image_error

        install_state = _install_distribution(args, root=root, image_path=image_path)
        if not install_state.get("ok"):
            return install_state

        names, list_error = _list_distributions(root=root, wsl_command=args.wsl_command)
        if list_error is not None:
            return list_error
        if args.wsl_distribution not in names:
            return _status(
                ok=False,
                state="missing-after-install",
                message="WSL distro is still missing after installer completed",
                distribution=args.wsl_distribution,
                installed_distributions=names,
            )

    if installed_already and not args.verify_existing:
        return _status(
            ok=True,
            state="already-installed",
            message="WSL executor distro already exists; deep verification skipped for fast start",
            distribution=args.wsl_distribution,
            runtime_profile=args.runtime_profile,
            verified=False,
        )

    verify_state = _verify_or_repair_executor(args, root=root)
    verify_state.setdefault("runtime_profile", args.runtime_profile)
    return verify_state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Ensure only the mode-scoped Main Computer WSL executor distro. "
            "This script intentionally does not start Docker compose stacks."
        )
    )
    parser.add_argument("action", nargs="?", choices=("ensure", "setup", "status"), default="ensure")
    parser.add_argument("--root", default=".", help="Repository or installed Main Computer root.")
    parser.add_argument("--runtime-profile", choices=("test", "prod"), default="test")
    parser.add_argument(
        "--wsl-distribution",
        default=os.environ.get("MAIN_COMPUTER_EXECUTOR_WSL_DISTRIBUTION", "MainComputerExecutorTest"),
    )
    parser.add_argument("--wsl-command", default=os.environ.get("MAIN_COMPUTER_EXECUTOR_WSL_COMMAND", "wsl.exe"))
    parser.add_argument("--wsl-runtime-root", default="")
    parser.add_argument("--runtime-image", default="")
    parser.add_argument("--powershell-command", default=os.environ.get("MAIN_COMPUTER_POWERSHELL_COMMAND", "powershell.exe"))
    parser.add_argument("--build-if-missing", action="store_true")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--verify-existing", action="store_true")
    parser.add_argument("--skip-install", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.action == "status":
        args.skip_install = True
        args.verify_existing = False

    state = ensure_wsl_executor(args)
    print(json.dumps(state, indent=2, sort_keys=True, default=_json_default))
    return 0 if state.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
