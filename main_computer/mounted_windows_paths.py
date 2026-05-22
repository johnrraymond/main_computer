from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_DRIVE_MOUNT_RE = re.compile(r"^\s*([A-Za-z])\s*=\s*(.+?)\s*$")


@dataclass(frozen=True)
class WindowsDriveMount:
    letter: str
    container_root: Path

    @property
    def root_id(self) -> str:
        return f"drive-{self.letter.lower()}"

    @property
    def label(self) -> str:
        return f"{self.letter.upper()}:"

    @property
    def display_root(self) -> str:
        return f"{self.letter.upper()}:\\"

    def available(self) -> bool:
        try:
            return self.container_root.exists() and self.container_root.is_dir()
        except OSError:
            return False


class MountedWindowsPathResolver:
    """Resolve configured Windows drive roots to mounted container paths.

    This is an opt-in bridge for production Docker, where Windows drives are
    bind-mounted into the container at predictable Linux paths such as /host/c.
    Local development keeps the existing Path-based behavior unless
    MAIN_COMPUTER_PATH_MODE=mounted-windows is explicitly selected.
    """

    def __init__(self, mounts: dict[str, WindowsDriveMount], *, path_mode: str = "local", host_os: str = "auto") -> None:
        self.mounts = {letter.upper(): mount for letter, mount in mounts.items()}
        self.path_mode = (path_mode or "local").strip().lower()
        self.host_os = (host_os or "auto").strip().lower()

    @property
    def enabled(self) -> bool:
        return self.path_mode == "mounted-windows"

    def root_candidates(self, *, available_only: bool = True) -> dict[str, Path]:
        if not self.enabled:
            return {}
        candidates: dict[str, Path] = {}
        for letter in sorted(self.mounts):
            mount = self.mounts[letter]
            if available_only and not mount.available():
                continue
            candidates[mount.root_id] = mount.container_root
        return candidates

    def roots(self, *, available_only: bool = False) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        roots: list[dict[str, Any]] = []
        for letter in sorted(self.mounts):
            mount = self.mounts[letter]
            available = mount.available()
            if available_only and not available:
                continue
            roots.append(
                {
                    "id": mount.root_id,
                    "label": mount.label,
                    "path_display": mount.display_root,
                    "container_path": str(mount.container_root),
                    "available": available,
                    "writable": _is_writable_directory(mount.container_root) if available else False,
                    "mounted_windows_drive": True,
                }
            )
        return roots

    def status(self) -> dict[str, Any]:
        roots = self.roots(available_only=False)
        return {
            "ok": True,
            "path_mode": self.path_mode,
            "host_os": self.host_os,
            "enabled": self.enabled,
            "mounts": roots,
            "count": len(roots),
        }

    def is_mounted_root(self, root_id: str) -> bool:
        return self._mount_for_root_id(root_id) is not None

    def resolve(self, root_id: str, relative_path: str = "", *, must_exist: bool = True) -> Path:
        mount = self._require_mount(root_id)
        if not mount.available():
            raise ValueError("Mounted Windows drive is unavailable.")
        root = mount.container_root.resolve()
        parts = _safe_relative_parts(relative_path)
        candidate = (root / Path(*parts)).resolve() if parts else root
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("Path escapes selected root.") from exc
        if must_exist and not candidate.exists():
            raise ValueError("Path not found.")
        return candidate

    def relative_path(self, root_id: str, path: Path) -> str:
        root = self.resolve(root_id, "", must_exist=True)
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            try:
                return path.resolve().relative_to(root).as_posix()
            except ValueError as exc:
                raise ValueError("Path escapes selected root.") from exc

    def display_path(self, root_id: str, relative_path: str = "") -> str:
        mount = self._require_mount(root_id)
        clean = "/".join(_safe_relative_parts(relative_path))
        if not clean:
            return mount.display_root
        return mount.display_root + clean.replace("/", "\\")

    def _require_mount(self, root_id: str) -> WindowsDriveMount:
        mount = self._mount_for_root_id(root_id)
        if mount is None:
            raise ValueError("Unknown mounted Windows drive.")
        return mount

    def _mount_for_root_id(self, root_id: str) -> WindowsDriveMount | None:
        normalized = str(root_id or "").strip().lower()
        if not normalized.startswith("drive-") or len(normalized) != len("drive-a"):
            return None
        letter = normalized[-1].upper()
        return self.mounts.get(letter)


def build_mounted_windows_path_resolver(config: Any) -> MountedWindowsPathResolver:
    host_os = str(getattr(config, "host_os", "auto") or "auto").strip().lower()
    host_drive_root = Path(getattr(config, "host_drive_root", Path("/host")))
    mounts = discover_host_drive_mounts(host_drive_root, host_os=host_os)
    mounts.update(parse_windows_drive_mounts(getattr(config, "windows_drive_mounts", "")))
    mounts_file = getattr(config, "windows_drive_mounts_file", None)
    if mounts_file:
        mounts.update(parse_windows_drive_mounts_file(Path(mounts_file)))
    return MountedWindowsPathResolver(
        mounts,
        path_mode=getattr(config, "path_mode", "local"),
        host_os=host_os,
    )


