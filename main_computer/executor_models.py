from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


EXECUTOR_RUNTIME_ENTRYPOINT = "/usr/local/bin/main-computer-exec"


def build_executor_runtime_command(
    request: "ExecutorRequest",
    *,
    timeout_s: float,
    artifact_dir: str = "/outputs",
    entrypoint: str = EXECUTOR_RUNTIME_ENTRYPOINT,
) -> list[str]:
    """Build the shared runtime entrypoint argv for every executor backend.

    Docker and WSL may use different transports, mounts, and process launchers, but
    they should both cross the same logical runtime boundary:

        /usr/local/bin/main-computer-exec run --cwd /workspace --timeout-ms ... --artifact-dir /outputs -- <command>
    """

    timeout_ms = max(1, int(round(float(timeout_s) * 1000)))
    return [
        entrypoint,
        "run",
        "--cwd",
        request.cwd,
        "--timeout-ms",
        str(timeout_ms),
        "--artifact-dir",
        artifact_dir,
        "--",
        request.command,
    ]


@dataclass(frozen=True)
class ExecutorRequest:
    """Validated request for the isolated Linux executor.

    The first implementation intentionally supports shell execution only as a
    backend-controlled primitive. AI/tool-loop integration should pass through
    this same request shape later rather than invoking Docker directly.
    """

    command: str
    cwd: str = "/workspace"
    timeout_s: float = 60.0
    input_ids: list[str] = field(default_factory=list)
    artifact_globs: list[str] = field(default_factory=lambda: ["**/*"])
    network: bool = False
    description: str = ""
    env: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(
        cls,
        data: dict[str, Any],
        *,
        max_timeout_s: float,
        max_command_chars: int = 12000,
    ) -> "ExecutorRequest":
        command = str(data.get("command", "") or "").strip()
        if not command:
            raise ValueError("Command is required.")
        if len(command) > max_command_chars:
            raise ValueError(f"Command is limited to {max_command_chars} characters.")

        cwd = str(data.get("cwd", "/workspace") or "/workspace").strip() or "/workspace"
        if not cwd.startswith("/workspace"):
            raise ValueError("cwd must be /workspace or a child path.")
        if ".." in [part for part in cwd.split("/") if part]:
            raise ValueError("cwd must not contain '..' path segments.")

        timeout_s = _coerce_float(data.get("timeout_s", 60.0), default=60.0)
        timeout_s = max(1.0, min(float(max_timeout_s), timeout_s))

        input_ids = _coerce_string_list(data.get("input_ids", data.get("inputs", [])), max_items=64)
        artifact_globs = _coerce_string_list(data.get("artifact_globs", ["**/*"]), max_items=32) or ["**/*"]
        env = _coerce_env(data.get("env", {}))

        return cls(
            command=command,
            cwd=cwd,
            timeout_s=timeout_s,
            input_ids=input_ids,
            artifact_globs=artifact_globs,
            network=_coerce_bool(data.get("network"), default=False),
            description=str(data.get("description", "") or "")[:500],
            env=env,
            metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutorArtifact:
    name: str
    relative_path: str
    size: int
    download_url: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutorResult:
    ok: bool
    job_id: str
    command: str
    cwd: str
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int
    artifacts: list[ExecutorArtifact] = field(default_factory=list)
    error: str | None = None
    backend: str = ""

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["artifacts"] = [artifact.as_dict() for artifact in self.artifacts]
        return data


def _coerce_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _coerce_string_list(value: Any, *, max_items: int) -> list[str]:
    if value is None:
        return []
    raw_items = value if isinstance(value, (list, tuple, set)) else [value]
    items: list[str] = []
    for raw in raw_items:
        text = str(raw or "").strip()
        if text and text not in items:
            items.append(text[:500])
        if len(items) >= max_items:
            break
    return items


def _coerce_env(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    env: dict[str, str] = {}
    for key, raw in value.items():
        name = str(key or "").strip()
        if not name or len(name) > 80:
            continue
        if not name.replace("_", "").isalnum() or name[0].isdigit():
            continue
        env[name] = str(raw or "")[:4000]
        if len(env) >= 64:
            break
    return env
