from __future__ import annotations

import importlib.util
import io
import stat
import zipfile
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "deploy" / "coolify" / "rsync" / "coolify_rsync_server.py"


def load_module():
    spec = importlib.util.spec_from_file_location("coolify_rsync_server", MODULE_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def make_symlink_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("index.html", b"ok")
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, b"target")
    return buffer.getvalue()


def make_multipart(filename: str, payload: bytes) -> tuple[str, bytes]:
    boundary = "main-computer-boundary"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode("ascii"),
            (
                'Content-Disposition: form-data; name="file"; '
                f'filename="{filename}"\r\n'
            ).encode("utf-8"),
            b"Content-Type: application/zip\r\n\r\n",
            payload,
            b"\r\n",
            f"--{boundary}--\r\n".encode("ascii"),
        ]
    )
    return f"multipart/form-data; boundary={boundary}", body


def test_validate_site_id_accepts_safe_names() -> None:
    module = load_module()

    assert module.validate_site_id("johnrraymond") == "johnrraymond"
    assert module.validate_site_id("client-site_001") == "client-site_001"


@pytest.mark.parametrize("site_id", ["../bad", "bad/site", "bad.site", "", "bad%2Fsite"])
def test_validate_site_id_rejects_unsafe_names(site_id: str) -> None:
    module = load_module()

    with pytest.raises(module.PublishError):
        module.validate_site_id(site_id)


def test_extract_zip_safely_accepts_root_index(tmp_path: Path) -> None:
    module = load_module()
    staging = tmp_path / "staging"
    staging.mkdir()

    extracted = module.extract_zip_safely(
        make_zip({"index.html": b"<!doctype html>", "assets/app.js": b"console.log(1)"}),
        staging,
    )

    assert sorted(extracted) == ["assets/app.js", "index.html"]
    assert (staging / "index.html").read_bytes() == b"<!doctype html>"


@pytest.mark.parametrize(
    "member",
    [
        "../index.html",
        "/index.html",
        "runtime/websites/johnrraymond/index.html",
        "C:/temp/index.html",
    ],
)
def test_extract_zip_safely_rejects_bad_shape_or_path(tmp_path: Path, member: str) -> None:
    module = load_module()
    staging = tmp_path / "staging"
    staging.mkdir()

    with pytest.raises(module.PublishError):
        module.extract_zip_safely(make_zip({member: b"bad"}), staging)


def test_extract_zip_safely_rejects_symlink_members(tmp_path: Path) -> None:
    module = load_module()
    staging = tmp_path / "staging"
    staging.mkdir()

    with pytest.raises(module.PublishError):
        module.extract_zip_safely(make_symlink_zip(), staging)


def test_parse_multipart_zip_requires_exactly_one_file() -> None:
    module = load_module()
    content_type, body = make_multipart("site.zip", make_zip({"index.html": b"ok"}))

    filename, payload = module.parse_multipart_zip(content_type, body)

    assert filename == "site.zip"
    assert zipfile.is_zipfile(io.BytesIO(payload))


def test_destination_for_site_stays_under_sites_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    monkeypatch.setenv("SITES_ROOT", str(tmp_path / "sites"))

    assert module.destination_for_site("johnrraymond") == (tmp_path / "sites" / "johnrraymond").resolve()
