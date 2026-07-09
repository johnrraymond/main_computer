from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence


Runner = Callable[..., subprocess.CompletedProcess[str]]


class ContainerRuntimeResolutionError(RuntimeError):
    """Raised when an explicitly requested container runtime cannot be used."""


_CONTAINER_COMMAND_ENV = (
    "MAIN_COMPUTER_CONTAINER_COMMAND",
    "MAIN_COMPUTER_DOCKER_COMMAND",
    "MAIN_COMPUTER_DOCKER",
)

_CONTAINER_COMPOSE_ENV = (
    "MAIN_COMPUTER_CONTAINER_COMPOSE_COMMAND",
    "MAIN_COMPUTER_DOCKER_COMPOSE",
    "MAIN_COMPUTER_DOCKER_COMPOSE_COMMAND",
)


# NSIS payload staging marker compatibility: podman compose delegated to Docker Compose
# NSIS payload staging marker compatibility: PODMAN_COMPOSE_PROVIDER

@dataclass(frozen=True)
class ContainerRuntime:
    """Resolved container CLI commands for Docker-compatible call sites.

    ``runtime`` is intentionally descriptive rather than authoritative for custom
    wrappers.  The command lists are the source of truth for process execution.
    """

    runtime: str
    container_command: tuple[str, ...]
    compose_command: tuple[str, ...]
    source: str = "auto"

    def container_args(self, *args: object) -> list[str]:
        return [*self.container_command, *map(str, args)]

    def compose_args(self, *args: object) -> list[str]:
        return [*self.compose_command, *map(str, args)]

    def as_dict(self) -> dict[str, object]:
        return {
            "runtime": self.runtime,
            "container_command": list(self.container_command),
            "compose_command": list(self.compose_command),
            "source": self.source,
        }


def podman_command_cwd(cwd: Path | str | None, *, environ: dict[str, str] | None = None) -> Path | None:
    """Return a safe current directory for Podman helper processes.

    Podman Desktop can leave ``win-sshproxy.exe`` running with an open handle on
    its launch directory.  When the launch directory is the managed install
    root, later installer refreshes cannot move that tree aside.  Docker is not
    affected, so this helper is intentionally Podman-specific.
    """

    env = environ if environ is not None else os.environ
    override = str(env.get("MAIN_COMPUTER_PODMAN_COMMAND_CWD") or "").strip()
    if override:
        path = Path(override).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    if cwd is None:
        return None

    workdir = Path(cwd).expanduser().resolve()
    safe = workdir.parent
    safe.mkdir(parents=True, exist_ok=True)
    return safe


def _runtime_command_cwd(runtime: str, cwd: Path | None, *, env: dict[str, str]) -> Path | None:
    if runtime == "podman":
        return podman_command_cwd(cwd, environ=env)
    return cwd


