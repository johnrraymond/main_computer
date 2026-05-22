from __future__ import annotations

import base64
import io
import struct
import zlib
from dataclasses import dataclass


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CSS_PX_TO_PDF_POINT = 72.0 / 96.0



@dataclass(frozen=True)
class PngInfo:
    width: int
    height: int
    bit_depth: int
    color_type: int
    has_alpha: bool


@dataclass(frozen=True)
class DecodedPngImage:
    info: PngInfo
    color_space: str
    colors: int
    samples: bytes
    alpha: bytes | None = None


def read_png_info(data: bytes) -> PngInfo:
    width, height, bit_depth, color_type, _idat = _read_png_chunks(data)
    return PngInfo(
        width=width,
        height=height,
        bit_depth=bit_depth,
        color_type=color_type,
        has_alpha=color_type in {4, 6},
    )


def build_raster_image_html(title: str, page_pngs: list[bytes], width_px: int, height_px: int) -> str:
    safe_title = _html_escape(title or "Main Computer Raster PDF Smoke")
    image_markup = []
    for index, png_bytes in enumerate(page_pngs, start=1):
        data_uri = base64.b64encode(png_bytes).decode("ascii")
        image_markup.append(
            '<section class="mc-raster-page">'
            f'<img alt="PDF smoke source page {index}" src="data:image/png;base64,{data_uri}">'
            "</section>"
        )
    pages = "\n".join(image_markup)
    return f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>{safe_title}</title>
    <style>
      @page {{
        size: {int(width_px)}px {int(height_px)}px;
        margin: 0;
      }}
      html,
      body {{
        margin: 0;
        padding: 0;
        background: white;
      }}
      .mc-raster-page {{
        position: relative;
        width: {int(width_px)}px;
        height: {int(height_px)}px;
        margin: 0;
        padding: 0;
        overflow: hidden;
        break-after: page;
        page-break-after: always;
        print-color-adjust: exact;
        -webkit-print-color-adjust: exact;
      }}
      .mc-raster-page:last-child {{
        break-after: auto;
        page-break-after: auto;
      }}
      .mc-raster-page img {{
        display: block;
        width: 100%;
        height: 100%;
        object-fit: fill;
        margin: 0;
        padding: 0;
        border: 0;
      }}
    </style>
  </head>
  <body>
{pages}
  </body>
