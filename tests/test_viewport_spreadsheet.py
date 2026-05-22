from __future__ import annotations

import base64
import io
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.viewport import APPLICATIONS_INDEX_HTML, ViewportServer, _application_route_target


class ViewportSpreadsheetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name)
        self.server = ViewportServer(("127.0.0.1", 0), MainComputerConfig(workspace=self.repo), verbose=False)
        self.server.debug_root = self.repo.resolve()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

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

    def assertContentHash(self, value: object) -> None:
        self.assertIsInstance(value, str)
        self.assertRegex(value, re.compile(r"^[0-9a-f]{64}$"))


    def _minimal_xlsx_base64(self) -> str:
        workbook_xml = """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Budget" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>
"""
        workbook_rels_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>
"""
        shared_strings_xml = """<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="2" uniqueCount="2">
  <si><t>Name</t></si>
  <si><t>Amount</t></si>
</sst>
"""
        sheet_xml = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1">
      <c r="A1" t="s"><v>0</v></c>
      <c r="B1" t="s"><v>1</v></c>
      <c r="C1" t="inlineStr"><is><t>Notes</t></is></c>
    </row>
    <row r="2">
      <c r="A2" t="inlineStr"><is><t>Rent</t></is></c>
      <c r="B2"><v>1200</v></c>
      <c r="C2" t="b"><v>1</v></c>
    </row>
    <row r="3">
      <c r="B3"><f>SUM(B2)</f></c>
    </row>
  </sheetData>
</worksheet>
"""
        content_types_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>