def discover_host_drive_mounts(host_root: str | Path = "/host", *, host_os: str = "auto") -> dict[str, WindowsDriveMount]:
    """Discover Docker-style /host/<drive> mounts for a Windows host."""

    if str(host_os or "auto").strip().lower() != "windows":
        return {}
    root = Path(host_root)
    try:
        children = list(root.iterdir())
    except OSError:
        return {}
    mounts: dict[str, WindowsDriveMount] = {}
    for child in children:
        if not re.fullmatch(r"[A-Za-z]", child.name):
            continue
        try:
            if not child.is_dir():
                continue
        except OSError:
            continue
        letter = child.name.upper()
        mounts[letter] = WindowsDriveMount(letter=letter, container_root=child)
    return mounts


def parse_windows_drive_mounts(value: str | None) -> dict[str, WindowsDriveMount]:
    mounts: dict[str, WindowsDriveMount] = {}
    if not value:
        return mounts
    for chunk in str(value).split(";"):
        item = chunk.strip()
        if not item:
            continue
        match = _DRIVE_MOUNT_RE.match(item)
        if not match:
            raise ValueError(f"Invalid Windows drive mount entry: {item!r}")
        letter, root = match.groups()
        mounts[letter.upper()] = WindowsDriveMount(letter=letter.upper(), container_root=Path(root))
    return mounts


def parse_windows_drive_mounts_file(path: Path) -> dict[str, WindowsDriveMount]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError("Windows drive mounts file must contain a JSON object.")
    raw_drives = data.get("drives", data)
    if not isinstance(raw_drives, dict):
        raise ValueError("Windows drive mounts file must contain a drives object.")
    mounts: dict[str, WindowsDriveMount] = {}
    for letter, root in raw_drives.items():
        letter_text = str(letter).strip().rstrip(":\\/")
        if not re.fullmatch(r"[A-Za-z]", letter_text):
            raise ValueError(f"Invalid Windows drive letter in mounts file: {letter!r}")
        mounts[letter_text.upper()] = WindowsDriveMount(letter=letter_text.upper(), container_root=Path(str(root)))
    return mounts



def host_drive_fallback_candidates(path: str | Path, *, host_root: str | Path = "/host") -> list[Path]:
    """Return literal-first equivalent paths for Windows drive and /host paths.

    No candidate is treated as virtual. Callers should try the returned paths in
    order and use the first one that exists.
    """

    raw = str(path)
    candidates = [Path(raw)]

    host_candidate = windows_path_to_host_path(raw, host_root=host_root)
    if host_candidate is not None and host_candidate != candidates[0]:
        candidates.append(host_candidate)
        return candidates

    windows_candidate = host_path_to_windows_path(raw, host_root=host_root)
    if windows_candidate is not None and windows_candidate != candidates[0]:
        candidates.append(windows_candidate)
    return candidates


def resolve_existing_host_path(path: str | Path, *, host_root: str | Path = "/host") -> Path:
    """Resolve a literal path, falling back to its host-drive equivalent.

    The literal path is always tried first. If neither candidate exists, a normal
    ValueError is raised instead of fabricating a virtual path.
    """

    for candidate in host_drive_fallback_candidates(path, host_root=host_root):
        if candidate.exists():
            return candidate.resolve()
    raise ValueError("Path not found.")


def windows_path_to_host_path(path: str | Path, *, host_root: str | Path = "/host") -> Path | None:
    raw = str(path).strip()
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", raw)
    if not match:
        if re.match(r"^[A-Za-z]:", raw):
            raise ValueError("Drive-relative Windows paths are not supported.")
        return None
    letter, tail = match.groups()
    parts = _safe_host_alias_tail(tail)
    root = Path(str(host_root))
    return root / letter.lower() / Path(*parts) if parts else root / letter.lower()


def host_path_to_windows_path(path: str | Path, *, host_root: str | Path = "/host") -> Path | None:
    raw = str(path).replace("\\", "/").strip()
    root = "/" + str(host_root).replace("\\", "/").strip("/")
    root = root.rstrip("/")
    prefix = f"{root}/"
    if not raw.startswith(prefix):
        return None
    remainder = raw[len(prefix) :]
    parts = [part for part in remainder.split("/") if part]
    if not parts:
        return None
    letter = parts[0]
    if not re.fullmatch(r"[A-Za-z]", letter):
        raise ValueError("Invalid host drive path.")
    tail = _safe_host_alias_tail("/".join(parts[1:]))
    windows = f"{letter.upper()}:\\"
    if tail:
        windows += "\\".join(tail)
    return Path(windows)


def _safe_relative_parts(relative_path: str | None) -> list[str]:
    raw = str(relative_path or "").strip()
    if not raw:
        return []
    normalized = raw.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise ValueError("Absolute paths are not allowed.")
    parts = [part for part in normalized.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        raise ValueError("Path traversal is not allowed.")
    return parts



def _safe_host_alias_tail(tail: str | None) -> list[str]:
    normalized = str(tail or "").replace("\\", "/").strip("/")
    parts = [part for part in normalized.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        raise ValueError("Path traversal is not allowed.")
    return parts

def _is_writable_directory(path: Path) -> bool:
    probe = path / ".main_computer_write_probe"
    try:
        with probe.open("w", encoding="utf-8") as handle:
            handle.write("")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
        return False
