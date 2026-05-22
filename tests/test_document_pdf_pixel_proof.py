from __future__ import annotations

import io
import json
import struct
import zipfile
import zlib
from pathlib import Path

from main_computer.document_pdf_pixel_proof import (
    build_pixel_proof_bundle_from_smoke_zip,
    default_pixel_proof_bundle_name,
    write_pixel_proof_bundle,
)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    import zlib as _zlib

    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", _zlib.crc32(kind + payload) & 0xFFFFFFFF)


def _make_rgb_png(width: int, height: int) -> bytes:
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            row.extend((x * 40 % 256, y * 50 % 256, 120))
        rows.append(b"\x00" + bytes(row))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", zlib.compress(b"".join(rows))) + _png_chunk(b"IEND", b"")


def _make_smoke_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(
            "manifest.json",
            json.dumps(
                {
                    "ok": True,
                    "kind": "main-computer-document-pdf-smoke-v1",
                    "capture": {
                        "renderer": {"label": "test chromium"},
                        "deviceScaleFactor": 1,
                        "media": "screen",
                    },
                }
            ),
        )
        bundle.writestr("payload.json", json.dumps({"title": "Pixel Proof"}))
        bundle.writestr("document-export.html", "<!doctype html><section class='mc-page'></section>")
        bundle.writestr("pages/page-001.png", _make_rgb_png(4, 6))
        bundle.writestr("pages/page-001.html", "<section class='mc-page'>one</section>")
        bundle.writestr("pages/page-002.png", _make_rgb_png(4, 6))


def test_build_pixel_proof_bundle_from_smoke_zip_embeds_source_pngs(tmp_path: Path) -> None:
    smoke_zip = tmp_path / "demo-pdf-smoke.zip"
    _make_smoke_zip(smoke_zip)

    proof_bytes = build_pixel_proof_bundle_from_smoke_zip(smoke_zip)

    with zipfile.ZipFile(io.BytesIO(proof_bytes)) as bundle:
        names = set(bundle.namelist())
        assert "pixel-proof.json" in names
        assert "source/pages/page-001.png" in names
        assert "source/pages/page-002.png" in names
        assert "candidates/pixel-raster-css96.pdf" in names
        assert "candidates/identity-pixel-points.pdf" in names
        proof = json.loads(bundle.read("pixel-proof.json").decode("utf-8"))
        assert proof["pageCount"] == 2
        assert proof["pageSizeCssPx"]["width"] == 4
        assert proof["pageSizeCssPx"]["height"] == 6
        assert proof["pixelRasterPdf"]["pdfPageSizePoints"] == {"width": 3.0, "height": 4.5}
        assert proof["pixelRasterPdf"]["expectedRenderedAt96DpiPx"] == {"width": 4, "height": 6}
        pdf = bundle.read("candidates/pixel-raster-css96.pdf")
        assert b"/MediaBox [0 0 3 4.5]" in pdf
        with zipfile.ZipFile(smoke_zip) as source:
            assert bundle.read("source/pages/page-001.png") == source.read("pages/page-001.png")


def test_write_pixel_proof_bundle_uses_default_name(tmp_path: Path) -> None:
    smoke_zip = tmp_path / "demo-pdf-smoke.zip"
    _make_smoke_zip(smoke_zip)

    output = write_pixel_proof_bundle(smoke_zip)

    assert output.name == "demo-pixel-proof.zip"
    assert output.exists()
    assert default_pixel_proof_bundle_name(smoke_zip) == "demo-pixel-proof.zip"