"""
        package_rels_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdWorkbook" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
"""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", content_types_xml)
            archive.writestr("_rels/.rels", package_rels_xml)
            archive.writestr("xl/workbook.xml", workbook_xml)
            archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
            archive.writestr("xl/sharedStrings.xml", shared_strings_xml)
            archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    def test_files_endpoint_creates_root(self) -> None:
        data = self._post("/api/applications/spreadsheet/files")

        self.assertTrue(data["ok"])
        self.assertEqual(data["root"], "spreadsheets")
        self.assertEqual(data["files"], [])
        self.assertTrue((self.repo / "spreadsheets").is_dir())


    def test_import_xlsx_upload_returns_workbook_without_writing_file(self) -> None:
        data = self._post(
            "/api/applications/spreadsheet/import-xlsx",
            {"filename": "Budget.xlsx", "content_base64": self._minimal_xlsx_base64()},
        )

        self.assertTrue(data["ok"])
        self.assertEqual(data["filename"], "Budget.xlsx")
        self.assertTrue(data["path"].endswith("-imported.json"))
        self.assertEqual(data["display_path"], f"spreadsheets/{data['path']}")
        self.assertFalse((self.repo / "spreadsheets" / data["path"]).exists())
        workbook = data["workbook"]
        self.assertEqual(workbook["active_sheet"], "Budget")
        self.assertEqual(workbook["metadata"]["source"], "xlsx-upload")
        self.assertEqual(workbook["metadata"]["source_filename"], "Budget.xlsx")
        cells = workbook["sheets"]["Budget"]["cells"]
        self.assertEqual(cells["A1"]["value"], "Name")
        self.assertEqual(cells["B1"]["value"], "Amount")
        self.assertEqual(cells["C1"]["value"], "Notes")
        self.assertEqual(cells["A2"]["value"], "Rent")
        self.assertEqual(cells["B2"]["value"], "1200")
        self.assertEqual(cells["C2"]["value"], "TRUE")
        self.assertEqual(cells["B3"]["value"], "=SUM(B2)")
        self.assertEqual(cells["B3"]["kind"], "formula")
        self.assertEqual(cells["B3"]["source"], "=SUM(B2)")

    def test_import_xlsx_rejects_invalid_extension(self) -> None:
        error = self._post_error(
            "/api/applications/spreadsheet/import-xlsx",
            {"filename": "Budget.xls", "content_base64": self._minimal_xlsx_base64()},
        )

        self.assertEqual(error.code, 400)
        payload = json.loads(error.read().decode("utf-8"))
        self.assertIn(".xlsx", payload["error"])

    def test_import_xlsx_rejects_invalid_base64(self) -> None:
        error = self._post_error(
            "/api/applications/spreadsheet/import-xlsx",
            {"filename": "Budget.xlsx", "content_base64": "not valid base64"},
        )

        self.assertEqual(error.code, 400)
        payload = json.loads(error.read().decode("utf-8"))
        self.assertIn("base64", payload["error"])

    def test_import_chat_shared_variables_blob(self) -> None:
        exported = self._post(
            "/api/applications/chat-console/shared-variables/export",
            {"variables": {"d": 4, "title": "demo", "nested": {"ok": True}}},
        )
        self.assertTrue(exported["ok"])
        self.assertRegex(exported["id"], re.compile(r"^[0-9a-f]{32}$"))
        self.assertEqual(exported["spreadsheet_url"], f"/applications/spreadsheet?chat_vars={exported['id']}")
        self.assertTrue((self.repo / "chat_console_shared_variables" / f"{exported['id']}.json").is_file())

        imported = self._post("/api/applications/spreadsheet/import-chat-variables", {"blob_id": exported["id"]})
        self.assertTrue(imported["ok"])
        self.assertEqual(imported["blob_id"], exported["id"])
        self.assertEqual(imported["display_path"], f"spreadsheets/{imported['path']}")
        self.assertTrue((self.repo / "spreadsheets" / imported["path"]).is_file())
        cells = imported["workbook"]["sheets"]["SharedVars"]["cells"]
        self.assertEqual(cells["A1"]["value"], "Name")
        rows = {cells[f"A{row}"]["value"]: row for row in range(2, 5)}
        self.assertEqual(cells[f"C{rows['d']}"]["value"], "4")
        self.assertEqual(cells[f"B{rows['title']}"]["value"], "string")
        self.assertEqual(cells[f"B{rows['nested']}"]["value"], "json")

    def test_import_chat_shared_variables_preserves_thread_link_metadata(self) -> None:
        exported = self._post(
            "/api/applications/chat-console/shared-variables/export",
            {
                "variables": {"total": 42},
                "source": {
                    "application": "chat-console",
                    "thread_id": "thread-demo",
                    "thread_title": "Demo Thread",
                },
            },
        )

        self.assertTrue(exported["ok"])
        self.assertEqual(exported["thread_id"], "thread-demo")
        self.assertEqual(exported["spreadsheet_url"], f"/applications/spreadsheet?chat_vars={exported['id']}&thread=thread-demo")

        imported = self._post("/api/applications/spreadsheet/import-chat-variables", {"blob_id": exported["id"]})
        metadata = imported["workbook"]["metadata"]
        self.assertEqual(metadata["chat"]["active_thread_id"], "thread-demo")
        self.assertEqual(metadata["chat"]["origin_thread_title"], "Demo Thread")
        self.assertEqual(metadata["chat"]["linked_by"], "chat-console-export")


    def test_create_read_write_and_conflict(self) -> None:
        created = self._post("/api/applications/spreadsheet/create", {"path": "budget.json", "rows": 50, "cols": 26})
        self.assertTrue(created["ok"])
        self.assertEqual(created["display_path"], "spreadsheets/budget.json")
        self.assertContentHash(created["content_hash"])
        self.assertTrue((self.repo / "spreadsheets" / "budget.json").is_file())

        files = self._post("/api/applications/spreadsheet/files")
        self.assertEqual(files["count"], 1)
        self.assertEqual(files["files"][0]["path"], "budget.json")

        read = self._post("/api/applications/spreadsheet/read", {"path": "budget.json"})
        self.assertEqual(read["workbook"]["sheets"]["Sheet1"]["cells"]["B1"]["value"], "42")
        old_hash = read["content_hash"]

        workbook = read["workbook"]
        workbook["sheets"]["Sheet1"]["cells"]["C1"] = {"value": "99"}
        written = self._post(
            "/api/applications/spreadsheet/write",
            {"path": "budget.json", "expected_content_hash": old_hash, "workbook": workbook},
        )
        self.assertTrue(written["ok"])
        self.assertNotEqual(written["content_hash"], old_hash)

        stale = self._post_error(
            "/api/applications/spreadsheet/write",
            {"path": "budget.json", "expected_content_hash": old_hash, "workbook": workbook},
        )
        self.assertEqual(stale.code, 409)

    def test_workbook_metadata_survives_save_load(self) -> None:
        created = self._post("/api/applications/spreadsheet/create", {"path": "metadata.json"})
        workbook = created["workbook"]
        workbook["metadata"] = {
            "chat": {
                "active_thread_id": "thread-abc",
                "linked_by": "spreadsheet",
            }
        }
        written = self._post(
            "/api/applications/spreadsheet/write",
            {"path": "metadata.json", "expected_content_hash": created["content_hash"], "workbook": workbook},
        )
        self.assertTrue(written["ok"])

        read = self._post("/api/applications/spreadsheet/read", {"path": "metadata.json"})
        self.assertEqual(read["workbook"]["metadata"]["chat"]["active_thread_id"], "thread-abc")
        self.assertEqual(read["workbook"]["metadata"]["chat"]["linked_by"], "spreadsheet")

    def test_multi_sheet_workbook_active_sheet_survives_save_load(self) -> None:
        created = self._post("/api/applications/spreadsheet/create", {"path": "multi.json"})
        workbook = created["workbook"]
        workbook["active_sheet"] = "Data"
        workbook["sheets"]["Data"] = {
            "rows": 50,
            "cols": 26,
            "cells": {
                "A1": {"value": "Name"},
                "A2": {"value": "Ada"},
            },
        }
        written = self._post(
            "/api/applications/spreadsheet/write",
            {"path": "multi.json", "expected_content_hash": created["content_hash"], "workbook": workbook},
        )
        self.assertTrue(written["ok"])

        read = self._post("/api/applications/spreadsheet/read", {"path": "multi.json"})
        self.assertEqual(read["workbook"]["active_sheet"], "Data")
        self.assertIn("Sheet1", read["workbook"]["sheets"])
        self.assertIn("Data", read["workbook"]["sheets"])
        self.assertEqual(read["workbook"]["sheets"]["Data"]["cells"]["A2"]["value"], "Ada")

    def test_unsafe_paths_and_extensions_rejected(self) -> None:
        for path in ["../escape.json", str((self.repo / "escape.json").resolve()), "nested/../../escape.json", "secret.py"]:
            with self.subTest(path=path):
                self.assertEqual(self._post_error("/api/applications/spreadsheet/read", {"path": path}).code, 400)

    def test_export_csv_returns_text(self) -> None:
        created = self._post("/api/applications/spreadsheet/create", {"path": "budget.json"})
        workbook = created["workbook"]
        workbook["sheets"]["Sheet1"]["cells"] = {"A1": {"value": "A"}, "B1": {"value": "B"}, "A2": {"value": "1"}, "B2": {"value": "2"}}
        written = self._post(
            "/api/applications/spreadsheet/write",
            {"path": "budget.json", "expected_content_hash": created["content_hash"], "workbook": workbook},
        )
        self.assertTrue(written["ok"])

        exported = self._post("/api/applications/spreadsheet/export-csv", {"path": "budget.json", "sheet": "Sheet1"})
        self.assertTrue(exported["ok"])
        self.assertEqual(exported["filename"], "budget-Sheet1.csv")
        self.assertIn("A,B", exported["content"])
        self.assertIn("1,2", exported["content"])

    def test_export_xlsx_returns_package_and_round_trips_formula_and_code_metadata(self) -> None:
        created = self._post("/api/applications/spreadsheet/create", {"path": "export.json"})
        workbook = created["workbook"]
        workbook["sheets"]["Sheet1"]["cells"] = {
            "A1": {"value": "2"},
            "B1": {"value": "3"},
            "C1": {
                "value": "5",
                "kind": "formula",
                "language": "none",
                "source": "=A1+B1",
                "metadata": {"formula": {"engine": "hyperformula", "source": "=A1+B1"}},
            },
            "D1": {
                "value": "10",
                "kind": "javascript",
                "language": "javascript",
                "source": 'return sheet.getNumber("C1") * 2;',
                "dependencies": ["C1"],
                "metadata": {"note": "keep this code cell"},
            },
        }

        exported = self._post("/api/applications/spreadsheet/export-xlsx", {"path": "export.json", "workbook": workbook})
        self.assertTrue(exported["ok"])
        self.assertEqual(exported["filename"], "export.xlsx")
        self.assertEqual(exported["encoding"], "base64")
        package = base64.b64decode(exported["content_base64"])
        self.assertGreater(exported["bytes"], 500)

        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            names = set(archive.namelist())
            self.assertIn("xl/workbook.xml", names)
            self.assertIn("xl/worksheets/sheet1.xml", names)
            self.assertIn("xl/worksheets/sheet2.xml", names)
            sheet_xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
            workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
            metadata_xml = archive.read("xl/worksheets/sheet2.xml").decode("utf-8")

        self.assertIn("<f>A1+B1</f>", sheet_xml)
        self.assertIn("state=\"veryHidden\"", workbook_xml)
        self.assertIn("__main_computer_metadata", workbook_xml)
        self.assertIn("main-computer-spreadsheet-xlsx-metadata-v1", metadata_xml)
        metadata_chunks = re.findall(r"<t>([^<]+)</t>", metadata_xml)[1:]
        metadata_payload = json.loads(base64.b64decode("".join(metadata_chunks)).decode("utf-8"))
        self.assertEqual(metadata_payload["version"], 2)
        self.assertRegex(metadata_payload["checksum"], re.compile(r"^[0-9a-f]{64}$"))
        self.assertRegex(metadata_payload["cells"][0]["checksum"], re.compile(r"^[0-9a-f]{64}$"))

        imported = self._post(
            "/api/applications/spreadsheet/import-xlsx",
            {"filename": exported["filename"], "content_base64": exported["content_base64"]},
        )
        cells = imported["workbook"]["sheets"]["Sheet1"]["cells"]
        self.assertEqual(cells["C1"]["kind"], "formula")
        self.assertEqual(cells["C1"]["source"], "=A1+B1")
        self.assertEqual(cells["C1"]["value"], "5")
        self.assertEqual(cells["D1"]["kind"], "javascript")
        self.assertEqual(cells["D1"]["language"], "javascript")
        self.assertIn("sheet.getNumber", cells["D1"]["source"])
        self.assertEqual(cells["D1"]["dependencies"], ["C1"])
        self.assertEqual(cells["D1"]["metadata"]["note"], "keep this code cell")
        self.assertIn("xlsx_round_trip", cells["D1"]["metadata"])
        self.assertEqual(cells["D1"]["metadata"]["xlsx_round_trip"]["status"], "clean")
        self.assertEqual(imported["workbook"]["metadata"]["main_computer_xlsx_metadata"]["status"], "clean")
        self.assertEqual(imported["workbook"]["metadata"]["main_computer_xlsx_metadata"]["restored"], 2)


    def test_export_xlsx_round_trips_multiple_sheets_and_active_sheet(self) -> None:
        workbook = {
            "version": 1,
            "active_sheet": "Data Sheet",
            "sheets": {
                "Sheet1": {
                    "rows": 50,
                    "cols": 26,
                    "cells": {
                        "A1": {"value": "2"},
                    },
                },
                "Data Sheet": {
                    "rows": 50,
                    "cols": 26,
                    "cells": {
                        "A1": {"value": "3"},
                        "B1": {
                            "value": "5",
                            "kind": "formula",
                            "language": "none",
                            "source": "=Sheet1!A1+A1",
                            "metadata": {"formula": {"engine": "hyperformula", "source": "=Sheet1!A1+A1"}},
                        },
                    },
                },
            },
            "metadata": {},
        }
        exported = self._post("/api/applications/spreadsheet/export-xlsx", {"path": "multi.json", "workbook": workbook})
        self.assertTrue(exported["ok"])
        package = base64.b64decode(exported["content_base64"])

        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            names = set(archive.namelist())
            self.assertIn("xl/worksheets/sheet1.xml", names)
            self.assertIn("xl/worksheets/sheet2.xml", names)
            self.assertIn("xl/worksheets/sheet3.xml", names)
            workbook_xml = archive.read("xl/workbook.xml").decode("utf-8")
            data_xml = archive.read("xl/worksheets/sheet2.xml").decode("utf-8")

        self.assertIn('name="Sheet1"', workbook_xml)
        self.assertIn('name="Data Sheet"', workbook_xml)
        self.assertIn('activeTab="1"', workbook_xml)
        self.assertIn("<f>Sheet1!A1+A1</f>", data_xml)
        self.assertIn("__main_computer_metadata", workbook_xml)

        imported = self._post(
            "/api/applications/spreadsheet/import-xlsx",
            {"filename": exported["filename"], "content_base64": exported["content_base64"]},
        )
        self.assertEqual(imported["workbook"]["active_sheet"], "Data Sheet")
        self.assertIn("Sheet1", imported["workbook"]["sheets"])
        self.assertIn("Data Sheet", imported["workbook"]["sheets"])
        cells = imported["workbook"]["sheets"]["Data Sheet"]["cells"]
        self.assertEqual(cells["B1"]["kind"], "formula")
        self.assertEqual(cells["B1"]["source"], "=Sheet1!A1+A1")



    def test_import_xlsx_marks_code_cell_dirty_when_visible_value_changed_after_export(self) -> None:
        workbook = {
            "version": 1,
            "active_sheet": "Sheet1",
            "sheets": {
                "Sheet1": {
                    "rows": 50,
                    "cols": 26,
                    "cells": {
                        "A1": {"value": "2"},
                        "D1": {
                            "value": "10",
                            "kind": "javascript",
                            "language": "javascript",
                            "source": 'return sheet.getNumber("A1") * 5;',
                            "dependencies": ["A1"],
                            "metadata": {"note": "keep this code cell"},
                        },
                    },
                }
            },
        }
        exported = self._post("/api/applications/spreadsheet/export-xlsx", {"path": "export.json", "workbook": workbook})
        package = base64.b64decode(exported["content_base64"])
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}

        sheet_xml = entries["xl/worksheets/sheet1.xml"].decode("utf-8")
        self.assertIn('<c r="D1"><v>10</v></c>', sheet_xml)
        entries["xl/worksheets/sheet1.xml"] = sheet_xml.replace('<c r="D1"><v>10</v></c>', '<c r="D1"><v>12</v></c>').encode("utf-8")
        mutated = io.BytesIO()
        with zipfile.ZipFile(mutated, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries.items():
                archive.writestr(name, data)

        imported = self._post(
            "/api/applications/spreadsheet/import-xlsx",
            {
                "filename": "export-edited.xlsx",
                "content_base64": base64.b64encode(mutated.getvalue()).decode("ascii"),
            },
        )
        cell = imported["workbook"]["sheets"]["Sheet1"]["cells"]["D1"]
        self.assertEqual(cell["kind"], "javascript")
        self.assertEqual(cell["value"], "12")
        self.assertEqual(cell["status"], "dirty")
        self.assertIn("sheet.getNumber", cell["source"])
        self.assertEqual(cell["metadata"]["xlsx_round_trip"]["status"], "dirty")
        self.assertTrue(cell["metadata"]["xlsx_round_trip"]["visible_value_changed"])


    def test_rich_code_cells_survive_save_load_and_strip_unknown_fields(self) -> None:
        created = self._post("/api/applications/spreadsheet/create", {"path": "code.json"})
        workbook = created["workbook"]
        workbook["sheets"]["Sheet1"]["cells"] = {
            "A1": {"value": "21"},
            "B1": {
                "value": "42",
                "kind": "javascript",
                "language": "javascript",
                "source": 'const x = sheet.getNumber("A1");\nreturn x * 2;',
                "output": {"parts": [{"kind": "stdout", "title": "stdout", "content": "ok", "metadata": {}}]},
                "status": "dirty",
                "dependencies": ["A1"],
                "writes": [{"kind": "write", "target": "C1", "value": 99}],
                "metadata": {"runtime": "worker"},
                "unsafe": "strip me",
            },
            "B2": {
                "value": "",
                "kind": "python",
                "language": "python",
                "source": "x = sheet.get_number('A1')",
            },
            "B3": {
                "value": "",
                "kind": "basic",
                "language": "basic",
                "source": 'PRINT "hello"',
            },
        }
        written = self._post(
            "/api/applications/spreadsheet/write",
            {"path": "code.json", "expected_content_hash": created["content_hash"], "workbook": workbook},
        )
        self.assertTrue(written["ok"])

        read = self._post("/api/applications/spreadsheet/read", {"path": "code.json"})
        cells = read["workbook"]["sheets"]["Sheet1"]["cells"]
        self.assertEqual(cells["B1"]["kind"], "javascript")
        self.assertEqual(cells["B1"]["language"], "javascript")
        self.assertIn("sheet.getNumber", cells["B1"]["source"])
        self.assertEqual(cells["B1"]["output"]["parts"][0]["kind"], "stdout")
        self.assertEqual(cells["B1"]["dependencies"], ["A1"])
        self.assertEqual(cells["B1"]["writes"][0]["target"], "C1")
        self.assertEqual(cells["B1"]["metadata"]["runtime"], "worker")
        self.assertNotIn("unsafe", cells["B1"])
        self.assertEqual(cells["B2"]["kind"], "python")
        self.assertEqual(cells["B3"]["kind"], "basic")

    def test_old_simple_cells_still_load_with_defaults(self) -> None:
        path = self.repo / "spreadsheets" / "legacy.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "active_sheet": "Sheet1", "sheets": {"Sheet1": {"rows": 1, "cols": 1, "cells": {"A1": {"value": "legacy"}}}}}), encoding="utf-8")

        read = self._post("/api/applications/spreadsheet/read", {"path": "legacy.json"})
        cell = read["workbook"]["sheets"]["Sheet1"]["cells"]["A1"]
        self.assertEqual(cell["value"], "legacy")
        self.assertEqual(cell["kind"], "value")
        self.assertEqual(cell["language"], "none")
        self.assertEqual(cell["source"], "")
        self.assertEqual(cell["output"], {"parts": []})
        self.assertEqual(cell["status"], "clean")

    def test_saved_evaluating_code_cell_normalizes_to_dirty_warning(self) -> None:
        created = self._post("/api/applications/spreadsheet/create", {"path": "interrupted.json"})
        workbook = created["workbook"]
        workbook["sheets"]["Sheet1"]["cells"] = {
            "A1": {
                "value": "1+1",
                "kind": "javascript",
                "language": "javascript",
                "source": "1+1",
                "status": "evaluating",
                "output": {"parts": [{"kind": "text", "title": "Running", "content": "Running javascript cell...", "metadata": {}}]},
            }
        }
        self._post(
            "/api/applications/spreadsheet/write",
            {"path": "interrupted.json", "expected_content_hash": created["content_hash"], "workbook": workbook},
        )

        read = self._post("/api/applications/spreadsheet/read", {"path": "interrupted.json"})
        cell = read["workbook"]["sheets"]["Sheet1"]["cells"]["A1"]
        self.assertEqual(cell["status"], "dirty")
        self.assertEqual(cell["output"]["parts"][0]["kind"], "warning")
        self.assertIn("interrupted", cell["output"]["parts"][0]["content"])

    def test_csv_export_uses_cached_value_not_code_source(self) -> None:
        created = self._post("/api/applications/spreadsheet/create", {"path": "code-export.json"})
        workbook = created["workbook"]
        workbook["sheets"]["Sheet1"]["cells"] = {
            "A1": {"value": "cached", "kind": "javascript", "language": "javascript", "source": "return 'source';"}
        }
        self._post(
            "/api/applications/spreadsheet/write",
            {"path": "code-export.json", "expected_content_hash": created["content_hash"], "workbook": workbook},
        )

        exported = self._post("/api/applications/spreadsheet/export-csv", {"path": "code-export.json", "sheet": "Sheet1"})
        self.assertIn("cached", exported["content"])
        self.assertNotIn("source", exported["content"])

    def test_frontend_contains_spreadsheet_hooks(self) -> None:
        for text in [
            "/api/applications/spreadsheet/files",
            "/api/applications/spreadsheet/read",
            "/api/applications/spreadsheet/write",
            "/api/applications/spreadsheet/create",
            "/api/applications/spreadsheet/export-csv",
            "/api/applications/spreadsheet/export-xlsx",
            "/api/applications/spreadsheet/import-xlsx",
            'id="spreadsheet-import-xlsx"',
            'id="spreadsheet-export-xlsx"',
            "exportSpreadsheetXlsx",
            'id="spreadsheet-import-xlsx-file"',
            "importSpreadsheetXlsxFile",
            "spreadsheetArrayBufferToBase64",
            'id="spreadsheet-file-list"',
            'id="spreadsheet-current-path"',
            'id="spreadsheet-selection-status"',
            'id="spreadsheet-plot-canvas"',
            'id="spreadsheet-grid-host"',
            'id="spreadsheet-cell-type"',
            'id="spreadsheet-run-cell"',
            "revo-grid",
            "SPREADSHEET_REVOGRID_VERSION",
            "SPREADSHEET_REVOGRID_VENDOR_URL",
            "/applications/vendor/revogrid/revo-grid.esm.js",
            "https://unpkg.com/@revolist/revogrid@4.20.0/dist/revo-grid/revo-grid.esm.js",
            "SPREADSHEET_HYPERFORMULA_VERSION",
            "SPREADSHEET_HYPERFORMULA_LICENSE_KEY",
            "SPREADSHEET_HYPERFORMULA_VENDOR_URL",
            "SPREADSHEET_HYPERFORMULA_CDN_URL",
            "/applications/vendor/hyperformula/hyperformula.full.min.js",
            "https://cdn.jsdelivr.net/npm/hyperformula@3.2.0/dist/hyperformula.full.min.js",
            "spreadsheetEnsureHyperFormulaLoaded",
            "loadSpreadsheetHyperFormula",
            "spreadsheetEnsureRevoGridLoaded",
            "customElements.whenDefined(\"revo-grid\")",
            "spreadsheetRevoGridLoadDiagnostic",
            "loadSpreadsheetRevoGrid",
            "spreadsheetSheetToGridSource",
            "spreadsheetSheetToGridColumns",
            "spreadsheetGridPositionToRef",
            "spreadsheetNormalizeGridRange",
            "spreadsheetApplyGridSelection",
            "configureSpreadsheetGrid",
            "spreadsheetGridEditValue",
            "spreadsheetSyncGridCellFromWorkbook",
            "spreadsheetApplyGridEditToWorkbook",
            "spreadsheetNormalizeFormulaCell",
            "spreadsheetRecalculateFormulas",
            "spreadsheetWorkbookToHyperFormulaSheets",
            "spreadsheetFormulaDisplayValue",
            "spreadsheetFormulaRawSource",
            "spreadsheetSetDirty(true, \"local edits pending disk save\")",
            "spreadsheet-js-worker.js",
            "spreadsheet-python-worker.js",
            "spreadsheet-basic-worker.js",
            "runSpreadsheetWorker",
            "spreadsheetWorkbookSnapshotForCodeCells",
            "workbook_snapshot: spreadsheetWorkbookSnapshotForCodeCells(spreadsheetWorkbook)",
            "formula_snapshot",
            "applySpreadsheetCodeResponse",
            "spreadsheetNormalizeCell(cell = {}, options = {})",
            "resetEvaluating = Boolean(options.resetEvaluating)",
            "spreadsheetNormalizeLoadedWorkbook",
            "spreadsheetNormalizeCell(cell, {resetEvaluating: true})",
            "spreadsheetNormalizeCell(sheet.cells[ref])",
            "spreadsheetWorkerSourceSummary",
            "testSpreadsheetWorker",
            "getSpreadsheetRuntimeTimeout",
            "https://cdn.jsdelivr.net/pyodide/v0.29.3/full/pyodide.js",
            "https://google.github.io/wwwbasic/wwwbasic.js",
            "worker.terminate()",
            "worker.onmessageerror",
            "worker.postMessage(request)",
            "Worker postMessage error",
            "Previous code-cell run was interrupted before completion.",
            "try {",
            "catch (error)",
            "plotSpreadsheetSelection",
            "setSpreadsheetSelection",
        ]:
            with self.subTest(text=text):
                self.assertIn(text, APPLICATIONS_INDEX_HTML)

    def test_frontend_contains_spreadsheet_chat_thread_integration(self) -> None:
        for text in [
            'id="spreadsheet-chat-thread-panel"',
            'id="spreadsheet-embedded-chat-notebook"',
            'data-chat-console-embed="spreadsheet"',
            'data-chat-console-active-app="spreadsheet"',
            'data-chat-console-id-prefix="spreadsheet-chat"',
            'data-chat-console-thread-title="Spreadsheet Chat"',
            'chatConsoleEmbeddedClass("chat-console-shell", config, "chat-console-shell")',
            'data-chat-console-embedded-notebook',
            'id="spreadsheet-import-history"',
            "function spreadsheetMountChatThreadController",
            "window.chatConsoleMountSpreadsheetEmbedded",
            "window.chatConsoleMountSpreadsheetEmbedded(panel",
            "function chatConsoleMountSpreadsheetEmbedded",
            "getLinkedThreadId: spreadsheetGetWorkbookChatThreadId",
            "setLinkedThreadId(threadId, threadValue, context = {})",
            "function spreadsheetImportChatCodeSnippetFromChatConsole",
            "function spreadsheetRenderChatImportHistory",
            "metadata.chat_import_history",
            "Import to selected cell",
            "Open Origin Thread",
            "spreadsheetInitChatThreadIntegration",
            "chatConsoleSpreadsheetEmbeddedNotebook",
        ]:
            with self.subTest(text=text):
                self.assertIn(text, APPLICATIONS_INDEX_HTML)

    def test_frontend_contains_spreadsheet_ai_range_action_staging(self) -> None:
        for text in [
            'id="spreadsheet-ai-range-request"',
            'id="spreadsheet-ai-range-generate"',
            'id="spreadsheet-ai-range-copy-context"',
            'id="spreadsheet-ai-range-context-preview"',
            "SPREADSHEET_AI_RANGE_CONTEXT_MAX_CELLS",
            "function spreadsheetBuildAiRangeContext",
            "function spreadsheetBuildAiRangePrompt",
            "function spreadsheetStageAiRangePromptInChat",
            "function spreadsheetRenderAiRangeContextPreview",
            "spreadsheet-ai-range-action",
            "spreadsheet_ai_range_action",
            "Return exactly one fenced `javascript` code block",
            "sheet.writeRange(range, values)",
            "Use sheet.write or sheet.writeRange for proposed changes",
            "spreadsheetAiRangeGenerate?.addEventListener",
            "spreadsheetAiRangeCopyContext?.addEventListener",
            "run it in Chat Console, then import the snippet into a code cell",
        ]:
            with self.subTest(text=text):
                self.assertIn(text, APPLICATIONS_INDEX_HTML)

    def test_frontend_contains_spreadsheet_sheet_tab_integration(self) -> None:
        for text in [
            'id="spreadsheet-sheet-tabbar"',
            'id="spreadsheet-sheet-tabs"',
            'id="spreadsheet-add-sheet"',
            'id="spreadsheet-sheet-add"',
            'id="spreadsheet-sheet-rename"',
            'id="spreadsheet-sheet-duplicate"',
            'id="spreadsheet-sheet-delete"',
            "function spreadsheetRenderSheetTabs",
            "function spreadsheetActivateSheet",
            "function spreadsheetPromptAddSheet",
            "function spreadsheetPromptRenameSheet",
            "function spreadsheetPromptDuplicateSheet",
            "function spreadsheetPromptDeleteSheet",
            "function spreadsheetPromptSheetAction",
            "spreadsheetAddSheet?.addEventListener",
            "spreadsheetSheetRename?.addEventListener",
            "spreadsheetWorkbook.active_sheet = sheetName",
            "spreadsheetUniqueSheetName",
            "spreadsheetRenameWorkbookSheet",
            "spreadsheetDuplicateWorkbookSheet",
            "spreadsheetDeleteWorkbookSheet",
            "role=\"tablist\"",
            "role=\"tab\"",
            "selection: ${spreadsheetActiveSheetName()}!",
            "Selected: ${sheetName}!",
        ]:
            with self.subTest(text=text):
                self.assertIn(text, APPLICATIONS_INDEX_HTML)

        css_source = (Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "styles" / "spreadsheet.css").read_text(encoding="utf-8")
        for text in [
            ".spreadsheet-sheet-tabbar",
            ".spreadsheet-sheet-tabs",
            ".spreadsheet-sheet-tab.active",
            ".spreadsheet-add-sheet",
        ]:
            with self.subTest(css=text):
                self.assertIn(text, css_source)

    def test_spreadsheet_code_runtime_include_order_is_top_level_safe(self) -> None:
        applications_source = (Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
        self.assertLess(
            applications_source.index("applications/scripts/spreadsheet-revogrid-loader.js"),
            applications_source.index("applications/scripts/spreadsheet-hyperformula-loader.js"),
        )
        self.assertLess(
            applications_source.index("applications/scripts/spreadsheet-hyperformula-loader.js"),
            applications_source.index("applications/scripts/spreadsheet-core.js"),
        )
        self.assertLess(
            applications_source.index("applications/scripts/spreadsheet-core.js"),
            applications_source.index("applications/scripts/spreadsheet-formula-engine.js"),
        )
        self.assertLess(
            applications_source.index("applications/scripts/spreadsheet-formula-engine.js"),
            applications_source.index("applications/scripts/spreadsheet-render.js"),
        )
        self.assertLess(
            applications_source.index("applications/scripts/spreadsheet-chart.js"),
            applications_source.index("applications/scripts/spreadsheet-code-runtime.js"),
        )
        self.assertLess(
            applications_source.index("applications/scripts/spreadsheet-code-runtime.js"),
            applications_source.index("applications/scripts/spreadsheet-app.js"),
        )
        self.assertLess(
            applications_source.index("applications/scripts/spreadsheet-chat-integration.js"),
            applications_source.index("applications/scripts/spreadsheet-ai-actions.js"),
        )
        self.assertLess(
            applications_source.index("applications/scripts/spreadsheet-ai-actions.js"),
            applications_source.index("applications/scripts/spreadsheet-app.js"),
        )
        self.assertLess(
            APPLICATIONS_INDEX_HTML.index("function applySpreadsheetWritePreview"),
            APPLICATIONS_INDEX_HTML.index("spreadsheetApplyWrites.addEventListener"),
        )
        self.assertLess(
            APPLICATIONS_INDEX_HTML.index("function runSpreadsheetSelectedCell"),
            APPLICATIONS_INDEX_HTML.index("spreadsheetRunCell.addEventListener"),
        )

    def test_spreadsheet_uses_revogrid_instead_of_contenteditable_table(self) -> None:
        self.assertIn("document.createElement(\"revo-grid\")", APPLICATIONS_INDEX_HTML)
        self.assertIn("grid.addEventListener(\"afteredit\"", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("grid.addEventListener(\"celledit\", spreadsheetApplyGridEditToWorkbook)", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("grid.addEventListener(\"rangeeditapply\", spreadsheetApplyGridEditToWorkbook)", APPLICATIONS_INDEX_HTML)
        self.assertIn("grid.source = spreadsheetSheetToGridSource(sheet, spreadsheetActiveSheetName())", APPLICATIONS_INDEX_HTML)
        self.assertIn("spreadsheetEnsureRevoGridLoaded().then(applyGrid).catch(renderSpreadsheetGridUnavailable)", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("cell.contentEditable = \"true\"", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("td[data-cell]", APPLICATIONS_INDEX_HTML)

    def test_spreadsheet_revogrid_user_edits_commit_after_grid_save(self) -> None:
        render_source = (Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "scripts" / "spreadsheet-render.js").read_text(encoding="utf-8")
        self.assertIn('grid.addEventListener("afteredit", spreadsheetApplyGridEditToWorkbook)', render_source)
        self.assertNotIn('grid.addEventListener("celledit", spreadsheetApplyGridEditToWorkbook)', render_source)
        self.assertNotIn('grid.addEventListener("rangeeditapply", spreadsheetApplyGridEditToWorkbook)', render_source)
        self.assertIn("function spreadsheetGridEditValue", render_source)
        self.assertIn('for (const key of ["val", "value", "newValue", "nextValue"])', render_source)
        self.assertIn("function spreadsheetSyncGridCellFromWorkbook", render_source)
        self.assertIn("spreadsheetSyncGridCellFromWorkbook(firstRef)", render_source)

    def test_spreadsheet_revogrid_range_selection_is_enabled_and_synced(self) -> None:
        render_source = (Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "scripts" / "spreadsheet-render.js").read_text(encoding="utf-8")
        for text in [
            'grid.theme = "darkCompact"',
            'grid.setAttribute("theme", "darkCompact")',
            "grid.range = true",
            "grid.useClipboard = true",
            "grid.addEventListener(\"focuscell\", spreadsheetApplyGridSelection)",
            "grid.addEventListener(\"afterfocus\", spreadsheetApplyGridSelection)",
            "grid.addEventListener(\"setrange\", spreadsheetApplyGridSelection)",
            "grid.addEventListener(\"selectionchangeinit\", spreadsheetApplyGridSelection)",
            "function spreadsheetGridPositionToRef",
            "function spreadsheetNormalizeGridRange",
            "function spreadsheetApplyGridSelection",
            "grid.getSelectedRange?.()",
            "grid.getFocused?.()",
            "setSpreadsheetSelection(normalized.startRef, normalized.endRef)",
        ]:
            with self.subTest(text=text):
                self.assertIn(text, render_source)

    def test_spreadsheet_revogrid_dark_theme_css_is_readable(self) -> None:
        css_source = (Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "styles" / "spreadsheet.css").read_text(encoding="utf-8")
        for text in [
            "--revo-grid-text: #f4f7ec",
            "--revo-grid-header-bg: #15180f",
            "--revo-grid-header-color: #f7c948",
            "--revo-grid-cell-border: #4b533f",
            "--revo-grid-focused-bg: rgba(121, 212, 242, 0.18)",
            "revo-grid.spreadsheet-grid revogr-data .rgCell",
            "-1px 0 0 0 var(--revo-grid-cell-border) inset",
            "revo-grid.spreadsheet-grid revogr-focus.focused-cell",
            "revo-grid.spreadsheet-grid revogr-overlay-selection .selection-border-range",
        ]:
            with self.subTest(text=text):
                self.assertIn(text, css_source)

    def test_plot_selection_uses_app_selected_range_not_table_dom_classes(self) -> None:
        chart_source = (Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "scripts" / "spreadsheet-chart.js").read_text(encoding="utf-8")
        self.assertIn("if (!spreadsheetSelectedRange)", chart_source)
        self.assertIn("spreadsheetSelectedRange.cells.forEach", chart_source)
        self.assertNotIn("querySelector", chart_source)
        self.assertNotIn("td[data-cell]", chart_source)

    def test_spreadsheet_revogrid_loader_prefers_same_origin_vendor_assets(self) -> None:
        loader_source = (Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "scripts" / "spreadsheet-revogrid-loader.js").read_text(encoding="utf-8")
        self.assertIn('const SPREADSHEET_REVOGRID_VERSION = "4.20.0"', loader_source)
        self.assertIn('const SPREADSHEET_REVOGRID_VENDOR_URL = "/applications/vendor/revogrid/revo-grid.esm.js"', loader_source)
        self.assertIn("SPREADSHEET_REVOGRID_CDN_URL", loader_source)
        self.assertIn("spreadsheetRevoGridLoadPromise", loader_source)
        self.assertIn('window.customElements.whenDefined("revo-grid")', loader_source)
        self.assertIn("Attempted:", loader_source)
        vendor_root = Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "vendor" / "revogrid"
        self.assertTrue((vendor_root / "revo-grid.esm.js").is_file())
        self.assertTrue((vendor_root / "README.md").is_file())
        self.assertIn("MIT", (vendor_root / "README.md").read_text(encoding="utf-8"))

    def test_spreadsheet_hyperformula_loader_uses_gpl_key_and_fallback_sources(self) -> None:
        loader_source = (Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "scripts" / "spreadsheet-hyperformula-loader.js").read_text(encoding="utf-8")
        for text in [
            'const SPREADSHEET_HYPERFORMULA_VERSION = "3.2.0"',
            'const SPREADSHEET_HYPERFORMULA_LICENSE_KEY = "gpl-v3"',
            'const SPREADSHEET_HYPERFORMULA_VENDOR_URL = "/applications/vendor/hyperformula/hyperformula.full.min.js"',
            'const SPREADSHEET_HYPERFORMULA_CDN_URL = "https://cdn.jsdelivr.net/npm/hyperformula@3.2.0/dist/hyperformula.full.min.js"',
            "spreadsheetHyperFormulaLoadPromise",
            "spreadsheetHyperFormulaGlobal",
            "spreadsheetEnsureHyperFormulaLoaded",
            "Attempted:",
        ]:
            with self.subTest(text=text):
                self.assertIn(text, loader_source)

    def test_spreadsheet_hyperformula_vendor_file_is_local_and_static(self) -> None:
        vendor_root = Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "vendor" / "hyperformula"
        vendor_build = vendor_root / "hyperformula.full.min.js"
        readme = vendor_root / "README.md"
        self.assertTrue(vendor_build.is_file())
        self.assertTrue(readme.is_file())
        source = vendor_build.read_text(encoding="utf-8")
        for text in [
            "HyperFormula.buildFromSheets",
            "getRegisteredFunctionNames",
            "__MAIN_COMPUTER_LOCAL_COMPAT__",
            "A1+B1",
        ]:
            with self.subTest(text=text):
                self.assertIn(text, source)
        self.assertIn("local fallback", readme.read_text(encoding="utf-8"))

    @unittest.skipUnless(shutil.which("node"), "node is required for local HyperFormula vendor checks")
    def test_spreadsheet_hyperformula_vendor_build_calculates_basic_formulas(self) -> None:
        vendor_path = Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "vendor" / "hyperformula" / "hyperformula.full.min.js"
        formula_path = Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "scripts" / "spreadsheet-formula-engine.js"
        script = f"""
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");
const context = {{
  console,
  globalThis: null,
  window: null,
  SPREADSHEET_HYPERFORMULA_LICENSE_KEY: "gpl-v3",
}};
context.globalThis = context;
context.window = context;
vm.createContext(context);
vm.runInContext(`
function spreadsheetColumnIndex(name) {{
  return String(name || "A").toUpperCase().split("").reduce((total, char) => total * 26 + char.charCodeAt(0) - 64, 0);
}}
function spreadsheetCellParts(ref) {{
  const match = String(ref || "A1").toUpperCase().match(/^([A-Z]+)([1-9][0-9]*)$/);
  return match ? {{col: spreadsheetColumnIndex(match[1]), row: Number(match[2])}} : {{col: 1, row: 1}};
}}
function spreadsheetCellRef(row, col) {{
  let name = "";
  let value = Number(col) || 1;
  while (value > 0) {{
    const remainder = (value - 1) % 26;
    name = String.fromCharCode(65 + remainder) + name;
    value = Math.floor((value - 1) / 26);
  }}
  return (name || "A") + (Number(row) || 1);
}}
`, context);
vm.runInContext(fs.readFileSync({json.dumps(str(vendor_path))}, "utf8"), context);
vm.runInContext(fs.readFileSync({json.dumps(str(formula_path))}, "utf8"), context);
const functions = context.HyperFormula.getRegisteredFunctionNames();
assert.ok(functions.includes("SUM"));
assert.ok(functions.includes("AVERAGE"));
const workbook = {{
  version: 1,
  active_sheet: "Sheet1",
  sheets: {{
    Sheet1: {{
      rows: 2,
      cols: 4,
      cells: {{
        A1: {{value: "2"}},
        B1: {{value: "3"}},
        C1: {{kind: "formula", language: "none", source: "=A1+B1", value: ""}},
        D1: {{kind: "formula", language: "none", source: "=SUM(A1:B1)", value: ""}},
      }},
    }},
  }},
}};
const result = context.spreadsheetRecalculateFormulas(workbook, {{HyperFormula: context.HyperFormula}});
assert.strictEqual(result.ok, true);
assert.strictEqual(result.cache["Sheet1!C1"].value, "5");
assert.strictEqual(result.cache["Sheet1!D1"].value, "5");
console.log("ok");
"""
        result = subprocess.run(["node", "-e", script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)

    def test_spreadsheet_formula_engine_include_order_is_not_trapped_in_open_spreadsheet(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "main_computer"
            / "web"
            / "applications.html"
        ).read_text(encoding="utf-8")
        revogrid = source.index("spreadsheet-revogrid-loader.js")
        hyperformula = source.index("spreadsheet-hyperformula-loader.js")
        formula = source.index("spreadsheet-formula-engine.js")
        core = source.index("spreadsheet-core.js")
        render = source.index("spreadsheet-render.js")
        runtime = source.index("spreadsheet-code-runtime.js")

        self.assertLess(revogrid, hyperformula)
        self.assertLess(hyperformula, formula)
        self.assertLess(formula, core)
        self.assertLess(core, render)
        self.assertLess(render, runtime)
        self.assertFalse(
            core < formula < render,
            "spreadsheet-formula-engine.js must not be included between "
            "spreadsheet-core.js and spreadsheet-render.js because those files "
            "split the openSpreadsheet() function body.",
        )

    def test_spreadsheet_formula_engine_adapter_contract_is_safe(self) -> None:
        formula_source = (Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "scripts" / "spreadsheet-formula-engine.js").read_text(encoding="utf-8")
        for text in [
            'const SPREADSHEET_FORMULA_ENGINE_ID = "hyperformula"',
            'const SPREADSHEET_FORMULA_CELL_KIND = "formula"',
            'const SPREADSHEET_FORMULA_CODE_KINDS = new Set(["javascript", "python", "basic"])',
            "function spreadsheetIsFormulaSource",
            "function spreadsheetNormalizeFormulaCell",
            "function spreadsheetWorkbookToHyperFormulaSheets",
            "function spreadsheetRecalculateFormulas",
            "function spreadsheetFormulaDisplayValue",
            "function spreadsheetFormulaRawSource",
            "function spreadsheetFormulaOutputParts",
            "HyperFormulaClass.buildFromSheets",
            "licenseKey: typeof SPREADSHEET_HYPERFORMULA_LICENSE_KEY",
            "source.value ?? \"\"",
        ]:
            with self.subTest(text=text):
                self.assertIn(text, formula_source)
        self.assertNotIn("runSpreadsheetWorker(", formula_source)
        self.assertNotIn("spreadsheetStageAiRangePromptInChat(", formula_source)

    def test_spreadsheet_formula_lifecycle_recalculates_before_persistence_and_write_apply(self) -> None:
        render_source = (Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "scripts" / "spreadsheet-render.js").read_text(encoding="utf-8")
        runtime_source = (Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "scripts" / "spreadsheet-code-runtime.js").read_text(encoding="utf-8")
        for text in [
            "async function spreadsheetRecalculateBeforePersistence()",
            "await spreadsheetRecalculateBeforePersistence();",
            'spreadsheetApi("/api/applications/spreadsheet/write"',
            'spreadsheetApi("/api/applications/spreadsheet/export-xlsx"',
        ]:
            with self.subTest(text=text):
                self.assertIn(text, render_source)
        save_index = render_source.index("async function saveSpreadsheet")
        save_recalc_index = render_source.index("await spreadsheetRecalculateBeforePersistence();", save_index)
        save_api_index = render_source.index('spreadsheetApi("/api/applications/spreadsheet/write"', save_index)
        self.assertLess(save_recalc_index, save_api_index)
        export_index = render_source.index("async function exportSpreadsheetXlsx")
        export_recalc_index = render_source.index("await spreadsheetRecalculateBeforePersistence();", export_index)
        export_api_index = render_source.index('spreadsheetApi("/api/applications/spreadsheet/export-xlsx"', export_index)
        self.assertLess(export_recalc_index, export_api_index)
        self.assertIn('spreadsheetRefreshFormulaResults({preserveSelection: true});\n      renderDiskSpreadsheet();', runtime_source)

    def test_spreadsheet_formula_cells_are_wired_into_grid_and_inspector(self) -> None:
        self.assertIn('<option value="formula">Formula</option>', APPLICATIONS_INDEX_HTML)
        expected = [
            "function spreadsheetActiveSheetName()",
            'function spreadsheetGridCellDisplayValue(cell, sheetName = spreadsheetActiveSheetName(), ref = "")',
            "spreadsheetFormulaDisplayValue(normalized, sheetName, ref)",
            "function spreadsheetPlainValueCell",
            "spreadsheetFormulaCellFromEditValue(value, existing)",
            "sheet.cells[ref] = spreadsheetPlainValueCell(value)",
            "function spreadsheetRecalculateFormulaCache()",
            "function spreadsheetRefreshFormulaResults(options = {})",
            "spreadsheetEnsureHyperFormulaLoaded().then",
            'spreadsheetCellType.value = isFormula ? "formula"',
            "spreadsheetCellSource.disabled = !(isCode || isFormula)",
            "spreadsheetRunCell.disabled = !isCode || !ref",
            "spreadsheetFormulaStatusText(cell, sheetName, ref)",
            "spreadsheetFormulaOutputParts(cell, sheetName, ref)",
            'spreadsheetSetDirty(true, "formula source changed")',
        ]
        for text in expected:
            with self.subTest(text=text):
                self.assertIn(text, APPLICATIONS_INDEX_HTML)


    @unittest.skipUnless(shutil.which("node"), "node is required for formula adapter execution checks")
    def test_spreadsheet_formula_engine_calculates_with_stubbed_hyperformula_without_mutating_workbook(self) -> None:
        formula_path = Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "scripts" / "spreadsheet-formula-engine.js"
        script = f"""
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");
const context = {{
  console,
  SPREADSHEET_HYPERFORMULA_LICENSE_KEY: "gpl-v3",
}};
vm.createContext(context);
vm.runInContext(`
function spreadsheetColumnIndex(name) {{
  return String(name || "A").toUpperCase().split("").reduce((total, char) => total * 26 + char.charCodeAt(0) - 64, 0);
}}
function spreadsheetCellParts(ref) {{
  const match = String(ref || "A1").toUpperCase().match(/^([A-Z]+)([1-9][0-9]*)$/);
  return match ? {{col: spreadsheetColumnIndex(match[1]), row: Number(match[2])}} : {{col: 1, row: 1}};
}}
function spreadsheetCellRef(row, col) {{
  let name = "";
  let value = Number(col) || 1;
  while (value > 0) {{
    const remainder = (value - 1) % 26;
    name = String.fromCharCode(65 + remainder) + name;
    value = Math.floor((value - 1) / 26);
  }}
  return (name || "A") + (Number(row) || 1);
}}
`, context);
vm.runInContext(fs.readFileSync({json.dumps(str(formula_path))}, "utf8"), context);

class FakeHyperFormula {{
  constructor(sheets) {{
    this.sheets = sheets;
  }}
  static buildFromSheets(sheets, options) {{
    FakeHyperFormula.lastSheets = sheets;
    FakeHyperFormula.lastOptions = options;
    return new FakeHyperFormula(sheets);
  }}
  getSheetId(sheetName) {{
    return sheetName;
  }}
  getCellValue(address) {{
    const sheet = this.sheets[address.sheet];
    const formula = sheet[address.row][address.col];
    const numberAt = (ref) => {{
      const parts = context.spreadsheetFormulaCellParts(ref);
      return Number(sheet[parts.row - 1][parts.col - 1]) || 0;
    }};
    if (formula === "=A1+B1") return numberAt("A1") + numberAt("B1");
    if (formula === "=SUM(A1:A3)") return numberAt("A1") + numberAt("A2") + numberAt("A3");
    return {{type: "VALUE", message: "unsupported formula"}};
  }}
  destroy() {{
    FakeHyperFormula.destroyed = true;
  }}
}}

const workbook = {{
  version: 1,
  active_sheet: "Sheet1",
  sheets: {{
    Sheet1: {{
      rows: 3,
      cols: 4,
      cells: {{
        A1: {{value: "1"}},
        B1: {{value: "2"}},
        C1: {{kind: "formula", language: "none", source: "=A1+B1", value: ""}},
        A2: {{value: "4"}},
        A3: {{value: "5"}},
        B2: {{kind: "javascript", language: "javascript", source: "throw new Error('should not run')", value: "cached"}},
        C2: {{kind: "formula", language: "none", source: "=SUM(A1:A3)", value: ""}},
        D1: {{kind: "formula", language: "none", source: "=NOPE()", value: ""}},
      }},
    }},
  }},
}};
const before = JSON.stringify(workbook);
const result = context.spreadsheetRecalculateFormulas(workbook, {{HyperFormula: FakeHyperFormula}});
assert.strictEqual(result.ok, true);
assert.strictEqual(result.formulaCount, 3);
assert.strictEqual(FakeHyperFormula.lastOptions.licenseKey, "gpl-v3");
assert.strictEqual(FakeHyperFormula.lastSheets.Sheet1[1][1], "cached");
assert.ok(!JSON.stringify(FakeHyperFormula.lastSheets).includes("should not run"));
assert.strictEqual(result.cache["Sheet1!C1"].value, "3");
assert.strictEqual(result.cache["Sheet1!C2"].value, "10");
assert.strictEqual(context.spreadsheetFormulaDisplayValue(workbook.sheets.Sheet1.cells.C1, "Sheet1", "C1", result.cache), "3");
assert.strictEqual(context.spreadsheetFormulaRawSource(workbook.sheets.Sheet1.cells.C1), "=A1+B1");
assert.deepStrictEqual(context.spreadsheetNormalizeFormulaCell({{value: "=A1+B1"}}).kind, "formula");
assert.strictEqual(context.spreadsheetFormulaCellFromEditValue("=A1+B1").kind, "formula");
assert.strictEqual(context.spreadsheetFormulaCellFromEditValue("plain value"), null);
assert.ok(context.spreadsheetFormulaStatusText(workbook.sheets.Sheet1.cells.C1, "Sheet1", "C1", result.cache).includes("=A1+B1 = 3"));
assert.ok(context.spreadsheetFormulaStatusText(workbook.sheets.Sheet1.cells.D1, "Sheet1", "D1", result.cache).includes("formula error"));
const errorParts = context.spreadsheetFormulaOutputParts(workbook.sheets.Sheet1.cells.D1, "Sheet1", "D1", result.cache);
assert.strictEqual(errorParts[0].kind, "error");
assert.strictEqual(errorParts[0].title, "Formula error");
assert.strictEqual(JSON.stringify(workbook), before);
assert.strictEqual(FakeHyperFormula.destroyed, true);
console.log("ok");
"""
        result = subprocess.run(["node", "-e", script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)

    def test_spreadsheet_revogrid_vendor_asset_route_serves_same_origin_build(self) -> None:
        request = Request(f"{self.base}/applications/vendor/revogrid/revo-grid.esm.js", method="GET")
        with urlopen(request, timeout=5) as response:
            content = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("bootstrapLazy", content)
            self.assertIn("revo-grid", content)

    def test_spreadsheet_hyperformula_vendor_asset_route_serves_same_origin_build(self) -> None:
        request = Request(f"{self.base}/applications/vendor/hyperformula/hyperformula.full.min.js", method="GET")
        with urlopen(request, timeout=5) as response:
            content = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("HyperFormula.buildFromSheets", content)
            self.assertIn("getRegisteredFunctionNames", content)

    def test_spreadsheet_browser_smoke_page_serves_local_vendor_checks(self) -> None:
        request = Request(f"{self.base}/applications/spreadsheet/smoke", method="GET")
        with urlopen(request, timeout=5) as response:
            content = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("Spreadsheet browser smoke", content)
            self.assertIn("/applications/vendor/hyperformula/hyperformula.full.min.js", content)
            self.assertIn("=A1+B1 calculates", content)
            self.assertIn("formula errors are visible", content)
            self.assertIn("XLSX export button exists", content)
            self.assertIn("code cell execution runtime exists", content)
            self.assertIn("RevoGrid loads", content)

    def test_spreadsheet_worker_sources_are_expanded_into_applications_page(self) -> None:
        self.assertIn('id="spreadsheet-js-worker-source"', APPLICATIONS_INDEX_HTML)
        self.assertIn("self.onmessage", APPLICATIONS_INDEX_HTML)
        self.assertIn("Spreadsheet JavaScript code-cell worker", APPLICATIONS_INDEX_HTML)
        self.assertNotIn("@include applications/scripts/spreadsheet-js-worker.js", APPLICATIONS_INDEX_HTML)

    def test_spreadsheet_worker_source_validation_hooks_exist(self) -> None:
        self.assertIn("Spreadsheet worker source was not expanded by the viewport include system.", APPLICATIONS_INDEX_HTML)
        self.assertIn("Spreadsheet worker source is invalid: missing self.onmessage.", APPLICATIONS_INDEX_HTML)
        self.assertIn('source.includes("@include applications/scripts/")', APPLICATIONS_INDEX_HTML)
        self.assertIn('source.includes("self.onmessage")', APPLICATIONS_INDEX_HTML)

    @unittest.skipUnless(shutil.which("node"), "node is required for worker source execution checks")
    def test_javascript_worker_supports_expression_and_return_cells(self) -> None:
        worker_path = Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "scripts" / "spreadsheet-js-worker.js"
        script = f"""
const {{ Worker }} = require("worker_threads");
const path = {json.dumps(str(worker_path))};
function runCell(source) {{
  return new Promise((resolve, reject) => {{
    const worker = new Worker(`
      const {{ parentPort }} = require("worker_threads");
      global.self = {{
        onmessage: null,
        postMessage: (message) => parentPort.postMessage(message)
      }};
      require(${{JSON.stringify(path)}});
      parentPort.on("message", (message) => self.onmessage({{data: message}}));
    `, {{eval: true}});
    const timer = setTimeout(() => {{
      worker.terminate();
      reject(new Error("timeout"));
    }}, 2000);
    worker.on("message", (message) => {{
      clearTimeout(timer);
      worker.terminate();
      resolve(message);
    }});
    worker.on("error", reject);
    worker.postMessage({{
      id: "test",
      language: "javascript",
      source,
      workbook_snapshot: {{sheets: {{Sheet1: {{cells: {{A1: {{value: "21"}}}}}}}}}},
      active_sheet: "Sheet1",
      cell_ref: "B1",
      timeout_ms: 2000
    }});
  }});
}}
(async () => {{
  const expression = await runCell("1+1");
  const returned = await runCell("const x = sheet.getNumber(\\\"A1\\\");\\nreturn x * 2;");
  console.log(JSON.stringify({{expression, returned}}));
}})().catch((error) => {{
  console.error(error && error.stack || error);
  process.exit(1);
}});
"""
        result = subprocess.run(["node", "-e", script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["expression"]["value"], 2)
        self.assertEqual(payload["returned"]["value"], 42)
        self.assertEqual(payload["returned"]["dependencies"], ["A1"])

    def test_python_and_basic_workers_use_real_browser_runtimes(self) -> None:
        worker_root = Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "scripts"
        python_source = (worker_root / "spreadsheet-python-worker.js").read_text(encoding="utf-8")
        basic_source = (worker_root / "spreadsheet-basic-worker.js").read_text(encoding="utf-8")

        self.assertIn("self.onmessage", python_source)
        self.assertIn("pyodide.js", python_source)
        self.assertIn("loadPyodide", python_source)
        self.assertIn("pyodide.setStdout", python_source)
        self.assertIn("pyodide.setStderr", python_source)
        self.assertIn("sheet = Sheet()", python_source)
        self.assertIn("spreadsheet = sheet", python_source)
        self.assertIn("def attach(self, target, source_or_value=None, fn=None):", python_source)
        self.assertIn("spreadsheet_write", python_source)
        self.assertNotIn("not implemented yet", python_source)

        self.assertIn("self.onmessage", basic_source)
        self.assertIn("wwwbasic.js", basic_source)
        self.assertIn("runtime.Basic", basic_source)
        self.assertIn("PutCh", basic_source)
        self.assertIn("PRINT", basic_source)
        self.assertIn("SPREADSHEET_ATTACH", basic_source)
        self.assertIn("CELL_EVAL", basic_source)
        self.assertIn("GETCELL", basic_source)
        self.assertNotIn("not implemented yet", basic_source)

    def test_spreadsheet_runtime_uses_language_specific_timeouts_and_diagnostics(self) -> None:
        runtime_source = (Path(__file__).resolve().parents[1] / "main_computer" / "web" / "applications" / "scripts" / "spreadsheet-code-runtime.js").read_text(encoding="utf-8")
        for text in [
            "function getSpreadsheetRuntimeTimeout(language)",
            'if (language === "python") return 60000;',
            'if (language === "basic") return 10000;',
            'return 2000;',
            'const testSource = language === "python"',
            '"1 + 1"',
            '"PRINT 1+1"',
            "window.spreadsheetWorkerSourceSummary",
            "window.testSpreadsheetWorker",
        ]:
            with self.subTest(text=text):
                self.assertIn(text, runtime_source)

    def test_spreadsheet_routes_still_target_app(self) -> None:
        self.assertEqual(_application_route_target("/applications/spreadsheet"), "spreadsheet")
        self.assertEqual(_application_route_target("/apps/spreadsheet"), "spreadsheet")
        self.assertEqual(_application_route_target("/app/spreadsheet"), "spreadsheet")


if __name__ == "__main__":
    unittest.main()
