from __future__ import annotations

import io
import json
import platform
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from main_computer.document_pdf_raster import (
    CSS_PX_TO_PDF_POINT,
    build_css_pixel_raster_pdf,
    build_png_xobject_pdf,
    read_png_info,
)


_SMOKE_PAGE_RE = re.compile(r"^pages/page-(\d{3,})\.png$")


@dataclass(frozen=True)
class SmokePagePng:
    index: int
    path: str
    png: bytes
    width_px: int
    height_px: int
    has_alpha: bool


def default_pixel_proof_bundle_name(smoke_zip: str | Path) -> str:
    stem = Path(smoke_zip).name
    if stem.lower().endswith(".zip"):
        stem = stem[:-4]
    if stem.lower().endswith("-pdf-smoke"):
        stem = stem[: -len("-pdf-smoke")]
    if not stem:
        stem = "main-computer-document"
    return f"{stem}-pixel-proof.zip"


def build_pixel_proof_bundle_from_smoke_zip(smoke_zip: str | Path) -> bytes:
    """Build a proof bundle from a Save PDF Smoke ZIP.

    The smoke bundle's ``pages/page-###.png`` files are the visual oracle captured
    from the export-only document DOM. This proof bundle reuses those exact PNG
    bytes to create the production-style pixel-raster PDF candidate.
    """

    smoke_path = Path(smoke_zip)
    with zipfile.ZipFile(smoke_path, "r") as source:
        manifest = _read_json_member(source, "manifest.json")
        payload = _read_optional_member(source, "payload.json")
        export_html = _read_optional_member(source, "document-export.html")
        pages = _read_smoke_page_pngs(source)
        if not pages:
            raise ValueError("smoke ZIP does not contain any pages/page-###.png files")

        page_pngs = [page.png for page in pages]
        pixel_pdf = build_css_pixel_raster_pdf(page_pngs)
        identity_pdf = build_png_xobject_pdf(page_pngs)
        proof = _build_pixel_proof_metadata(
            source_name=smoke_path.name,
            source_manifest=manifest,
            pages=pages,
            pixel_pdf_bytes=len(pixel_pdf),
            identity_pdf_bytes=len(identity_pdf),
        )

        readme = _build_readme(proof)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as out:
            _write_member(out, "README.md", readme.encode("utf-8"))
            _write_member(out, "pixel-proof.json", json.dumps(proof, ensure_ascii=False, indent=2).encode("utf-8"))
            if manifest is not None:
                _write_member(out, "source/manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
            if payload is not None:
                _write_member(out, "source/payload.json", payload)
            if export_html is not None:
                _write_member(out, "source/document-export.html", export_html)
            for page in pages:
                _write_member(out, f"source/{page.path}", page.png)
                html_path = page.path[:-4] + ".html"
                html_data = _read_optional_member(source, html_path)
                if html_data is not None:
                    _write_member(out, f"source/{html_path}", html_data)
            _write_member(out, "candidates/pixel-raster-css96.pdf", pixel_pdf)
            _write_member(out, "candidates/identity-pixel-points.pdf", identity_pdf)
        return buffer.getvalue()


def write_pixel_proof_bundle(smoke_zip: str | Path, output_zip: str | Path | None = None) -> Path:
    output = Path(output_zip) if output_zip else Path(smoke_zip).with_name(default_pixel_proof_bundle_name(smoke_zip))
    output.write_bytes(build_pixel_proof_bundle_from_smoke_zip(smoke_zip))
    return output


def _read_smoke_page_pngs(source: zipfile.ZipFile) -> list[SmokePagePng]:
    matched: list[tuple[int, str]] = []
    for name in source.namelist():
        clean_name = name.replace("\\", "/")
        match = _SMOKE_PAGE_RE.match(clean_name)
        if match:
            matched.append((int(match.group(1)), clean_name))
    pages: list[SmokePagePng] = []
    for index, path in sorted(matched):
        data = source.read(path)
        info = read_png_info(data)
        pages.append(
            SmokePagePng(
                index=index,
                path=path,
                png=data,
                width_px=info.width,
                height_px=info.height,
                has_alpha=info.has_alpha,
            )
        )
    return pages


def _build_pixel_proof_metadata(
    *,
    source_name: str,
    source_manifest: dict[str, Any] | None,
    pages: list[SmokePagePng],
    pixel_pdf_bytes: int,
    identity_pdf_bytes: int,
) -> dict[str, Any]:
    widths = sorted({page.width_px for page in pages})
    heights = sorted({page.height_px for page in pages})
    first_width = pages[0].width_px
    first_height = pages[0].height_px
    pdf_width_points = first_width * CSS_PX_TO_PDF_POINT
    pdf_height_points = first_height * CSS_PX_TO_PDF_POINT
    source_capture = source_manifest.get("capture") if isinstance(source_manifest, dict) else None
    return {
        "ok": True,
        "kind": "main-computer-document-pixel-proof-v1",
        "sourceSmokeZip": source_name,
        "sourceSmokeKind": source_manifest.get("kind") if isinstance(source_manifest, dict) else None,
        "sourceCapture": source_capture if isinstance(source_capture, dict) else None,
        "pageCount": len(pages),
        "pageSizeCssPx": {
            "width": first_width,
            "height": first_height,
            "allWidths": widths,
            "allHeights": heights,
            "consistent": len(widths) == 1 and len(heights) == 1,
        },
        "cssPxToPdfPoint": CSS_PX_TO_PDF_POINT,
        "pixelRasterPdf": {
            "path": "candidates/pixel-raster-css96.pdf",
            "bytes": pixel_pdf_bytes,
            "strategy": "embed smoke source PNGs directly as page image XObjects",
            "pdfPageSizePoints": {
                "width": pdf_width_points,
                "height": pdf_height_points,
            },
            "expectedRenderedAt96DpiPx": {
                "width": first_width,
                "height": first_height,
            },
            "intendedProductionEndpoint": "/api/applications/docs/export/pdf",
        },
        "debugIdentityPdf": {
            "path": "candidates/identity-pixel-points.pdf",
            "bytes": identity_pdf_bytes,
            "strategy": "one PDF point per source PNG pixel; useful for raw image identity debugging, not final physical page size",
        },
        "sourcePages": [
            {
                "index": page.index,
                "path": f"source/{page.path}",
                "widthPx": page.width_px,
                "heightPx": page.height_px,
                "bytes": len(page.png),
                "hasAlpha": page.has_alpha,
            }
            for page in pages
        ],
        "renderBackComparison": {
            "status": "not-run",
            "reason": (
                "This proof embeds the smoke source PNGs into candidate PDFs. "
                "Final pixel-perfect proof still requires rendering candidates back to PNG at 96 DPI "
                "and diffing them against source/pages/page-###.png with a local PDF renderer."
            ),
        },
        "createdBy": {
            "pythonExecutable": str(getattr(sys, "executable", "python") or "python"),
            "pythonVersion": platform.python_version(),
            "platform": platform.platform(),
        },
    }


def _read_json_member(source: zipfile.ZipFile, name: str) -> dict[str, Any] | None:
    data = _read_optional_member(source, name)
    if data is None:
        return None
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{name} must contain a JSON object")
    return value


def _read_optional_member(source: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        return source.read(name)
    except KeyError:
        return None


def _write_member(bundle: zipfile.ZipFile, path: str, data: bytes) -> None:
    clean_path = path.replace("\\", "/").lstrip("/")
    if not clean_path or clean_path.startswith("../") or "/../" in clean_path:
        raise ValueError(f"unsafe output bundle path: {path}")
    bundle.writestr(clean_path, data)


def _build_readme(proof: dict[str, Any]) -> str:
    page_count = proof.get("pageCount")
    size = proof.get("pageSizeCssPx", {})
    width = size.get("width")
    height = size.get("height")
    return (
        "# Main Computer PDF pixel proof\n\n"
        "This bundle is built from a `Save PDF Smoke` ZIP. The source PNG pages are treated as the visual oracle.\n\n"
        f"- Source pages: {page_count}\n"
        f"- Source page size: {width} × {height} CSS pixels\n"
        "- `candidates/pixel-raster-css96.pdf` is the production-style PDF candidate: each source PNG is embedded as the full page image and the page is sized with 96 CSS px = 72 PDF points.\n"
        "- `candidates/identity-pixel-points.pdf` is a debug candidate that maps one PNG pixel to one PDF point; it is not the normal document export size.\n"
        "- `pixel-proof.json` records dimensions, source capture metadata, and the remaining render-back comparison status.\n\n"
        "The candidate is only a final pixel-perfect proof after rendering the PDF back to PNG at 96 DPI and diffing it against `source/pages/page-###.png`.\n"
    )
