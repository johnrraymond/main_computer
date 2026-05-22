from __future__ import annotations

import base64
import binascii
import io
import platform
import re
import struct
import sys
import zipfile
import zlib

from html import escape as html_escape

from main_computer.document_pdf_raster import (
    build_css_pixel_raster_pdf,
    build_png_xobject_pdf,
    build_raster_image_html,
    decode_png_image,
    read_png_info,
)
from main_computer.viewport_state import *  # noqa: F401,F403

class ViewportDocsRoutesMixin:
    def _handle_docs_files(self) -> None:
        try:
            self._read_json()
            documents = self._pretty_docs_documents()
            self.server.signal("api-docs-files", count=len(documents))
            self._send_json(
                {
                    "ok": True,
                    "root": "pretty_docs",
                    "documents": documents,
                    "count": len(documents),
                    "read_only": True,
                    "draft_storage": "backend",
                }
            )
        except Exception as exc:
            self.server.signal("api-docs-error", route="files", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_docs_read(self) -> None:
        try:
            body = self._read_json()
            path = self._pretty_docs_path(str(body.get("path", "") or ""))
            content = path.read_text(encoding="utf-8", errors="replace")
            stat = path.stat()
            relative_path = path.relative_to(self._pretty_docs_root()).as_posix()
            metadata = self._pretty_docs_index_metadata().get(relative_path, {})
            self.server.signal("api-docs-read", path=relative_path, bytes=stat.st_size)
            self._send_json(
                {
                    "ok": True,
                    "root": "pretty_docs",
                    "path": relative_path,
                    "display_path": f"pretty_docs/{relative_path}",
                    "title": str(metadata.get("title") or self._pretty_docs_title(relative_path)),
                    "kind": str(metadata.get("kind") or self._pretty_docs_kind(path)),
                    "content": content,
                    "bytes": stat.st_size,
                    "mtime": stat.st_mtime,
                    "content_hash": self._pretty_docs_content_hash(path),
                    "read_only": True,
                    "draft_storage": "backend",
                }
            )
        except Exception as exc:
            self.server.signal("api-docs-error", route="read", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_docs_draft_read(self) -> None:
        try:
            body = self._read_json()
            path, relative_path = self._docs_draft_path(str(body.get("path", "") or ""))
            if not path.exists():
                self.server.signal("api-docs-draft-read", path=relative_path, exists=False)
                self._send_json(
                    {
                        "ok": True,
                        "root": "runtime/document_editor_drafts",
                        "path": relative_path,
                        "exists": False,
                        "html": None,
                        "layout": None,
                        "revision": None,
                    }
                )
                return
            payload = self._read_docs_draft_file(path)
            self.server.signal("api-docs-draft-read", path=relative_path, exists=True)
            self._send_json(
                {
                    "ok": True,
                    "root": "runtime/document_editor_drafts",
                    "path": relative_path,
                    "exists": True,
                    "html": str(payload.get("html") or ""),
                    "layout": payload.get("layout") if isinstance(payload.get("layout"), dict) else None,
                    "revision": payload.get("revision") if isinstance(payload.get("revision"), dict) else None,
                    "updated_at": str(payload.get("updated_at") or ""),
                    "source": "backend-draft",
                }
            )
        except Exception as exc:
            self.server.signal("api-docs-error", route="draft-read", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_docs_draft_write(self) -> None:
        try:
            body = self._read_json()
            path, relative_path = self._docs_draft_path(str(body.get("path", "") or ""))
            html = str(body.get("html") or "")
            if len(html.encode("utf-8")) > 5 * 1024 * 1024:
                raise ValueError("Document draft is too large.")
            layout = body.get("layout") if isinstance(body.get("layout"), dict) else None
            revision = body.get("revision") if isinstance(body.get("revision"), dict) else None
            payload = {
                "format": 1,
                "path": relative_path,
                "html": html,
                "layout": layout,
                "revision": revision,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_name(f"{path.name}.tmp")
            tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            tmp_path.replace(path)
            self.server.signal("api-docs-draft-write", path=relative_path, bytes=len(html.encode("utf-8")))
            self._send_json(
                {
                    "ok": True,
                    "root": "runtime/document_editor_drafts",
                    "path": relative_path,
                    "exists": True,
                    "updated_at": payload["updated_at"],
                    "source": "backend-draft",
                }
            )
        except Exception as exc:
            self.server.signal("api-docs-error", route="draft-write", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_docs_draft_delete(self) -> None:
        try:
            body = self._read_json()
            path, relative_path = self._docs_draft_path(str(body.get("path", "") or ""))
            existed = path.exists()
            if existed:
                path.unlink()
            self.server.signal("api-docs-draft-delete", path=relative_path, existed=existed)
            self._send_json(
                {
                    "ok": True,
                    "root": "runtime/document_editor_drafts",
                    "path": relative_path,
                    "existed": existed,
                    "exists": False,
                }
            )
        except Exception as exc:
            self.server.signal("api-docs-error", route="draft-delete", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_docs_ai(self) -> None:
        try:
            body = self._read_json()
            instruction = str(body.get("instruction") or "").strip()
            document_body = body.get("document") if isinstance(body.get("document"), dict) else {}
            document_text = str(document_body.get("text") or "").strip()
            document_html = str(document_body.get("html") or "")
            if not instruction:
                self._send_json({"ok": False, "error": "instruction is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            if not document_text:
                self._send_json({"ok": False, "error": "document text is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            if len(instruction) > 4000:
                self._send_json({"ok": False, "error": "instruction is too long"}, status=HTTPStatus.BAD_REQUEST)
                return
            if len(document_text) > 120000:
                document_text = document_text[:120000]
            anchor = body.get("anchor") if isinstance(body.get("anchor"), dict) else {}
            thread = body.get("thread") if isinstance(body.get("thread"), dict) else {}
            messages = thread.get("messages") if isinstance(thread.get("messages"), list) else []
            selected_text = str((anchor.get("range") or {}).get("selected_text") or "")
            block_text = str((anchor.get("block") or {}).get("text") or "")
            preferred_operation = str(body.get("preferred_operation") or "").strip()
            system_prompt = (
                "You are the Document Editor AI. Your primary job is to improve the user's document text. "
                "Use the full document for context, but only edit the locked target selection/caret/block unless "
                "the user asks for a whole-document edit. Return concise, high-quality writing. Do not invent facts. "
                "Preserve meaning unless the instruction asks for transformation. Prefer direct replacement text over commentary. "
                "Return strict JSON only with keys: content, suggestion. suggestion must include operation, replacement_text, "
                "replacement_html, and rationale. Operation must be one of replace_selection, insert_at_caret, replace_block, "
                "append_after_selection, replace_document, comment_only."
            )
            user_prompt = "\n".join(
                [
                    f"Instruction: {instruction}",
                    f"Preferred operation: {preferred_operation or 'infer'}",
                    f"Document path: {document_body.get('path') or ''}",
                    f"Document kind: {document_body.get('kind') or 'draft'}",
                    f"Anchor selected text: {selected_text}",
                    f"Anchor block text: {block_text}",
                    f"Recent thread messages: {json.dumps(messages[-6:], ensure_ascii=False)}",
                    "Full current document text:",
                    document_text,
                    "Return JSON only.",
                ]
            )
            response = self.server.computer.provider.chat(
                [
                    ChatMessage(role="system", content=system_prompt),
                    ChatMessage(role="user", content=user_prompt),
                ]
            )
            raw_content = response.content or ""
            try:
                parsed = json.loads(raw_content)
            except json.JSONDecodeError:
                match = re.search(r"\{.*\}", raw_content, re.S)
                parsed = json.loads(match.group(0)) if match else {
                    "content": raw_content,
                    "suggestion": {"operation": "comment_only", "replacement_text": "", "replacement_html": None, "rationale": raw_content},
                }
            suggestion = parsed.get("suggestion") if isinstance(parsed, dict) and isinstance(parsed.get("suggestion"), dict) else {}
            operation = str(suggestion.get("operation") or "comment_only")
            allowed_operations = {"replace_selection", "insert_at_caret", "replace_block", "append_after_selection", "replace_document", "comment_only"}
            if operation not in allowed_operations:
                operation = "comment_only"
            self.server.signal("api-docs-ai", action=str(body.get("action") or "custom"), operation=operation)
            self._send_json(
                {
                    "ok": True,
                    "content": str(parsed.get("content") if isinstance(parsed, dict) else raw_content),
                    "suggestion": {
                        "operation": operation,
                        "replacement_text": str(suggestion.get("replacement_text") or ""),
                        "replacement_html": suggestion.get("replacement_html") if suggestion.get("replacement_html") is None else str(suggestion.get("replacement_html")),
                        "rationale": str(suggestion.get("rationale") or ""),
                    },
                    "provider": response.provider,
                    "model": response.model,
                }
            )
        except Exception as exc:
            self.server.signal("api-docs-error", route="ai", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_docs_export_pdf(self) -> None:
        try:
            body = self._read_json()
            title, filename, export_html, width_px, height_px = self._build_document_pdf_export_html(body)
            vector_settings = self._document_pdf_production_vector_settings()
            pdf_bytes = self._render_document_pdf_with_playwright(
                export_html,
                width_px,
                height_px,
                media=str(vector_settings["media"]),
                pdf_scale=float(vector_settings["pdfScale"]),
                prefer_css_page_size=bool(vector_settings["preferCssPageSize"]),
            )
            self.server.signal(
                "api-docs-export-pdf",
                title=title,
                bytes=len(pdf_bytes),
                pages=len(body.get("pages", []) if isinstance(body.get("pages"), list) else []),
                strategy="chromium-vector-live-dom-fixed-fit",
                vector_setting=str(vector_settings["id"]),
                media=str(vector_settings["media"]),
                pdf_scale=float(vector_settings["pdfScale"]),
            )
            self._send_pdf_bytes(
                pdf_bytes,
                filename,
                extra_headers={
                    "X-Main-Computer-PDF-Strategy": "chromium-vector-live-dom-fixed-fit",
                    "X-Main-Computer-PDF-Vector-Setting": str(vector_settings["id"]),
                },
            )
        except RuntimeError as exc:
            self.server.signal("api-docs-error", route="export-pdf", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.SERVICE_UNAVAILABLE)
        except Exception as exc:
            self.server.signal("api-docs-error", route="export-pdf", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_docs_export_pdf_vector(self) -> None:
        try:
            body = self._read_json()
            title, filename, export_html, width_px, height_px = self._build_document_pdf_export_html(body)
            vector_settings = self._document_pdf_production_vector_settings()
            pdf_bytes = self._render_document_pdf_with_playwright(
                export_html,
                width_px,
                height_px,
                media=str(vector_settings["media"]),
                pdf_scale=float(vector_settings["pdfScale"]),
                prefer_css_page_size=bool(vector_settings["preferCssPageSize"]),
            )
            self.server.signal(
                "api-docs-export-pdf-vector",
                title=title,
                bytes=len(pdf_bytes),
                pages=len(body.get("pages", []) if isinstance(body.get("pages"), list) else []),
                strategy="chromium-vector-live-dom-fixed-fit",
                vector_setting=str(vector_settings["id"]),
                media=str(vector_settings["media"]),
                pdf_scale=float(vector_settings["pdfScale"]),
            )
            self._send_pdf_bytes(
                pdf_bytes,
                filename,
                extra_headers={
                    "X-Main-Computer-PDF-Strategy": "chromium-vector-live-dom-fixed-fit",
                    "X-Main-Computer-PDF-Vector-Setting": str(vector_settings["id"]),
                },
            )
        except RuntimeError as exc:
            self.server.signal("api-docs-error", route="export-pdf-vector", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.SERVICE_UNAVAILABLE)
        except Exception as exc:
            self.server.signal("api-docs-error", route="export-pdf-vector", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)


    def _handle_docs_export_pdf_smoke(self) -> None:
        try:
            body = self._read_json()
            title, pdf_filename, export_html, width_px, height_px = self._build_document_pdf_export_html(body)
            pages = self._document_pdf_pages(body)
            page_pngs, capture_info = self._document_pdf_page_pngs_for_body(body, export_html, width_px, height_px)
            bundle_bytes, bundle_filename = self._build_document_pdf_smoke_bundle(
                title=title,
                pdf_filename=pdf_filename,
                body=body,
                export_html=export_html,
                width_px=width_px,
                height_px=height_px,
                pages=pages,
                page_pngs=page_pngs,
                capture_info=capture_info,
            )
            self.server.signal("api-docs-export-pdf-smoke", title=title, bytes=len(bundle_bytes), pages=len(page_pngs))
            self._send_zip_bytes(bundle_bytes, bundle_filename)
        except RuntimeError as exc:
            self.server.signal("api-docs-error", route="export-pdf-smoke", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.SERVICE_UNAVAILABLE)
        except Exception as exc:
            self.server.signal("api-docs-error", route="export-pdf-smoke", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_docs_export_pdf_raster_smoke(self) -> None:
        try:
            body = self._read_json()
            title, pdf_filename, export_html, width_px, height_px = self._build_document_pdf_export_html(body)
            pages = self._document_pdf_pages(body)
            page_pngs, capture_info = self._document_pdf_page_pngs_for_body(body, export_html, width_px, height_px)
            candidate_files, metrics = self._build_document_pdf_raster_smoke_candidates(
                title=title,
                page_pngs=page_pngs,
                width_px=width_px,
                height_px=height_px,
            )
            metrics["sourcePageCount"] = len(page_pngs)
            proof_files, render_comparison = self._build_document_pdf_raster_smoke_render_proof(
                page_pngs=page_pngs,
                candidate_files=candidate_files,
            )
            metrics["renderComparison"] = render_comparison
            bundle_bytes, bundle_filename = self._build_document_pdf_smoke_bundle(
                title=title,
                pdf_filename=pdf_filename,
                body=body,
                export_html=export_html,
                width_px=width_px,
                height_px=height_px,
                pages=pages,
                page_pngs=page_pngs,
                capture_info=capture_info,
                kind="main-computer-document-raster-pdf-smoke-v1",
                bundle_filename=self._document_pdf_raster_smoke_filename(title),
                extra_files=[
                    *candidate_files,
                    *proof_files,
                    (
                        "metrics.json",
                        json.dumps(metrics, ensure_ascii=False, indent=2).encode("utf-8"),
                        "application/json",
                    ),
                ],
                readme_extra=(
                    "\n## Raster PDF candidates\n\n"
                    "This bundle also includes candidate PDFs under `candidates/`. "
                    "`pixel-raster-css96.pdf` is the production-style candidate: it embeds each source PNG as a full-page PDF image and maps 96 CSS pixels to 72 PDF points. "
                    "`identity-pixel-points.pdf` is a raw identity debugging candidate, not the final physical document size. "
                    "`raster-chromium-html-img.pdf`, when present, asks Chromium to print HTML pages containing those PNGs as full-page images. "
                    "Render-back proof files are written under `rendered/<candidate>/` and `diffs/<candidate>/` when PyMuPDF is installed. "
                    "`metrics.json` records the 96 DPI source-vs-render comparison and treats `pixel-raster-css96` as the production candidate.\n"
                ),
            )
            self.server.signal("api-docs-export-pdf-raster-smoke", title=title, bytes=len(bundle_bytes), pages=len(page_pngs), candidates=len(candidate_files))
            self._send_zip_bytes(bundle_bytes, bundle_filename)
        except RuntimeError as exc:
            self.server.signal("api-docs-error", route="export-pdf-raster-smoke", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.SERVICE_UNAVAILABLE)
        except Exception as exc:
            self.server.signal("api-docs-error", route="export-pdf-raster-smoke", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_docs_export_pdf_vector_fit_smoke(self) -> None:
        try:
            body = self._read_json()
            title, pdf_filename, export_html, width_px, height_px = self._build_document_pdf_export_html(body)
            pages = self._document_pdf_pages(body)
            vector_source_info = self._document_pdf_vector_source_info(body, width_px, height_px)
            page_pngs, capture_info = self._document_pdf_page_pngs_for_body(body, export_html, width_px, height_px)
            candidate_files, proof_files, metrics = self._build_document_pdf_vector_fit_smoke_candidates(
                title=title,
                export_html=export_html,
                width_px=width_px,
                height_px=height_px,
                page_pngs=page_pngs,
                vector_source_info=vector_source_info,
            )
            metrics["sourcePageCount"] = len(page_pngs)
            metrics["sourceImageCapture"] = self._document_pdf_source_image_capture_summary(capture_info)
            bundle_bytes, bundle_filename = self._build_document_pdf_smoke_bundle(
                title=title,
                pdf_filename=pdf_filename,
                body=body,
                export_html=export_html,
                width_px=width_px,
                height_px=height_px,
                pages=pages,
                page_pngs=page_pngs,
                capture_info=capture_info,
                kind="main-computer-document-vector-fit-smoke-v1",
                bundle_filename=self._document_pdf_vector_fit_smoke_filename(title),
                extra_files=[
                    *candidate_files,
                    *proof_files,
                    (
                        "metrics.json",
                        json.dumps(metrics, ensure_ascii=False, indent=2).encode("utf-8"),
                        "application/json",
                    ),
                ],
                readme_extra=(
                    "\n## Vector fit smoke\n\n"
                    "This bundle compares live page PNG captures to backend-generated vector PDF candidates. "
                    "Each vector candidate is rendered back to PNG at 96 DPI and scored with ink-mask stencils, "
                    "horizontal/vertical projection scans, filled glyph masks, and mipmap occupancy grids. "
                    "`metrics.json` names the best backend setting candidate under `bestCandidate` and records "
                    "whether the source oracle images were direct client PNGs or live-DOM SVG snapshots rasterized by backend Chromium. "
                    "The bundle is a tuning aid for the fast vector PDF path, not a raster-PDF production path.\n"
                ),
            )
            self.server.signal(
                "api-docs-export-pdf-vector-fit-smoke",
                title=title,
                bytes=len(bundle_bytes),
                pages=len(page_pngs),
                candidates=len(candidate_files),
                best=metrics.get("bestCandidate", {}).get("id") if isinstance(metrics.get("bestCandidate"), dict) else "",
            )
            self._send_zip_bytes(bundle_bytes, bundle_filename)
        except RuntimeError as exc:
            self.server.signal("api-docs-error", route="export-pdf-vector-fit-smoke", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.SERVICE_UNAVAILABLE)
        except Exception as exc:
            self.server.signal("api-docs-error", route="export-pdf-vector-fit-smoke", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _send_pdf_bytes(self, data: bytes, filename: str, extra_headers: dict[str, str] | None = None) -> None:
        safe_filename = self._document_pdf_filename(filename)
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", f'attachment; filename="{safe_filename}"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            for header, value in (extra_headers or {}).items():
                if header and value is not None:
                    self.send_header(str(header), str(value))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
            self.server.signal("client-disconnected", path=self.path, error=exc)


    def _send_zip_bytes(self, data: bytes, filename: str) -> None:
        safe_filename = self._document_pdf_smoke_filename(filename)
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{safe_filename}"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
            self.server.signal("client-disconnected", path=self.path, error=exc)

    def _document_pdf_filename(self, value: object) -> str:
        stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "main-computer-document")).strip(".-_")
        if not stem:
            stem = "main-computer-document"
        if not stem.lower().endswith(".pdf"):
            stem = f"{stem}.pdf"
        return stem[:180]


    def _document_pdf_smoke_filename(self, value: object) -> str:
        stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "main-computer-document")).strip(".-_")
        if not stem:
            stem = "main-computer-document"
        if stem.lower().endswith(".pdf"):
            stem = stem[:-4].rstrip(".-_") or "main-computer-document"
        if stem.lower().endswith(".zip"):
            stem = stem[:-4].rstrip(".-_") or "main-computer-document"
        if not (stem.lower().endswith("-pdf-smoke") or stem.lower().endswith("-raster-pdf-smoke") or stem.lower().endswith("-vector-fit-smoke")):
            stem = f"{stem}-pdf-smoke"
        return f"{stem[:170]}.zip"

    def _document_pdf_raster_smoke_filename(self, value: object) -> str:
        stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "main-computer-document")).strip(".-_")
        if not stem:
            stem = "main-computer-document"
        for suffix in (".pdf", ".zip"):
            if stem.lower().endswith(suffix):
                stem = stem[: -len(suffix)].rstrip(".-_") or "main-computer-document"
        if stem.lower().endswith("-pdf-smoke"):
            stem = stem[: -len("-pdf-smoke")].rstrip(".-_") or "main-computer-document"
        if not stem.lower().endswith("-raster-pdf-smoke"):
            stem = f"{stem}-raster-pdf-smoke"
        return f"{stem[:170]}.zip"

    def _document_pdf_vector_fit_smoke_filename(self, value: object) -> str:
        stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "main-computer-document")).strip(".-_")
        if not stem:
            stem = "main-computer-document"
        for suffix in (".pdf", ".zip"):
            if stem.lower().endswith(suffix):
                stem = stem[: -len(suffix)].rstrip(".-_") or "main-computer-document"
        for suffix in ("-pdf-smoke", "-raster-pdf-smoke"):
            if stem.lower().endswith(suffix):
                stem = stem[: -len(suffix)].rstrip(".-_") or "main-computer-document"
        if not stem.lower().endswith("-vector-fit-smoke"):
            stem = f"{stem}-vector-fit-smoke"
        return f"{stem[:170]}.zip"

    def _document_pdf_number(self, value: object, fallback: int, minimum: int, maximum: int) -> int:
        try:
            number = int(round(float(value)))
        except (TypeError, ValueError):
            return fallback
        return max(minimum, min(maximum, number))

    def _document_pdf_layout(self, state: object) -> tuple[int, int, dict[str, int]]:
        presets = {
            "letter": {"widthPx": 816, "heightPx": 1056},
            "a4": {"widthPx": 794, "heightPx": 1123},
            "legal": {"widthPx": 816, "heightPx": 1344},
            "screen": {"widthPx": 960, "heightPx": 1280},
        }
        raw_state = state if isinstance(state, dict) else {}
        raw_layout = raw_state.get("layout") if isinstance(raw_state.get("layout"), dict) else {}
        preset = str(raw_layout.get("preset") or "letter")
        preset_size = presets.get(preset, presets["letter"])
        custom = raw_layout.get("custom") if isinstance(raw_layout.get("custom"), dict) else {}
        if raw_layout.get("mode") == "custom":
            width_px = self._document_pdf_number(custom.get("widthPx"), preset_size["widthPx"], 320, 2400)
            height_px = self._document_pdf_number(custom.get("heightPx"), preset_size["heightPx"], 480, 3200)
        else:
            width_px = preset_size["widthPx"]
            height_px = preset_size["heightPx"]
        raw_margins = raw_layout.get("margins") if isinstance(raw_layout.get("margins"), dict) else {}
        max_horizontal = max(0, (width_px // 2) - 24)
        max_vertical = max(0, (height_px // 2) - 24)
        margins = {
            "top": self._document_pdf_number(raw_margins.get("top"), 96, 0, min(480, max_vertical)),
            "right": self._document_pdf_number(raw_margins.get("right"), 96, 0, min(480, max_horizontal)),
            "bottom": self._document_pdf_number(raw_margins.get("bottom"), 96, 0, min(480, max_vertical)),
            "left": self._document_pdf_number(raw_margins.get("left"), 96, 0, min(480, max_horizontal)),
        }
        return width_px, height_px, margins

    def _document_pdf_pages(self, body: dict[str, Any]) -> list[dict[str, str]]:
        raw_pages = body.get("pages")
        pages: list[dict[str, str]] = []
        if isinstance(raw_pages, list):
            for item in raw_pages[:100]:
                if not isinstance(item, dict):
                    continue
                content_html = str(item.get("contentHtml") or "")
                overlay_html = str(item.get("overlayHtml") or "")
                if len(content_html) + len(overlay_html) > 1_500_000:
                    raise ValueError("PDF export page content is too large.")
                pages.append({"contentHtml": content_html, "overlayHtml": overlay_html})
        if not pages:
            content_html = str(body.get("contentHtml") or "<p></p>")
            if len(content_html) > 1_500_000:
                raise ValueError("PDF export content is too large.")
            pages.append({"contentHtml": content_html, "overlayHtml": ""})
        return pages

    def _document_pdf_live_vector_pages(self, body: dict[str, Any], width_px: int, height_px: int) -> list[dict[str, Any]]:
        raw_pages = body.get("vectorPages")
        if not isinstance(raw_pages, list):
            return []
        pages: list[dict[str, Any]] = []
        total_bytes = 0
        for position, item in enumerate(raw_pages[:100], start=1):
            if not isinstance(item, dict):
                continue
            page_html = str(item.get("html") or "")
            if not page_html.strip():
                continue
            if "<script" in page_html.lower():
                raise ValueError("Live vector PDF page HTML cannot contain script tags.")
            page_bytes = len(page_html.encode("utf-8"))
            total_bytes += page_bytes
            if page_bytes > 2_500_000 or total_bytes > 25_000_000:
                raise ValueError("Live vector PDF page HTML is too large.")
            declared_width = self._document_pdf_number(item.get("widthPx"), width_px, 1, 10_000)
            declared_height = self._document_pdf_number(item.get("heightPx"), height_px, 1, 10_000)
            if declared_width != width_px or declared_height != height_px:
                raise ValueError(
                    f"Live vector PDF page {position} has size {declared_width}x{declared_height}; expected {width_px}x{height_px}."
                )
            pages.append(
                {
                    "index": int(item.get("index") or position),
                    "html": page_html,
                    "bytes": page_bytes,
                    "source": str(item.get("source") or body.get("vectorPageSource") or "client-live-dom-page-html"),
                }
            )
        return pages

    def _document_pdf_vector_source_info(self, body: dict[str, Any], width_px: int, height_px: int) -> dict[str, Any]:
        try:
            pages = self._document_pdf_live_vector_pages(body, width_px, height_px)
        except Exception as exc:
            return {
                "source": str(body.get("vectorPageSource") or "content-html-fallback"),
                "valid": False,
                "pageCount": 0,
                "reason": f"{type(exc).__name__}: {exc}",
            }
        if pages:
            return {
                "source": "client-live-dom-page-html",
                "valid": True,
                "pageCount": len(pages),
                "totalBytes": sum(int(page.get("bytes") or 0) for page in pages),
            }
        return {
            "source": "content-html-fallback",
            "valid": False,
            "pageCount": 0,
            "reason": "Payload did not include client live DOM vector pages.",
        }

    def _build_document_pdf_export_html(self, body: dict[str, Any]) -> tuple[str, str, str, int, int]:
        title = str(body.get("title") or "Main Computer Document").strip()[:160] or "Main Computer Document"
        width_px, height_px, margins = self._document_pdf_layout(body.get("layoutState"))
        live_vector_pages = self._document_pdf_live_vector_pages(body, width_px, height_px)
        if live_vector_pages:
            page_markup = "\n".join(str(page["html"]) for page in live_vector_pages)
            vector_source = "client-live-dom-page-html"
        else:
            pages = self._document_pdf_pages(body)
            page_markup = "\n".join(
                [
                    (
                        '<section class="mc-page">'
                        f'<div class="mc-page-content">{page["contentHtml"]}</div>'
                        f'<div class="mc-page-overlay-layer">{page["overlayHtml"]}</div>'
                        "</section>"
                    )
                    for page in pages
                ]
            )
            vector_source = "content-html-fallback"
        css = f"""
          @page {{
            size: {width_px}px {height_px}px;
            margin: 0;
          }}
          html,
          body {{
            margin: 0;
            padding: 0;
            background: transparent;
          }}
          body {{
            color: #191b16;
          }}
          .mc-page {{
            position: relative;
            width: {width_px}px;
            height: {height_px}px;
            break-after: page;
            page-break-after: always;
            background: #f7f4eb;
            color: #191b16;
            overflow: hidden;
            print-color-adjust: exact;
            -webkit-print-color-adjust: exact;
            box-sizing: border-box;
          }}
          .mc-page:last-child {{
            break-after: auto !important;
            page-break-after: auto !important;
          }}
          .mc-page-content {{
            position: absolute;
            top: {margins["top"]}px;
            right: {margins["right"]}px;
            bottom: {margins["bottom"]}px;
            left: {margins["left"]}px;
            min-width: 0;
            overflow: visible;
            font: 700 16px Arial, Helvetica, sans-serif;
            letter-spacing: 0;
            line-height: 1.55;
            outline: none;
          }}
          .mc-page-content * {{
            box-sizing: border-box;
          }}
          [data-vector-live-page] .mc-page-content {{
            /* Live DOM vector pages already carry frozen computed positioning. */
          }}
          .mc-page-content h1,
          .mc-page-content h2,
          .mc-page-content h3 {{
            line-height: 1.2;
            margin: 0.8em 0 0.45em;
          }}
          .mc-page-content h1:first-child,
          .mc-page-content h2:first-child,
          .mc-page-content h3:first-child,
          .mc-page-content p:first-child {{
            margin-top: 0;
          }}
          .mc-page-content p,
          .mc-page-content ul,
          .mc-page-content ol,
          .mc-page-content blockquote,
          .mc-page-content pre {{
            margin: 0 0 0.85em;
          }}
          .mc-page-content img {{
            max-width: 100%;
            height: auto;
          }}
          .mc-page-content pre {{
            white-space: pre-wrap;
            overflow-wrap: anywhere;
          }}
          .mc-page-content blockquote {{
            border-left: 4px solid rgba(25, 27, 22, 0.28);
            margin-left: 0;
            padding-left: 1em;
          }}
          .mc-page-overlay-layer {{
            position: absolute;
            inset: 0;
            z-index: 3;
            pointer-events: none;
          }}
          .document-object {{
            cursor: default;
            user-select: text;
            contain: layout paint;
          }}
          .document-math-inline {{
            display: inline;
            margin: 0;
            padding: 0;
            border: 0;
            background: transparent;
            color: inherit;
            vertical-align: baseline;
            font: inherit;
            line-height: inherit;
          }}
          .document-math-paragraph {{
            display: block;
            margin: 1em 0;
            padding: 0;
            border: 0;
            background: transparent;
            color: inherit;
            text-align: center;
            font: inherit;
            line-height: inherit;
          }}
          .document-math-body {{
            display: inline;
            white-space: pre-wrap;
            overflow-wrap: anywhere;
            color: inherit;
            font: inherit;
            line-height: inherit;
          }}
          .mc-page-break-guide,
          .document-plugin-rail,
          .document-plugin-marker,
          .document-plugin-anchor-highlight,
          [data-document-caret-marker] {{
            display: none !important;
          }}
        """
        filename = self._document_pdf_filename(title)
        export_html = f"""<!doctype html>
<html data-vector-source="{html_escape(vector_source)}">
  <head>
    <meta charset="utf-8">
    <title>{html_escape(title)}</title>
    <style>{css}</style>
  </head>
  <body>
{page_markup}
  </body>
</html>
"""
        return title, filename, export_html, width_px, height_px

    def _document_pdf_smoke_page_html(
        self,
        page: dict[str, str],
        width_px: int,
        height_px: int,
        margins: dict[str, int],
        title: str = "Main Computer Document Page",
    ) -> str:
        escaped_title = html_escape(title)
        return f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>{escaped_title}</title>
    <style>
      html,
      body {{
        margin: 0;
        padding: 0;
        background: transparent;
      }}
      .mc-page {{
        position: relative;
        width: {width_px}px;
        height: {height_px}px;
        overflow: hidden;
        background: #f7f4eb;
        color: #191b16;
        box-sizing: border-box;
      }}
      .mc-page-content {{
        position: absolute;
        top: {margins["top"]}px;
        right: {margins["right"]}px;
        bottom: {margins["bottom"]}px;
        left: {margins["left"]}px;
        min-width: 0;
        overflow: visible;
        font: 700 16px Arial, Helvetica, sans-serif;
        letter-spacing: 0;
        line-height: 1.55;
      }}
      .mc-page-content * {{
        box-sizing: border-box;
      }}
      .mc-page-overlay-layer {{
        position: absolute;
        inset: 0;
        z-index: 3;
        pointer-events: none;
      }}
      .mc-page-break-guide,
      .document-plugin-rail,
      .document-plugin-marker,
      [data-document-caret-marker] {{
        display: none !important;
      }}
    </style>
  </head>
  <body>
    <section class="mc-page">
      <div class="mc-page-content">{page["contentHtml"]}</div>
      <div class="mc-page-overlay-layer">{page["overlayHtml"]}</div>
    </section>
  </body>
</html>
"""

    def _build_document_pdf_smoke_bundle(
        self,
        *,
        title: str,
        pdf_filename: str,
        body: dict[str, Any],
        export_html: str,
        width_px: int,
        height_px: int,
        pages: list[dict[str, str]],
        page_pngs: list[bytes],
        capture_info: dict[str, Any] | None = None,
        kind: str = "main-computer-document-pdf-smoke-v1",
        bundle_filename: str | None = None,
        extra_files: list[tuple[str, bytes, str]] | None = None,
        readme_extra: str = "",
    ) -> tuple[bytes, str]:
        if not page_pngs:
            raise RuntimeError("PDF smoke export did not capture any page PNGs.")
        _, _, margins = self._document_pdf_layout(body.get("layoutState"))
        bundle_filename = bundle_filename or self._document_pdf_smoke_filename(title)
        files: list[dict[str, object]] = []

        def add_file(bundle: zipfile.ZipFile, path: str, data: bytes, content_type: str) -> None:
            clean_path = path.replace("\\", "/").lstrip("/")
            if not clean_path or clean_path.startswith("../") or "/../" in clean_path:
                raise ValueError(f"Unsafe PDF smoke bundle path: {path}")
            bundle.writestr(clean_path, data)
            files.append({"path": clean_path, "bytes": len(data), "contentType": content_type})

        payload = dict(body)
        payload.setdefault("title", title)
        payload.setdefault("layoutState", body.get("layoutState") if isinstance(body.get("layoutState"), dict) else {})
        screenshot_metadata = self._document_pdf_screenshot_metadata(page_pngs)
        readme = (
            "# Main Computer PDF smoke assets\n\n"
            "This bundle is emitted by the document editor PDF smoke workflow. "
            "It captures page PNG screenshots plus the export-only HTML used for backend fallback/debugging. "
            "When the client sends live page images, `pages/page-###.png` comes from the current frontend `.mc-page` DOM rather than backend re-layout. "
            "Use these files as the visual oracle when trying PDF build commands.\n\n"
            "- `document-export.html` is the complete export-only document HTML.\n"
            "- `payload.json` is the client payload that produced the export HTML.\n"
            "- `pages/page-###.png` are the source page images used by raster PDF export. The manifest `capture.source` says whether they came from live frontend DOM capture or backend Chromium fallback.\n"
            "- `pages/page-###.html` are standalone HTML files for the corresponding page fragments.\n"
            "- `manifest.json` records layout, margins, screenshot dimensions, Python/runtime details, and the renderer that captured the PNGs.\n"
            f"{readme_extra}"
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            add_file(bundle, "README.md", readme.encode("utf-8"), "text/markdown; charset=utf-8")
            add_file(bundle, "payload.json", json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"), "application/json")
            add_file(bundle, "document-export.html", export_html.encode("utf-8"), "text/html; charset=utf-8")
            for index, page in enumerate(pages, start=1):
                page_path = f"pages/page-{index:03d}.html"
                page_html = self._document_pdf_smoke_page_html(
                    page=page,
                    width_px=width_px,
                    height_px=height_px,
                    margins=margins,
                    title=f"{title} - page {index}",
                )
                add_file(bundle, page_path, page_html.encode("utf-8"), "text/html; charset=utf-8")
            for index, png_bytes in enumerate(page_pngs, start=1):
                add_file(bundle, f"pages/page-{index:03d}.png", png_bytes, "image/png")
            for path, data, content_type in extra_files or []:
                add_file(bundle, path, data, content_type)
            manifest = {
                "ok": True,
                "kind": kind,
                "title": title,
                "sourcePath": str(body.get("sourcePath") or ""),
                "pdfFilename": pdf_filename,
                "pageWidthPx": width_px,
                "pageHeightPx": height_px,
                "marginsPx": margins,
                "payloadPageCount": len(pages),
                "capturedPageCount": len(page_pngs),
                "capture": capture_info or {},
                "pageScreenshots": screenshot_metadata,
                "files": [*files, {"path": "manifest.json", "contentType": "application/json"}],
            }
            manifest_bytes = b""
            for _ in range(3):
                manifest["files"][-1]["bytes"] = len(manifest_bytes)
                next_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
                if len(next_bytes) == len(manifest_bytes):
                    manifest_bytes = next_bytes
                    break
                manifest_bytes = next_bytes
            add_file(bundle, "manifest.json", manifest_bytes, "application/json")
        return buffer.getvalue(), bundle_filename

    def _document_pdf_page_pngs_for_body(
        self,
        body: dict[str, Any],
        export_html: str,
        width_px: int,
        height_px: int,
    ) -> tuple[list[bytes], dict[str, Any]]:
        """Return source page PNGs for raster PDF export.

        Prefer client-supplied live page images. Those are captured from the
        actual frontend `.mc-page` DOM before the request is sent, so they avoid
        the backend/export-only HTML re-layout mismatch. If the client did not
        send images, fall back to the older backend Chromium capture path.
        """

        client_pngs, client_info = self._document_pdf_client_page_pngs(body, width_px, height_px)
        if client_pngs:
            return client_pngs, client_info
        return self._render_document_pdf_smoke_pages_with_playwright(export_html, width_px, height_px)

    def _document_pdf_source_image_capture_summary(self, capture_info: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(capture_info, dict) or not capture_info:
            return {
                "source": "unknown",
                "valid": False,
                "pageCount": 0,
                "proofLevel": "unknown",
                "reason": "No source image capture diagnostics were recorded.",
            }

        source = str(capture_info.get("source") or "unknown")
        page_images = capture_info.get("pageImages")
        if not isinstance(page_images, list):
            page_images = []
        method_counts: dict[str, int] = {}
        client_png_errors: list[str] = []
        backend_rasterized_pages = 0
        direct_client_png_pages = 0
        for item in page_images:
            if not isinstance(item, dict):
                continue
            method = str(item.get("method") or "unknown")
            method_counts[method] = method_counts.get(method, 0) + 1
            if method == "client-svg-foreignobject-backend-rasterize":
                backend_rasterized_pages += 1
            elif method in {"client-canvas-png", "client-svg-foreignobject-png"}:
                direct_client_png_pages += 1
            if item.get("clientPngError"):
                message = str(item.get("clientPngError"))
                if message not in client_png_errors:
                    client_png_errors.append(message)

        page_count = int(capture_info.get("pageCount") or len(page_images) or 0)
        fallback_count = int(capture_info.get("clientLiveDomSvgFallbackPages") or backend_rasterized_pages or 0)
        source_is_live_dom = "client-live-dom" in source
        if fallback_count and fallback_count >= page_count:
            proof_level = "live-dom-svg-backend-rasterized"
        elif fallback_count:
            proof_level = "mixed-live-dom-png-and-svg-backend-rasterized"
        elif source_is_live_dom and direct_client_png_pages:
            proof_level = "client-live-dom-png"
        elif source_is_live_dom:
            proof_level = "client-live-dom"
        else:
            proof_level = "backend-export-html-fallback"

        summary: dict[str, Any] = {
            "source": source,
            "valid": page_count > 0,
            "pageCount": page_count,
            "proofLevel": proof_level,
            "sourceIsLiveDom": source_is_live_dom,
            "directClientPngPages": direct_client_png_pages,
            "backendRasterizedSvgPages": fallback_count,
            "methodCounts": method_counts,
            "clientPngErrorCount": len(client_png_errors),
        }
        if client_png_errors:
            summary["clientPngErrors"] = client_png_errors[:5]
        if fallback_count:
            summary["note"] = (
                "Source oracle images came from frozen live-DOM SVG snapshots rendered by backend Chromium, "
                "not direct client canvas PNG export."
            )
        return summary

    def _document_pdf_client_page_pngs(
        self,
        body: dict[str, Any],
        width_px: int,
        height_px: int,
    ) -> tuple[list[bytes], dict[str, Any]]:
        raw_images = body.get("pageImages")
        if not isinstance(raw_images, list) or not raw_images:
            return [], {}

        entries: list[dict[str, Any]] = []
        svg_pages: list[tuple[int, int, str]] = []
        page_details: list[dict[str, object]] = []

        for position, item in enumerate(raw_images[:100], start=1):
            if not isinstance(item, dict):
                continue
            raw_data = str(item.get("dataUrl") or item.get("pngDataUrl") or item.get("base64") or "")
            raw_svg = str(item.get("svgDataUrl") or item.get("svgText") or item.get("svg") or "")
            method = str(item.get("method") or ("client-svg-foreignobject" if raw_data else "client-svg-foreignobject-backend-rasterize"))
            detail: dict[str, object] = {
                "index": int(item.get("index") or position),
                "widthPx": item.get("widthPx"),
                "heightPx": item.get("heightPx"),
                "sourceWidthPx": item.get("sourceWidthPx"),
                "sourceHeightPx": item.get("sourceHeightPx"),
                "scaleX": item.get("scaleX"),
                "scaleY": item.get("scaleY"),
                "method": method,
            }
            for key in (
                "offsetWidthPx",
                "offsetHeightPx",
                "clientWidthPx",
                "clientHeightPx",
                "rectWidthPx",
                "rectHeightPx",
                "computedBoxSizing",
                "computedBorderTopPx",
                "computedBorderRightPx",
                "computedBorderBottomPx",
                "computedBorderLeftPx",
            ):
                if key in item:
                    detail[key] = item.get(key)
            if item.get("clientPngError"):
                detail["clientPngError"] = str(item.get("clientPngError"))

            if raw_data:
                try:
                    png_bytes = self._decode_document_pdf_png_data(raw_data)
                    info = read_png_info(png_bytes)
                except Exception as exc:
                    raise ValueError(f"Client PDF page image {position} is not a valid PNG: {type(exc).__name__}: {exc}") from exc
                if info.width != width_px or info.height != height_px:
                    raise ValueError(
                        f"Client PDF page image {position} has size {info.width}x{info.height}; "
                        f"expected {width_px}x{height_px}. The frontend capture should render page images "
                        "at the document layout size, not the viewport preview size."
                    )
                detail["widthPx"] = info.width
                detail["heightPx"] = info.height
                entries.append({"kind": "png", "png": png_bytes, "detail": detail})
                page_details.append(detail)
                continue

            if raw_svg:
                try:
                    svg_text = self._decode_document_pdf_svg_data(raw_svg)
                except Exception as exc:
                    raise ValueError(f"Client PDF page image {position} is not a valid SVG snapshot: {type(exc).__name__}: {exc}") from exc
                declared_width = item.get("widthPx")
                declared_height = item.get("heightPx")
                if declared_width not in (None, "", width_px) or declared_height not in (None, "", height_px):
                    raise ValueError(
                        f"Client PDF SVG snapshot {position} declares size {declared_width}x{declared_height}; "
                        f"expected {width_px}x{height_px}."
                    )
                entries.append({"kind": "svg", "detail": detail, "svgIndex": len(svg_pages)})
                svg_pages.append((position, int(detail["index"]), svg_text))
                page_details.append(detail)

        if not entries:
            return [], {}

        rendered_svg_pngs: list[bytes] = []
        svg_capture_info: dict[str, Any] = {}
        if svg_pages:
            rendered_svg_pngs, svg_capture_info = self._render_document_pdf_client_svg_pages_with_playwright(svg_pages, width_px, height_px)
            if len(rendered_svg_pngs) != len(svg_pages):
                raise RuntimeError(
                    f"Client PDF SVG rasterization returned {len(rendered_svg_pngs)} pages for {len(svg_pages)} SVG snapshots."
                )

        page_pngs: list[bytes] = []
        for entry in entries:
            if entry["kind"] == "png":
                page_pngs.append(entry["png"])
                continue
            svg_index = int(entry["svgIndex"])
            png_bytes = rendered_svg_pngs[svg_index]
            info = read_png_info(png_bytes)
            if info.width != width_px or info.height != height_px:
                raise RuntimeError(
                    f"Client PDF SVG snapshot {svg_index + 1} rasterized to {info.width}x{info.height}; "
                    f"expected {width_px}x{height_px}."
                )
            detail = entry["detail"]
            detail["widthPx"] = info.width
            detail["heightPx"] = info.height
            detail["rasterizedBy"] = svg_capture_info.get("renderer", {}).get("label", "backend Chromium")
            page_pngs.append(png_bytes)

        if not page_pngs:
            return [], {}

        source = "client-live-dom .mc-page images"
        renderer = {
            "label": "frontend browser canvas/SVG foreignObject",
            "executablePath": "current-browser",
        }
        if svg_pages and len(svg_pages) == len(page_pngs):
            source = "client-live-dom SVG snapshots rasterized by backend Chromium"
            renderer = svg_capture_info.get("renderer", renderer) if isinstance(svg_capture_info, dict) else renderer
        elif svg_pages:
            source = "mixed client-live-dom PNG images and SVG snapshots"
            renderer = {
                "label": "frontend PNGs plus backend Chromium SVG rasterizer",
                "executablePath": str(svg_capture_info.get("renderer", {}).get("executablePath", "mixed")) if isinstance(svg_capture_info, dict) else "mixed",
            }

        return page_pngs, {
            "source": source,
            "renderer": renderer,
            "viewport": {
                "widthPx": width_px,
                "heightPx": height_px,
            },
            "deviceScaleFactor": str(body.get("devicePixelRatio") or ""),
            "media": "screen",
            "pageCount": len(page_pngs),
            "pageImages": page_details,
            "pythonExecutable": str(getattr(sys, "executable", "python") or "python"),
            "pythonVersion": sys.version.split()[0],
            "platform": platform.platform(),
            "clientLiveDomSvgFallbackPages": len(svg_pages),
        }

    def _decode_document_pdf_png_data(self, value: str) -> bytes:
        data = value.strip()
        if data.lower().startswith("data:"):
            header, separator, payload = data.partition(",")
            if not separator:
                raise ValueError("data URL is missing a comma separator")
            if "image/png" not in header.lower() or "base64" not in header.lower():
                raise ValueError("data URL must be a base64 image/png")
            data = payload
        data = "".join(data.split())
        if len(data) > 40_000_000:
            raise ValueError("PNG data is too large")
        try:
            png_bytes = base64.b64decode(data, validate=True)
        except binascii.Error as exc:
            raise ValueError(f"base64 decode failed: {exc}") from exc
        if not png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("decoded bytes do not start with a PNG signature")
        return png_bytes

    def _decode_document_pdf_svg_data(self, value: str) -> str:
        data = value.strip()
        if data.lower().startswith("data:"):
            header, separator, payload = data.partition(",")
            if not separator:
                raise ValueError("data URL is missing a comma separator")
            if "image/svg+xml" not in header.lower():
                raise ValueError("data URL must be image/svg+xml")
            if "base64" not in header.lower():
                raise ValueError("SVG data URL must be base64 encoded")
            data = "".join(payload.split())
            if len(data) > 60_000_000:
                raise ValueError("SVG snapshot data is too large")
            try:
                svg_bytes = base64.b64decode(data, validate=True)
            except binascii.Error as exc:
                raise ValueError(f"base64 decode failed: {exc}") from exc
            if len(svg_bytes) > 30_000_000:
                raise ValueError("decoded SVG snapshot is too large")
            try:
                svg_text = svg_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"SVG snapshot is not UTF-8: {exc}") from exc
        else:
            svg_text = data
            if len(svg_text.encode("utf-8")) > 30_000_000:
                raise ValueError("SVG snapshot is too large")
        if "<svg" not in svg_text[:1000].lower():
            raise ValueError("decoded SVG snapshot does not contain an <svg> root")
        return svg_text

    def _document_pdf_inline_svg_fragment(self, svg_text: str) -> str:
        fragment = re.sub(r"^\s*<\?xml[^>]*\?>\s*", "", svg_text, flags=re.IGNORECASE)
        fragment = re.sub(r"^\s*<!doctype[^>]*>\s*", "", fragment, flags=re.IGNORECASE)
        return fragment.strip()

    def _render_document_pdf_client_svg_pages_with_playwright(
        self,
        svg_pages: list[tuple[int, int, str]],
        width_px: int,
        height_px: int,
    ) -> tuple[list[bytes], dict[str, Any]]:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            python_executable = str(getattr(sys, "executable", "python") or "python")
            raise RuntimeError(
                "PDF export received live DOM SVG snapshots because the browser refused client-side PNG canvas export. "
                "Rasterizing those snapshots requires Playwright/Chromium on the backend. "
                f"Playwright import failed in {python_executable}: {type(exc).__name__}: {exc}. "
                f"Install it with `{python_executable} -m pip install playwright` and "
                f"`{python_executable} -m playwright install chromium`."
            ) from exc

        launch_args: dict[str, Any] = {
            "headless": True,
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-setuid-sandbox",
                "--disable-software-rasterizer",
                "--single-process",
            ],
        }
        candidates: list[tuple[str, str | None]] = [("playwright managed chromium", None)]
        for label, executable in self._document_pdf_chromium_executable_candidates():
            if any(existing == executable for _, existing in candidates):
                continue
            candidates.append((label, executable))

        def render_with_browser(browser: Any, label: str, executable: str | None) -> tuple[list[bytes], dict[str, Any]]:
            context = browser.new_context(
                viewport={"width": width_px, "height": height_px},
                device_scale_factor=1,
                java_script_enabled=False,
            )
            try:
                page = context.new_page()
                page.emulate_media(media="screen")
                screenshots: list[bytes] = []
                for request_index, page_index, svg_text in svg_pages:
                    svg_fragment = self._document_pdf_inline_svg_fragment(svg_text)
                    html = f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <style>
      html,
      body {{
        margin: 0;
        padding: 0;
        width: {width_px}px;
        height: {height_px}px;
        overflow: hidden;
        background: transparent;
      }}
      #capture-root {{
        width: {width_px}px;
        height: {height_px}px;
        overflow: hidden;
        background: #f7f4eb;
      }}
      #capture-root > svg {{
        display: block;
        width: {width_px}px !important;
        height: {height_px}px !important;
      }}
    </style>
  </head>
  <body>
    <div id="capture-root" data-request-index="{request_index}" data-page-index="{page_index}">
{svg_fragment}
    </div>
  </body>
</html>
"""
                    page.set_content(html, wait_until="load")
                    locator = page.locator("#capture-root")
                    screenshots.append(
                        locator.screenshot(
                            type="png",
                            animations="disabled",
                            caret="hide",
                            scale="css",
                            timeout=10000,
                        )
                    )
                capture_info = {
                    "source": "client-live-dom SVG snapshots rasterized by backend Chromium",
                    "renderer": {
                        "label": label,
                        "executablePath": executable or "playwright-managed",
                    },
                    "viewport": {"widthPx": width_px, "heightPx": height_px},
                    "deviceScaleFactor": 1,
                    "media": "screen",
                    "pageCount": len(screenshots),
                    "pythonExecutable": str(getattr(sys, "executable", "python") or "python"),
                    "pythonVersion": sys.version.split()[0],
                    "platform": platform.platform(),
                }
                return screenshots, capture_info
            finally:
                context.close()

        failures: list[str] = []
        with sync_playwright() as playwright:
            for label, executable in candidates:
                browser = None
                try:
                    per_launch_args = dict(launch_args)
                    if executable:
                        per_launch_args["executable_path"] = executable
                    browser = playwright.chromium.launch(**per_launch_args)
                    return render_with_browser(browser, label, executable)
                except Exception as exc:
                    failures.append(f"{label}: {exc}")
                finally:
                    try:
                        browser.close() if browser else None
                    except Exception:
                        pass
        details = " | ".join(failures[-4:])
        raise RuntimeError(f"PDF export could not rasterize client live DOM SVG snapshots. {details}")

    def _document_pdf_screenshot_metadata(self, page_pngs: list[bytes]) -> list[dict[str, object]]:
        metadata: list[dict[str, object]] = []
        for index, png_bytes in enumerate(page_pngs, start=1):
            item: dict[str, object] = {
                "index": index,
                "path": f"pages/page-{index:03d}.png",
                "bytes": len(png_bytes),
            }
            try:
                info = read_png_info(png_bytes)
                item.update(
                    {
                        "widthPx": info.width,
                        "heightPx": info.height,
                        "bitDepth": info.bit_depth,
                        "colorType": info.color_type,
                        "hasAlpha": info.has_alpha,
                    }
                )
            except Exception as exc:
                item.update({"error": f"{type(exc).__name__}: {exc}"})
            metadata.append(item)
        return metadata

    def _render_document_pdf_pixel_raster(self, export_html: str, width_px: int, height_px: int) -> tuple[bytes, dict[str, Any], list[dict[str, object]]]:
        """Capture the export DOM as page PNGs and embed those pixels in a PDF.

        This is the production PDF path for visual fidelity. It uses the same
        page-screenshot oracle as the smoke bundle, then builds a raster PDF
        directly from those screenshots instead of asking browser print layout
        to reflow the document a second time.
        """

        page_pngs, capture_info = self._render_document_pdf_smoke_pages_with_playwright(export_html, width_px, height_px)
        if not page_pngs:
            raise RuntimeError("Pixel PDF export did not capture any page PNGs.")
        screenshot_metadata = self._document_pdf_screenshot_metadata(page_pngs)
        for item in screenshot_metadata:
            if item.get("widthPx") != width_px or item.get("heightPx") != height_px:
                raise RuntimeError(
                    "Pixel PDF export captured a page with unexpected dimensions: "
                    f"{item.get('widthPx')}x{item.get('heightPx')} instead of {width_px}x{height_px}."
                )
        return build_css_pixel_raster_pdf(page_pngs), capture_info, screenshot_metadata

    def _build_document_pdf_raster_smoke_candidates(
        self,
        *,
        title: str,
        page_pngs: list[bytes],
        width_px: int,
        height_px: int,
    ) -> tuple[list[tuple[str, bytes, str]], dict[str, Any]]:
        candidate_files: list[tuple[str, bytes, str]] = []
        metrics: dict[str, Any] = {
            "ok": True,
            "kind": "main-computer-document-raster-pdf-smoke-metrics-v1",
            "sourcePageSizeCssPx": {"width": width_px, "height": height_px},
            "candidates": [],
        }

        try:
            pixel_pdf = build_css_pixel_raster_pdf(page_pngs)
            candidate_files.append(("candidates/pixel-raster-css96.pdf", pixel_pdf, "application/pdf"))
            metrics["candidates"].append(
                {
                    "id": "pixel-raster-css96",
                    "path": "candidates/pixel-raster-css96.pdf",
                    "status": "created",
                    "bytes": len(pixel_pdf),
                    "description": (
                        "Production-style pixel export candidate. It embeds each source PNG as the sole page image "
                        "and maps 96 CSS pixels to 72 PDF points, so an 816x1056 CSS-pixel Letter page becomes "
                        "a 612x792 point PDF page and renders back to 816x1056 at 96 DPI."
                    ),
                    "expectedPdfPageSizePoints": {
                        "width": width_px * 72.0 / 96.0,
                        "height": height_px * 72.0 / 96.0,
                    },
                    "expectedRenderedAt96DpiPx": {"width": width_px, "height": height_px},
                }
            )
        except Exception as exc:
            metrics["candidates"].append(
                {
                    "id": "pixel-raster-css96",
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

        try:
            xobject_pdf = build_png_xobject_pdf(page_pngs)
            candidate_files.append(("candidates/identity-pixel-points.pdf", xobject_pdf, "application/pdf"))
            metrics["candidates"].append(
                {
                    "id": "identity-pixel-points",
                    "path": "candidates/identity-pixel-points.pdf",
                    "status": "created",
                    "bytes": len(xobject_pdf),
                    "description": (
                        "Debug-only identity candidate. It maps one source PNG pixel to one PDF point, "
                        "which is useful for raw image inspection but is not the final physical document size."
                    ),
                }
            )
        except Exception as exc:
            metrics["candidates"].append(
                {
                    "id": "identity-pixel-points",
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

        try:
            raster_html = build_raster_image_html(title=f"{title} raster PDF smoke", page_pngs=page_pngs, width_px=width_px, height_px=height_px)
            chromium_pdf = self._render_document_pdf_with_playwright(raster_html, width_px, height_px)
            candidate_files.append(("candidates/raster-chromium-html-img.pdf", chromium_pdf, "application/pdf"))
            candidate_files.append(("candidates/raster-chromium-html-img.html", raster_html.encode("utf-8"), "text/html; charset=utf-8"))
            metrics["candidates"].append(
                {
                    "id": "raster-chromium-html-img",
                    "path": "candidates/raster-chromium-html-img.pdf",
                    "sourceHtmlPath": "candidates/raster-chromium-html-img.html",
                    "status": "created",
                    "bytes": len(chromium_pdf),
                    "description": "Chromium PDF generated from full-page image HTML that uses the source PNGs as page images. This remains a comparison candidate because Chromium print can still rescale.",
                }
            )
        except Exception as exc:
            metrics["candidates"].append(
                {
                    "id": "raster-chromium-html-img",
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

        if not candidate_files:
            metrics["ok"] = False
            raise RuntimeError("Raster PDF smoke could not create any candidate PDFs.")
        return candidate_files, metrics

    def _build_document_pdf_raster_smoke_render_proof(
        self,
        *,
        page_pngs: list[bytes],
        candidate_files: list[tuple[str, bytes, str]],
    ) -> tuple[list[tuple[str, bytes, str]], dict[str, Any]]:
        proof_files: list[tuple[str, bytes, str]] = []
        pdf_candidates = [(path, data) for path, data, content_type in candidate_files if content_type == "application/pdf"]
        comparison: dict[str, Any] = {
            "status": "not-run",
            "kind": "main-computer-document-raster-pdf-render-comparison-v1",
            "productionCandidateId": "pixel-raster-css96",
            "dpi": 96,
            "renderer": {},
            "candidates": [],
        }
        if not pdf_candidates:
            comparison["reason"] = "No PDF candidates were created, so there was nothing to render back."
            return proof_files, comparison

        try:
            fitz_module = self._document_pdf_import_pymupdf()
        except Exception as exc:
            comparison["reason"] = (
                "Render-back proof requires PyMuPDF so the smoke workflow can render candidate PDFs to PNG at 96 DPI. "
                f"PyMuPDF import failed: {type(exc).__name__}: {exc}."
            )
            comparison["renderer"] = {"name": "PyMuPDF", "available": False}
            return proof_files, comparison

        comparison["renderer"] = {
            "name": "PyMuPDF",
            "module": str(getattr(fitz_module, "__name__", "fitz")),
            "version": str(getattr(fitz_module, "__doc__", "")).splitlines()[0][:120] if getattr(fitz_module, "__doc__", "") else "",
            "available": True,
        }
        production_status: str | None = None

        for path, pdf_bytes in pdf_candidates:
            candidate_id = self._document_pdf_candidate_id(path)
            included_in_overall = candidate_id == "pixel-raster-css96"
            candidate: dict[str, Any] = {
                "id": candidate_id,
                "path": path,
                "includedInOverallStatus": included_in_overall,
                "status": "not-run",
                "pages": [],
            }
            try:
                rendered_pngs = self._render_document_pdf_candidate_with_pymupdf(fitz_module, pdf_bytes, dpi=96)
                candidate["renderedPageCount"] = len(rendered_pngs)
                candidate["sourcePageCount"] = len(page_pngs)
                candidate["pageCountMatches"] = len(rendered_pngs) == len(page_pngs)
                changed_pixels = 0
                total_pixels = 0
                exact_pages = 0

                for index in range(1, max(len(page_pngs), len(rendered_pngs)) + 1):
                    source_png = page_pngs[index - 1] if index <= len(page_pngs) else None
                    rendered_png = rendered_pngs[index - 1] if index <= len(rendered_pngs) else None
                    rendered_path = f"rendered/{candidate_id}/page-{index:03d}.png"
                    diff_path = f"diffs/{candidate_id}/page-{index:03d}-diff.png"

                    if rendered_png is not None:
                        proof_files.append((rendered_path, rendered_png, "image/png"))
                    if source_png is None or rendered_png is None:
                        page_metrics = {
                            "index": index,
                            "status": "missing-source" if source_png is None else "missing-rendered-page",
                            "sourcePath": f"pages/page-{index:03d}.png",
                            "renderedPath": rendered_path if rendered_png is not None else "",
                            "diffPath": "",
                            "exactMatch": False,
                        }
                        candidate["pages"].append(page_metrics)
                        continue

                    page_metrics, diff_png = self._compare_document_pdf_png_pair(
                        source_png=source_png,
                        rendered_png=rendered_png,
                    )
                    page_metrics.update(
                        {
                            "index": index,
                            "status": "compared",
                            "sourcePath": f"pages/page-{index:03d}.png",
                            "renderedPath": rendered_path,
                            "diffPath": diff_path,
                        }
                    )
                    proof_files.append((diff_path, diff_png, "image/png"))
                    candidate["pages"].append(page_metrics)
                    changed_pixels += int(page_metrics.get("changedPixels", 0) or 0)
                    total_pixels += int(page_metrics.get("totalPixels", 0) or 0)
                    if page_metrics.get("exactMatch") is True:
                        exact_pages += 1

                candidate["changedPixels"] = changed_pixels
                candidate["totalPixels"] = total_pixels
                candidate["changedPixelPercent"] = (changed_pixels / total_pixels * 100.0) if total_pixels else None
                candidate["exactPageCount"] = exact_pages
                candidate["exactMatch"] = bool(candidate.get("pageCountMatches")) and exact_pages == len(page_pngs)
                candidate["status"] = "passed" if candidate["exactMatch"] else "different"
            except Exception as exc:
                candidate["status"] = "failed"
                candidate["error"] = f"{type(exc).__name__}: {exc}"

            if included_in_overall:
                production_status = str(candidate["status"])
            comparison["candidates"].append(candidate)

        if production_status is None:
            comparison["status"] = "completed"
            comparison["reason"] = "No pixel-raster-css96 production candidate was present."
        else:
            comparison["status"] = production_status
        return proof_files, comparison

    def _build_document_pdf_vector_fit_smoke_candidates(
        self,
        *,
        title: str,
        export_html: str,
        width_px: int,
        height_px: int,
        page_pngs: list[bytes],
        vector_source_info: dict[str, Any] | None = None,
    ) -> tuple[list[tuple[str, bytes, str]], list[tuple[str, bytes, str]], dict[str, Any]]:
        candidate_files: list[tuple[str, bytes, str]] = []
        proof_files: list[tuple[str, bytes, str]] = []
        settings = self._document_pdf_vector_fit_candidate_settings()
        metrics: dict[str, Any] = {
            "ok": True,
            "kind": "main-computer-document-vector-fit-smoke-metrics-v1",
            "purpose": "Find the backend Chromium/vector PDF setting that best matches live page PNG captures.",
            "sourcePageSizeCssPx": {"width": width_px, "height": height_px},
            "vectorSource": vector_source_info or {
                "source": "unknown",
                "valid": False,
                "reason": "Vector source diagnostics were not provided.",
            },
            "algorithm": {
                "summary": "Render each vector candidate back to PNG at 96 DPI, derive filled ink masks, then score horizontal/vertical projection scans plus mipmap occupancy grids.",
                "mask": "Background-relative dark/different pixels become ink; enclosed background holes are flood-filled so letters become simple stencils.",
                "scans": "Horizontal and vertical ink projections are compared with a small shift search.",
                "mipmaps": "Ink occupancy grids at multiple block sizes reduce sensitivity to antialiasing while still detecting layout drift.",
            },
            "searchSpace": settings,
            "candidates": [],
            "renderComparison": {
                "status": "not-run",
                "kind": "main-computer-document-vector-fit-render-comparison-v1",
                "dpi": 96,
                "renderer": {},
                "candidates": [],
            },
            "bestCandidate": None,
        }
        if not metrics["vectorSource"].get("valid"):
            metrics["ok"] = False
            metrics["renderComparison"]["vectorSourceValid"] = False
            metrics["renderComparison"]["vectorSourceReason"] = str(metrics["vectorSource"].get("reason") or "")
        else:
            metrics["renderComparison"]["vectorSourceValid"] = True

        for setting in settings:
            candidate_id = str(setting["id"])
            try:
                pdf_bytes = self._render_document_pdf_with_playwright(
                    export_html,
                    width_px,
                    height_px,
                    media=str(setting["media"]),
                    pdf_scale=float(setting["pdfScale"]),
                    prefer_css_page_size=bool(setting["preferCssPageSize"]),
                )
                path = f"candidates/{candidate_id}.pdf"
                candidate_files.append((path, pdf_bytes, "application/pdf"))
                metrics["candidates"].append(
                    {
                        **setting,
                        "path": path,
                        "status": "created",
                        "bytes": len(pdf_bytes),
                    }
                )
            except Exception as exc:
                metrics["candidates"].append(
                    {
                        **setting,
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

        if not candidate_files:
            metrics["ok"] = False
            raise RuntimeError("Vector fit smoke could not create any vector PDF candidates.")

        comparison = metrics["renderComparison"]
        try:
            fitz_module = self._document_pdf_import_pymupdf()
        except Exception as exc:
            comparison["reason"] = (
                "Vector fit scoring requires PyMuPDF so candidate vector PDFs can be rendered back to PNG at 96 DPI. "
                f"PyMuPDF import failed: {type(exc).__name__}: {exc}."
            )
            comparison["renderer"] = {"name": "PyMuPDF", "available": False}
            return candidate_files, proof_files, metrics

        comparison["renderer"] = {
            "name": "PyMuPDF",
            "module": str(getattr(fitz_module, "__name__", "fitz")),
            "version": str(getattr(fitz_module, "__doc__", "")).splitlines()[0][:120] if getattr(fitz_module, "__doc__", "") else "",
            "available": True,
        }

        best_candidate: dict[str, Any] | None = None
        candidate_settings_by_id = {str(setting["id"]): setting for setting in settings}
        for path, pdf_bytes, _content_type in candidate_files:
            candidate_id = self._document_pdf_candidate_id(path)
            candidate_metric: dict[str, Any] = {
                "id": candidate_id,
                "path": path,
                "settings": candidate_settings_by_id.get(candidate_id, {}),
                "status": "not-run",
                "pages": [],
                "pdfIntegrity": self._document_pdf_vector_pdf_integrity_summary(fitz_module, pdf_bytes),
            }
            try:
                rendered_pngs = self._render_document_pdf_candidate_with_pymupdf(fitz_module, pdf_bytes, dpi=96)
                candidate_metric["renderedPageCount"] = len(rendered_pngs)
                candidate_metric["sourcePageCount"] = len(page_pngs)
                candidate_metric["pageCountMatches"] = len(rendered_pngs) == len(page_pngs)

                page_scores: list[float] = []
                for index in range(1, max(len(page_pngs), len(rendered_pngs)) + 1):
                    source_png = page_pngs[index - 1] if index <= len(page_pngs) else None
                    rendered_png = rendered_pngs[index - 1] if index <= len(rendered_pngs) else None
                    rendered_path = f"rendered/vector-fit/{candidate_id}/page-{index:03d}.png"
                    diff_path = f"diffs/vector-fit/{candidate_id}/page-{index:03d}-mask-diff.png"

                    if rendered_png is not None:
                        proof_files.append((rendered_path, rendered_png, "image/png"))
                    if source_png is None or rendered_png is None:
                        candidate_metric["pages"].append(
                            {
                                "index": index,
                                "status": "missing-source" if source_png is None else "missing-rendered-page",
                                "sourcePath": f"pages/page-{index:03d}.png",
                                "renderedPath": rendered_path if rendered_png is not None else "",
                                "diffPath": "",
                                "fitScore": 1_000_000.0,
                            }
                        )
                        page_scores.append(1_000_000.0)
                        continue

                    page_metric, diff_png = self._compare_document_pdf_text_mask_fit(
                        source_png=source_png,
                        rendered_png=rendered_png,
                    )
                    page_metric.update(
                        {
                            "index": index,
                            "status": "compared",
                            "sourcePath": f"pages/page-{index:03d}.png",
                            "renderedPath": rendered_path,
                            "diffPath": diff_path,
                        }
                    )
                    proof_files.append((diff_path, diff_png, "image/png"))
                    candidate_metric["pages"].append(page_metric)
                    page_scores.append(float(page_metric.get("fitScore", 1_000_000.0)))

                candidate_metric["meanFitScore"] = sum(page_scores) / len(page_scores) if page_scores else 1_000_000.0
                candidate_metric["maxFitScore"] = max(page_scores) if page_scores else 1_000_000.0
                candidate_metric["status"] = "scored"
                if best_candidate is None or self._document_pdf_vector_fit_candidate_rank_key(
                    candidate_metric
                ) < self._document_pdf_vector_fit_candidate_rank_key(best_candidate):
                    best_candidate = candidate_metric
            except Exception as exc:
                candidate_metric["status"] = "failed"
                candidate_metric["error"] = f"{type(exc).__name__}: {exc}"

            comparison["candidates"].append(candidate_metric)

        if best_candidate is None:
            comparison["status"] = "failed"
            comparison["reason"] = "No vector candidate could be rendered and scored."
            metrics["ok"] = False
        else:
            comparison["status"] = "scored"
            best_selection = self._document_pdf_vector_fit_best_selection_summary(
                comparison["candidates"],
                best_candidate,
            )
            comparison["bestCandidateSelection"] = best_selection
            metrics["bestCandidate"] = {
                "id": best_candidate["id"],
                "path": best_candidate["path"],
                "settings": best_candidate.get("settings", {}),
                "meanFitScore": best_candidate["meanFitScore"],
                "maxFitScore": best_candidate["maxFitScore"],
                "pdfIntegrity": best_candidate.get("pdfIntegrity", {}),
                "selection": best_selection,
            }
        return candidate_files, proof_files, metrics

    def _document_pdf_vector_pdf_integrity_summary(self, fitz_module: Any, pdf_bytes: bytes) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "status": "not-run",
            "pageCount": 0,
            "extractableTextChars": 0,
            "embeddedImageRefs": 0,
            "hasExtractableText": False,
            "imageOnly": False,
            "likelyVectorText": False,
        }
        try:
            opener = getattr(fitz_module, "open")
            document = opener(stream=pdf_bytes, filetype="pdf")
        except Exception as exc:
            summary["status"] = "unavailable"
            summary["reason"] = f"{type(exc).__name__}: {exc}"
            return summary

        try:
            try:
                page_count = len(document)
            except Exception:
                page_count = 0
            text_chars = 0
            image_refs = 0
            for page in document:
                try:
                    page_text = page.get_text("text")
                except TypeError:
                    page_text = page.get_text()
                except Exception:
                    page_text = ""
                text_chars += len(str(page_text or "").strip())

                try:
                    page_images = page.get_images(full=True)
                except TypeError:
                    page_images = page.get_images()
                except Exception:
                    page_images = []
                try:
                    image_refs += len(page_images)
                except Exception:
                    pass

            summary.update(
                {
                    "status": "scanned",
                    "pageCount": int(page_count),
                    "extractableTextChars": int(text_chars),
                    "embeddedImageRefs": int(image_refs),
                    "hasExtractableText": text_chars > 0,
                    "imageOnly": page_count > 0 and text_chars == 0 and image_refs > 0,
                    "likelyVectorText": page_count > 0 and text_chars > 0 and image_refs == 0,
                }
            )
            return summary
        finally:
            close = getattr(document, "close", None)
            if callable(close):
                close()

    def _document_pdf_vector_fit_candidate_rank_key(self, candidate_metric: dict[str, Any]) -> tuple[float, float, str]:
        return (
            float(candidate_metric.get("meanFitScore", 1_000_000.0)),
            float(candidate_metric.get("maxFitScore", 1_000_000.0)),
            str(candidate_metric.get("id") or candidate_metric.get("path") or ""),
        )

    def _document_pdf_vector_fit_best_selection_summary(
        self,
        candidates: list[dict[str, Any]],
        best_candidate: dict[str, Any],
    ) -> dict[str, Any]:
        best_mean = float(best_candidate.get("meanFitScore", 1_000_000.0))
        best_max = float(best_candidate.get("maxFitScore", 1_000_000.0))
        exact_ties: list[dict[str, Any]] = []
        near_ties: list[dict[str, Any]] = []
        near_epsilon = 1e-9
        for candidate in candidates:
            if candidate.get("status") != "scored":
                continue
            mean_score = float(candidate.get("meanFitScore", 1_000_000.0))
            max_score = float(candidate.get("maxFitScore", 1_000_000.0))
            tie_summary = {
                "id": str(candidate.get("id") or ""),
                "path": str(candidate.get("path") or ""),
                "meanFitScore": mean_score,
                "maxFitScore": max_score,
            }
            if mean_score == best_mean and max_score == best_max:
                exact_ties.append(tie_summary)
            elif abs(mean_score - best_mean) <= near_epsilon and abs(max_score - best_max) <= near_epsilon:
                near_ties.append(tie_summary)
        exact_ties.sort(key=lambda item: str(item.get("id") or ""))
        near_ties.sort(key=lambda item: str(item.get("id") or ""))
        return {
            "rankOrder": ["meanFitScore", "maxFitScore", "candidateId"],
            "tieBreak": "candidateId",
            "tieBreakReason": (
                "When fit scores are identical, choose a deterministic candidate id so repo probes "
                "and bundle metrics report the same best candidate."
            ),
            "exactTieCount": len(exact_ties),
            "exactTies": exact_ties,
            "nearTieEpsilon": near_epsilon,
            "nearTieCount": len(near_ties),
            "nearTies": near_ties,
        }

    def _document_pdf_production_vector_settings(self) -> dict[str, Any]:
        return {
            "id": "vector-print-scale-1_000",
            "media": "print",
            "pdfScale": 1.0,
            "preferCssPageSize": True,
            "description": (
                "Production Export PDF setting proven by vector-fit smoke: Chromium vector PDF "
                "using print CSS media, Playwright page.pdf scale=1.000, and CSS page size."
            ),
        }

    def _document_pdf_vector_fit_candidate_settings(self) -> list[dict[str, Any]]:
        settings: list[dict[str, Any]] = []
        for media, scales in (("screen", (0.995, 1.0, 1.005)), ("print", (1.0,))):
            for scale in scales:
                scale_id = f"{scale:.3f}".replace(".", "_")
                settings.append(
                    {
                        "id": f"vector-{media}-scale-{scale_id}",
                        "media": media,
                        "pdfScale": scale,
                        "preferCssPageSize": True,
                        "description": (
                            f"Chromium vector PDF using {media} CSS media and Playwright page.pdf scale={scale:.3f}."
                        ),
                    }
                )
        return settings

    def _compare_document_pdf_text_mask_fit(self, *, source_png: bytes, rendered_png: bytes) -> tuple[dict[str, Any], bytes]:
        source_width, source_height, source_rgb = self._document_pdf_png_rgb_pixels(source_png)
        rendered_width, rendered_height, rendered_rgb = self._document_pdf_png_rgb_pixels(rendered_png)
        source_mask = self._document_pdf_filled_ink_mask(source_width, source_height, source_rgb)
        rendered_mask = self._document_pdf_filled_ink_mask(rendered_width, rendered_height, rendered_rgb)
        width = max(source_width, rendered_width)
        height = max(source_height, rendered_height)

        xor_pixels = 0
        source_ink = 0
        rendered_ink = 0
        union_ink = 0
        diff_rgb = bytearray(width * height * 3)
        for y in range(height):
            for x in range(width):
                s = x < source_width and y < source_height and source_mask[y * source_width + x] == 1
                r = x < rendered_width and y < rendered_height and rendered_mask[y * rendered_width + x] == 1
                offset = (y * width + x) * 3
                if s:
                    source_ink += 1
                if r:
                    rendered_ink += 1
                if s or r:
                    union_ink += 1
                if s != r:
                    xor_pixels += 1
                    if s:
                        diff_rgb[offset : offset + 3] = b"\xff\x30\x30"
                    else:
                        diff_rgb[offset : offset + 3] = b"\x30\x30\xff"
                elif s and r:
                    diff_rgb[offset : offset + 3] = b"\x18\x18\x18"
                else:
                    diff_rgb[offset : offset + 3] = b"\xff\xff\xff"

        horizontal_score = self._document_pdf_projection_distance(
            self._document_pdf_mask_projection(source_mask, source_width, source_height, axis="y"),
            self._document_pdf_mask_projection(rendered_mask, rendered_width, rendered_height, axis="y"),
        )
        vertical_score = self._document_pdf_projection_distance(
            self._document_pdf_mask_projection(source_mask, source_width, source_height, axis="x"),
            self._document_pdf_mask_projection(rendered_mask, rendered_width, rendered_height, axis="x"),
        )
        mipmap_score = self._document_pdf_mipmap_mask_distance(
            source_mask,
            source_width,
            source_height,
            rendered_mask,
            rendered_width,
            rendered_height,
        )
        source_box = self._document_pdf_mask_bbox(source_mask, source_width, source_height)
        rendered_box = self._document_pdf_mask_bbox(rendered_mask, rendered_width, rendered_height)
        bbox_score = self._document_pdf_bbox_distance(source_box, rendered_box, width, height)
        centroid_score = self._document_pdf_centroid_distance(
            self._document_pdf_mask_centroid(source_mask, source_width, source_height),
            self._document_pdf_mask_centroid(rendered_mask, rendered_width, rendered_height),
            width,
            height,
        )
        dimension_score = 0.0 if source_width == rendered_width and source_height == rendered_height else 1.0
        xor_score = xor_pixels / max(1, union_ink)
        ink_balance_score = abs(source_ink - rendered_ink) / max(1, max(source_ink, rendered_ink))
        source_quality = self._document_pdf_mask_quality(source_mask, source_width, source_height)
        rendered_quality = self._document_pdf_mask_quality(rendered_mask, rendered_width, rendered_height)
        mask_quality = {
            "valid": bool(source_quality.get("valid")) and bool(rendered_quality.get("valid")),
            "source": source_quality,
            "rendered": rendered_quality,
        }
        fit_score = (
            xor_score * 0.34
            + horizontal_score * 0.19
            + vertical_score * 0.19
            + mipmap_score * 0.16
            + bbox_score * 0.05
            + centroid_score * 0.04
            + ink_balance_score * 0.02
            + dimension_score * 0.01
        )
        if not mask_quality["valid"]:
            fit_score += 1_000_000.0

        metrics = {
            "sourceWidthPx": source_width,
            "sourceHeightPx": source_height,
            "renderedWidthPx": rendered_width,
            "renderedHeightPx": rendered_height,
            "dimensionsMatch": source_width == rendered_width and source_height == rendered_height,
            "sourceInkPixels": source_ink,
            "renderedInkPixels": rendered_ink,
            "unionInkPixels": union_ink,
            "xorInkPixels": xor_pixels,
            "xorInkPercentOfUnion": (xor_pixels / union_ink * 100.0) if union_ink else 0.0,
            "horizontalScanScore": horizontal_score,
            "verticalScanScore": vertical_score,
            "mipmapScore": mipmap_score,
            "bboxScore": bbox_score,
            "centroidScore": centroid_score,
            "inkBalanceScore": ink_balance_score,
            "fitScore": fit_score,
            "sourceInkBoundingBox": source_box,
            "renderedInkBoundingBox": rendered_box,
            "maskQuality": mask_quality,
        }
        return metrics, self._encode_document_pdf_rgb_png(width, height, bytes(diff_rgb))

    def _document_pdf_filled_ink_mask(self, width: int, height: int, rgb: bytes) -> bytearray:
        mask = self._document_pdf_ink_mask(width, height, rgb)
        self._document_pdf_clear_mask_edge_band(mask, width, height)
        return self._document_pdf_fill_enclosed_background(mask, width, height)

    def _document_pdf_clear_mask_edge_band(self, mask: bytearray, width: int, height: int) -> None:
        """Remove page-edge chrome before glyph-mask filling.

        A one-pixel editor page border can form a closed rectangle. If it is
        treated as ink, the later enclosed-background fill marks the entire page
        as a glyph stencil. Clearing a narrow edge band keeps scoring focused on
        text/content ink instead of page chrome.
        """

        if width <= 0 or height <= 0:
            return
        band = max(2, min(8, round(min(width, height) * 0.01)))
        for y in range(height):
            row = y * width
            for x in range(width):
                if x < band or y < band or x >= width - band or y >= height - band:
                    mask[row + x] = 0

    def _document_pdf_ink_mask(self, width: int, height: int, rgb: bytes) -> bytearray:
        bg_r, bg_g, bg_b = self._document_pdf_estimate_background_rgb(width, height, rgb)
        bg_luma = (bg_r * 299 + bg_g * 587 + bg_b * 114) / 1000.0
        mask = bytearray(width * height)
        for index in range(width * height):
            offset = index * 3
            r = rgb[offset]
            g = rgb[offset + 1]
            b = rgb[offset + 2]
            luma = (r * 299 + g * 587 + b * 114) / 1000.0
            color_distance = abs(r - bg_r) + abs(g - bg_g) + abs(b - bg_b)
            if color_distance >= 36 or luma <= bg_luma - 18:
                mask[index] = 1
        return mask

    def _document_pdf_estimate_background_rgb(self, width: int, height: int, rgb: bytes) -> tuple[int, int, int]:
        samples: list[tuple[int, int, int]] = []
        sample_size = min(24, max(1, width), max(1, height))
        corners = (
            (0, 0),
            (max(0, width - sample_size), 0),
            (0, max(0, height - sample_size)),
            (max(0, width - sample_size), max(0, height - sample_size)),
        )
        for start_x, start_y in corners:
            for y in range(start_y, min(height, start_y + sample_size)):
                for x in range(start_x, min(width, start_x + sample_size)):
                    offset = (y * width + x) * 3
                    samples.append((rgb[offset], rgb[offset + 1], rgb[offset + 2]))
        if not samples:
            return 255, 255, 255
        samples.sort(key=lambda item: item[0] + item[1] + item[2])
        return samples[len(samples) // 2]

    def _document_pdf_fill_enclosed_background(self, mask: bytearray, width: int, height: int) -> bytearray:
        if width <= 0 or height <= 0:
            return mask
        visited = bytearray(width * height)
        queue: list[int] = []

        def push_if_background(x: int, y: int) -> None:
            if x < 0 or y < 0 or x >= width or y >= height:
                return
            index = y * width + x
            if mask[index] or visited[index]:
                return
            visited[index] = 1
            queue.append(index)

        for x in range(width):
            push_if_background(x, 0)
            push_if_background(x, height - 1)
        for y in range(height):
            push_if_background(0, y)
            push_if_background(width - 1, y)

        head = 0
        while head < len(queue):
            index = queue[head]
            head += 1
            x = index % width
            y = index // width
            push_if_background(x + 1, y)
            push_if_background(x - 1, y)
            push_if_background(x, y + 1)
            push_if_background(x, y - 1)

        filled = bytearray(mask)
        for index, value in enumerate(mask):
            if not value and not visited[index]:
                filled[index] = 1
        return filled

    def _document_pdf_mask_quality(self, mask: bytearray, width: int, height: int) -> dict[str, Any]:
        ink_pixels = sum(1 for value in mask if value)
        total_pixels = max(1, width * height)
        ink_fraction = ink_pixels / total_pixels
        bbox = self._document_pdf_mask_bbox(mask, width, height)
        reasons: list[str] = []
        blank = ink_pixels == 0
        if not blank:
            touches_full_page = (
                bbox.get("left") is not None
                and int(bbox.get("left") or 0) <= 1
                and int(bbox.get("top") or 0) <= 1
                and int(bbox.get("right") or 0) >= width - 2
                and int(bbox.get("bottom") or 0) >= height - 2
            )
            if touches_full_page:
                reasons.append("ink-bounding-box-covers-full-page")
            if ink_fraction > 0.45:
                reasons.append("ink-mask-covers-too-much-page")
        return {
            "valid": not reasons,
            "reasons": reasons,
            "inkPixels": ink_pixels,
            "inkFraction": ink_fraction,
            "blank": blank,
            "boundingBox": bbox,
        }

    def _document_pdf_mask_projection(self, mask: bytearray, width: int, height: int, *, axis: str) -> list[float]:
        if axis == "x":
            projection = [0.0] * width
            if height <= 0:
                return projection
            for y in range(height):
                row = y * width
                for x in range(width):
                    if mask[row + x]:
                        projection[x] += 1.0
            return [value / height for value in projection]
        projection = [0.0] * height
        if width <= 0:
            return projection
        for y in range(height):
            row = y * width
            total = 0
            for x in range(width):
                total += 1 if mask[row + x] else 0
            projection[y] = total / width
        return projection

    def _document_pdf_projection_distance(self, source: list[float], rendered: list[float], *, max_shift: int = 3) -> float:
        length = max(len(source), len(rendered))
        if length <= 0:
            return 0.0

        def value_at(values: list[float], index: int) -> float:
            return values[index] if 0 <= index < len(values) else 0.0

        best = None
        for shift in range(-max_shift, max_shift + 1):
            total = 0.0
            for index in range(length):
                total += abs(value_at(source, index) - value_at(rendered, index + shift))
            score = total / length
            if best is None or score < best:
                best = score
        return float(best or 0.0)

    def _document_pdf_mipmap_mask_distance(
        self,
        source_mask: bytearray,
        source_width: int,
        source_height: int,
        rendered_mask: bytearray,
        rendered_width: int,
        rendered_height: int,
    ) -> float:
        scores: list[float] = []
        for block_size in (2, 4, 8, 16, 32):
            width_blocks = max((max(source_width, rendered_width) + block_size - 1) // block_size, 1)
            height_blocks = max((max(source_height, rendered_height) + block_size - 1) // block_size, 1)
            total = 0.0
            count = 0
            for by in range(height_blocks):
                for bx in range(width_blocks):
                    total += abs(
                        self._document_pdf_block_ink_fraction(source_mask, source_width, source_height, bx, by, block_size)
                        - self._document_pdf_block_ink_fraction(rendered_mask, rendered_width, rendered_height, bx, by, block_size)
                    )
                    count += 1
            scores.append(total / max(1, count))
        return sum(scores) / len(scores) if scores else 0.0

    def _document_pdf_block_ink_fraction(self, mask: bytearray, width: int, height: int, bx: int, by: int, block_size: int) -> float:
        start_x = bx * block_size
        start_y = by * block_size
        end_x = min(width, start_x + block_size)
        end_y = min(height, start_y + block_size)
        if start_x >= width or start_y >= height or end_x <= start_x or end_y <= start_y:
            return 0.0
        ink = 0
        total = 0
        for y in range(start_y, end_y):
            row = y * width
            for x in range(start_x, end_x):
                total += 1
                if mask[row + x]:
                    ink += 1
        return ink / max(1, total)

    def _document_pdf_mask_bbox(self, mask: bytearray, width: int, height: int) -> dict[str, int | None]:
        min_x: int | None = None
        min_y: int | None = None
        max_x: int | None = None
        max_y: int | None = None
        for y in range(height):
            row = y * width
            for x in range(width):
                if not mask[row + x]:
                    continue
                min_x = x if min_x is None else min(min_x, x)
                max_x = x if max_x is None else max(max_x, x)
                min_y = y if min_y is None else min(min_y, y)
                max_y = y if max_y is None else max(max_y, y)
        return {"left": min_x, "top": min_y, "right": max_x, "bottom": max_y}

    def _document_pdf_bbox_distance(self, source: dict[str, int | None], rendered: dict[str, int | None], width: int, height: int) -> float:
        if source.get("left") is None and rendered.get("left") is None:
            return 0.0
        if source.get("left") is None or rendered.get("left") is None:
            return 1.0
        total = 0.0
        for key, denominator in (("left", width), ("right", width), ("top", height), ("bottom", height)):
            total += abs(float(source.get(key) or 0) - float(rendered.get(key) or 0)) / max(1.0, float(denominator))
        return total / 4.0

    def _document_pdf_mask_centroid(self, mask: bytearray, width: int, height: int) -> dict[str, float | None]:
        total = 0
        sum_x = 0.0
        sum_y = 0.0
        for y in range(height):
            row = y * width
            for x in range(width):
                if mask[row + x]:
                    total += 1
                    sum_x += x
                    sum_y += y
        if not total:
            return {"x": None, "y": None}
        return {"x": sum_x / total, "y": sum_y / total}

    def _document_pdf_centroid_distance(self, source: dict[str, float | None], rendered: dict[str, float | None], width: int, height: int) -> float:
        if source.get("x") is None and rendered.get("x") is None:
            return 0.0
        if source.get("x") is None or rendered.get("x") is None:
            return 1.0
        dx = abs(float(source["x"]) - float(rendered["x"])) / max(1.0, float(width))
        dy = abs(float(source["y"]) - float(rendered["y"])) / max(1.0, float(height))
        return (dx + dy) / 2.0

    def _document_pdf_import_pymupdf(self) -> Any:
        import fitz

        return fitz

    def _render_document_pdf_candidate_with_pymupdf(self, fitz_module: Any, pdf_bytes: bytes, *, dpi: int) -> list[bytes]:
        document = fitz_module.open(stream=pdf_bytes, filetype="pdf")
        try:
            zoom = float(dpi) / 72.0
            matrix = fitz_module.Matrix(zoom, zoom)
            rendered: list[bytes] = []
            for page_index in range(int(document.page_count)):
                page = document.load_page(page_index)
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                rendered.append(pixmap.tobytes("png"))
            return rendered
        finally:
            document.close()

    def _compare_document_pdf_png_pair(self, *, source_png: bytes, rendered_png: bytes) -> tuple[dict[str, Any], bytes]:
        source_width, source_height, source_rgb = self._document_pdf_png_rgb_pixels(source_png)
        rendered_width, rendered_height, rendered_rgb = self._document_pdf_png_rgb_pixels(rendered_png)
        diff_width = max(source_width, rendered_width)
        diff_height = max(source_height, rendered_height)
        diff = bytearray(diff_width * diff_height * 3)

        changed_pixels = 0
        total_abs_channel_delta = 0
        max_channel_delta = 0
        for y in range(diff_height):
            for x in range(diff_width):
                diff_offset = (y * diff_width + x) * 3
                if x >= source_width or y >= source_height or x >= rendered_width or y >= rendered_height:
                    diff[diff_offset : diff_offset + 3] = b"\xff\xff\xff"
                    changed_pixels += 1
                    total_abs_channel_delta += 255 * 3
                    max_channel_delta = 255
                    continue
                source_offset = (y * source_width + x) * 3
                rendered_offset = (y * rendered_width + x) * 3
                dr = abs(source_rgb[source_offset] - rendered_rgb[rendered_offset])
                dg = abs(source_rgb[source_offset + 1] - rendered_rgb[rendered_offset + 1])
                db = abs(source_rgb[source_offset + 2] - rendered_rgb[rendered_offset + 2])
                if dr or dg or db:
                    changed_pixels += 1
                total_abs_channel_delta += dr + dg + db
                max_channel_delta = max(max_channel_delta, dr, dg, db)
                diff[diff_offset : diff_offset + 3] = bytes((dr, dg, db))

        total_pixels = diff_width * diff_height
        dimensions_match = source_width == rendered_width and source_height == rendered_height
        metrics = {
            "sourceWidthPx": source_width,
            "sourceHeightPx": source_height,
            "renderedWidthPx": rendered_width,
            "renderedHeightPx": rendered_height,
            "dimensionsMatch": dimensions_match,
            "totalPixels": total_pixels,
            "changedPixels": changed_pixels,
            "changedPixelPercent": (changed_pixels / total_pixels * 100.0) if total_pixels else 0.0,
            "meanAbsChannelDelta": (total_abs_channel_delta / (total_pixels * 3)) if total_pixels else 0.0,
            "maxChannelDelta": max_channel_delta,
            "exactMatch": dimensions_match and changed_pixels == 0,
        }
        return metrics, self._encode_document_pdf_rgb_png(diff_width, diff_height, bytes(diff))

    def _document_pdf_png_rgb_pixels(self, png_bytes: bytes) -> tuple[int, int, bytes]:
        image = decode_png_image(png_bytes)
        width = image.info.width
        height = image.info.height
        if image.color_space == "DeviceRGB":
            rgb = bytearray(image.samples)
        elif image.color_space == "DeviceGray":
            rgb = bytearray(width * height * 3)
            for index, value in enumerate(image.samples):
                target = index * 3
                rgb[target : target + 3] = bytes((value, value, value))
        else:
            raise ValueError(f"Unsupported PNG color space: {image.color_space}")

        if image.alpha is not None:
            if len(image.alpha) != width * height:
                raise ValueError("PNG alpha channel length does not match dimensions")
            for pixel_index, alpha in enumerate(image.alpha):
                if alpha == 255:
                    continue
                target = pixel_index * 3
                inverse = 255 - alpha
                rgb[target] = (rgb[target] * alpha + 255 * inverse + 127) // 255
                rgb[target + 1] = (rgb[target + 1] * alpha + 255 * inverse + 127) // 255
                rgb[target + 2] = (rgb[target + 2] * alpha + 255 * inverse + 127) // 255
        return width, height, bytes(rgb)

    def _encode_document_pdf_rgb_png(self, width: int, height: int, rgb: bytes) -> bytes:
        if width <= 0 or height <= 0:
            raise ValueError("PNG dimensions must be positive")
        expected = width * height * 3
        if len(rgb) != expected:
            raise ValueError(f"RGB buffer has {len(rgb)} bytes; expected {expected}")
        rows = bytearray()
        stride = width * 3
        for y in range(height):
            rows.append(0)
            offset = y * stride
            rows.extend(rgb[offset : offset + stride])
        ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + self._document_pdf_png_chunk(b"IHDR", ihdr)
            + self._document_pdf_png_chunk(b"IDAT", zlib.compress(bytes(rows)))
            + self._document_pdf_png_chunk(b"IEND", b"")
        )

    def _document_pdf_png_chunk(self, kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    def _document_pdf_candidate_id(self, path: str) -> str:
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        if name.lower().endswith(".pdf"):
            name = name[:-4]
        cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "-", name).strip(".-")
        return cleaned or "candidate"

    def _render_document_pdf_smoke_pages_with_playwright(self, export_html: str, width_px: int, height_px: int) -> tuple[list[bytes], dict[str, Any]]:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            python_executable = str(getattr(sys, "executable", "python") or "python")
            raise RuntimeError(
                "PDF smoke export requires Playwright so the backend can screenshot each document page. "
                f"Playwright import failed in {python_executable}: {type(exc).__name__}: {exc}. "
                f"Install it with `{python_executable} -m pip install playwright` and "
                f"`{python_executable} -m playwright install chromium`."
            ) from exc

        launch_args: dict[str, Any] = {
            "headless": True,
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-setuid-sandbox",
                "--disable-software-rasterizer",
                "--single-process",
            ],
        }
        candidates: list[tuple[str, str | None]] = [("playwright managed chromium", None)]
        for label, executable in self._document_pdf_chromium_executable_candidates():
            if any(existing == executable for _, existing in candidates):
                continue
            candidates.append((label, executable))

        def capture_with_browser(browser: Any, label: str, executable: str | None) -> tuple[list[bytes], dict[str, Any]]:
            context = browser.new_context(
                viewport={"width": width_px, "height": height_px},
                device_scale_factor=1,
                java_script_enabled=True,
            )
            try:
                page = context.new_page()
                page.emulate_media(media="screen")
                page.set_content(export_html, wait_until="load")
                try:
                    page.wait_for_load_state("networkidle", timeout=1000)
                except Exception:
                    pass
                try:
                    page.evaluate("() => document.fonts ? document.fonts.ready : Promise.resolve()")
                except Exception:
                    pass
                page_locator = page.locator(".mc-page")
                page_count = page_locator.count()
                if page_count < 1:
                    raise RuntimeError("PDF smoke export found no .mc-page elements to screenshot.")
                screenshots: list[bytes] = []
                for index in range(page_count):
                    screenshots.append(
                        page_locator.nth(index).screenshot(
                            type="png",
                            animations="disabled",
                            caret="hide",
                            scale="css",
                            timeout=10000,
                        )
                    )
                capture_info = {
                    "source": "export-only-html .mc-page screenshots",
                    "renderer": {
                        "label": label,
                        "executablePath": executable or "playwright-managed",
                    },
                    "viewport": {"widthPx": width_px, "heightPx": height_px},
                    "deviceScaleFactor": 1,
                    "media": "screen",
                    "pageCount": len(screenshots),
                    "pythonExecutable": str(getattr(sys, "executable", "python") or "python"),
                    "pythonVersion": sys.version.split()[0],
                    "platform": platform.platform(),
                }
                return screenshots, capture_info
            finally:
                context.close()

        failures: list[str] = []
        with sync_playwright() as playwright:
            for label, executable in candidates:
                browser = None
                try:
                    per_launch_args = dict(launch_args)
                    if executable:
                        per_launch_args["executable_path"] = executable
                    browser = playwright.chromium.launch(**per_launch_args)
                    return capture_with_browser(browser, label, executable)
                except Exception as exc:
                    failures.append(f"{label}: {exc}")
                finally:
                    try:
                        browser.close() if browser else None
                    except Exception:
                        pass
        details = " | ".join(failures[-4:])
        raise RuntimeError(f"PDF smoke export requires a working Chromium renderer. {details}")

    def _document_pdf_chromium_executable_candidates(self) -> list[tuple[str, str]]:
        seen: set[str] = set()
        candidates: list[tuple[str, str]] = []

        def add(label: str, value: object) -> None:
            if not value:
                return
            executable = str(value).strip().strip('"')
            if not executable:
                return
            path = Path(executable)
            resolved = shutil.which(executable) if not path.is_absolute() else None
            if resolved:
                executable = resolved
            if not Path(executable).exists():
                return
            key = str(Path(executable).resolve()).lower()
            if key in seen:
                return
            seen.add(key)
            candidates.append((label, executable))

        add("DOCUMENT_PDF_CHROMIUM_EXECUTABLE", os.environ.get("DOCUMENT_PDF_CHROMIUM_EXECUTABLE"))
        add("PLAYWRIGHT_CHROMIUM_EXECUTABLE", os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"))
        for command in (
            "chromium",
            "chromium-browser",
            "google-chrome",
            "google-chrome-stable",
            "chrome",
            "chrome.exe",
            "msedge",
            "msedge.exe",
        ):
            add(command, command)

        program_files = [
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        ]
        for base in program_files:
            if not base:
                continue
            for relative in (
                "Google/Chrome/Application/chrome.exe",
                "Microsoft/Edge/Application/msedge.exe",
            ):
                add(relative, Path(base) / relative)

        for absolute in (
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
        ):
            add(absolute, absolute)

        return candidates

    def _render_document_pdf_with_chromium_cli(self, export_html: str, width_px: int, height_px: int) -> bytes:
        candidates = self._document_pdf_chromium_executable_candidates()
        if not candidates:
            raise RuntimeError("No local Chrome, Edge, or Chromium executable was found.")

        failures: list[str] = []
        with tempfile.TemporaryDirectory(prefix="main-computer-pdf-") as temp_name:
            temp_path = Path(temp_name)
            html_path = temp_path / "document-export.html"
            output_path = temp_path / "document-export.pdf"
            html_path.write_text(export_html, encoding="utf-8")
            html_uri = html_path.resolve().as_uri()
            for label, executable in candidates:
                for headless_flag in ("--headless=new", "--headless"):
                    try:
                        if output_path.exists():
                            output_path.unlink()
                        command = [
                            executable,
                            headless_flag,
                            "--disable-gpu",
                            "--disable-dev-shm-usage",
                            "--no-sandbox",
                            "--disable-setuid-sandbox",
                            "--disable-software-rasterizer",
                            "--single-process",
                            "--run-all-compositor-stages-before-draw",
                            "--virtual-time-budget=1000",
                            "--print-to-pdf-no-header",
                            f"--print-to-pdf={output_path}",
                            html_uri,
                        ]
                        completed = subprocess.run(
                            command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            timeout=30,
                            check=False,
                        )
                        if completed.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
                            return output_path.read_bytes()
                        failure = (completed.stderr or completed.stdout or f"exit code {completed.returncode}").strip()
                        failures.append(f"{label} {headless_flag}: {failure[:500]}")
                    except Exception as exc:
                        failures.append(f"{label} {headless_flag}: {exc}")
        detail = " | ".join(failures[-4:]) if failures else "No Chromium fallback attempts completed."
        raise RuntimeError(f"Local Chromium command-line PDF fallback failed. {detail}")

    def _render_document_pdf_with_playwright(
        self,
        export_html: str,
        width_px: int,
        height_px: int,
        *,
        media: str = "screen",
        pdf_scale: float = 1.0,
        prefer_css_page_size: bool = True,
    ) -> bytes:
        media = "print" if str(media).lower() == "print" else "screen"
        pdf_scale = max(0.1, min(2.0, float(pdf_scale or 1.0)))
        prefer_css_page_size = bool(prefer_css_page_size)
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            import_error = f"{type(exc).__name__}: {exc}"
            try:
                return self._render_document_pdf_with_chromium_cli(export_html, width_px, height_px)
            except RuntimeError as cli_exc:
                python_executable = str(getattr(sys, "executable", "python") or "python")
                raise RuntimeError(
                    "PDF export requires Playwright or a local Chrome/Edge/Chromium executable. "
                    f"Playwright import failed in {python_executable}: {import_error}. "
                    f"Chromium command-line fallback also failed: {cli_exc}. "
                    f"Install Playwright for the viewport Python with `{python_executable} -m pip install playwright` "
                    f"and `{python_executable} -m playwright install chromium`, or set DOCUMENT_PDF_CHROMIUM_EXECUTABLE."
                ) from exc

        launch_args: dict[str, Any] = {
            "headless": True,
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-setuid-sandbox",
                "--disable-software-rasterizer",
                "--single-process",
            ],
        }
        candidates: list[tuple[str, str | None]] = [("playwright managed chromium", None)]
        for label, executable in self._document_pdf_chromium_executable_candidates():
            if any(existing == executable for _, existing in candidates):
                continue
            candidates.append((label, executable))

        def render_with_browser(browser: Any) -> bytes:
            context = browser.new_context(
                viewport={"width": width_px, "height": height_px},
                device_scale_factor=1,
                java_script_enabled=False,
            )
            try:
                page = context.new_page()
                page.emulate_media(media=media)
                page.set_content(export_html, wait_until="load")
                return page.pdf(
                    print_background=True,
                    prefer_css_page_size=prefer_css_page_size,
                    margin={"top": "0px", "right": "0px", "bottom": "0px", "left": "0px"},
                    scale=pdf_scale,
                    display_header_footer=False,
                )
            finally:
                context.close()

        try:
            failures: list[str] = []
            with sync_playwright() as playwright:
                for label, executable in candidates:
                    browser = None
                    try:
                        per_launch_args = dict(launch_args)
                        if executable:
                            per_launch_args["executable_path"] = executable
                        browser = playwright.chromium.launch(**per_launch_args)
                        return render_with_browser(browser)
                    except Exception as exc:
                        failures.append(f"{label}: {exc}")
                    finally:
                        try:
                            browser.close() if browser else None
                        except Exception:
                            pass
            details = " | ".join(failures[-4:])
            try:
                return self._render_document_pdf_with_chromium_cli(export_html, width_px, height_px)
            except RuntimeError as cli_exc:
                raise RuntimeError(f"PDF export requires a working Chromium renderer. Playwright failed: {details}. Chromium command-line fallback failed: {cli_exc}")
        except RuntimeError:
            raise
        except Exception as exc:
            try:
                return self._render_document_pdf_with_chromium_cli(export_html, width_px, height_px)
            except RuntimeError as cli_exc:
                raise RuntimeError(f"PDF export failed in Chromium: {exc}. Chromium command-line fallback failed: {cli_exc}") from exc


    def _docs_drafts_root(self) -> Path:
        return (self.server.debug_root / "runtime" / "document_editor_drafts").resolve()

    def _docs_draft_path(self, requested: str) -> tuple[Path, str]:
        raw = str(requested or "").replace("\\", "/").strip()
        root = self._docs_drafts_root()
        if not raw:
            return root / "scratchpad.json", ""
        pretty_path = self._pretty_docs_path(raw)
        relative_path = pretty_path.relative_to(self._pretty_docs_root()).as_posix()
        digest = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()
        target = (root / "pretty_docs" / f"{digest}.json").resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError("Document draft path must stay inside draft storage.") from exc
        return target, relative_path

    def _read_docs_draft_file(self, path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Document draft storage file is not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Document draft storage file must contain an object.")
        return payload

    def _pretty_docs_root(self) -> Path:
        return (self.server.debug_root / "pretty_docs").resolve()

    def _pretty_docs_kind(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".md":
            return "markdown"
        if suffix == ".html":
            return "html"
        return "text"

    def _pretty_docs_title(self, relative_path: str) -> str:
        stem = Path(relative_path).stem.replace("-", " ").replace("_", " ").strip()
        return stem.title() if stem else relative_path

    def _pretty_docs_normalized_parts(self, requested: str) -> list[str]:
        raw = str(requested or "").replace("\\", "/").strip()
        candidate = Path(raw)
        if candidate.is_absolute() or raw.startswith("/"):
            raise ValueError("Pretty Docs paths must be relative.")
        parts = [part for part in raw.split("/") if part and part != "."]
        if not parts:
            raise ValueError("Pretty Docs path is required.")
        if any(part == ".." for part in parts):
            raise ValueError("Pretty Docs paths may not contain traversal.")
        return parts

    def _pretty_docs_path(self, requested: str) -> Path:
        root = self._pretty_docs_root()
        parts = self._pretty_docs_normalized_parts(requested)
        candidate = (root.joinpath(*parts)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("Pretty Docs paths must stay inside pretty_docs.") from exc
        if candidate.suffix.lower() not in {".md", ".txt", ".html"}:
            raise ValueError("Pretty Docs supports only .md, .txt, and .html files.")
        if not candidate.is_file():
            raise ValueError("Pretty Docs file does not exist.")
        return candidate

    def _pretty_docs_index_metadata(self) -> dict[str, dict[str, Any]]:
        index_path = self._pretty_docs_root() / "index.json"
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        documents = payload.get("documents", []) if isinstance(payload, dict) else []
        metadata: dict[str, dict[str, Any]] = {}
        if not isinstance(documents, list):
            return metadata
        for item in documents:
            if not isinstance(item, dict):
                continue
            raw_path = str(item.get("path", "") or "")
            try:
                path = self._pretty_docs_path(raw_path)
            except Exception:
                continue
            relative_path = path.relative_to(self._pretty_docs_root()).as_posix()
            metadata[relative_path] = item
        return metadata

    def _pretty_docs_document_payload(self, path: Path, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        relative_path = path.relative_to(self._pretty_docs_root()).as_posix()
        stat = path.stat()
        metadata = metadata or {}
        return {
            "path": relative_path,
            "display_path": f"pretty_docs/{relative_path}",
            "title": str(metadata.get("title") or self._pretty_docs_title(relative_path)),
            "kind": str(metadata.get("kind") or self._pretty_docs_kind(path)),
            "bytes": stat.st_size,
            "mtime": stat.st_mtime,
            "content_hash": self._pretty_docs_content_hash(path),
        }

    def _pretty_docs_content_hash(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _pretty_docs_documents(self) -> list[dict[str, Any]]:
        root = self._pretty_docs_root()
        if not root.exists() or not root.is_dir():
            return []
        metadata = self._pretty_docs_index_metadata()
        paths: dict[str, Path] = {}
        for relative_path in metadata:
            try:
                path = self._pretty_docs_path(relative_path)
            except Exception:
                continue
            paths[relative_path] = path
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".md", ".txt", ".html"}:
                continue
            relative_path = path.relative_to(root).as_posix()
            paths.setdefault(relative_path, path)
        documents = [
            self._pretty_docs_document_payload(path, metadata.get(relative_path))
            for relative_path, path in paths.items()
        ]
        return sorted(
            documents,
            key=lambda item: (
                int(metadata.get(str(item.get("path", "")), {}).get("order", 10_000)),
                str(item.get("title", "")).lower(),
                str(item.get("path", "")).lower(),
            ),
        )
