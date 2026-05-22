#!/usr/bin/env python
from __future__ import annotations

import argparse
import base64
import io
import json
import re
import sys
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from main_computer.config import MainComputerConfig  # noqa: E402
from main_computer.viewport import ViewportServer  # noqa: E402


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

SCRIPT_CELLS = {
    "B2": {
        "kind": "javascript",
        "language": "javascript",
        "value": "20",
        "dirty_value": "21",
        "source": 'const base = sheet.getNumber("C1");\nreturn base * 4;',
        "dependency": "C1",
        "write": {"target": "D2", "value": {"language": "javascript", "result": 20}, "kind": "write"},
        "output_kind": "stdout",
    },
    "B3": {
        "kind": "python",
        "language": "python",
        "value": "30",
        "dirty_value": "31",
        "source": 'base = sheet.get_number("C1")\nresult = base * 6',
        "dependency": "C1",
        "write": {"target": "D3", "value": {"language": "python", "result": 30}, "kind": "write"},
        "output_kind": "text",
    },
    "B4": {
        "kind": "basic",
        "language": "basic",
        "value": "40",
        "dirty_value": "41",
        "source": 'LET BASE = GETNUMBER("C1")\nPRINT BASE * 8',
        "dependency": "C1",
        "write": {"target": "D4", "value": {"language": "basic", "result": 40}, "kind": "write"},
        "output_kind": "stdout",
    },
}


class SmokeFailure(AssertionError):
    pass


