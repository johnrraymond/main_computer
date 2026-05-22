from __future__ import annotations

import json
import urllib.request
from pathlib import Path


class DownloadError(RuntimeError):
    pass


def download_file(url: str, destination: Path, *, timeout_seconds: int = 180) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")

    request = urllib.request.Request(url, headers={"User-Agent": "main-computer-python-bootstrap/1.0"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        with temporary.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)

    temporary.replace(destination)
    if destination.stat().st_size < 1000:
        raise DownloadError(f"Downloaded file looks too small: {destination}")
    return destination


def download_text(url: str, *, timeout_seconds: int = 60) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "main-computer-python-bootstrap/1.0"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return response.read().decode("utf-8")


def ensure_pip_wheel(wheelhouse: Path, version: str, *, no_download: bool = False) -> Path:
    """Ensure the pinned pip wheel is cached without invoking pip."""

    wheelhouse.mkdir(parents=True, exist_ok=True)
    wheel_path = wheelhouse / f"pip-{version}-py3-none-any.whl"
    if wheel_path.exists() and wheel_path.stat().st_size > 1000:
        print(f"Using cached pip wheel: {wheel_path}", flush=True)
        return wheel_path

    if no_download:
        raise DownloadError(f"Pip wheel is not cached and downloads are disabled: {wheel_path}")

    metadata_url = f"https://pypi.org/pypi/pip/{version}/json"
    print(f"Fetching pip wheel metadata: {metadata_url}", flush=True)
    metadata = json.loads(download_text(metadata_url, timeout_seconds=60))
    for item in metadata.get("urls", []):
        if item.get("filename") == wheel_path.name and item.get("packagetype") == "bdist_wheel":
            print(f"Downloading pip wheel: {item['url']}", flush=True)
            return download_file(item["url"], wheel_path, timeout_seconds=180)

    raise DownloadError(f"Could not find expected pip wheel in PyPI metadata: {wheel_path.name}")