</html>
"""


def build_css_pixel_raster_pdf(page_pngs: list[bytes]) -> bytes:
    """Build a physically sized PDF from CSS-pixel screenshots.

    Browser/editor pages are measured in CSS pixels. PDF pages are measured in
    points, so the normal document export maps 96 CSS pixels to 72 PDF points.
    Rendering the resulting PDF back at 96 DPI should produce the same pixel
    dimensions as the source screenshots without changing the printed page size.
    """

    return build_png_xobject_pdf(page_pngs, pdf_points_per_css_px=CSS_PX_TO_PDF_POINT)


def build_png_xobject_pdf(page_pngs: list[bytes], *, pdf_points_per_css_px: float = 1.0) -> bytes:
    """Build a simple one-image-per-page raster PDF from 8-bit non-interlaced PNGs.

    This intentionally avoids third-party dependencies so the PDF smoke workflow can
    always emit at least one raster candidate. It supports the PNG formats emitted by
    Chromium screenshots: 8-bit RGB and RGBA, with grayscale variants also accepted.

    By default, one CSS pixel maps to one PDF point for smoke-test identity
    experiments. The real PDF export passes 72/96 so Letter pages remain Letter
    sized while preserving a 1:1 source-pixel grid when rendered at 96 DPI.
    """

    if not page_pngs:
        raise ValueError("at least one page PNG is required")
    if pdf_points_per_css_px <= 0:
        raise ValueError("pdf_points_per_css_px must be greater than zero")

    objects: list[bytes] = []

    def add_object(payload: bytes) -> int:
        objects.append(payload)
        return len(objects)

    catalog_obj = add_object(b"")
    pages_obj = add_object(b"")
    page_objects: list[int] = []

    for png_bytes in page_pngs:
        image = decode_png_image(png_bytes)
        width = image.info.width
        height = image.info.height
        page_width = width * pdf_points_per_css_px
        page_height = height * pdf_points_per_css_px
        page_width_text = _pdf_number(page_width)
        page_height_text = _pdf_number(page_height)

        smask_obj: int | None = None
        if image.alpha is not None:
            alpha_stream = zlib.compress(image.alpha)
            smask_obj = add_object(
                _pdf_stream(
                    (
                        f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
                        f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode /Length {len(alpha_stream)} >>"
                    ).encode("ascii"),
                    alpha_stream,
                )
            )

        sample_stream = zlib.compress(image.samples)
        smask_ref = f" /SMask {smask_obj} 0 R" if smask_obj else ""
        image_obj = add_object(
            _pdf_stream(
                (
                    f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
                    f"/ColorSpace /{image.color_space} /BitsPerComponent 8 /Filter /FlateDecode{smask_ref} "
                    f"/Length {len(sample_stream)} >>"
                ).encode("ascii"),
                sample_stream,
            )
        )

        content = f"q {page_width_text} 0 0 {page_height_text} 0 0 cm /Im0 Do Q\n".encode("ascii")
        content_obj = add_object(_pdf_stream(f"<< /Length {len(content)} >>".encode("ascii"), content))
        page_obj = add_object(
            (
                f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 {page_width_text} {page_height_text}] "
                f"/Resources << /XObject << /Im0 {image_obj} 0 R >> >> "
                f"/Contents {content_obj} 0 R >>"
            ).encode("ascii")
        )
        page_objects.append(page_obj)

    objects[catalog_obj - 1] = f"<< /Type /Catalog /Pages {pages_obj} 0 R >>".encode("ascii")
    kids = " ".join(f"{obj} 0 R" for obj in page_objects)
    objects[pages_obj - 1] = f"<< /Type /Pages /Count {len(page_objects)} /Kids [{kids}] >>".encode("ascii")
    return _write_pdf(objects, catalog_obj)


def decode_png_image(data: bytes) -> DecodedPngImage:
    width, height, bit_depth, color_type, idat = _read_png_chunks(data)
    if bit_depth != 8:
        raise ValueError(f"only 8-bit PNG screenshots are supported, got bit depth {bit_depth}")
    if color_type not in {0, 2, 4, 6}:
        raise ValueError(f"unsupported PNG color type {color_type}")

    samples_per_pixel = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    raw = zlib.decompress(idat)
    stride = width * samples_per_pixel
    expected = (stride + 1) * height
    if len(raw) < expected:
        raise ValueError("PNG IDAT stream is shorter than expected for the image dimensions")

    unfiltered = _unfilter_png_scanlines(raw, width=width, height=height, bytes_per_pixel=samples_per_pixel)
    info = PngInfo(width=width, height=height, bit_depth=bit_depth, color_type=color_type, has_alpha=color_type in {4, 6})

    if color_type == 0:
        return DecodedPngImage(info=info, color_space="DeviceGray", colors=1, samples=unfiltered)
    if color_type == 2:
        return DecodedPngImage(info=info, color_space="DeviceRGB", colors=3, samples=unfiltered)

    if color_type == 4:
        gray = bytearray(width * height)
        alpha = bytearray(width * height)
        for pixel_index in range(width * height):
            base = pixel_index * 2
            gray[pixel_index] = unfiltered[base]
            alpha[pixel_index] = unfiltered[base + 1]
        return DecodedPngImage(info=info, color_space="DeviceGray", colors=1, samples=bytes(gray), alpha=bytes(alpha))

    rgb = bytearray(width * height * 3)
    alpha = bytearray(width * height)
    for pixel_index in range(width * height):
        source = pixel_index * 4
        target = pixel_index * 3
        rgb[target : target + 3] = unfiltered[source : source + 3]
        alpha[pixel_index] = unfiltered[source + 3]
    return DecodedPngImage(info=info, color_space="DeviceRGB", colors=3, samples=bytes(rgb), alpha=bytes(alpha))


def _read_png_chunks(data: bytes) -> tuple[int, int, int, int, bytes]:
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("not a PNG file")

    offset = len(PNG_SIGNATURE)
    width = height = bit_depth = color_type = None
    idat_parts: list[bytes] = []
    interlace = 0
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_start = offset + 8
        chunk_end = chunk_start + length
        if chunk_end + 4 > len(data):
            raise ValueError("truncated PNG chunk")
        chunk = data[chunk_start:chunk_end]
        offset = chunk_end + 4

        if chunk_type == b"IHDR":
            if length != 13:
                raise ValueError("invalid PNG IHDR length")
            width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(">IIBBBBB", chunk)
            if width <= 0 or height <= 0:
                raise ValueError("invalid PNG dimensions")
            if compression != 0 or filter_method != 0:
                raise ValueError("unsupported PNG compression or filter method")
        elif chunk_type == b"IDAT":
            idat_parts.append(chunk)
        elif chunk_type == b"IEND":
            break

    if width is None or height is None or bit_depth is None or color_type is None:
        raise ValueError("PNG IHDR not found")
    if interlace:
        raise ValueError("interlaced PNG screenshots are not supported")
    if not idat_parts:
        raise ValueError("PNG IDAT not found")
    return int(width), int(height), int(bit_depth), int(color_type), b"".join(idat_parts)


def _unfilter_png_scanlines(raw: bytes, *, width: int, height: int, bytes_per_pixel: int) -> bytes:
    stride = width * bytes_per_pixel
    output = bytearray(width * height * bytes_per_pixel)
    previous = bytearray(stride)
    offset = 0
    out_offset = 0
    for _row in range(height):
        filter_type = raw[offset]
        offset += 1
        scanline = raw[offset : offset + stride]
        offset += stride
        if len(scanline) != stride:
            raise ValueError("truncated PNG scanline")
        reconstructed = bytearray(stride)
        for index, value in enumerate(scanline):
            left = reconstructed[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            up = previous[index]
            up_left = previous[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = up
            elif filter_type == 3:
                predictor = (left + up) // 2
            elif filter_type == 4:
                predictor = _paeth_predictor(left, up, up_left)
            else:
                raise ValueError(f"unsupported PNG scanline filter {filter_type}")
            reconstructed[index] = (value + predictor) & 0xFF
        output[out_offset : out_offset + stride] = reconstructed
        out_offset += stride
        previous = reconstructed
    return bytes(output)


def _paeth_predictor(left: int, up: int, up_left: int) -> int:
    estimate = left + up - up_left
    distance_left = abs(estimate - left)
    distance_up = abs(estimate - up)
    distance_up_left = abs(estimate - up_left)
    if distance_left <= distance_up and distance_left <= distance_up_left:
        return left
    if distance_up <= distance_up_left:
        return up
    return up_left


def _pdf_number(value: float) -> str:
    if abs(value - round(value)) < 0.000001:
        return str(int(round(value)))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _pdf_stream(dictionary: bytes, stream: bytes) -> bytes:
    return dictionary + b"\nstream\n" + stream + b"\nendstream"


def _write_pdf(objects: list[bytes], root_obj: int) -> bytes:
    buffer = io.BytesIO()
    buffer.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, payload in enumerate(objects, start=1):
        offsets.append(buffer.tell())
        buffer.write(f"{index} 0 obj\n".encode("ascii"))
        buffer.write(payload)
        buffer.write(b"\nendobj\n")
    xref_offset = buffer.tell()
    buffer.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    buffer.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        buffer.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    buffer.write(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root {root_obj} 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return buffer.getvalue()


def _html_escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