def split_command_override(value: str | Sequence[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(part) for part in value if str(part)]
    text = str(value or "").strip()
    if not text:
        return []
    try:
        if re.match(r"^(?:[A-Za-z]:\\|\\\\)", text):
            return [part.strip('"') for part in shlex.split(text, posix=False) if part.strip('"')]
        return shlex.split(text)
    except ValueError:
        return text.split()


def command_display(command: Sequence[object] | object) -> str:
    if isinstance(command, (list, tuple)):
        return " ".join(shlex.quote(str(part)) for part in command)
    return str(command)


def _first_env_command(names: Iterable[str], environ: dict[str, str]) -> tuple[str, list[str]]:
    for name in names:
        command = split_command_override(environ.get(name, ""))
        if command:
            return name, command
    return "", []


def _normalize_runtime(value: str | None) -> str:
    text = str(value or "auto").strip().lower()
    aliases = {
        "": "auto",
        "default": "auto",
        "detect": "auto",
        "container": "auto",
        "containers": "auto",
        "docker": "docker",
        "docker-desktop": "docker",
        "podman": "podman",
        "podman-desktop": "podman",
    }
    return aliases.get(text, text)


def _command_executable(command: Sequence[str]) -> str:
    if not command:
        return ""
    # ``Path(...).name`` only understands the current platform's separators.
    # Main Computer is frequently configured from Windows paths even when tests
    # run elsewhere, so normalize backslashes before extracting the executable.
    executable = Path(str(command[0]).replace("\\", "/")).name.lower()
    if executable.endswith(".exe"):
        executable = executable[:-4]
    return executable


def _infer_runtime_from_command(command: Sequence[str]) -> str:
    executable = _command_executable(command)
    if executable in {"docker", "docker-compose"}:
        return "docker"
    if executable in {"podman", "podman-compose"}:
        return "podman"
    return ""


def _is_compose_command(command: Sequence[str]) -> bool:
    executable = _command_executable(command)
    if executable in {"docker-compose", "podman-compose"}:
        return True
    return any(str(part) == "compose" for part in command[1:])


def _compose_candidates(runtime: str, *, container_command: Sequence[str] | None = None) -> list[list[str]]:
    candidates: list[list[str]] = []
    container = [str(part) for part in (container_command or []) if str(part)]
    executable = _command_executable(container)

    if runtime == "podman":
        # Prefer the standalone podman-compose command.  On Windows, `podman
        # compose` commonly delegates to an external Docker Compose provider,
        # which can silently route Podman call sites through Docker Desktop.
        # Keep `podman compose` as a last probe only so real Podman setups that
        # do not delegate can still work.
        candidates.append(["podman-compose"])
        if container and not _is_compose_command(container) and executable == "podman":
            candidates.append([*container, "compose"])
        candidates.append(["podman", "compose"])
    else:
        if container and not _is_compose_command(container) and executable == "docker":
            candidates.append([*container, "compose"])
        candidates.extend([["docker", "compose"], ["docker-compose"]])

    deduped: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for candidate in candidates:
        key = tuple(candidate)
        if key not in seen:
            seen.add(key)
            deduped.append(candidate)
    return deduped


def _existing_path_command(candidates: Iterable[Path]) -> list[str]:
    for candidate in candidates:
        try:
            if candidate.is_file():
                return [str(candidate)]
        except OSError:
            continue
    return []


def _windows_program_dir_candidates(env: dict[str, str], variable_name: str) -> list[Path]:
    value = env.get(variable_name, "")
    return [Path(value)] if value else []


def _local_appdata_candidates(env: dict[str, str]) -> list[Path]:
    value = env.get("LOCALAPPDATA", "")
    return [Path(value)] if value else []


def _default_container_command(runtime: str, env: dict[str, str] | None = None) -> list[str]:
    environ = env if env is not None else os.environ
    if runtime == "podman":
        resolved = _existing_path_command(
            [
                *(
                    base / "Programs" / "Podman" / "podman.exe"
                    for base in _local_appdata_candidates(environ)
                ),
                *(
                    base / "RedHat" / "Podman" / "podman.exe"
                    for base in _windows_program_dir_candidates(environ, "ProgramFiles")
                ),
                *(
                    base / "Podman" / "podman.exe"
                    for base in _windows_program_dir_candidates(environ, "ProgramFiles")
                ),
                *(
                    base / "RedHat" / "Podman" / "podman.exe"
                    for base in _windows_program_dir_candidates(environ, "ProgramFiles(x86)")
                ),
                *(
                    base / "Podman" / "podman.exe"
                    for base in _windows_program_dir_candidates(environ, "ProgramFiles(x86)")
                ),
            ]
        )
        return resolved or ["podman"]

    resolved = _existing_path_command(
        [
            *(
                base / "Docker" / "Docker" / "resources" / "bin" / "docker.exe"
                for base in _windows_program_dir_candidates(environ, "ProgramFiles")
            ),
            *(
                base / "Docker" / "Docker" / "resources" / "bin" / "docker.exe"
                for base in _windows_program_dir_candidates(environ, "ProgramFiles(x86)")
            ),
        ]
    )
    return resolved or ["docker"]


def _completed_result(
    command: list[str],
    *,
    runner: Runner,
    cwd: Path | None,
    timeout: float,
) -> tuple[bool, int | None, str]:
    try:
        completed = runner(
            command,
            cwd=str(cwd) if cwd is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return False, None, str(exc)
    except OSError as exc:
        return False, None, str(exc)
    except subprocess.TimeoutExpired as exc:
        return False, None, f"timed out after {timeout:g}s: {exc}"

    returncode = int(getattr(completed, "returncode", 1))
    output = "\n".join(
        part.strip()
        for part in (getattr(completed, "stdout", "") or "", getattr(completed, "stderr", "") or "")
        if part and part.strip()
    )
    return returncode == 0, returncode, output


def _podman_compose_delegated_to_docker(command: Sequence[str], output: str) -> bool:
    """Return true when a Podman compose probe is actually using Docker Compose.

    Podman for Windows can print a warning such as
    ``Executing external compose provider "...\\Docker\\...\\docker-compose.exe"``.
    That command may return success for ``version`` but later fail against the
    Podman socket.  Treat it as unavailable so Podman remains a boring backend
    instead of depending on Docker Desktop's Compose provider.
    """

    if _infer_runtime_from_command(command) != "podman":
        return False
    if not _is_compose_command(command):
        return False
    lowered = str(output or "").lower()
    return (
        "external compose provider" in lowered
        and ("docker-compose" in lowered or "docker\\docker" in lowered or "docker/docker" in lowered)
    )


def _compose_probe_ok(
    runtime: str,
    command: list[str],
    *,
    runner: Runner,
    cwd: Path | None,
    timeout: float,
) -> bool:
    ok, _returncode, output = _completed_result([*command, "version"], runner=runner, cwd=cwd, timeout=timeout)
    if not ok:
        return False
    if runtime == "podman" and _podman_compose_delegated_to_docker(command, output):
        return False
    return True


def _completed_ok(command: list[str], *, runner: Runner, cwd: Path | None, timeout: float) -> bool:
    ok, _returncode, _output = _completed_result(command, runner=runner, cwd=cwd, timeout=timeout)
    return ok


def _raise_unavailable(
    *,
    runtime: str,
    command: Sequence[str],
    probe_args: Sequence[str],
    runner: Runner,
    cwd: Path | None,
    timeout: float,
    env_name: str,
    command_role: str,
) -> None:
    probe_command = [*map(str, command), *map(str, probe_args)]
    ok, returncode, output = _completed_result(probe_command, runner=runner, cwd=cwd, timeout=timeout)
    if ok:
        return

    detail = output.strip() if output else "no output"
    exit_detail = "command was not found" if returncode is None else f"exit code {returncode}"
    raise ContainerRuntimeResolutionError(
        f"{env_name}={runtime} requires a working {command_role} command "
        f"({command_display(command)}), but `{command_display(probe_command)}` failed: "
        f"{exit_detail}; {detail}"
    )


def _explicit_runtime_env(environ: dict[str, str]) -> str:
    requested = _normalize_runtime(environ.get("MAIN_COMPUTER_CONTAINER_RUNTIME"))
    return requested if requested in {"docker", "podman"} else ""


def _validate_explicit_runtime(
    runtime: str,
    *,
    container: Sequence[str],
    compose: Sequence[str],
    runner: Runner,
    cwd: Path | None,
    timeout: float,
    env_name: str = "MAIN_COMPUTER_CONTAINER_RUNTIME",
    require_compose: bool = True,
) -> None:
    _raise_unavailable(
        runtime=runtime,
        command=container,
        probe_args=("version",),
        runner=runner,
        cwd=cwd,
        timeout=timeout,
        env_name=env_name,
        command_role="container CLI",
    )

    if not require_compose:
        return

    if runtime == "podman" and _infer_runtime_from_command(compose) == "docker":
        raise ContainerRuntimeResolutionError(
            f"{env_name}=podman requires a Podman-compatible Compose command, "
            f"but resolved Compose command is Docker-based: {command_display(compose)}"
        )

    probe_command = [*map(str, compose), "version"]
    ok, returncode, output = _completed_result(probe_command, runner=runner, cwd=cwd, timeout=timeout)
    if ok and not _podman_compose_delegated_to_docker(compose, output):
        return

    detail = output.strip() if output else "no output"
    if ok:
        exit_detail = "delegated to Docker Compose"
    else:
        exit_detail = "command was not found" if returncode is None else f"exit code {returncode}"
    raise ContainerRuntimeResolutionError(
        f"{env_name}={runtime} requires a working Compose CLI command "
        f"({command_display(compose)}), but `{command_display(probe_command)}` failed: "
        f"{exit_detail}; {detail}"
    )

def _first_working_compose(
    runtime: str,
    *,
    runner: Runner,
    cwd: Path | None,
    timeout: float,
    container_command: Sequence[str] | None = None,
    probe: bool = True,
) -> list[str]:
    candidates = _compose_candidates(runtime, container_command=container_command)
    if not probe:
        return candidates[0]
    for candidate in candidates:
        if _compose_probe_ok(runtime, candidate, runner=runner, cwd=cwd, timeout=timeout):
            return candidate
    return candidates[0]


def _runtime_has_working_compose(
    runtime: str,
    *,
    runner: Runner,
    cwd: Path | None,
    timeout: float,
) -> tuple[bool, list[str]]:
    command = _first_working_compose(runtime, runner=runner, cwd=cwd, timeout=timeout)
    ok = _compose_probe_ok(runtime, command, runner=runner, cwd=cwd, timeout=timeout)
    return ok, command


def resolve_container_runtime(
    *,
    cwd: Path | str | None = None,
    runner: Runner | None = None,
    environ: dict[str, str] | None = None,
    timeout: float = 3.0,
    container_command: str | Sequence[str] | None = None,
    compose_command: str | Sequence[str] | None = None,
    probe: bool = True,
    require_compose: bool = True,
) -> ContainerRuntime:
    """Resolve Docker- or Podman-compatible command lines.

    Environment controls, highest priority first:

    - ``MAIN_COMPUTER_CONTAINER_RUNTIME=docker|podman|auto``
    - ``MAIN_COMPUTER_CONTAINER_COMMAND`` for direct CLI calls.
    - ``MAIN_COMPUTER_CONTAINER_COMPOSE_COMMAND`` for Compose calls.
    - Legacy Docker-named overrides remain supported:
      ``MAIN_COMPUTER_DOCKER_COMMAND``, ``MAIN_COMPUTER_DOCKER``,
      ``MAIN_COMPUTER_DOCKER_COMPOSE``, and
      ``MAIN_COMPUTER_DOCKER_COMPOSE_COMMAND``.

    ``container_command`` and ``compose_command`` are explicit call-site
    overrides. They are primarily used to keep older ``--docker-command`` CLI
    options working while still allowing Podman via the new environment names.

    Call sites that only use direct container commands can set
    ``require_compose=False``.  That still validates an explicitly requested
    container CLI, but it does not fail Podman just because ``podman-compose`` is
    absent from the caller's PATH.
    """

    env = environ if environ is not None else os.environ
    run = runner or subprocess.run
    workdir = Path(cwd).resolve() if cwd is not None else None
    requested = _normalize_runtime(env.get("MAIN_COMPUTER_CONTAINER_RUNTIME"))
    explicit_runtime = _explicit_runtime_env(env)

    explicit_container = split_command_override(container_command)
    explicit_compose = split_command_override(compose_command)
    container_env_name, env_container = _first_env_command(_CONTAINER_COMMAND_ENV, env)
    compose_env_name, env_compose = _first_env_command(_CONTAINER_COMPOSE_ENV, env)

    container_override = explicit_container or env_container
    compose_override = explicit_compose or env_compose

    inferred = (
        _infer_runtime_from_command(compose_override)
        or _infer_runtime_from_command(container_override)
    )
    if requested not in {"auto", "docker", "podman"}:
        requested = inferred or "auto"

    if requested == "auto" and inferred:
        requested = inferred

    if requested in {"docker", "podman"}:
        runtime = requested
        container = container_override or _default_container_command(runtime, env=env)
        command_cwd = _runtime_command_cwd(runtime, workdir, env=env)
        compose = compose_override or _first_working_compose(
            runtime,
            runner=run,
            cwd=command_cwd,
            timeout=timeout,
            container_command=container,
            probe=probe if require_compose else False,
        )
        if explicit_runtime:
            _validate_explicit_runtime(
                runtime,
                container=container,
                compose=compose,
                runner=run,
                cwd=command_cwd,
                timeout=timeout,
                require_compose=require_compose,
            )
        sources = []
        if explicit_container:
            sources.append("argument-container-command")
        elif container_env_name:
            sources.append(container_env_name)
        if explicit_compose:
            sources.append("argument-compose-command")
        elif compose_env_name:
            sources.append(compose_env_name)
        return ContainerRuntime(
            runtime=runtime,
            container_command=tuple(container),
            compose_command=tuple(compose),
            source="+".join(sources) or f"{runtime}-preference",
        )

    if not probe:
        runtime = inferred or "docker"
        container = container_override or _default_container_command(runtime, env=env)
        command_cwd = _runtime_command_cwd(runtime, workdir, env=env)
        compose = compose_override or _first_working_compose(
            runtime,
            runner=run,
            cwd=command_cwd,
            timeout=timeout,
            container_command=container,
            probe=False,
        )
        return ContainerRuntime(
            runtime=runtime,
            container_command=tuple(container),
            compose_command=tuple(compose),
            source="fallback",
        )

    for runtime in ("docker", "podman"):
        command_cwd = _runtime_command_cwd(runtime, workdir, env=env)
        compose_ok, compose = _runtime_has_working_compose(runtime, runner=run, cwd=command_cwd, timeout=timeout)
        if compose_ok:
            return ContainerRuntime(
                runtime=runtime,
                container_command=tuple(_default_container_command(runtime, env=env)),
                compose_command=tuple(compose),
                source="auto-detected",
            )

    # Preserve Docker as the historical default when neither runtime probes cleanly.
    return ContainerRuntime(
        runtime="docker",
        container_command=tuple(container_override or ["docker"]),
        compose_command=tuple(compose_override or ["docker", "compose"]),
        source="fallback",
    )


def legacy_docker_command_override(value: str | Sequence[str] | None) -> list[str]:
    """Return a legacy ``--docker-command`` value only when it is truly custom.

    Existing services pass ``docker`` as the historical default. Treat that value
    as "no explicit override" so MAIN_COMPUTER_CONTAINER_RUNTIME=podman can still
    switch the runtime without requiring every old CLI option to change.
    """

    command = split_command_override(value)
    if command == ["docker"]:
        return []
    return command


def main(argv: Sequence[str] | None = None) -> int:
    """Small CLI used by Windows launch scripts for early runtime preflight."""

    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(description="Resolve the Main Computer Docker/Podman runtime.")
    parser.add_argument("--check", action="store_true", help="resolve the runtime and fail if an explicit runtime is unavailable")
    parser.add_argument("--cwd", default=None, help="working directory used while probing compose")
    parser.add_argument("--no-probe", action="store_true", help="skip auto-detection probes unless an explicit runtime must be validated")
    parser.add_argument("--json", action="store_true", help="print the resolved runtime as JSON")
    args = parser.parse_args(argv)

    try:
        runtime = resolve_container_runtime(cwd=args.cwd, probe=not args.no_probe)
    except ContainerRuntimeResolutionError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(runtime.as_dict(), sort_keys=True))
    else:
        print(command_display(runtime.container_command))
        print(command_display(runtime.compose_command))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
