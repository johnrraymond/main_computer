from __future__ import annotations

import base64
import builtins
import io
import json
import re
import struct
import tempfile
import zipfile
import zlib
import threading
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.models import ChatResponse
from main_computer.viewport import ViewportServer
from main_computer.viewport_routes_docs import ViewportDocsRoutesMixin


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def _make_rgba_png(width: int, height: int) -> bytes:
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            row.extend((x * 37 % 256, y * 41 % 256, 128, 255))
        rows.append(b"\x00" + bytes(row))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", zlib.compress(b"".join(rows))) + _png_chunk(b"IEND", b"")


def _png_data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _svg_data_url(svg: str) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")



class FakeDocumentProvider:
    name = "fake"
    model = "fake-doc-model"

    def __init__(self) -> None:
        self.messages = []

    def chat(self, messages):
        self.messages = messages
        return ChatResponse(
            content=json.dumps(
                {
                    "content": "Here is a cleaner version.",
                    "suggestion": {
                        "operation": "replace_selection",
                        "replacement_text": "Clearer text.",
                        "replacement_html": "<p>Clearer text.</p>",
                        "rationale": "Improves clarity.",
                    },
                }
            ),
            provider=self.name,
            model=self.model,
        )


class ViewportPrettyDocsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name)
        docs = self.repo / "pretty_docs"
        docs.mkdir()
        (docs / "index.json").write_text(
            json.dumps(
                {
                    "documents": [
                        {
                            "path": "main-computer-user-guide.md",
                            "title": "Main Computer User Guide",
                            "kind": "markdown",
                            "order": 10,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        (docs / "main-computer-user-guide.md").write_text(
            "# Main Computer User Guide\n\nThis document will contain the polished user-facing guide for the Main Computer.\n",
            encoding="utf-8",
        )
        (docs / "secret.py").write_text("print('nope')\n", encoding="utf-8")
        self.server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=self.repo), verbose=False)
        self.server.debug_root = self.repo.resolve()
        self.fake_provider = FakeDocumentProvider()
        self.server.computer.provider = self.fake_provider
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def assertContentHash(self, value: object) -> None:
        self.assertIsInstance(value, str)
        self.assertRegex(value, re.compile(r"^[0-9a-f]{64}$"))

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tempdir.cleanup()

    def _post(self, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        request = Request(
            f"{self.base}{path}",
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post_error(self, path: str, payload: dict[str, object] | None = None) -> HTTPError:
        request = Request(
            f"{self.base}{path}",
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as raised:
            urlopen(request, timeout=5)
        return raised.exception

    def _post_binary(self, path: str, payload: dict[str, object] | None = None) -> tuple[bytes, str, str]:
        request = Request(
            f"{self.base}{path}",
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            return (
                response.read(),
                response.headers.get("Content-Type", ""),
                response.headers.get("Content-Disposition", ""),
            )

    def test_docs_files_returns_user_guide(self) -> None:
        data = self._post("/api/applications/docs/files")

        self.assertTrue(data["ok"])
        self.assertEqual(data["root"], "pretty_docs")
        self.assertTrue(data["read_only"])
        self.assertEqual(data["draft_storage"], "backend")
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["documents"][0]["title"], "Main Computer User Guide")
        self.assertEqual(data["documents"][0]["display_path"], "pretty_docs/main-computer-user-guide.md")
        self.assertIn("content_hash", data["documents"][0])
        self.assertContentHash(data["documents"][0]["content_hash"])

    def test_docs_read_returns_user_guide_content(self) -> None:
        data = self._post("/api/applications/docs/read", {"path": "main-computer-user-guide.md"})

        self.assertTrue(data["ok"])
        self.assertTrue(data["read_only"])
        self.assertEqual(data["draft_storage"], "backend")
        self.assertEqual(data["display_path"], "pretty_docs/main-computer-user-guide.md")
        self.assertIn("# Main Computer User Guide", data["content"])
        self.assertIn("content_hash", data)
        self.assertContentHash(data["content_hash"])

    def test_docs_content_hash_changes_when_file_content_changes(self) -> None:
        first = self._post("/api/applications/docs/read", {"path": "main-computer-user-guide.md"})
        guide = self.repo / "pretty_docs" / "main-computer-user-guide.md"
        guide.write_text("# Main Computer User Guide\n\nUpdated bytes.\n", encoding="utf-8")
        second = self._post("/api/applications/docs/read", {"path": "main-computer-user-guide.md"})

        self.assertContentHash(first["content_hash"])
        self.assertContentHash(second["content_hash"])
        self.assertNotEqual(first["content_hash"], second["content_hash"])

    def test_docs_read_rejects_traversal(self) -> None:
        self.assertEqual(self._post_error("/api/applications/docs/read", {"path": "../README.md"}).code, 400)

    def test_docs_read_rejects_absolute_path(self) -> None:
        absolute = str((self.repo / "pretty_docs" / "main-computer-user-guide.md").resolve())
        self.assertEqual(self._post_error("/api/applications/docs/read", {"path": absolute}).code, 400)

    def test_docs_read_rejects_unsupported_extension(self) -> None:
        self.assertEqual(self._post_error("/api/applications/docs/read", {"path": "secret.py"}).code, 400)

    def test_docs_read_rejects_missing_file(self) -> None:
        self.assertEqual(self._post_error("/api/applications/docs/read", {"path": "missing.md"}).code, 400)

    def test_docs_draft_backend_round_trip(self) -> None:
        missing = self._post("/api/applications/docs/draft/read", {"path": "main-computer-user-guide.md"})
        self.assertTrue(missing["ok"])
        self.assertFalse(missing["exists"])

        write = self._post(
            "/api/applications/docs/draft/write",
            {
                "path": "main-computer-user-guide.md",
                "html": "<h1>Main Computer User Guide</h1><p>Backend draft.</p>",
                "layout": {"view": {"mode": "endless"}},
                "revision": {"content_hash": "abc123"},
            },
        )
        self.assertTrue(write["ok"])
        self.assertEqual(write["path"], "main-computer-user-guide.md")
        self.assertTrue((self.repo / "runtime" / "document_editor_drafts").exists())

        saved = self._post("/api/applications/docs/draft/read", {"path": "main-computer-user-guide.md"})
        self.assertTrue(saved["exists"])
        self.assertIn("Backend draft.", saved["html"])
        self.assertEqual(saved["layout"]["view"]["mode"], "endless")
        self.assertEqual(saved["revision"]["content_hash"], "abc123")

        deleted = self._post("/api/applications/docs/draft/delete", {"path": "main-computer-user-guide.md"})
        self.assertTrue(deleted["ok"])
        self.assertTrue(deleted["existed"])

        missing_again = self._post("/api/applications/docs/draft/read", {"path": "main-computer-user-guide.md"})
        self.assertFalse(missing_again["exists"])

    def test_docs_scratchpad_draft_backend_round_trip(self) -> None:
        write = self._post(
            "/api/applications/docs/draft/write",
            {"path": "", "html": "<h2>Scratchpad</h2>", "layout": {"view": {"mode": "paged"}}},
        )
        self.assertTrue(write["ok"])
        self.assertEqual(write["path"], "")

        saved = self._post("/api/applications/docs/draft/read", {"path": ""})
        self.assertTrue(saved["exists"])
        self.assertEqual(saved["html"], "<h2>Scratchpad</h2>")

    def test_docs_draft_write_rejects_traversal(self) -> None:
        self.assertEqual(self._post_error("/api/applications/docs/draft/write", {"path": "../README.md", "html": "nope"}).code, 400)

    def test_docs_export_pdf_route_uses_vector_renderer(self) -> None:
        payload = {
            "title": "Export Test",
            "layoutState": {
                "layout": {"mode": "custom", "custom": {"widthPx": 320, "heightPx": 480}, "margins": {"top": 48, "right": 48, "bottom": 48, "left": 48}},
                "view": {"mode": "paged", "zoom": 1},
            },
            "pages": [{"contentHtml": "<h1>Export Test</h1><p>Hello PDF.</p>", "overlayHtml": ""}],
            "plugins": [],
        }

        with patch(
            "main_computer.viewport_routes_docs.ViewportDocsRoutesMixin._render_document_pdf_with_playwright",
            return_value=b"%PDF-1.7\\n% fake vector pdf\\n%%EOF\\n",
        ) as renderer, patch(
            "main_computer.viewport_routes_docs.ViewportDocsRoutesMixin._document_pdf_page_pngs_for_body",
            side_effect=AssertionError("Export PDF should use the fast vector path, not page image capture."),
        ):
            data, content_type, disposition = self._post_binary("/api/applications/docs/export/pdf", payload)

        self.assertTrue(data.startswith(b"%PDF-1.7"))
        self.assertEqual(content_type, "application/pdf")
        self.assertIn('filename="Export-Test.pdf"', disposition)
        renderer.assert_called_once()
        self.assertEqual(renderer.call_args.kwargs, {"media": "print", "pdf_scale": 1.0, "prefer_css_page_size": True})

    def test_docs_production_pdf_vector_settings_match_fit_smoke_winner(self) -> None:
        class DummyDocsRoutes(ViewportDocsRoutesMixin):
            pass

        route = DummyDocsRoutes()
        production = route._document_pdf_production_vector_settings()

        self.assertEqual(production["id"], "vector-print-scale-1_000")
        self.assertEqual(production["media"], "print")
        self.assertEqual(production["pdfScale"], 1.0)
        self.assertTrue(production["preferCssPageSize"])
        matching_candidates = [
            candidate
            for candidate in route._document_pdf_vector_fit_candidate_settings()
            if candidate["id"] == production["id"]
            and candidate["media"] == production["media"]
            and candidate["pdfScale"] == production["pdfScale"]
            and candidate["preferCssPageSize"] == production["preferCssPageSize"]
        ]
        self.assertEqual(len(matching_candidates), 1)


    def test_docs_export_pdf_smoke_route_rasterizes_client_live_svg_when_canvas_is_tainted(self) -> None:
        page_png = _make_rgba_png(320, 480)
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="480"><rect width="320" height="480" fill="#f7f4eb"/></svg>'
        payload = {
            "title": "SVG Snapshot Export Test",
            "layoutState": {
                "layout": {"mode": "custom", "custom": {"widthPx": 320, "heightPx": 480}, "margins": {"top": 48, "right": 48, "bottom": 48, "left": 48}},
                "view": {"mode": "paged", "zoom": 1},
            },
            "pages": [{"contentHtml": "<h1>Export Test</h1><p>Hello PDF.</p>", "overlayHtml": ""}],
            "pageImages": [
                {
                    "index": 1,
                    "svgDataUrl": _svg_data_url(svg),
                    "widthPx": 320,
                    "heightPx": 480,
                    "method": "client-svg-foreignobject-backend-rasterize",
                    "clientPngError": "Tainted canvases may not be exported.",
                }
            ],
            "plugins": [],
        }

        with patch(
            "main_computer.viewport_routes_docs.ViewportDocsRoutesMixin._render_document_pdf_smoke_pages_with_playwright",
            side_effect=AssertionError("export-only HTML fallback should not run when client SVG snapshots are present"),
        ), patch(
            "main_computer.viewport_routes_docs.ViewportDocsRoutesMixin._render_document_pdf_client_svg_pages_with_playwright",
            return_value=([page_png], {"source": "client-live-dom SVG snapshots rasterized by backend Chromium", "renderer": {"label": "test chromium"}}),
        ) as svg_renderer:
            data, content_type, disposition = self._post_binary("/api/applications/docs/export/pdf-smoke", payload)

        self.assertEqual(content_type, "application/zip")
        self.assertIn('filename="SVG-Snapshot-Export-Test-pdf-smoke.zip"', disposition)
        svg_renderer.assert_called_once()
        svg_pages = svg_renderer.call_args.args[0]
        self.assertEqual(svg_pages[0][0], 1)
        self.assertEqual(svg_pages[0][1], 1)
        self.assertIn("<svg", svg_pages[0][2])
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            self.assertEqual(bundle.read("pages/page-001.png"), page_png)

    def test_docs_export_pdf_smoke_route_falls_back_to_backend_png_capture(self) -> None:
        payload = {
            "title": "Export Test",
            "layoutState": {
                "layout": {"mode": "custom", "custom": {"widthPx": 320, "heightPx": 480}, "margins": {"top": 48, "right": 48, "bottom": 48, "left": 48}},
                "view": {"mode": "paged", "zoom": 1},
            },
            "pages": [{"contentHtml": "<h1>Export Test</h1><p>Hello PDF.</p>", "overlayHtml": ""}],
            "plugins": [],
        }
        page_png = _make_rgba_png(320, 480)

        with patch(
            "main_computer.viewport_routes_docs.ViewportDocsRoutesMixin._render_document_pdf_smoke_pages_with_playwright",
            return_value=([page_png], {"renderer": {"label": "test chromium"}}),
        ) as renderer:
            data, content_type, disposition = self._post_binary("/api/applications/docs/export/pdf-smoke", payload)

        self.assertEqual(content_type, "application/zip")
        self.assertIn('filename="Export-Test-pdf-smoke.zip"', disposition)
        renderer.assert_called_once()
        export_html = next(arg for arg in renderer.call_args.args if isinstance(arg, str) and "<!doctype html>" in arg)
        self.assertIn('<section class="mc-page">', export_html)
        self.assertIn("<h1>Export Test</h1>", export_html)
        self.assertIn("@page", export_html)
        self.assertIn("size: 320px 480px", export_html)
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            self.assertEqual(bundle.read("pages/page-001.png"), page_png)

    def test_docs_export_pdf_vector_route_returns_chromium_pdf(self) -> None:
        payload = {
            "title": "Vector Export Test",
            "layoutState": {
                "layout": {"mode": "preset", "preset": "letter", "margins": {"top": 96, "right": 96, "bottom": 96, "left": 96}},
                "view": {"mode": "paged", "zoom": 1},
            },
            "pages": [{"contentHtml": "<h1>Vector Export Test</h1><p>Hello PDF.</p>", "overlayHtml": ""}],
            "plugins": [],
        }

        with patch(
            "main_computer.viewport_routes_docs.ViewportDocsRoutesMixin._render_document_pdf_with_playwright",
            return_value=b"%PDF-1.7\n% fake vector pdf\n%%EOF\n",
        ) as renderer:
            data, content_type, disposition = self._post_binary("/api/applications/docs/export/pdf-vector", payload)

        self.assertTrue(data.startswith(b"%PDF-1.7"))
        self.assertEqual(content_type, "application/pdf")
        self.assertIn('filename="Vector-Export-Test.pdf"', disposition)
        renderer.assert_called_once()


    def test_docs_export_pdf_smoke_route_returns_html_payload_and_page_pngs(self) -> None:
        payload = {
            "title": "Smoke Test",
            "sourcePath": "main-computer-user-guide.md",
            "layoutState": {
                "layout": {"mode": "preset", "preset": "letter", "margins": {"top": 72, "right": 80, "bottom": 90, "left": 88}},
                "view": {"mode": "paged", "zoom": 1},
            },
            "pages": [
                {"contentHtml": "<h1>Smoke Test</h1><p>First page.</p>", "overlayHtml": "<span>overlay</span>"},
                {"contentHtml": "<p>Second page.</p>", "overlayHtml": ""},
            ],
            "plugins": [],
        }
        fake_png = _make_rgba_png(816, 1056)
        payload["pageImages"] = [
            {
                "index": 1,
                "dataUrl": _png_data_url(fake_png),
                "widthPx": 816,
                "heightPx": 1056,
                "sourceWidthPx": 816,
                "sourceHeightPx": 1056,
                "scaleX": 1,
                "scaleY": 1,
                "offsetWidthPx": 816,
                "offsetHeightPx": 1056,
                "clientWidthPx": 814,
                "clientHeightPx": 1054,
                "computedBoxSizing": "border-box",
                "computedBorderTopPx": 1,
                "computedBorderRightPx": 1,
                "computedBorderBottomPx": 1,
                "computedBorderLeftPx": 1,
                "method": "client-svg-foreignobject",
            },
            {"index": 2, "dataUrl": _png_data_url(fake_png), "widthPx": 816, "heightPx": 1056, "method": "client-svg-foreignobject"},
        ]
        with patch(
            "main_computer.viewport_routes_docs.ViewportDocsRoutesMixin._render_document_pdf_smoke_pages_with_playwright",
            side_effect=AssertionError("backend screenshot fallback should not run when client pageImages are present"),
        ):
            data, content_type, disposition = self._post_binary("/api/applications/docs/export/pdf-smoke", payload)

        self.assertEqual(content_type, "application/zip")
        self.assertIn('filename="Smoke-Test-pdf-smoke.zip"', disposition)
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            names = set(bundle.namelist())
            self.assertIn("manifest.json", names)
            self.assertIn("payload.json", names)
            self.assertIn("document-export.html", names)
            self.assertIn("pages/page-001.png", names)
            self.assertIn("pages/page-002.png", names)
            self.assertIn("pages/page-001.html", names)
            manifest = json.loads(bundle.read("manifest.json").decode("utf-8"))
            self.assertEqual(manifest["kind"], "main-computer-document-pdf-smoke-v1")
            self.assertEqual(manifest["payloadPageCount"], 2)
            self.assertEqual(manifest["capturedPageCount"], 2)
            self.assertEqual(manifest["capture"]["source"], "client-live-dom .mc-page images")
            first_capture = manifest["capture"]["pageImages"][0]
            self.assertEqual(first_capture["sourceWidthPx"], 816)
            self.assertEqual(first_capture["sourceHeightPx"], 1056)
            self.assertEqual(first_capture["scaleX"], 1)
            self.assertEqual(first_capture["scaleY"], 1)
            self.assertEqual(first_capture["offsetWidthPx"], 816)
            self.assertEqual(first_capture["offsetHeightPx"], 1056)
            self.assertEqual(first_capture["clientWidthPx"], 814)
            self.assertEqual(first_capture["clientHeightPx"], 1054)
            self.assertEqual(first_capture["computedBoxSizing"], "border-box")
            self.assertEqual(first_capture["computedBorderTopPx"], 1)
            self.assertEqual(first_capture["computedBorderRightPx"], 1)
            self.assertEqual(first_capture["computedBorderBottomPx"], 1)
            self.assertEqual(first_capture["computedBorderLeftPx"], 1)
            export_html = bundle.read("document-export.html").decode("utf-8")
            self.assertIn("<h1>Smoke Test</h1>", export_html)
            self.assertIn("size: 816px 1056px", export_html)
            self.assertEqual(bundle.read("pages/page-001.png"), fake_png)

    def test_docs_export_pdf_raster_smoke_route_generates_render_back_metrics_and_diff_images(self) -> None:
        payload = {
            "title": "Raster Proof Test",
            "sourcePath": "main-computer-user-guide.md",
            "layoutState": {
                "layout": {
                    "mode": "custom",
                    "custom": {"widthPx": 320, "heightPx": 480},
                    "margins": {"top": 24, "right": 24, "bottom": 24, "left": 24},
                },
                "view": {"mode": "paged", "zoom": 1},
            },
            "pages": [{"contentHtml": "<h1>Raster Proof Test</h1>", "overlayHtml": ""}],
            "plugins": [],
        }
        fake_png = _make_rgba_png(320, 480)
        payload["pageImages"] = [
            {
                "index": 1,
                "dataUrl": _png_data_url(fake_png),
                "widthPx": 320,
                "heightPx": 480,
                "sourceWidthPx": 320,
                "sourceHeightPx": 480,
                "scaleX": 1,
                "scaleY": 1,
                "method": "client-canvas-png",
            }
        ]

        with patch(
            "main_computer.viewport_routes_docs.ViewportDocsRoutesMixin._render_document_pdf_smoke_pages_with_playwright",
            side_effect=AssertionError("backend screenshot fallback should not run when client pageImages are present"),
        ), patch(
            "main_computer.viewport_routes_docs.ViewportDocsRoutesMixin._render_document_pdf_with_playwright",
            side_effect=RuntimeError("skip optional chromium HTML candidate"),
        ), patch(
            "main_computer.viewport_routes_docs.ViewportDocsRoutesMixin._document_pdf_import_pymupdf",
            return_value=object(),
        ), patch(
            "main_computer.viewport_routes_docs.ViewportDocsRoutesMixin._render_document_pdf_candidate_with_pymupdf",
            return_value=[fake_png],
        ):
            data, content_type, disposition = self._post_binary("/api/applications/docs/export/pdf-raster-smoke", payload)

        self.assertEqual(content_type, "application/zip")
        self.assertIn('filename="Raster-Proof-Test-raster-pdf-smoke.zip"', disposition)
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            names = set(bundle.namelist())
            self.assertIn("manifest.json", names)
            self.assertIn("metrics.json", names)
            self.assertIn("candidates/pixel-raster-css96.pdf", names)
            self.assertIn("rendered/pixel-raster-css96/page-001.png", names)
            self.assertIn("diffs/pixel-raster-css96/page-001-diff.png", names)
            manifest = json.loads(bundle.read("manifest.json").decode("utf-8"))
            self.assertEqual(manifest["kind"], "main-computer-document-raster-pdf-smoke-v1")
            metrics = json.loads(bundle.read("metrics.json").decode("utf-8"))
            self.assertEqual(metrics["sourcePageCount"], 1)
            comparison = metrics["renderComparison"]
            self.assertEqual(comparison["status"], "passed")
            self.assertEqual(comparison["productionCandidateId"], "pixel-raster-css96")
            candidates = {candidate["id"]: candidate for candidate in comparison["candidates"]}
            production = candidates["pixel-raster-css96"]
            self.assertTrue(production["includedInOverallStatus"])
            self.assertTrue(production["exactMatch"])
            self.assertEqual(production["changedPixels"], 0)
            self.assertEqual(production["pages"][0]["changedPixels"], 0)
            self.assertTrue(production["pages"][0]["dimensionsMatch"])
            self.assertTrue(production["pages"][0]["exactMatch"])

    def test_docs_export_pdf_vector_fit_smoke_route_scores_vector_candidates(self) -> None:
        payload = {
            "title": "Vector Fit Proof Test",
            "sourcePath": "main-computer-user-guide.md",
            "layoutState": {
                "layout": {
                    "mode": "custom",
                    "custom": {"widthPx": 320, "heightPx": 480},
                    "margins": {"top": 24, "right": 24, "bottom": 24, "left": 24},
                },
                "view": {"mode": "paged", "zoom": 1},
            },
            "pages": [{"contentHtml": "<h1>Vector Fit Proof Test</h1>", "overlayHtml": ""}],
            "vectorPages": [
                {
                    "index": 1,
                    "widthPx": 320,
                    "heightPx": 480,
                    "source": "client-live-dom-page-html",
                    "html": (
                        '<section class="mc-page" data-vector-live-page="1" '
                        'style="position:relative;width:320px;height:480px;background:#f7f4eb;">'
                        '<div class="mc-page-content" style="position:absolute;left:24px;top:24px;'
                        'font-family:Arial;font-size:16px;font-weight:700;text-transform:uppercase;">'
                        '<h1>Vector Fit Proof Test</h1>'
                        '</div></section>'
                    ),
                }
            ],
            "vectorPageSource": "client-live-dom-page-html",
            "plugins": [],
        }
        fake_png = _make_rgba_png(320, 480)
        payload["pageImages"] = [
            {
                "index": 1,
                "dataUrl": _png_data_url(fake_png),
                "widthPx": 320,
                "heightPx": 480,
                "sourceWidthPx": 320,
                "sourceHeightPx": 480,
                "scaleX": 1,
                "scaleY": 1,
                "method": "client-canvas-png",
            }
        ]

        with patch(
            "main_computer.viewport_routes_docs.ViewportDocsRoutesMixin._render_document_pdf_smoke_pages_with_playwright",
            side_effect=AssertionError("backend screenshot fallback should not run when client pageImages are present"),
        ), patch(
            "main_computer.viewport_routes_docs.ViewportDocsRoutesMixin._document_pdf_vector_fit_candidate_settings",
            return_value=[
                {
                    "id": "vector-screen-scale-1_000",
                    "media": "screen",
                    "pdfScale": 1.0,
                    "preferCssPageSize": True,
                    "description": "test candidate",
                }
            ],
        ), patch(
            "main_computer.viewport_routes_docs.ViewportDocsRoutesMixin._render_document_pdf_with_playwright",
            return_value=b"%PDF-1.7\n% fake vector candidate\n%%EOF\n",
        ) as vector_renderer, patch(
            "main_computer.viewport_routes_docs.ViewportDocsRoutesMixin._document_pdf_import_pymupdf",
            return_value=object(),
        ), patch(
            "main_computer.viewport_routes_docs.ViewportDocsRoutesMixin._render_document_pdf_candidate_with_pymupdf",
            return_value=[fake_png],
        ):
            data, content_type, disposition = self._post_binary("/api/applications/docs/export/pdf-vector-fit-smoke", payload)

        self.assertEqual(content_type, "application/zip")
        self.assertIn('filename="Vector-Fit-Proof-Test-vector-fit-smoke.zip"', disposition)
        self.assertEqual(vector_renderer.call_count, 1)
        rendered_html = str(vector_renderer.call_args.args[0])
        self.assertIn('data-vector-live-page="1"', rendered_html)
        self.assertIn("text-transform:uppercase", rendered_html)
        self.assertIn('data-vector-source="client-live-dom-page-html"', rendered_html)
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            names = set(bundle.namelist())
            self.assertIn("manifest.json", names)
            self.assertIn("metrics.json", names)
            self.assertTrue(any(name.startswith("candidates/vector-screen-scale-") for name in names))
            self.assertTrue(any(name.startswith("rendered/vector-fit/vector-screen-scale-") for name in names))
            self.assertTrue(any(name.startswith("diffs/vector-fit/vector-screen-scale-") for name in names))
            manifest = json.loads(bundle.read("manifest.json").decode("utf-8"))
            self.assertEqual(manifest["kind"], "main-computer-document-vector-fit-smoke-v1")
            metrics = json.loads(bundle.read("metrics.json").decode("utf-8"))
            self.assertEqual(metrics["kind"], "main-computer-document-vector-fit-smoke-metrics-v1")
            self.assertEqual(metrics["sourcePageCount"], 1)
            self.assertEqual(metrics["vectorSource"]["source"], "client-live-dom-page-html")
            self.assertTrue(metrics["vectorSource"]["valid"])
            self.assertEqual(metrics["vectorSource"]["pageCount"], 1)
            self.assertEqual(metrics["sourceImageCapture"]["proofLevel"], "client-live-dom-png")
            self.assertEqual(metrics["sourceImageCapture"]["directClientPngPages"], 1)
            self.assertEqual(metrics["sourceImageCapture"]["backendRasterizedSvgPages"], 0)
            self.assertTrue(metrics["renderComparison"]["vectorSourceValid"])
            self.assertEqual(metrics["renderComparison"]["status"], "scored")
            self.assertIsNotNone(metrics["bestCandidate"])
            self.assertIn("pdfIntegrity", metrics["bestCandidate"])
            self.assertEqual(metrics["bestCandidate"]["pdfIntegrity"]["status"], "unavailable")
            self.assertIn("horizontalScanScore", metrics["renderComparison"]["candidates"][0]["pages"][0])
            self.assertIn("mipmapScore", metrics["renderComparison"]["candidates"][0]["pages"][0])

    def test_docs_vector_fit_pdf_integrity_summary_marks_text_vector_candidates(self) -> None:
        class DummyDocsRoutes(ViewportDocsRoutesMixin):
            pass

        class FakeTextPage:
            def get_text(self, _kind: str = "text") -> str:
                return "Selectable vector text"

            def get_images(self, full: bool = True) -> list[object]:
                return []

        class FakeImageOnlyPage:
            def get_text(self, _kind: str = "text") -> str:
                return ""

            def get_images(self, full: bool = True) -> list[object]:
                return [object(), object()]

        class FakeDocument(list):
            def __init__(self, pages: list[object]) -> None:
                super().__init__(pages)
                self.closed = False

            def close(self) -> None:
                self.closed = True

        class FakeFitz:
            def __init__(self, document: FakeDocument) -> None:
                self.document = document

            def open(self, *, stream: bytes, filetype: str) -> FakeDocument:
                self.stream = stream
                self.filetype = filetype
                return self.document

        route = DummyDocsRoutes()

        text_doc = FakeDocument([FakeTextPage()])
        text_fitz = FakeFitz(text_doc)
        text_summary = route._document_pdf_vector_pdf_integrity_summary(text_fitz, b"%PDF text")
        self.assertEqual(text_fitz.stream, b"%PDF text")
        self.assertEqual(text_fitz.filetype, "pdf")
        self.assertTrue(text_doc.closed)
        self.assertEqual(text_summary["status"], "scanned")
        self.assertEqual(text_summary["pageCount"], 1)
        self.assertGreater(text_summary["extractableTextChars"], 0)
        self.assertEqual(text_summary["embeddedImageRefs"], 0)
        self.assertTrue(text_summary["hasExtractableText"])
        self.assertFalse(text_summary["imageOnly"])
        self.assertTrue(text_summary["likelyVectorText"])

        image_doc = FakeDocument([FakeImageOnlyPage()])
        image_summary = route._document_pdf_vector_pdf_integrity_summary(FakeFitz(image_doc), b"%PDF image")
        self.assertEqual(image_summary["status"], "scanned")
        self.assertFalse(image_summary["hasExtractableText"])
        self.assertTrue(image_summary["imageOnly"])
        self.assertFalse(image_summary["likelyVectorText"])

    def test_docs_export_pdf_vector_fit_smoke_uses_deterministic_tie_break(self) -> None:
        payload = {
            "title": "Vector Fit Tie Test",
            "layoutState": {
                "layout": {
                    "mode": "custom",
                    "custom": {"widthPx": 320, "heightPx": 480},
                    "margins": {"top": 24, "right": 24, "bottom": 24, "left": 24},
                },
                "view": {"mode": "paged", "zoom": 1},
            },
            "pages": [{"contentHtml": "<h1>Fallback path should not be used</h1>", "overlayHtml": ""}],
            "vectorPages": [
                {
                    "index": 1,
                    "widthPx": 320,
                    "heightPx": 480,
                    "source": "client-live-dom-page-html",
                    "html": (
                        '<section class="mc-page" data-vector-live-page="1" '
                        'style="position:relative;width:320px;height:480px;background:#f7f4eb;">'
                        '<div class="mc-page-content" style="position:absolute;left:24px;top:24px;">'
                        "Tie candidate smoke test"
                        "</div></section>"
                    ),
                }
            ],
            "vectorPageSource": "client-live-dom-page-html",
            "plugins": [],
        }
        fake_png = _make_rgba_png(320, 480)
        payload["pageImages"] = [
            {
                "index": 1,
                "dataUrl": _png_data_url(fake_png),
                "widthPx": 320,
                "heightPx": 480,
                "sourceWidthPx": 320,
                "sourceHeightPx": 480,
                "scaleX": 1,
                "scaleY": 1,
                "method": "client-canvas-png",
            }
        ]

        with patch(
            "main_computer.viewport_routes_docs.ViewportDocsRoutesMixin._document_pdf_vector_fit_candidate_settings",
            return_value=[
                {
                    "id": "vector-screen-scale-1_000",
                    "media": "screen",
                    "pdfScale": 1.0,
                    "preferCssPageSize": True,
                    "description": "screen candidate",
                },
                {
                    "id": "vector-print-scale-1_000",
                    "media": "print",
                    "pdfScale": 1.0,
                    "preferCssPageSize": True,
                    "description": "print candidate",
                },
            ],
        ), patch(
            "main_computer.viewport_routes_docs.ViewportDocsRoutesMixin._render_document_pdf_with_playwright",
            return_value=b"%PDF-1.7\n% fake vector candidate\n%%EOF\n",
        ), patch(
            "main_computer.viewport_routes_docs.ViewportDocsRoutesMixin._document_pdf_import_pymupdf",
            return_value=object(),
        ), patch(
            "main_computer.viewport_routes_docs.ViewportDocsRoutesMixin._render_document_pdf_candidate_with_pymupdf",
            return_value=[fake_png],
        ):
            data, content_type, _disposition = self._post_binary(
                "/api/applications/docs/export/pdf-vector-fit-smoke",
                payload,
            )

        self.assertEqual(content_type, "application/zip")
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            metrics = json.loads(bundle.read("metrics.json").decode("utf-8"))

        self.assertEqual(metrics["bestCandidate"]["id"], "vector-print-scale-1_000")
        selection = metrics["bestCandidate"]["selection"]
        self.assertEqual(selection["rankOrder"], ["meanFitScore", "maxFitScore", "candidateId"])
        self.assertEqual(selection["tieBreak"], "candidateId")
        self.assertEqual(selection["exactTieCount"], 2)
        self.assertEqual(
            [tie["id"] for tie in selection["exactTies"]],
            ["vector-print-scale-1_000", "vector-screen-scale-1_000"],
        )
        self.assertEqual(metrics["renderComparison"]["bestCandidateSelection"], selection)

    def test_docs_vector_fit_source_image_capture_summary_marks_svg_backend_fallback(self) -> None:
        class DummyDocsRoutes(ViewportDocsRoutesMixin):
            pass

        route = DummyDocsRoutes()
        summary = route._document_pdf_source_image_capture_summary(
            {
                "source": "client-live-dom SVG snapshots rasterized by backend Chromium",
                "pageCount": 2,
                "pageImages": [
                    {
                        "index": 1,
                        "method": "client-svg-foreignobject-backend-rasterize",
                        "clientPngError": "Tainted canvases may not be exported.",
                    },
                    {
                        "index": 2,
                        "method": "client-svg-foreignobject-backend-rasterize",
                        "clientPngError": "Tainted canvases may not be exported.",
                    },
                ],
                "clientLiveDomSvgFallbackPages": 2,
            }
        )

        self.assertTrue(summary["valid"])
        self.assertTrue(summary["sourceIsLiveDom"])
        self.assertEqual(summary["proofLevel"], "live-dom-svg-backend-rasterized")
        self.assertEqual(summary["backendRasterizedSvgPages"], 2)
        self.assertEqual(summary["directClientPngPages"], 0)
        self.assertEqual(summary["clientPngErrorCount"], 1)
        self.assertIn("not direct client canvas PNG export", summary["note"])

    def test_docs_vector_fit_ink_mask_ignores_thin_page_border(self) -> None:
        class DummyDocsRoutes(ViewportDocsRoutesMixin):
            pass

        route = DummyDocsRoutes()
        width = 160
        height = 220
        rgb = bytearray((247, 244, 235) * width * height)

        def set_pixel(x: int, y: int, color: tuple[int, int, int]) -> None:
            offset = (y * width + x) * 3
            rgb[offset : offset + 3] = bytes(color)

        for x in range(width):
            set_pixel(x, 0, (40, 40, 40))
            set_pixel(x, height - 1, (40, 40, 40))
        for y in range(height):
            set_pixel(0, y, (40, 40, 40))
            set_pixel(width - 1, y, (40, 40, 40))
        for y in range(60, 84):
            for x in range(45, 92):
                set_pixel(x, y, (20, 20, 20))

        png = route._encode_document_pdf_rgb_png(width, height, bytes(rgb))
        page_metric, _diff_png = route._compare_document_pdf_text_mask_fit(source_png=png, rendered_png=png)

        self.assertTrue(page_metric["maskQuality"]["valid"])
        self.assertEqual(page_metric["fitScore"], 0)
        self.assertGreater(page_metric["sourceInkBoundingBox"]["left"], 1)
        self.assertGreater(page_metric["sourceInkBoundingBox"]["top"], 1)
        self.assertLess(page_metric["sourceInkBoundingBox"]["right"], width - 2)
        self.assertLess(page_metric["sourceInkBoundingBox"]["bottom"], height - 2)

    def test_document_pdf_vector_payload_inlines_computed_page_styles(self) -> None:
        script = Path("main_computer/web/applications/scripts/document-pdf.js").read_text(encoding="utf-8")

        self.assertIn("function copyComputedStylesForPdfVectorExport", script)
        self.assertIn("function cloneLivePageForPdfVectorExport", script)
        self.assertIn('"text-transform"', script)
        self.assertIn("for (let index = 0; index < computed.length; index += 1)", script)
        self.assertIn('clone.setAttribute("data-vector-live-page", String(index))', script)
        self.assertIn("payload.vectorPages = collectPdfVectorPages()", script)
        self.assertIn("buildPdfPayload({includeLiveVectorPages: true, includeLivePageImages: true})", script)

    def test_docs_export_pdf_falls_back_to_local_chromium_when_playwright_import_fails(self) -> None:
        class DummyDocsRoutes(ViewportDocsRoutesMixin):
            pass

        original_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "playwright" or name == "playwright.sync_api" or name.startswith("playwright."):
                raise ModuleNotFoundError("No module named 'playwright'")
            return original_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=fake_import), patch.object(
            ViewportDocsRoutesMixin,
            "_render_document_pdf_with_chromium_cli",
            return_value=b"%PDF-1.7\n% fallback pdf\n%%EOF\n",
        ) as chromium_fallback:
            pdf_bytes = DummyDocsRoutes()._render_document_pdf_with_playwright("<!doctype html><html></html>", 816, 1056)

        self.assertTrue(pdf_bytes.startswith(b"%PDF-1.7"))
        chromium_fallback.assert_called_once()

    def test_docs_export_pdf_uses_live_vector_page_html_when_present(self) -> None:
        payload = {
            "title": "Live Vector DOM Test",
            "layoutState": {
                "layout": {
                    "mode": "custom",
                    "custom": {"widthPx": 320, "heightPx": 480},
                    "margins": {"top": 24, "right": 24, "bottom": 24, "left": 24},
                }
            },
            "pages": [{"contentHtml": "<h1>Fallback path should not be used</h1>", "overlayHtml": ""}],
            "vectorPages": [
                {
                    "index": 1,
                    "widthPx": 320,
                    "heightPx": 480,
                    "source": "client-live-dom-page-html",
                    "html": (
                        '<section class="mc-page" data-vector-live-page="1">'
                        '<div class="mc-page-content" style="text-transform: uppercase;">Live vector DOM wins</div>'
                        '</section>'
                    ),
                }
            ],
        }

        class DummyDocsRoutes(ViewportDocsRoutesMixin):
            pass

        title, filename, export_html, width_px, height_px = DummyDocsRoutes()._build_document_pdf_export_html(payload)

        self.assertEqual(title, "Live Vector DOM Test")
        self.assertEqual(filename, "Live-Vector-DOM-Test.pdf")
        self.assertEqual(width_px, 320)
        self.assertEqual(height_px, 480)
        self.assertIn('data-vector-source="client-live-dom-page-html"', export_html)
        self.assertIn('data-vector-live-page="1"', export_html)
        self.assertIn("Live vector DOM wins", export_html)
        self.assertNotIn("Fallback path should not be used", export_html)

    def test_docs_export_pdf_rejects_oversized_page_content(self) -> None:
        error = self._post_error(
            "/api/applications/docs/export/pdf",
            {
                "title": "Too Large",
                "pages": [{"contentHtml": "x" * 1_500_001, "overlayHtml": ""}],
            },
        )

        self.assertEqual(error.code, 400)

    def test_docs_ai_rejects_empty_instruction(self) -> None:
        error = self._post_error(
            "/api/applications/docs/ai",
            {"instruction": "", "document": {"text": "Some document text.", "html": "<p>Some document text.</p>"}},
        )

        self.assertEqual(error.code, 400)

    def test_docs_ai_rejects_empty_document_text(self) -> None:
        error = self._post_error(
            "/api/applications/docs/ai",
            {"instruction": "Improve this.", "document": {"text": "", "html": ""}},
        )

        self.assertEqual(error.code, 400)

    def test_docs_ai_returns_suggestion_without_writing_pretty_doc(self) -> None:
        guide = self.repo / "pretty_docs" / "main-computer-user-guide.md"
        before = guide.read_text(encoding="utf-8")
        data = self._post(
            "/api/applications/docs/ai",
            {
                "action": "rewrite",
                "instruction": "Improve this selection.",
                "document": {
                    "path": "main-computer-user-guide.md",
                    "title": "Guide",
                    "kind": "draft",
                    "revision_hash": "abc",
                    "html": "<p>Some document text.</p>",
                    "text": "Some document text.",
                    "layout": {},
                },
                "anchor": {"range": {"selected_text": "Some document text."}, "block": {"text": "Some document text."}},
                "thread": {"id": "thread-1", "messages": []},
            },
        )

        self.assertTrue(data["ok"])
        self.assertEqual(data["provider"], "fake")
        self.assertEqual(data["model"], "fake-doc-model")
        self.assertEqual(data["suggestion"]["operation"], "replace_selection")
        self.assertEqual(data["suggestion"]["replacement_text"], "Clearer text.")
        prompt = "\n".join(message.content for message in self.fake_provider.messages)
        self.assertIn("Improve this selection.", prompt)
        self.assertIn("Some document text.", prompt)
        self.assertEqual(guide.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
