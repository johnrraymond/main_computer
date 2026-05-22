from __future__ import annotations

import binascii
import io
import posixpath
import xml.etree.ElementTree as ET

from main_computer.viewport_state import *  # noqa: F401,F403

class ViewportSpreadsheetRoutesMixin:
    def _handle_spreadsheet_files(self) -> None:
        try:
            self._read_json()
            files = [self._spreadsheet_file_payload(path) for path in sorted(self._spreadsheet_root().rglob("*")) if path.is_file() and path.suffix.lower() in {".json", ".csv"}]
            self.server.signal("api-spreadsheet-files", count=len(files))
            self._send_json({"ok": True, "root": "spreadsheets", "files": files, "count": len(files)})
        except Exception as exc:
            self.server.signal("api-spreadsheet-error", route="files", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_spreadsheet_read(self) -> None:
        try:
            body = self._read_json()
            path = self._spreadsheet_safe_path(str(body.get("path", "") or ""))
            payload = self._spreadsheet_file_payload(path)
            workbook = self._spreadsheet_csv_workbook(path) if path.suffix.lower() == ".csv" else self._spreadsheet_normalize_workbook(json.loads(path.read_text(encoding="utf-8")))
            self.server.signal("api-spreadsheet-read", path=payload["path"])
            self._send_json({"ok": True, "root": "spreadsheets", **payload, "workbook": workbook})
        except Exception as exc:
            self.server.signal("api-spreadsheet-error", route="read", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_spreadsheet_write(self) -> None:
        try:
            body = self._read_json()
            path = self._spreadsheet_safe_path(str(body.get("path", "") or ""), must_exist=False)
            if path.suffix.lower() != ".json":
                raise ValueError("Spreadsheet writes must target a .json workbook.")
            expected = str(body.get("expected_content_hash", "") or "")
            if path.exists() and expected != self._spreadsheet_content_hash(path):
                self.server.signal("api-spreadsheet-conflict", path=path.name)
                self._send_json({"ok": False, "conflict": True, "error": "expected_content_hash is stale."}, status=HTTPStatus.CONFLICT)
                return
            workbook = self._spreadsheet_normalize_workbook(body.get("workbook"))
            content = (json.dumps(workbook, indent=2) + "\n").encode("utf-8")
            self._spreadsheet_atomic_write(path, content)
            self.server.signal("api-spreadsheet-write", path=path.name, bytes=len(content))
            self._send_json({"ok": True, **self._spreadsheet_file_payload(path)})
        except Exception as exc:
            self.server.signal("api-spreadsheet-error", route="write", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_spreadsheet_create(self) -> None:
        try:
            body = self._read_json()
            path = self._spreadsheet_safe_path(str(body.get("path", "") or "untitled.json"), must_exist=False)
            if path.exists():
                raise ValueError("Spreadsheet already exists.")
            if path.suffix.lower() != ".json":
                raise ValueError("New spreadsheets must use .json.")
            workbook = self._spreadsheet_default_workbook(int(body.get("rows", 50) or 50), int(body.get("cols", 26) or 26))
            content = (json.dumps(workbook, indent=2) + "\n").encode("utf-8")
            self._spreadsheet_atomic_write(path, content)
            self.server.signal("api-spreadsheet-create", path=path.name)
            self._send_json({"ok": True, **self._spreadsheet_file_payload(path), "workbook": workbook})
        except Exception as exc:
            self.server.signal("api-spreadsheet-error", route="create", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_spreadsheet_export_csv(self) -> None:
        try:
            body = self._read_json()
            path = self._spreadsheet_safe_path(str(body.get("path", "") or ""))
            workbook = self._spreadsheet_csv_workbook(path) if path.suffix.lower() == ".csv" else self._spreadsheet_normalize_workbook(json.loads(path.read_text(encoding="utf-8")))
            sheet_name = str(body.get("sheet", "") or workbook.get("active_sheet") or "Sheet1")
            sheet = workbook["sheets"].get(sheet_name) or next(iter(workbook["sheets"].values()))
            rows = int(sheet.get("rows", 50))
            cols = int(sheet.get("cols", 26))
            cells = sheet.get("cells", {})
            output = tempfile.SpooledTemporaryFile(mode="w+", newline="", encoding="utf-8")
            writer = csv.writer(output)
            for row in range(1, rows + 1):
                writer.writerow([str(cells.get(f"{self._spreadsheet_col_name(col)}{row}", {}).get("value", "")) for col in range(1, cols + 1)])
            output.seek(0)
            content = output.read()
            output.close()
            filename = f"{path.stem}-{sheet_name}.csv"
            self.server.signal("api-spreadsheet-export-csv", path=path.name, sheet=sheet_name)
            self._send_json({"ok": True, "filename": filename, "content": content, "encoding": "text"})
        except Exception as exc:
            self.server.signal("api-spreadsheet-error", route="export-csv", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)


    def _handle_spreadsheet_export_xlsx(self) -> None:
        try:
            body = self._read_json()
            raw_workbook = body.get("workbook")
            raw_path = str(body.get("path", "") or "").strip()
            if isinstance(raw_workbook, dict):
                workbook = self._spreadsheet_normalize_workbook(raw_workbook)
                filename_stem = Path(raw_path.replace("\\", "/")).stem if raw_path else "spreadsheet"
            else:
                path = self._spreadsheet_safe_path(raw_path)
                workbook = self._spreadsheet_csv_workbook(path) if path.suffix.lower() == ".csv" else self._spreadsheet_normalize_workbook(json.loads(path.read_text(encoding="utf-8")))
                filename_stem = path.stem
            content = self._spreadsheet_xlsx_export_workbook(workbook)
            filename = f"{re.sub(r'[^A-Za-z0-9._ -]+', '-', filename_stem).strip(' .-_') or 'spreadsheet'}.xlsx"
            self.server.signal("api-spreadsheet-export-xlsx", filename=filename, bytes=len(content), sheets=len(workbook.get("sheets", {})))
            self._send_json({
                "ok": True,
                "filename": filename,
                "content_base64": base64.b64encode(content).decode("ascii"),
                "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "bytes": len(content),
                "encoding": "base64",
            })
        except Exception as exc:
            self.server.signal("api-spreadsheet-error", route="export-xlsx", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)


    def _handle_spreadsheet_import_xlsx(self) -> None:
        try:
            body = self._read_json()
            filename = str(body.get("filename", "") or "").strip()
            if not filename.lower().endswith(".xlsx"):
                raise ValueError("Only .xlsx spreadsheet imports are supported.")
            encoded = str(body.get("content_base64", "") or "").strip()
            if "," in encoded and encoded.lower().startswith("data:"):
                encoded = encoded.split(",", 1)[1]
            if not encoded:
                raise ValueError("XLSX import content is required.")
            if len(encoded) > 14 * 1024 * 1024:
                raise ValueError("XLSX import is limited to 10 MB.")
            try:
                content = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError("XLSX import content must be valid base64.") from exc
            if len(content) > 10 * 1024 * 1024:
                raise ValueError("XLSX import is limited to 10 MB.")

            workbook, warnings = self._spreadsheet_xlsx_workbook(content, filename)
            suggested_path = self._spreadsheet_unique_import_path(filename)
            self.server.signal(
                "api-spreadsheet-import-xlsx",
                filename=Path(filename.replace("\\", "/")).name,
                path=suggested_path,
                sheets=len(workbook.get("sheets", {})),
                warnings=len(warnings),
            )
            self._send_json({
                "ok": True,
                "filename": Path(filename.replace("\\", "/")).name,
                "path": suggested_path,
                "display_path": f"spreadsheets/{suggested_path}",
                "workbook": workbook,
                "warnings": warnings,
            })
        except Exception as exc:
            self.server.signal("api-spreadsheet-error", route="import-xlsx", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_spreadsheet_import_chat_variables(self) -> None:
        try:
            body = self._read_json()
            blob_id = str(body.get("blob_id", "") or "").strip()
            blob_path = self._chat_console_shared_variables_path(blob_id)
            if not blob_path.is_file():
                raise ValueError("Shared variable blob was not found.")
            blob = json.loads(blob_path.read_text(encoding="utf-8"))
            if blob.get("kind") != "chat-console-shared-variables":
                raise ValueError("Shared variable blob has the wrong kind.")
            variables = self._chat_console_clean_shared_variables(blob.get("variables"))
            if not variables:
                raise ValueError("Shared variable blob is empty.")
            workbook = self._spreadsheet_workbook_from_chat_variables(blob_id, variables, blob)
            requested_path = str(body.get("path", "") or "").strip()
            default_name = f"chat-shared-variables-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{blob_id[:8]}.json"
            path = self._spreadsheet_safe_path(requested_path or default_name, must_exist=False)
            if path.exists() and not requested_path:
                path = self._spreadsheet_safe_path(f"chat-shared-variables-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')}-{blob_id[:8]}.json", must_exist=False)
            if path.suffix.lower() != ".json":
                raise ValueError("Imported shared variables must be saved as a .json workbook.")
            content = (json.dumps(workbook, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            self._spreadsheet_atomic_write(path, content)
            payload = self._spreadsheet_file_payload(path)
            self.server.signal("api-spreadsheet-import-chat-variables", id=blob_id, path=payload["path"], count=len(variables))
            self._send_json({"ok": True, **payload, "workbook": workbook, "blob_id": blob_id, "count": len(variables)})
        except Exception as exc:
            self.server.signal("api-spreadsheet-error", route="import-chat-variables", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _spreadsheet_workbook_from_chat_variables(self, blob_id: str, variables: dict[str, Any], blob: dict[str, Any] | None = None) -> dict[str, Any]:
        cells: dict[str, dict[str, str]] = {
            "A1": {"value": "Name"},
            "B1": {"value": "Type"},
            "C1": {"value": "Value"},
            "D1": {"value": "JSON"},
        }
        for row, (name, value) in enumerate(sorted(variables.items(), key=lambda item: item[0].lower()), start=2):
            value_type = type(value).__name__
            if value is None:
                display_value = ""
                value_type = "null"
            elif isinstance(value, bool):
                display_value = "TRUE" if value else "FALSE"
                value_type = "boolean"
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                display_value = str(value)
                value_type = "number"
            elif isinstance(value, str):
                display_value = value
                value_type = "string"
            else:
                display_value = json.dumps(value, ensure_ascii=False)
                value_type = "json"
            cells[f"A{row}"] = {"value": str(name)}
            cells[f"B{row}"] = {"value": value_type}
            cells[f"C{row}"] = {"value": str(display_value)[:4000]}
            cells[f"D{row}"] = {"value": json.dumps(value, ensure_ascii=False, default=str)[:4000]}
        rows = max(50, len(variables) + 2)
        return {
            "version": 1,
            "active_sheet": "SharedVars",
            "sheets": {"SharedVars": {"rows": rows, "cols": 4, "cells": cells}},
            "metadata": {
                "source": "chat-console-shared-variables",
                "blob_id": blob_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source_created_at": str((blob or {}).get("created_at") or ""),
                "chat": {
                    "active_thread_id": str(((blob or {}).get("source") or {}).get("thread_id") or ((blob or {}).get("source") or {}).get("active_thread_id") or ""),
                    "origin_thread_id": str(((blob or {}).get("source") or {}).get("thread_id") or ((blob or {}).get("source") or {}).get("active_thread_id") or ""),
                    "origin_thread_title": str(((blob or {}).get("source") or {}).get("thread_title") or ""),
                    "linked_by": "chat-console-export",
                    "linked_at": datetime.now(timezone.utc).isoformat(),
                },
            },
        }


    def _spreadsheet_unique_import_path(self, filename: str) -> str:
        name = Path(str(filename or "imported-workbook.xlsx").replace("\\", "/")).name
        stem = re.sub(r"(?i)\.xlsx$", "", name).strip()
        stem = re.sub(r"[^A-Za-z0-9._ -]+", "-", stem)
        stem = re.sub(r"\s+", "-", stem).strip(" .-_") or "imported-workbook"
        candidate = f"{stem}-imported.json"
        path = self._spreadsheet_safe_path(candidate, must_exist=False)
        if not path.exists():
            return candidate
        for index in range(2, 1000):
            candidate = f"{stem}-imported-{index}.json"
            path = self._spreadsheet_safe_path(candidate, must_exist=False)
            if not path.exists():
                return candidate
        raise ValueError("Could not find an available import filename.")

    def _spreadsheet_xlsx_workbook(self, content: bytes, filename: str) -> tuple[dict[str, Any], list[str]]:
        warnings: list[str] = []
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if archive.testzip() is not None:
                    raise ValueError("XLSX archive failed integrity validation.")
                shared_strings = self._spreadsheet_xlsx_shared_strings(archive, warnings)
                relationships = self._spreadsheet_xlsx_workbook_relationships(archive, warnings)
                sheet_specs = self._spreadsheet_xlsx_sheet_specs(archive, relationships, warnings)
                if not sheet_specs:
                    sheet_specs = self._spreadsheet_xlsx_fallback_sheet_specs(archive)
                if not sheet_specs:
                    raise ValueError("XLSX workbook does not contain any worksheets.")
                active_sheet_index = self._spreadsheet_xlsx_active_sheet_index(archive, warnings)
                app_metadata = self._spreadsheet_xlsx_main_computer_metadata(archive, sheet_specs, shared_strings, warnings)
                sheets: dict[str, Any] = {}
                active_sheet = ""
                for index, (sheet_name, sheet_path) in enumerate(sheet_specs, start=1):
                    if str(sheet_name or "").strip() == "__main_computer_metadata":
                        continue
                    parsed_name = self._spreadsheet_unique_sheet_name(sheets, sheet_name or f"Sheet{index}")
                    try:
                        sheet = self._spreadsheet_xlsx_sheet(archive, sheet_path, shared_strings, warnings)
                    except KeyError:
                        warnings.append(f"Worksheet was missing from the XLSX archive: {sheet_path}")
                        continue
                    sheets[parsed_name] = sheet
                    if active_sheet_index == index - 1:
                        active_sheet = parsed_name
                    if not active_sheet:
                        active_sheet = parsed_name
                if not sheets:
                    raise ValueError("XLSX workbook did not contain readable worksheets.")
        except zipfile.BadZipFile as exc:
            raise ValueError("Invalid .xlsx file.") from exc

        workbook = {
            "version": 1,
            "active_sheet": active_sheet or next(iter(sheets)),
            "sheets": sheets,
            "metadata": {
                "source": "xlsx-upload",
                "source_filename": Path(str(filename or "workbook.xlsx").replace("\\", "/")).name,
                "imported_at": datetime.now(timezone.utc).isoformat(),
                "warnings": warnings[:20],
            },
        }
        normalized = self._spreadsheet_normalize_workbook(workbook)
        self._spreadsheet_apply_xlsx_main_computer_metadata(normalized, app_metadata, warnings)
        return normalized, warnings[:20]

    def _spreadsheet_xlsx_main_computer_metadata(self, archive: zipfile.ZipFile, sheet_specs: list[tuple[str, str]], shared_strings: list[str], warnings: list[str]) -> dict[str, Any]:
        for sheet_name, sheet_path in sheet_specs:
            if str(sheet_name or "").strip() != "__main_computer_metadata":
                continue
            try:
                sheet = self._spreadsheet_xlsx_sheet(archive, sheet_path, shared_strings, warnings)
            except Exception as exc:
                warnings.append(f"Could not read Main Computer XLSX metadata: {exc}")
                return {}
            cells = sheet.get("cells", {}) if isinstance(sheet, dict) else {}
            if str(cells.get("A1", {}).get("value", "")) != "main-computer-spreadsheet-xlsx-metadata-v1":
                return {}
            chunks: list[str] = []
            for row in range(2, int(sheet.get("rows", 2)) + 1):
                value = str(cells.get(f"A{row}", {}).get("value", ""))
                if value:
                    chunks.append(value)
            if not chunks:
                return {}
            try:
                raw = base64.b64decode("".join(chunks).encode("ascii"), validate=True)
                metadata = json.loads(raw.decode("utf-8"))
            except Exception as exc:
                warnings.append(f"Could not decode Main Computer XLSX metadata: {exc}")
                return {}
            return metadata if isinstance(metadata, dict) else {}
        return {}

    def _spreadsheet_apply_xlsx_main_computer_metadata(self, workbook: dict[str, Any], metadata: dict[str, Any], warnings: list[str]) -> None:
        workbook.setdefault("metadata", {})
        if not isinstance(workbook["metadata"], dict):
            workbook["metadata"] = {}
        if not metadata or metadata.get("schema") != "main-computer-spreadsheet-xlsx-metadata-v1":
            workbook["metadata"]["main_computer_xlsx_metadata"] = {
                "schema": "main-computer-spreadsheet-xlsx-metadata-v1",
                "status": "metadata_missing",
                "restored": 0,
            }
            return

        metadata_status = self._spreadsheet_xlsx_metadata_status(metadata)
        metadata_active_sheet = str(metadata.get("active_sheet") or "")
        if metadata_active_sheet in (workbook.get("sheets") or {}):
            workbook["active_sheet"] = metadata_active_sheet
        if metadata_status == "stale":
            warnings.append("Main Computer XLSX metadata checksum did not match; restored cells are marked stale.")
        restored = 0
        status_counts: dict[str, int] = {}
        for entry in metadata.get("cells", []):
            if not isinstance(entry, dict):
                continue
            sheet_name = str(entry.get("sheet") or workbook.get("active_sheet") or "Sheet1")
            ref = str(entry.get("ref") or "").upper()
            if not re.match(r"^[A-Z]{1,3}[1-9][0-9]*$", ref):
                continue
            sheet = workbook.get("sheets", {}).get(sheet_name)
            if not isinstance(sheet, dict):
                continue
            cells = sheet.setdefault("cells", {})
            visible_cell = cells.get(ref) if isinstance(cells.get(ref), dict) else {}
            metadata_cell = self._spreadsheet_normalize_cell(entry.get("cell") or {})
            entry_status = "clean" if metadata_status in {"clean", "legacy"} else "stale"
            expected_entry_checksum = str(entry.get("checksum") or "").strip().lower()
            if expected_entry_checksum:
                actual_entry_checksum = self._spreadsheet_xlsx_metadata_entry_checksum(sheet_name, ref, metadata_cell)
                if actual_entry_checksum != expected_entry_checksum:
                    entry_status = "stale"
            visible_value = str(visible_cell.get("value", "")) if visible_cell else ""
            metadata_value = str(metadata_cell.get("value", ""))
            visible_changed = bool(visible_cell) and visible_value != "" and visible_value != metadata_value

            if metadata_cell.get("kind") in {"javascript", "python", "basic"}:
                if visible_changed:
                    metadata_cell["value"] = visible_value
                    if entry_status == "clean":
                        entry_status = "dirty"
                metadata_cell["status"] = entry_status
                metadata_cell.setdefault("metadata", {})
                metadata_cell["metadata"] = metadata_cell["metadata"] if isinstance(metadata_cell["metadata"], dict) else {}
                metadata_cell["metadata"]["xlsx_round_trip"] = {
                    "restored_at": datetime.now(timezone.utc).isoformat(),
                    "source": "main-computer-hidden-metadata-sheet",
                    "status": entry_status,
                    "visible_value_changed": visible_changed,
                    "metadata_checksum": metadata_status,
                }
                cells[ref] = metadata_cell
                restored += 1
                status_counts[entry_status] = status_counts.get(entry_status, 0) + 1
            elif metadata_cell.get("kind") == "formula" and visible_cell:
                visible_cell.setdefault("metadata", {})
                if isinstance(visible_cell["metadata"], dict):
                    visible_cell["metadata"].update(metadata_cell.get("metadata") if isinstance(metadata_cell.get("metadata"), dict) else {})
                    visible_cell["metadata"]["xlsx_round_trip"] = {
                        "restored_at": datetime.now(timezone.utc).isoformat(),
                        "source": "main-computer-hidden-metadata-sheet",
                        "status": entry_status,
                        "metadata_checksum": metadata_status,
                    }
                cells[ref] = self._spreadsheet_normalize_cell(visible_cell)
                restored += 1
                status_counts[entry_status] = status_counts.get(entry_status, 0) + 1
        workbook["metadata"]["main_computer_xlsx_metadata"] = {
            "schema": "main-computer-spreadsheet-xlsx-metadata-v1",
            "version": int(metadata.get("version") or 1),
            "status": metadata_status,
            "restored": restored,
            "status_counts": status_counts,
        }
        if restored:
            workbook["metadata"]["main_computer_xlsx_metadata_restored"] = restored
            warnings.append(f"Restored {restored} Main Computer spreadsheet metadata cells from the XLSX package.")

    def _spreadsheet_xlsx_xml_bytes(self, root: ET.Element) -> bytes:
        ET.register_namespace("", "http://schemas.openxmlformats.org/spreadsheetml/2006/main")
        ET.register_namespace("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships")
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)

    def _spreadsheet_xlsx_safe_sheet_title(self, raw_name: str, used: set[str]) -> str:
        name = re.sub(r"[\[\]:*?/\\]", "-", str(raw_name or "Sheet").strip())[:31].strip("'") or "Sheet"
        if name == "__main_computer_metadata":
            name = "Sheet"
        candidate = name
        index = 2
        while candidate in used or candidate == "__main_computer_metadata":
            suffix = f"-{index}"
            candidate = f"{name[:31 - len(suffix)]}{suffix}"
            index += 1
        used.add(candidate)
        return candidate

    def _spreadsheet_xlsx_export_sheet_pairs(self, workbook: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
        sheets = workbook.get("sheets") if isinstance(workbook, dict) else {}
        if not isinstance(sheets, dict) or not sheets:
            workbook = self._spreadsheet_default_workbook()
            sheets = workbook["sheets"]
        used: set[str] = set()
        pairs: list[tuple[str, str, dict[str, Any]]] = []
        for raw_name, raw_sheet in sheets.items():
            sheet = raw_sheet if isinstance(raw_sheet, dict) else {}
            pairs.append((str(raw_name or "Sheet1"), self._spreadsheet_xlsx_safe_sheet_title(str(raw_name or "Sheet1"), used), sheet))
        return pairs

    def _spreadsheet_xlsx_cell_sort_key(self, ref: str) -> tuple[int, int]:
        parts = re.match(r"^([A-Z]+)([1-9][0-9]*)$", str(ref or "").upper())
        if not parts:
            return (10**9, 10**9)
        return (int(parts.group(2)), self._spreadsheet_col_index(parts.group(1)))

    def _spreadsheet_xlsx_numeric_value(self, value: Any) -> str | None:
        text = str(value if value is not None else "").strip()
        if not text or not re.match(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$", text):
            return None
        try:
            number = float(text)
        except ValueError:
            return None
        if not (number == number and abs(number) != float("inf")):
            return None
        return text

    def _spreadsheet_xlsx_export_cell_xml(self, ref: str, raw_cell: Any) -> ET.Element | None:
        namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        cell = self._spreadsheet_normalize_cell(raw_cell)
        element = ET.Element(f"{{{namespace}}}c", {"r": ref})
        kind = str(cell.get("kind") or "value").lower()
        formula_source = ""
        if kind == "formula":
            formula_metadata = (cell.get("metadata") or {}).get("formula") if isinstance(cell.get("metadata"), dict) else None
            if isinstance(formula_metadata, dict):
                formula_source = str(cell.get("source") or formula_metadata.get("source") or cell.get("value") or "").strip()
            else:
                formula_source = str(cell.get("source") or formula_metadata or cell.get("value") or "").strip()
            if formula_source.startswith("="):
                formula_source = formula_source[1:]
        if formula_source:
            formula = ET.SubElement(element, f"{{{namespace}}}f")
            formula.text = formula_source
            cached = str(cell.get("value") or "")
            if cached and not cached.startswith("="):
                numeric = self._spreadsheet_xlsx_numeric_value(cached)
                if numeric is None:
                    element.set("t", "str")
                    value = ET.SubElement(element, f"{{{namespace}}}v")
                    value.text = cached
                else:
                    value = ET.SubElement(element, f"{{{namespace}}}v")
                    value.text = numeric
            return element

        value_text = str(cell.get("value") if cell.get("value") is not None else "")
        if value_text == "":
            return None
        numeric = self._spreadsheet_xlsx_numeric_value(value_text)
        if numeric is not None:
            value = ET.SubElement(element, f"{{{namespace}}}v")
            value.text = numeric
            return element
        if value_text.upper() in {"TRUE", "FALSE"}:
            element.set("t", "b")
            value = ET.SubElement(element, f"{{{namespace}}}v")
            value.text = "1" if value_text.upper() == "TRUE" else "0"
            return element
        element.set("t", "inlineStr")
        inline = ET.SubElement(element, f"{{{namespace}}}is")
        text = ET.SubElement(inline, f"{{{namespace}}}t")
        if value_text.strip() != value_text:
            text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        text.text = value_text
        return element

    def _spreadsheet_xlsx_export_worksheet_xml(self, sheet: dict[str, Any]) -> bytes:
        namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        worksheet = ET.Element(f"{{{namespace}}}worksheet")
        sheet_data = ET.SubElement(worksheet, f"{{{namespace}}}sheetData")
        cells = sheet.get("cells", {}) if isinstance(sheet.get("cells"), dict) else {}
        rows: dict[int, list[tuple[str, Any]]] = {}
        for ref, cell in cells.items():
            ref_text = str(ref or "").upper()
            match = re.match(r"^[A-Z]{1,3}([1-9][0-9]*)$", ref_text)
            if not match:
                continue
            rows.setdefault(int(match.group(1)), []).append((ref_text, cell))
        for row_index in sorted(rows):
            row = ET.SubElement(sheet_data, f"{{{namespace}}}row", {"r": str(row_index)})
            for ref, cell in sorted(rows[row_index], key=lambda item: self._spreadsheet_xlsx_cell_sort_key(item[0])):
                cell_xml = self._spreadsheet_xlsx_export_cell_xml(ref, cell)
                if cell_xml is not None:
                    row.append(cell_xml)
        return self._spreadsheet_xlsx_xml_bytes(worksheet)

    def _spreadsheet_xlsx_metadata_checksum(self, value: Any) -> str:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _spreadsheet_xlsx_metadata_entry_checksum(self, sheet_name: str, ref: str, cell: dict[str, Any]) -> str:
        return self._spreadsheet_xlsx_metadata_checksum({
            "sheet": str(sheet_name),
            "ref": str(ref).upper(),
            "cell": cell,
        })

    def _spreadsheet_xlsx_metadata_status(self, metadata: dict[str, Any]) -> str:
        expected = str(metadata.get("checksum") or "").strip().lower()
        if not expected:
            return "legacy"
        comparable = dict(metadata)
        comparable.pop("checksum", None)
        actual = self._spreadsheet_xlsx_metadata_checksum(comparable)
        return "clean" if actual == expected else "stale"

    def _spreadsheet_xlsx_export_metadata(self, workbook: dict[str, Any]) -> dict[str, Any]:
        cells: list[dict[str, Any]] = []
        for sheet_name, sheet in (workbook.get("sheets") or {}).items():
            if not isinstance(sheet, dict) or not isinstance(sheet.get("cells"), dict):
                continue
            for ref, raw_cell in sheet["cells"].items():
                cell = self._spreadsheet_normalize_cell(raw_cell)
                kind = str(cell.get("kind") or "value").lower()
                metadata = cell.get("metadata") if isinstance(cell.get("metadata"), dict) else {}
                has_output = bool((cell.get("output") or {}).get("parts") if isinstance(cell.get("output"), dict) else False)
                if kind in {"formula", "javascript", "python", "basic"} or metadata or cell.get("dependencies") or cell.get("writes") or has_output:
                    ref_text = str(ref).upper()
                    cells.append({
                        "sheet": str(sheet_name),
                        "ref": ref_text,
                        "cell": cell,
                        "checksum": self._spreadsheet_xlsx_metadata_entry_checksum(str(sheet_name), ref_text, cell),
                    })
        metadata = {
            "schema": "main-computer-spreadsheet-xlsx-metadata-v1",
            "version": 2,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "active_sheet": str(workbook.get("active_sheet") or "Sheet1"),
            "cells": cells,
        }
        metadata["checksum"] = self._spreadsheet_xlsx_metadata_checksum(metadata)
        return metadata

    def _spreadsheet_xlsx_metadata_sheet(self, metadata: dict[str, Any]) -> dict[str, Any]:
        encoded = base64.b64encode(json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")).decode("ascii")
        chunks = [encoded[index:index + 3000] for index in range(0, len(encoded), 3000)] or [""]
        cells = {"A1": {"value": "main-computer-spreadsheet-xlsx-metadata-v1"}}
        for index, chunk in enumerate(chunks, start=2):
            cells[f"A{index}"] = {"value": chunk}
        return {"rows": max(2, len(chunks) + 1), "cols": 1, "cells": cells}

    def _spreadsheet_xlsx_export_workbook_xml(self, sheet_titles: list[str], metadata_sheet_index: int | None = None, active_sheet_index: int = 0) -> bytes:
        namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        rel_namespace = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        workbook = ET.Element(f"{{{namespace}}}workbook")
        visible_count = max(1, len(sheet_titles) - (1 if metadata_sheet_index else 0))
        active_tab = max(0, min(int(active_sheet_index or 0), visible_count - 1))
        book_views = ET.SubElement(workbook, f"{{{namespace}}}bookViews")
        ET.SubElement(book_views, f"{{{namespace}}}workbookView", {"activeTab": str(active_tab), "firstSheet": str(active_tab)})
        sheets = ET.SubElement(workbook, f"{{{namespace}}}sheets")
        for index, title in enumerate(sheet_titles, start=1):
            attrs = {"name": title, "sheetId": str(index), f"{{{rel_namespace}}}id": f"rId{index}"}
            if metadata_sheet_index == index:
                attrs["state"] = "veryHidden"
            ET.SubElement(sheets, f"{{{namespace}}}sheet", attrs)
        ET.SubElement(workbook, f"{{{namespace}}}calcPr", {"calcMode": "auto", "fullCalcOnLoad": "1", "forceFullCalc": "1"})
        return self._spreadsheet_xlsx_xml_bytes(workbook)

    def _spreadsheet_xlsx_export_workbook_rels_xml(self, sheet_count: int) -> bytes:
        namespace = "http://schemas.openxmlformats.org/package/2006/relationships"
        relationships = ET.Element(f"{{{namespace}}}Relationships")
        for index in range(1, sheet_count + 1):
            ET.SubElement(relationships, f"{{{namespace}}}Relationship", {
                "Id": f"rId{index}",
                "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet",
                "Target": f"worksheets/sheet{index}.xml",
            })
        return ET.tostring(relationships, encoding="utf-8", xml_declaration=True)

    def _spreadsheet_xlsx_export_content_types_xml(self, sheet_count: int) -> bytes:
        namespace = "http://schemas.openxmlformats.org/package/2006/content-types"
        types = ET.Element(f"{{{namespace}}}Types")
        ET.SubElement(types, f"{{{namespace}}}Default", {"Extension": "rels", "ContentType": "application/vnd.openxmlformats-package.relationships+xml"})
        ET.SubElement(types, f"{{{namespace}}}Default", {"Extension": "xml", "ContentType": "application/xml"})
        ET.SubElement(types, f"{{{namespace}}}Override", {"PartName": "/xl/workbook.xml", "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"})
        for index in range(1, sheet_count + 1):
            ET.SubElement(types, f"{{{namespace}}}Override", {"PartName": f"/xl/worksheets/sheet{index}.xml", "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"})
        return ET.tostring(types, encoding="utf-8", xml_declaration=True)

    def _spreadsheet_xlsx_export_package_rels_xml(self) -> bytes:
        namespace = "http://schemas.openxmlformats.org/package/2006/relationships"
        relationships = ET.Element(f"{{{namespace}}}Relationships")
        ET.SubElement(relationships, f"{{{namespace}}}Relationship", {
            "Id": "rIdWorkbook",
            "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
            "Target": "xl/workbook.xml",
        })
        return ET.tostring(relationships, encoding="utf-8", xml_declaration=True)

    def _spreadsheet_xlsx_export_workbook(self, workbook: dict[str, Any]) -> bytes:
        normalized = self._spreadsheet_normalize_workbook(workbook)
        sheet_pairs = self._spreadsheet_xlsx_export_sheet_pairs(normalized)
        metadata = self._spreadsheet_xlsx_export_metadata(normalized)
        include_metadata = bool(metadata.get("cells"))
        sheet_titles = [title for _, title, _ in sheet_pairs]
        active_sheet_name = str(normalized.get("active_sheet") or "")
        active_sheet_index = 0
        for index, (raw_name, _, _) in enumerate(sheet_pairs):
            if str(raw_name) == active_sheet_name:
                active_sheet_index = index
                break
        metadata_sheet_index = None
        if include_metadata:
            sheet_titles.append("__main_computer_metadata")
            metadata_sheet_index = len(sheet_titles)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", self._spreadsheet_xlsx_export_content_types_xml(len(sheet_titles)))
            archive.writestr("_rels/.rels", self._spreadsheet_xlsx_export_package_rels_xml())
            archive.writestr("xl/workbook.xml", self._spreadsheet_xlsx_export_workbook_xml(sheet_titles, metadata_sheet_index, active_sheet_index))
            archive.writestr("xl/_rels/workbook.xml.rels", self._spreadsheet_xlsx_export_workbook_rels_xml(len(sheet_titles)))
            for index, (_, _, sheet) in enumerate(sheet_pairs, start=1):
                archive.writestr(f"xl/worksheets/sheet{index}.xml", self._spreadsheet_xlsx_export_worksheet_xml(sheet))
            if include_metadata:
                archive.writestr(f"xl/worksheets/sheet{metadata_sheet_index}.xml", self._spreadsheet_xlsx_export_worksheet_xml(self._spreadsheet_xlsx_metadata_sheet(metadata)))
        return buffer.getvalue()

    def _spreadsheet_xlsx_read_xml(self, archive: zipfile.ZipFile, name: str, warnings: list[str], max_bytes: int = 20 * 1024 * 1024) -> ET.Element | None:
        safe_name = self._spreadsheet_xlsx_safe_member(name)
        try:
            info = archive.getinfo(safe_name)
        except KeyError:
            return None
        if info.file_size > max_bytes:
            warnings.append(f"Skipped oversized XLSX XML part: {safe_name}")
            return None
        data = archive.read(info)
        try:
            return ET.fromstring(data)
        except ET.ParseError as exc:
            raise ValueError(f"Invalid XLSX XML part: {safe_name}") from exc

    def _spreadsheet_xlsx_safe_member(self, name: str) -> str:
        safe_name = str(name or "").replace("\\", "/").lstrip("/")
        normalized = posixpath.normpath(safe_name)
        if normalized in {"", "."} or normalized.startswith("../") or "/../" in normalized:
            raise ValueError("Unsafe XLSX internal path.")
        return normalized

    def _spreadsheet_xlsx_local_name(self, tag: str) -> str:
        return str(tag or "").rsplit("}", 1)[-1]

    def _spreadsheet_xlsx_attr(self, element: ET.Element, name: str) -> str:
        for key, value in element.attrib.items():
            if key == name or key.endswith(f"}}{name}") or key.endswith(f":{name}"):
                return str(value or "")
        return ""

    def _spreadsheet_xlsx_text(self, element: ET.Element | None, tag_name: str = "t") -> str:
        if element is None:
            return ""
        parts: list[str] = []
        for node in element.iter():
            if self._spreadsheet_xlsx_local_name(node.tag) == tag_name and node.text is not None:
                parts.append(node.text)
        return "".join(parts)

    def _spreadsheet_xlsx_child_text(self, element: ET.Element, tag_name: str) -> str:
        for child in element:
            if self._spreadsheet_xlsx_local_name(child.tag) == tag_name:
                return str(child.text or "")
        return ""

    def _spreadsheet_xlsx_shared_strings(self, archive: zipfile.ZipFile, warnings: list[str]) -> list[str]:
        root = self._spreadsheet_xlsx_read_xml(archive, "xl/sharedStrings.xml", warnings)
        if root is None:
            return []
        strings: list[str] = []
        for node in root:
            if self._spreadsheet_xlsx_local_name(node.tag) != "si":
                continue
            strings.append(self._spreadsheet_xlsx_text(node))
            if len(strings) >= 200000:
                warnings.append("Shared strings were truncated after 200000 entries.")
                break
        return strings

    def _spreadsheet_xlsx_workbook_relationships(self, archive: zipfile.ZipFile, warnings: list[str]) -> dict[str, str]:
        root = self._spreadsheet_xlsx_read_xml(archive, "xl/_rels/workbook.xml.rels", warnings)
        if root is None:
            return {}
        relationships: dict[str, str] = {}
        for node in root.iter():
            if self._spreadsheet_xlsx_local_name(node.tag) != "Relationship":
                continue
            rel_id = str(node.attrib.get("Id") or "")
            target = str(node.attrib.get("Target") or "")
            if not rel_id or not target:
                continue
            relationships[rel_id] = self._spreadsheet_xlsx_resolve_target("xl/workbook.xml", target)
        return relationships

    def _spreadsheet_xlsx_resolve_target(self, source: str, target: str) -> str:
        normalized_target = str(target or "").replace("\\", "/")
        if normalized_target.startswith("/"):
            return self._spreadsheet_xlsx_safe_member(normalized_target)
        source_dir = posixpath.dirname(self._spreadsheet_xlsx_safe_member(source))
        return self._spreadsheet_xlsx_safe_member(posixpath.join(source_dir, normalized_target))

    def _spreadsheet_xlsx_active_sheet_index(self, archive: zipfile.ZipFile, warnings: list[str]) -> int:
        root = self._spreadsheet_xlsx_read_xml(archive, "xl/workbook.xml", warnings)
        if root is None:
            return 0
        for node in root.iter():
            if self._spreadsheet_xlsx_local_name(node.tag) != "workbookView":
                continue
            active_tab = str(node.attrib.get("activeTab") or "").strip()
            if not active_tab:
                return 0
            try:
                return max(0, int(active_tab))
            except ValueError:
                return 0
        return 0

    def _spreadsheet_xlsx_sheet_specs(self, archive: zipfile.ZipFile, relationships: dict[str, str], warnings: list[str]) -> list[tuple[str, str]]:
        root = self._spreadsheet_xlsx_read_xml(archive, "xl/workbook.xml", warnings)
        if root is None:
            return []
        sheets: list[tuple[str, str]] = []
        for node in root.iter():
            if self._spreadsheet_xlsx_local_name(node.tag) != "sheet":
                continue
            name = str(node.attrib.get("name") or f"Sheet{len(sheets) + 1}")[:80]
            rel_id = self._spreadsheet_xlsx_attr(node, "id")
            target = relationships.get(rel_id)
            if not target:
                warnings.append(f"Skipped worksheet without a readable relationship: {name}")
                continue
            sheets.append((name, target))
        return sheets

    def _spreadsheet_xlsx_fallback_sheet_specs(self, archive: zipfile.ZipFile) -> list[tuple[str, str]]:
        worksheet_names = sorted(
            name for name in archive.namelist()
            if re.match(r"^xl/worksheets/sheet[0-9]+\.xml$", name.replace("\\", "/"), re.IGNORECASE)
        )
        return [(f"Sheet{index}", name.replace("\\", "/")) for index, name in enumerate(worksheet_names, start=1)]

    def _spreadsheet_unique_sheet_name(self, sheets: dict[str, Any], raw_name: str) -> str:
        base = self._spreadsheet_clean_text(raw_name, 80).strip() or "Sheet"
        if base not in sheets:
            return base
        for index in range(2, 1000):
            candidate = f"{base}-{index}"
            if candidate not in sheets:
                return candidate
        return f"{base}-{len(sheets) + 1}"

    def _spreadsheet_xlsx_sheet(self, archive: zipfile.ZipFile, sheet_path: str, shared_strings: list[str], warnings: list[str]) -> dict[str, Any]:
        root = self._spreadsheet_xlsx_read_xml(archive, sheet_path, warnings)
        if root is None:
            raise KeyError(sheet_path)
        cells: dict[str, dict[str, Any]] = {}
        max_row = 0
        max_col = 0
        truncated_rows = False
        truncated_cols = False

        for node in root.iter():
            if self._spreadsheet_xlsx_local_name(node.tag) != "c":
                continue
            ref = str(node.attrib.get("r") or "").upper()
            if not re.match(r"^[A-Z]{1,3}[1-9][0-9]*$", ref):
                continue
            parts = re.match(r"^([A-Z]+)([1-9][0-9]*)$", ref)
            if not parts:
                continue
            col = self._spreadsheet_col_index(parts.group(1))
            row = int(parts.group(2))
            max_row = max(max_row, min(row, 500))
            max_col = max(max_col, min(col, 100))
            if row > 500:
                truncated_rows = True
                continue
            if col > 100:
                truncated_cols = True
                continue
            cell = self._spreadsheet_xlsx_cell(node, shared_strings)
            if cell.get("value") or cell.get("source"):
                cells[ref] = cell

        if truncated_rows:
            warnings.append("Rows beyond row 500 were skipped during XLSX import.")
        if truncated_cols:
            warnings.append("Columns beyond column CV were skipped during XLSX import.")
        return {"rows": max(50, max_row or 1), "cols": max(26, max_col or 1), "cells": cells}

    def _spreadsheet_xlsx_cell(self, cell: ET.Element, shared_strings: list[str]) -> dict[str, Any]:
        cell_type = str(cell.attrib.get("t") or "").strip()
        raw_value = self._spreadsheet_xlsx_child_text(cell, "v")
        formula = self._spreadsheet_xlsx_child_text(cell, "f").strip()
        value = ""

        if cell_type == "s":
            try:
                value = shared_strings[int(raw_value)]
            except (ValueError, IndexError):
                value = ""
        elif cell_type == "inlineStr":
            for child in cell:
                if self._spreadsheet_xlsx_local_name(child.tag) == "is":
                    value = self._spreadsheet_xlsx_text(child)
                    break
        elif cell_type == "b":
            value = "TRUE" if raw_value.strip() in {"1", "true", "TRUE"} else "FALSE"
        elif raw_value != "":
            value = raw_value
        elif formula:
            value = f"={formula}"

        result: dict[str, Any] = {"value": self._spreadsheet_clean_text(value)}
        if formula:
            formula_source = f"={formula}" if not formula.startswith("=") else formula
            formula_source = formula_source[:20000]
            result.update({
                "kind": "formula",
                "language": "none",
                "source": formula_source,
                "metadata": {
                    "formula": {
                        "engine": "xlsx",
                        "source": formula_source,
                        "cached": raw_value != "",
                    }
                },
            })
        return result

    def _spreadsheet_col_index(self, name: str) -> int:
        index = 0
        for char in str(name or "").upper():
            if "A" <= char <= "Z":
                index = index * 26 + (ord(char) - 64)
        return index


    def _spreadsheet_root(self) -> Path:
        root = self.server.debug_root / "spreadsheets"
        root.mkdir(parents=True, exist_ok=True)
        return root.resolve()

    def _spreadsheet_safe_path(self, raw_path: str, must_exist: bool = True) -> Path:
        raw_path = str(raw_path or "").replace("\\", "/").strip()
        if not raw_path:
            raise ValueError("Spreadsheet path is required.")
        if raw_path.startswith("/") or re.match(r"^[A-Za-z]:", raw_path):
            raise ValueError("Absolute spreadsheet paths are not allowed.")
        parts = [part for part in raw_path.split("/") if part and part != "."]
        if not parts or any(part == ".." or part.startswith(".") for part in parts):
            raise ValueError("Unsafe spreadsheet path.")
        root = self._spreadsheet_root()
        candidate = (root / Path(*parts)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("Spreadsheet path escapes spreadsheets/.") from exc
        if candidate.suffix.lower() not in {".json", ".csv"}:
            raise ValueError("Spreadsheet files must be .json or .csv.")
        if must_exist and not candidate.is_file():
            raise ValueError("Spreadsheet file not found.")
        return candidate

    def _spreadsheet_content_hash(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _spreadsheet_file_payload(self, path: Path) -> dict[str, Any]:
        root = self._spreadsheet_root()
        relative_path = path.resolve().relative_to(root).as_posix()
        stat = path.stat()
        return {
            "path": relative_path,
            "display_path": f"spreadsheets/{relative_path}",
            "kind": "csv" if path.suffix.lower() == ".csv" else "workbook",
            "bytes": stat.st_size,
            "mtime": stat.st_mtime,
            "content_hash": self._spreadsheet_content_hash(path),
        }

    def _spreadsheet_default_workbook(self, rows: int = 50, cols: int = 26) -> dict[str, Any]:
        rows = max(1, min(int(rows), 500))
        cols = max(1, min(int(cols), 100))
        return {
            "version": 1,
            "active_sheet": "Sheet1",
            "sheets": {
                "Sheet1": {
                    "rows": rows,
                    "cols": cols,
                    "cells": {
                        "A1": {"value": "Example"},
                        "B1": {"value": "42"},
                    },
                }
            },
            "metadata": {},
        }

    def _spreadsheet_normalize_workbook(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raw = {}
        workbook = dict(raw)
        sheets = workbook.get("sheets")
        if not isinstance(sheets, dict) or not sheets:
            return self._spreadsheet_default_workbook()
        normalized_sheets: dict[str, Any] = {}
        for name, sheet in sheets.items():
            sheet_name = str(name or "Sheet1")
            if not isinstance(sheet, dict):
                sheet = {}
            rows = max(1, min(int(sheet.get("rows", 50) or 50), 500))
            cols = max(1, min(int(sheet.get("cols", 26) or 26), 100))
            raw_cells = sheet.get("cells", {})
            cells: dict[str, dict[str, Any]] = {}
            if isinstance(raw_cells, dict):
                for ref, cell in raw_cells.items():
                    ref_text = str(ref or "").upper()
                    if not re.match(r"^[A-Z]{1,3}[1-9][0-9]*$", ref_text):
                        continue
                    cells[ref_text] = self._spreadsheet_normalize_cell(cell)
            normalized_sheets[sheet_name] = {"rows": rows, "cols": cols, "cells": cells}
        active_sheet = str(workbook.get("active_sheet") or next(iter(normalized_sheets)))
        if active_sheet not in normalized_sheets:
            active_sheet = next(iter(normalized_sheets))
        metadata = self._spreadsheet_clean_json_value(workbook.get("metadata") or {}, limit=24000)
        if not isinstance(metadata, dict):
            metadata = {}
        return {"version": 1, "active_sheet": active_sheet, "sheets": normalized_sheets, "metadata": metadata}

    def _spreadsheet_clean_text(self, value: Any, limit: int = 4000) -> str:
        text = str(value or "")
        return text[:limit]

    def _spreadsheet_clean_json_value(self, value: Any, limit: int = 12000) -> Any:
        try:
            encoded = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return {}
        if len(encoded) > limit:
            return {}
        return json.loads(encoded)

    def _spreadsheet_clean_string_list(self, value: Any, limit: int = 200) -> list[str]:
        if not isinstance(value, list):
            return []
        cleaned: list[str] = []
        for item in value[:limit]:
            text = str(item or "").strip().upper()
            if re.match(r"^[A-Z]{1,3}[1-9][0-9]*(:[A-Z]{1,3}[1-9][0-9]*)?$", text):
                cleaned.append(text)
        return cleaned

    def _spreadsheet_clean_writes(self, value: Any, limit: int = 200) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        cleaned: list[dict[str, Any]] = []
        for item in value[:limit]:
            if not isinstance(item, dict):
                continue
            target = str(item.get("target") or item.get("ref") or item.get("range") or "").strip().upper()
            if not re.match(r"^[A-Z]{1,3}[1-9][0-9]*(:[A-Z]{1,3}[1-9][0-9]*)?$", target):
                continue
            cleaned.append({
                "target": target,
                "value": self._spreadsheet_clean_json_value(item.get("value"), limit=4000),
                "kind": self._spreadsheet_clean_text(item.get("kind") or "write", 40),
            })
        return cleaned

    def _spreadsheet_clean_output(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {"parts": []}
        raw_parts = value.get("parts", [])
        parts: list[dict[str, Any]] = []
        if isinstance(raw_parts, list):
            for part in raw_parts[:100]:
                if not isinstance(part, dict):
                    continue
                kind = self._spreadsheet_clean_text(part.get("kind") or "text", 40)
                if kind not in {"text", "stdout", "stderr", "json", "table", "warning", "error", "write_preview"}:
                    kind = "text"
                parts.append({
                    "kind": kind,
                    "title": self._spreadsheet_clean_text(part.get("title"), 120),
                    "content": self._spreadsheet_clean_json_value(part.get("content"), limit=8000),
                    "metadata": self._spreadsheet_clean_json_value(part.get("metadata") or {}, limit=4000),
                })
        return {
            "parts": parts,
            "text": self._spreadsheet_clean_text(value.get("text"), 4000),
            "error": self._spreadsheet_clean_text(value.get("error"), 4000),
            "updated_at": self._spreadsheet_clean_text(value.get("updated_at"), 80),
        }

    def _spreadsheet_normalize_cell(self, raw_cell: Any) -> dict[str, Any]:
        source = raw_cell if isinstance(raw_cell, dict) else {"value": raw_cell}
        kind = str(source.get("kind") or "value").strip().lower()
        if kind not in {"value", "formula", "python", "javascript", "basic"}:
            kind = "value"
        language = str(source.get("language") or ("none" if kind in {"value", "formula"} else kind)).strip().lower()
        if language not in {"none", "python", "javascript", "basic"}:
            language = "none"
        status = str(source.get("status") or "clean").strip().lower()
        if status not in {"clean", "dirty", "evaluating", "error", "stale", "moved", "metadata_missing", "orphaned"}:
            status = "clean"
        output = self._spreadsheet_clean_output(source.get("output"))
        if status == "evaluating":
            status = "dirty"
            output["parts"] = [
                {
                    "kind": "warning",
                    "title": "Interrupted run",
                    "content": "Previous code-cell run was interrupted before completion.",
                    "metadata": {},
                },
                *[part for part in output.get("parts", []) if part.get("title") != "Running"],
            ]
        return {
            "value": self._spreadsheet_clean_text(source.get("value"), 4000),
            "kind": kind,
            "language": language,
            "source": self._spreadsheet_clean_text(source.get("source"), 20000),
            "output": output,
            "status": status,
            "dependencies": self._spreadsheet_clean_string_list(source.get("dependencies")),
            "writes": self._spreadsheet_clean_writes(source.get("writes")),
            "metadata": self._spreadsheet_clean_json_value(source.get("metadata") or {}, limit=24000),
        }

    def _spreadsheet_atomic_write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=path.parent, prefix=f".{path.name}.", suffix=".tmp") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            temp_name = handle.name
        os.replace(temp_name, path)

    def _spreadsheet_col_name(self, index: int) -> str:
        name = ""
        value = int(index)
        while value > 0:
            value, remainder = divmod(value - 1, 26)
            name = chr(65 + remainder) + name
        return name or "A"

    def _spreadsheet_csv_workbook(self, path: Path) -> dict[str, Any]:
        rows: list[list[str]] = []
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        row_count = max(50, len(rows))
        col_count = max(26, max((len(row) for row in rows), default=0))
        workbook = {"version": 1, "active_sheet": "Sheet1", "sheets": {"Sheet1": {"rows": row_count, "cols": col_count, "cells": {}}}}
        cells = workbook["sheets"]["Sheet1"]["cells"]
        for row_index, row in enumerate(rows, start=1):
            for col_index, value in enumerate(row, start=1):
                if value:
                    cells[f"{self._spreadsheet_col_name(col_index)}{row_index}"] = {"value": value}
        return workbook
