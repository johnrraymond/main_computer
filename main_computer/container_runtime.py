from __future__ import annotations

import os
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


def split_command_override(value: str | Sequence[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(part) for part in value if str(part)]
    text = str(value or "").strip()
    if not text:
        return []
    try:
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
    executable = Path(str(command[0])).name.lower()
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
    if container and not _is_compose_command(container):
        executable = _command_executable(container)
        if executable in {"docker", "podman"}:
            candidates.append([*container, "compose"])

    if runtime == "podman":
        candidates.extend([["podman", "compose"], ["podman-compose"]])
    else:
        candidates.extend([["docker", "compose"], ["docker-compose"]])

    deduped: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for candidate in candidates:
        key = tuple(candidate)
        if key not in seen:
            seen.add(key)
            deduped.append(candidate)
    return deduped


def _default_container_command(runtime: str) -> list[str]:
    return ["podman"] if runtime == "podman" else ["docker"]


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
    _raise_unavailable(
        runtime=runtime,
        command=compose,
        probe_args=("version",),
        runner=runner,
        cwd=cwd,
        timeout=timeout,
        env_name=env_name,
        command_role="Compose CLI",
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
        if _completed_ok([*candidate, "version"], runner=runner, cwd=cwd, timeout=timeout):
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
    ok = _completed_ok([*command, "version"], runner=runner, cwd=cwd, timeout=timeout)
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
        container = container_override or _default_container_command(runtime)
        compose = compose_override or _first_working_compose(
            runtime,
            runner=run,
            cwd=workdir,
            timeout=timeout,
            container_command=container,
            probe=probe,
        )
        if explicit_runtime:
            _validate_explicit_runtime(
                runtime,
                container=container,
                compose=compose,
                runner=run,
                cwd=workdir,
                timeout=timeout,
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
        container = container_override or _default_container_command(runtime)
        compose = compose_override or _first_working_compose(
            runtime,
            runner=run,
            cwd=workdir,
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
        compose_ok, compose = _runtime_has_working_compose(runtime, runner=run, cwd=workdir, timeout=timeout)
        if compose_ok:
            return ContainerRuntime(
                runtime=runtime,
                container_command=tuple(_default_container_command(runtime)),
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
