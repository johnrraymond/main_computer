from __future__ import annotations

import struct
import zlib

from main_computer.document_pdf_raster import build_css_pixel_raster_pdf, build_png_xobject_pdf, build_raster_image_html, read_png_info


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    import zlib as _zlib

    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", _zlib.crc32(kind + payload) & 0xFFFFFFFF)


def _make_rgba_png(width: int, height: int) -> bytes:
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            row.extend((x * 40 % 256, y * 50 % 256, 120, 255 if (x + y) % 2 == 0 else 180))
        rows.append(b"\x00" + bytes(row))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", zlib.compress(b"".join(rows))) + _png_chunk(b"IEND", b"")


def test_read_png_info_reports_dimensions_and_alpha() -> None:
    png = _make_rgba_png(3, 2)

    info = read_png_info(png)

    assert info.width == 3
    assert info.height == 2
    assert info.bit_depth == 8
    assert info.color_type == 6
    assert info.has_alpha is True


def test_build_png_xobject_pdf_uses_one_page_per_png() -> None:
    pdf = build_png_xobject_pdf([_make_rgba_png(2, 2), _make_rgba_png(4, 3)])

    assert pdf.startswith(b"%PDF-1.4")
    assert b"/Count 2" in pdf
    assert b"/MediaBox [0 0 2 2]" in pdf
    assert b"/MediaBox [0 0 4 3]" in pdf
    assert b"/SMask" in pdf


def test_build_css_pixel_raster_pdf_keeps_css96_physical_page_size() -> None:
    pdf = build_css_pixel_raster_pdf([_make_rgba_png(320, 480)])

    assert b"/MediaBox [0 0 240 360]" in pdf
    assert b"q 240 0 0 360 0 0 cm /Im0 Do Q" in pdf


def test_build_raster_image_html_contains_full_page_png_data_uri() -> None:
    html = build_raster_image_html("Demo", [_make_rgba_png(1, 1)], 816, 1056)

    assert "@page" in html
    assert "size: 816px 1056px" in html
    assert "data:image/png;base64," in html
    assert "object-fit: fill" in html
