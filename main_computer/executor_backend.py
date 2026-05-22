from __future__ import annotations

from pathlib import Path
from typing import Any, BinaryIO, Protocol

from main_computer.executor_models import ExecutorRequest, ExecutorResult


class ExecutorBackend(Protocol):
    """Common control surface for local execution backends.

    Docker and WSL implementations should both expose this shape so route,
    tool-loop, and smoke-test code can switch backends through configuration
    instead of importing backend-specific modules.
    """

    backend_name: str
    inputs_root: Path
    outputs_root: Path
    jobs_root: Path

    def status(self) -> dict[str, Any]:
        ...

    def save_upload(
        self,
        *,
        filename: str,
        stream: BinaryIO,
        content_length: int,
        mime_type: str | None = None,
    ) -> Any:
        ...

    def list_uploads(self, *, limit: int = 200) -> list[dict[str, Any]]:
        ...

    def run(self, request: ExecutorRequest) -> ExecutorResult:
        ...

    def artifact_path(self, job_id: str, relative_path: str) -> Path:
        ...


def normalize_executor_backend(value: str | None) -> str:
    raw = str(value or "docker").strip().lower()
    aliases = {
        "": "docker",
        "container": "docker",
        "containers": "docker",
        "docker": "docker",
        "wsl": "wsl",
        "wsl2": "wsl",
        "windows-subsystem-linux": "wsl",
    }
    try:
        return aliases[raw]
    except KeyError as exc:
        allowed = ", ".join(sorted({"docker", "wsl"}))
        raise ValueError(f"Unsupported executor backend {value!r}; expected one of: {allowed}") from exc


def create_executor_backend(config: Any, *, runtime_root: Path | str) -> ExecutorBackend:
    """Build the configured executor backend.

    Docker remains the default and existing production-like path. WSL is an
    alternate local backend selected with:

        MAIN_COMPUTER_EXECUTOR_BACKEND=wsl

    Dev/test WSL users should point MAIN_COMPUTER_EXECUTOR_WSL_DISTRIBUTION at
    MainComputerExecutorTest. Production WSL can point it at MainComputerExecutor.
    """

    backend = normalize_executor_backend(getattr(config, "executor_backend", "docker"))
    if backend == "docker":
        from main_computer.docker_executor import DockerExecutor

        return DockerExecutor(
            image=getattr(config, "executor_image", "main-computer-executor:latest"),
            runtime_root=runtime_root,
            enabled=getattr(config, "executor_enabled", False),
            max_timeout_s=getattr(config, "executor_timeout_s", 120.0),
            max_upload_bytes=getattr(config, "executor_max_upload_bytes", 2 * 1024 * 1024 * 1024),
            max_output_chars=getattr(config, "executor_max_output_chars", 128_000),
        )

    if backend == "wsl":
        from main_computer.wsl_executor import WslExecutor

        return WslExecutor(
            distribution=getattr(config, "executor_wsl_distribution", "MainComputerExecutorTest"),
            wsl_command=getattr(config, "executor_wsl_command", "wsl.exe"),
            runtime_root=runtime_root,
            enabled=getattr(config, "executor_enabled", False),
            max_timeout_s=getattr(config, "executor_timeout_s", 120.0),
            max_upload_bytes=getattr(config, "executor_max_upload_bytes", 2 * 1024 * 1024 * 1024),
            max_output_chars=getattr(config, "executor_max_output_chars", 128_000),
        )

    # normalize_executor_backend should have rejected everything else.
    raise AssertionError(f"Unhandled executor backend: {backend}")