class SpreadsheetXlsxFunctionalSmoke:
    def __init__(self, keep_xlsx: Path | None = None) -> None:
        self.keep_xlsx = keep_xlsx
        self.tempdir: tempfile.TemporaryDirectory[str] | None = None
        self.repo: Path | None = None
        self.server: ViewportServer | None = None
        self.thread: threading.Thread | None = None
        self.base = ""
        self.pass_count = 0

    def pass_(self, message: str) -> None:
        self.pass_count += 1
        print(f"PASS {self.pass_count:02d}: {message}")

    def check(self, condition: Any, message: str) -> None:
        if not condition:
            raise SmokeFailure(message)
        self.pass_(message)

    def check_equal(self, actual: Any, expected: Any, message: str) -> None:
        if actual != expected:
            raise SmokeFailure(f"{message}: expected {expected!r}, got {actual!r}")
        self.pass_(message)

    def start_server(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name)
        self.server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=self.repo), verbose=False)
        self.server.debug_root = self.repo.resolve()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.pass_(f"temporary viewport server started at {self.base}")

    def stop_server(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=5)
        if self.tempdir is not None:
            self.tempdir.cleanup()

    def post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        request = Request(
            f"{self.base}{path}",
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def workbook_fixture(self) -> dict[str, Any]:
        script_cells: dict[str, dict[str, Any]] = {
            "A1": {"value": "Language"},
            "B1": {"value": "Executable code cell"},
            "C1": {"value": "5"},
        }
        for ref, spec in SCRIPT_CELLS.items():
            language = spec["language"]
            script_cells[ref] = {
                "value": spec["value"],
                "kind": spec["kind"],
                "language": language,
                "source": spec["source"],
                "status": "clean",
                "dependencies": [spec["dependency"]],
                "writes": [spec["write"]],
                "output": {
                    "parts": [
                        {
                            "kind": spec["output_kind"],
                            "title": f"{language} output",
                            "content": f"{language} preserved output",
                            "metadata": {"language": language, "smoke": True},
                        }
                    ],
                    "text": f"{language} text output",
                    "updated_at": "2026-05-11T00:00:00Z",
                },
                "metadata": {
                    "runtime": f"{language}-worker",
                    "script_language": language,
                    "smoke_id": f"{language}-code-cell",
                    "expected_visible_value": spec["value"],
                },
            }

        return {
            "version": 1,
            "active_sheet": "Script Cells",
            "sheets": {
                "Inputs": {
                    "rows": 50,
                    "cols": 26,
                    "cells": {
                        "A1": {"value": "2"},
                        "B1": {"value": "3"},
                        "C1": {
                            "value": "5",
                            "kind": "formula",
                            "language": "none",
                            "source": "=A1+B1",
                            "metadata": {"formula": {"engine": "hyperformula", "source": "=A1+B1"}},
                        },
                    },
                },
                "Script Cells": {
                    "rows": 50,
                    "cols": 26,
                    "cells": script_cells,
                },
            },
            "metadata": {"purpose": "spreadsheet XLSX functional smoke"},
        }

    @staticmethod
    def parse_xml(data: bytes | str) -> ET.Element:
        if isinstance(data, str):
            data = data.encode("utf-8")
        return ET.fromstring(data)

    @staticmethod
    def local_name(tag: str) -> str:
        return str(tag or "").rsplit("}", 1)[-1]

    @staticmethod
    def attr_by_local_name(element: ET.Element, local_name: str) -> str:
        for key, value in element.attrib.items():
            if key.rsplit("}", 1)[-1] == local_name:
                return value
        return ""

    def workbook_sheet_info(self, workbook_xml: str) -> list[dict[str, str]]:
        root = self.parse_xml(workbook_xml)
        sheets: list[dict[str, str]] = []
        for node in root.iter():
            if self.local_name(node.tag) == "sheet":
                sheets.append({
                    "name": node.attrib.get("name", ""),
                    "state": node.attrib.get("state", "visible"),
                    "rid": self.attr_by_local_name(node, "id"),
                })
        return sheets

    def workbook_active_tab(self, workbook_xml: str) -> int:
        root = self.parse_xml(workbook_xml)
        for node in root.iter():
            if self.local_name(node.tag) == "workbookView":
                return int(node.attrib.get("activeTab", "0") or 0)
        return 0

    def workbook_relationships(self, rels_xml: str) -> dict[str, str]:
        root = self.parse_xml(rels_xml)
        rels: dict[str, str] = {}
        for node in root.iter():
            if self.local_name(node.tag) == "Relationship":
                rels[node.attrib.get("Id", "")] = node.attrib.get("Target", "")
        return rels

    def metadata_payload(self, archive: zipfile.ZipFile, sheet_path: str) -> dict[str, Any]:
        root = self.parse_xml(archive.read(sheet_path))
        texts: list[str] = []
        for node in root.iter():
            if self.local_name(node.tag) == "t" and node.text is not None:
                texts.append(node.text)
        self.check(texts and texts[0] == "main-computer-spreadsheet-xlsx-metadata-v1", "metadata sheet sentinel is present")
        encoded = "".join(texts[1:])
        return json.loads(base64.b64decode(encoded).decode("utf-8"))

    def find_cell(self, sheet_xml: str, ref: str) -> ET.Element | None:
        root = self.parse_xml(sheet_xml)
        for node in root.iter():
            if self.local_name(node.tag) == "c" and node.attrib.get("r", "").upper() == ref.upper():
                return node
        return None

    def cell_text(self, sheet_xml: str, ref: str, child_name: str) -> str:
        cell = self.find_cell(sheet_xml, ref)
        if cell is None:
            return ""
        for child in cell:
            if self.local_name(child.tag) == child_name:
                return child.text or ""
        return ""

    def mutate_cell_values(self, package: bytes, sheet_path: str, replacements: dict[str, str]) -> bytes:
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}

        root = self.parse_xml(entries[sheet_path])
        changed = set()
        for cell in root.iter():
            if self.local_name(cell.tag) != "c":
                continue
            ref = cell.attrib.get("r", "").upper()
            if ref not in replacements:
                continue
            value = None
            for child in cell:
                if self.local_name(child.tag) == "v":
                    value = child
                    break
            if value is None:
                value = ET.SubElement(cell, f"{{{MAIN_NS}}}v")
            value.text = replacements[ref]
            changed.add(ref)

        missing = sorted(set(replacements) - changed)
        if missing:
            raise SmokeFailure(f"Could not mutate visible XLSX cells: {', '.join(missing)}")

        entries[sheet_path] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        return output.getvalue()

    def code_entries_by_language(self, metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
        entries: dict[str, dict[str, Any]] = {}
        for entry in metadata.get("cells", []):
            if not isinstance(entry, dict):
                continue
            cell = entry.get("cell") if isinstance(entry.get("cell"), dict) else {}
            language = str(cell.get("language") or "")
            kind = str(cell.get("kind") or "")
            if kind in {"javascript", "python", "basic"}:
                entries[language] = entry
        return entries

    def verify_metadata_script_entry(self, entry: dict[str, Any], ref: str, spec: dict[str, Any]) -> None:
        cell = entry.get("cell")
        self.check(isinstance(cell, dict), f"metadata entry for {spec['language']} is a cell object")
        cell = cell if isinstance(cell, dict) else {}
        language = spec["language"]
        self.check_equal(entry.get("sheet"), "Script Cells", f"metadata entry for {language} records sheet")
        self.check_equal(entry.get("ref"), ref, f"metadata entry for {language} records ref")
        self.check(re.fullmatch(r"[0-9a-f]{64}", str(entry.get("checksum") or "")) is not None, f"metadata entry for {language} has checksum")
        self.check_equal(cell.get("kind"), spec["kind"], f"metadata entry for {language} preserves kind")
        self.check_equal(cell.get("language"), language, f"metadata entry for {language} preserves language")
        self.check_equal(cell.get("source"), spec["source"], f"metadata entry for {language} preserves source")
        self.check_equal(cell.get("dependencies"), [spec["dependency"]], f"metadata entry for {language} preserves dependencies")
        self.check_equal(cell.get("writes"), [spec["write"]], f"metadata entry for {language} preserves write preview")
        parts = ((cell.get("output") or {}).get("parts") or [])
        self.check(parts and parts[0].get("metadata", {}).get("language") == language, f"metadata entry for {language} preserves output metadata")
        self.check_equal((cell.get("metadata") or {}).get("script_language"), language, f"metadata entry for {language} preserves custom metadata")

    def verify_imported_script_cell(self, workbook: dict[str, Any], ref: str, spec: dict[str, Any], expected_status: str, expected_value: str) -> None:
        cells = workbook["sheets"]["Script Cells"]["cells"]
        cell = cells.get(ref)
        language = spec["language"]
        self.check(isinstance(cell, dict), f"imported {language} cell exists at Script Cells!{ref}")
        cell = cell if isinstance(cell, dict) else {}
        self.check_equal(cell.get("kind"), spec["kind"], f"imported {language} cell preserves kind")
        self.check_equal(cell.get("language"), language, f"imported {language} cell preserves language")
        self.check_equal(cell.get("source"), spec["source"], f"imported {language} cell preserves source")
        self.check_equal(cell.get("value"), expected_value, f"imported {language} cell preserves expected visible value")
        self.check_equal(cell.get("dependencies"), [spec["dependency"]], f"imported {language} cell preserves dependencies")
        self.check_equal(cell.get("writes"), [spec["write"]], f"imported {language} cell preserves write preview")
        output = cell.get("output") if isinstance(cell.get("output"), dict) else {}
        parts = output.get("parts") if isinstance(output.get("parts"), list) else []
        self.check(parts and parts[0].get("metadata", {}).get("language") == language, f"imported {language} cell preserves output metadata")
        metadata = cell.get("metadata") if isinstance(cell.get("metadata"), dict) else {}
        self.check_equal(metadata.get("script_language"), language, f"imported {language} cell preserves custom script metadata")
        self.check_equal(metadata.get("runtime"), f"{language}-worker", f"imported {language} cell preserves runtime metadata")
        round_trip = metadata.get("xlsx_round_trip") if isinstance(metadata.get("xlsx_round_trip"), dict) else {}
        self.check_equal(cell.get("status"), expected_status, f"imported {language} cell status is {expected_status}")
        self.check_equal(round_trip.get("status"), expected_status, f"imported {language} xlsx_round_trip status is {expected_status}")
        self.check_equal(round_trip.get("metadata_checksum"), "clean", f"imported {language} xlsx metadata checksum is clean")
        self.check_equal(bool(round_trip.get("visible_value_changed")), expected_status == "dirty", f"imported {language} visible edit flag matches status")

    def run(self) -> None:
        self.start_server()
        try:
            workbook = self.workbook_fixture()
            exported = self.post("/api/applications/spreadsheet/export-xlsx", {"path": "script-matrix.json", "workbook": workbook})
            self.check(exported.get("ok") is True, "export-xlsx route returns ok")
            self.check_equal(exported.get("filename"), "script-matrix.xlsx", "export-xlsx returns expected filename")
            self.check_equal(exported.get("encoding"), "base64", "export-xlsx returns base64 encoding")

            package = base64.b64decode(str(exported["content_base64"]))
            self.check(len(package) > 500, "export-xlsx returns non-trivial XLSX bytes")
            if self.keep_xlsx:
                self.keep_xlsx.parent.mkdir(parents=True, exist_ok=True)
                self.keep_xlsx.write_bytes(package)
                self.pass_(f"wrote original XLSX to {self.keep_xlsx}")

            with zipfile.ZipFile(io.BytesIO(package)) as archive:
                self.check(archive.testzip() is None, "XLSX zip integrity passes")
                names = set(archive.namelist())
                self.check("xl/workbook.xml" in names, "XLSX contains xl/workbook.xml")
                self.check("xl/_rels/workbook.xml.rels" in names, "XLSX contains workbook relationships")
                workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
                rels_xml = archive.read("xl/_rels/workbook.xml.rels").decode("utf-8")
                sheet_info = self.workbook_sheet_info(workbook_xml)
                visible_sheet_names = [sheet["name"] for sheet in sheet_info if sheet["state"] != "veryHidden"]
                self.check_equal(visible_sheet_names, ["Inputs", "Script Cells"], "XLSX preserves visible sheet names and order")
                self.check_equal(self.workbook_active_tab(workbook_xml), 1, "XLSX workbookView activeTab points at Script Cells")
                metadata_sheets = [sheet for sheet in sheet_info if sheet["name"] == "__main_computer_metadata"]
                self.check(len(metadata_sheets) == 1, "XLSX includes hidden Main Computer metadata sheet")
                self.check_equal(metadata_sheets[0].get("state"), "veryHidden", "metadata sheet is veryHidden")
                rels = self.workbook_relationships(rels_xml)
                inputs_path = "xl/" + rels[sheet_info[0]["rid"]]
                scripts_path = "xl/" + rels[sheet_info[1]["rid"]]
                metadata_path = "xl/" + rels[metadata_sheets[0]["rid"]]
                inputs_xml = archive.read(inputs_path).decode("utf-8")
                scripts_xml = archive.read(scripts_path).decode("utf-8")
                self.check_equal(self.cell_text(inputs_xml, "C1", "f"), "A1+B1", "formula C1 is exported as a native XLSX formula")
                for ref, spec in SCRIPT_CELLS.items():
                    self.check_equal(self.cell_text(scripts_xml, ref, "v"), spec["value"], f"{spec['language']} code cell exports visible value")
                metadata = self.metadata_payload(archive, metadata_path)

            self.check_equal(metadata.get("schema"), "main-computer-spreadsheet-xlsx-metadata-v1", "metadata schema is present")
            self.check_equal(metadata.get("version"), 2, "metadata version is current")
            self.check_equal(metadata.get("active_sheet"), "Script Cells", "metadata records active sheet")
            self.check(re.fullmatch(r"[0-9a-f]{64}", str(metadata.get("checksum") or "")) is not None, "metadata checksum is present")
            metadata_cells = metadata.get("cells") if isinstance(metadata.get("cells"), list) else []
            self.check(len(metadata_cells) >= 4, "metadata includes formula plus scripting cells")
            self.check(any(entry.get("sheet") == "Inputs" and entry.get("ref") == "C1" and (entry.get("cell") or {}).get("kind") == "formula" for entry in metadata_cells if isinstance(entry, dict)), "metadata includes formula cell Inputs!C1")
            entries_by_language = self.code_entries_by_language(metadata)
            self.check_equal(sorted(entries_by_language), ["basic", "javascript", "python"], "metadata includes JS, Python, and BASIC code cells")
            for ref, spec in SCRIPT_CELLS.items():
                self.verify_metadata_script_entry(entries_by_language[spec["language"]], ref, spec)

            imported = self.post("/api/applications/spreadsheet/import-xlsx", {"filename": exported["filename"], "content_base64": exported["content_base64"]})
            self.check(imported.get("ok") is True, "import-xlsx response is ok")
            imported_workbook = imported.get("workbook")
            self.check(isinstance(imported_workbook, dict), "import-xlsx returns a workbook")
            imported_workbook = imported_workbook if isinstance(imported_workbook, dict) else {}
            self.check_equal(list(imported_workbook.get("sheets", {}).keys()), ["Inputs", "Script Cells"], "imported workbook preserves sheet names and order")
            self.check_equal(imported_workbook.get("active_sheet"), "Script Cells", "imported workbook restores active sheet")
            formula_cell = imported_workbook["sheets"]["Inputs"]["cells"]["C1"]
            self.check_equal(formula_cell.get("kind"), "formula", "imported Inputs!C1 remains a formula cell")
            self.check_equal(formula_cell.get("source"), "=A1+B1", "imported Inputs!C1 preserves raw formula source")
            for ref, spec in SCRIPT_CELLS.items():
                self.verify_imported_script_cell(imported_workbook, ref, spec, "clean", spec["value"])
            workbook_metadata = imported_workbook.get("metadata") if isinstance(imported_workbook.get("metadata"), dict) else {}
            xlsx_metadata = workbook_metadata.get("main_computer_xlsx_metadata") if isinstance(workbook_metadata.get("main_computer_xlsx_metadata"), dict) else {}
            self.check_equal(xlsx_metadata.get("status"), "clean", "metadata checksum validates as clean")
            self.check_equal(xlsx_metadata.get("restored"), 4, "metadata restored formula plus three scripting cells")
            self.check_equal((xlsx_metadata.get("status_counts") or {}).get("clean"), 4, "normal re-import records all metadata cells as clean")

            dirty_package = self.mutate_cell_values(
                package,
                scripts_path,
                {ref: spec["dirty_value"] for ref, spec in SCRIPT_CELLS.items()},
            )
            dirty_imported = self.post(
                "/api/applications/spreadsheet/import-xlsx",
                {"filename": "script-matrix-edited.xlsx", "content_base64": base64.b64encode(dirty_package).decode("ascii")},
            )
            self.check(dirty_imported.get("ok") is True, "dirty import response is ok")
            dirty_workbook = dirty_imported.get("workbook")
            self.check(isinstance(dirty_workbook, dict), "dirty import returns a workbook")
            dirty_workbook = dirty_workbook if isinstance(dirty_workbook, dict) else {}
            for ref, spec in SCRIPT_CELLS.items():
                self.verify_imported_script_cell(dirty_workbook, ref, spec, "dirty", spec["dirty_value"])
            dirty_metadata = ((dirty_workbook.get("metadata") or {}).get("main_computer_xlsx_metadata") or {})
            self.check_equal(dirty_metadata.get("status"), "clean", "dirty visible edit keeps metadata checksum clean")
            self.check_equal(dirty_metadata.get("restored"), 4, "dirty import restores formula plus three scripting cells")
            self.check_equal((dirty_metadata.get("status_counts") or {}).get("dirty"), 3, "dirty import records all three scripting cells as dirty")
            self.check_equal((dirty_metadata.get("status_counts") or {}).get("clean"), 1, "dirty import keeps unchanged formula metadata clean")
        finally:
            self.stop_server()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Functional smoke test for spreadsheet XLSX formulas, sheets, metadata, and code-cell scripting preservation.")
    parser.add_argument("--keep-xlsx", type=Path, help="Optional path to write the generated XLSX for manual inspection.")
    args = parser.parse_args(argv)

    smoke = SpreadsheetXlsxFunctionalSmoke(keep_xlsx=args.keep_xlsx)
    try:
        smoke.run()
    except SmokeFailure as exc:
        print(f"\nFAIL: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"\nERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(f"\nOK: spreadsheet XLSX functional smoke passed with {smoke.pass_count} checks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
