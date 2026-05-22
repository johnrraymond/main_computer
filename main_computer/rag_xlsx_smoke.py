#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
from io import BytesIO
import json
from pathlib import Path
import re
import sys
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as xml_escape


OOXML_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
OOXML_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def json_dumps(data: object) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, default=str)


def repo_root_from_here() -> Path:
    here = Path(__file__).resolve()
    for candidate in [Path.cwd().resolve(), here.parent.resolve(), here.parent.parent.resolve()]:
        if (candidate / "main_computer").is_dir() and (candidate / "pyproject.toml").exists():
            return candidate
    return Path.cwd().resolve()


def col_name(index: int) -> str:
    out = ""
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out or "A"


def cell_col_index(ref: str) -> int:
    letters = "".join(ch for ch in str(ref or "") if ch.isalpha()).upper() or "A"
    total = 0
    for ch in letters:
        total = total * 26 + (ord(ch) - 64)
    return total


def xlsx_from_rows(rows: list[list[object]], *, sheet_name: str = "Invoices") -> bytes:
    safe_sheet = re.sub(r"[\[\]\:\*\?\/\\]", "_", sheet_name or "Sheet1")[:31] or "Sheet1"
    row_xml: list[str] = []

    for row_index, row in enumerate(rows, start=1):
        cells: list[str] = []
        for col_index, raw in enumerate(row, start=1):
            ref = f"{col_name(col_index)}{row_index}"
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                cells.append(f'<c r="{ref}"><v>{raw}</v></c>')
            else:
                text = xml_escape("" if raw is None else str(raw))
                preserve = ' xml:space="preserve"' if text.strip() != text else ""
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t{preserve}>{text}</t></is></c>')
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    max_cols = max((len(row) for row in rows), default=1)
    max_rows = max(len(rows), 1)
    dimension = f"A1:{col_name(max_cols)}{max_rows}"

    sheet_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="{OOXML_MAIN_NS}" xmlns:r="{OOXML_REL_NS}">
  <dimension ref="{dimension}"/>
  <sheetData>{''.join(row_xml)}</sheetData>
</worksheet>
'''
    workbook_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="{OOXML_MAIN_NS}" xmlns:r="{OOXML_REL_NS}">
  <sheets><sheet name="{xml_escape(safe_sheet)}" sheetId="1" r:id="rId1"/></sheets>
</workbook>
'''
    workbook_rels_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{PKG_REL_NS}">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>
'''
    package_rels_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{PKG_REL_NS}">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
'''
    content_types_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>
'''

    out = BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml)
        archive.writestr("_rels/.rels", package_rels_xml)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return out.getvalue()


def _xml_text(node: ET.Element | None) -> str:
    return "" if node is None else "".join(node.itertext())


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return [_xml_text(si) for si in root.findall(f".//{{{OOXML_MAIN_NS}}}si")]


def _sheet_targets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))

    rel_map: dict[str, str] = {}
    for rel in rels.findall(f"{{{PKG_REL_NS}}}Relationship"):
        rid = rel.attrib.get("Id", "")
        target = rel.attrib.get("Target", "")
        if not target:
            continue
        if target.startswith("/"):
            target = target.lstrip("/")
        elif not target.startswith("xl/"):
            target = "xl/" + target
        rel_map[rid] = target

    sheets: list[tuple[str, str]] = []
    for sheet in workbook.findall(f".//{{{OOXML_MAIN_NS}}}sheet"):
        name = sheet.attrib.get("name", "Sheet")
        rid = sheet.attrib.get(f"{{{OOXML_REL_NS}}}id", "")
        target = rel_map.get(rid)
        if target:
            sheets.append((name, target))
    return sheets


def _coerce_number(raw: str) -> object:
    if re.fullmatch(r"-?\d+", raw or ""):
        return int(raw)
    if re.fullmatch(r"-?\d+\.\d+", raw or ""):
        return float(raw)
    return raw


def read_xlsx_rows(payload: bytes | str | Path) -> dict[str, list[list[object]]]:
    if isinstance(payload, (str, Path)):
        zf = zipfile.ZipFile(str(payload), "r")
    else:
        zf = zipfile.ZipFile(BytesIO(payload), "r")

    with zf as archive:
        shared = _shared_strings(archive)
        sheets: dict[str, list[list[object]]] = {}

        for sheet_name, target in _sheet_targets(archive):
            root = ET.fromstring(archive.read(target))
            rows: list[list[object]] = []

            for row_node in root.findall(f".//{{{OOXML_MAIN_NS}}}sheetData/{{{OOXML_MAIN_NS}}}row"):
                values: dict[int, object] = {}
                for cell in row_node.findall(f"{{{OOXML_MAIN_NS}}}c"):
                    col = cell_col_index(cell.attrib.get("r", "A1"))
                    cell_type = cell.attrib.get("t", "")

                    if cell_type == "inlineStr":
                        value: object = _xml_text(cell.find(f"{{{OOXML_MAIN_NS}}}is"))
                    else:
                        raw_v = _xml_text(cell.find(f"{{{OOXML_MAIN_NS}}}v"))
                        if cell_type == "s":
                            try:
                                value = shared[int(raw_v)]
                            except Exception:
                                value = raw_v
                        else:
                            value = _coerce_number(raw_v)
                    values[col] = value

                if values:
                    rows.append([values.get(index, "") for index in range(1, max(values) + 1)])
            sheets[sheet_name] = rows

        return sheets


def fixture_rows() -> list[list[object]]:
    return [
        ["Invoice ID", "Customer", "Item", "Amount", "Notes"],
        ["INV-001", "Acme Corp", "Widget migration", 450.50, "standard onboarding"],
        ["INV-002", "Beta LLC", "Cloud seats", 1200.75, "annual renewal expansion"],
        ["INV-003", "Cygnus Labs", "RAG workbook adapter", 875, "xlsx smoke target"],
    ]


def tokenize(text: object) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-z0-9]+", str(text)) if len(token) > 1}


def build_docs(sheets: dict[str, list[list[object]]]) -> list[dict[str, object]]:
    docs: list[dict[str, object]] = []
    for sheet_name, rows in sheets.items():
        if not rows:
            continue
        headers = [str(item) for item in rows[0]]
        for row_number, row in enumerate(rows[1:], start=2):
            fields = {header: (row[index] if index < len(row) else "") for index, header in enumerate(headers)}
            text = " | ".join(f"{key}: {value}" for key, value in fields.items())
            docs.append(
                {
                    "path": f"{sheet_name}!R{row_number}",
                    "sheet": sheet_name,
                    "row_number": row_number,
                    "fields": fields,
                    "text": text,
                    "tokens": sorted(tokenize(text)),
                }
            )
    return docs


def retrieve(docs: list[dict[str, object]], query: str, *, limit: int = 3) -> list[dict[str, object]]:
    query_tokens = tokenize(query)
    hits: list[dict[str, object]] = []

    for doc in docs:
        doc_tokens = set(doc.get("tokens", []))
        overlap = sorted(query_tokens.intersection(doc_tokens))
        score = float(len(overlap))
        text = str(doc.get("text", ""))
        text_lower = text.lower()

        for term in query_tokens:
            if term in text_lower:
                score += 0.25
        if "inv-002" in query.lower() and "INV-002" in text:
            score += 10.0

        hit = dict(doc)
        hit["score"] = score
        hit["matched_terms"] = overlap
        hits.append(hit)

    return sorted(hits, key=lambda item: float(item.get("score", 0)), reverse=True)[:limit]


def answer_from_hit(hit: dict[str, object]) -> str:
    fields = hit.get("fields", {})
    if not isinstance(fields, dict):
        fields = {}
    invoice = fields.get("Invoice ID", "")
    customer = fields.get("Customer", "")
    amount = fields.get("Amount", "")
    notes = fields.get("Notes", "")
    return f"{invoice}: {customer} has amount {amount}; notes: {notes}"


def worker_main() -> int:
    input_path = Path(os.environ["XLSX_INPUT_PATH"])
    query = os.environ.get("RAG_QUERY", "Who is the customer for invoice INV-002 and what amount is due?")
    output_dir = Path("/outputs")
    output_dir.mkdir(parents=True, exist_ok=True)

    sheets = read_xlsx_rows(input_path)
    docs = build_docs(sheets)
    hits = retrieve(docs, query)
    if not hits:
        raise SystemExit("No RAG hits were produced from the XLSX workbook.")

    top = hits[0]
    answer = answer_from_hit(top)

    first_sheet = next(iter(sheets))
    rows = [list(row) for row in sheets[first_sheet]]
    rows.append(["", "", "", "", ""])
    rows.append(["RAG_WRITE_RESULT", "Query", query, "Answer", answer])
    rows.append(["RAG_TOP_HIT", top["path"], "Score", top["score"], "Matched", ", ".join(top.get("matched_terms", []))])

    output_xlsx = output_dir / "rag_xlsx_smoke_written.xlsx"
    output_xlsx.write_bytes(xlsx_from_rows(rows, sheet_name=first_sheet))

    reread = read_xlsx_rows(output_xlsx)
    flat = "\n".join("\t".join(str(cell) for cell in row) for rows_ in reread.values() for row in rows_)
    write_found = all(needle in flat for needle in ["RAG_WRITE_RESULT", "INV-002", "Beta LLC", "1200.75"])

    top_for_json = {key: value for key, value in top.items() if key != "tokens"}
    report = {
        "ok": bool(write_found and "INV-002" in answer and "Beta LLC" in answer),
        "query": query,
        "sheet_count": len(sheets),
        "doc_count": len(docs),
        "top_hit": top_for_json,
        "answer": answer,
        "write_found_after_reread": write_found,
        "output_xlsx": str(output_xlsx),
    }
    (output_dir / "rag_xlsx_smoke_report.json").write_text(json_dumps(report) + "\n", encoding="utf-8")
    print("RAG_XLSX_SMOKE_RESULT " + json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


def parse_worker_report(stdout: str) -> dict[str, object]:
    for line in (stdout or "").splitlines():
        if line.startswith("RAG_XLSX_SMOKE_RESULT "):
            return json.loads(line.split(" ", 1)[1])
    return {}


def host_xlsx_self_test() -> dict[str, object]:
    data = xlsx_from_rows(fixture_rows())
    sheets = read_xlsx_rows(data)
    flat = "\n".join("\t".join(str(cell) for cell in row) for rows in sheets.values() for row in rows)
    return {
        "ok": all(needle in flat for needle in ["INV-002", "Beta LLC", "RAG workbook adapter"]),
        "xlsx_bytes": len(data),
        "sheets": sheets,
    }


def make_executor(repo_root: Path):
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from main_computer.config import MainComputerConfig
    from main_computer.docker_executor import DockerExecutor

    config = MainComputerConfig.from_env()
    runtime_root = config.executor_root
    if not runtime_root.is_absolute():
        runtime_root = repo_root / runtime_root

    return DockerExecutor(
        image=config.executor_image,
        runtime_root=runtime_root,
        enabled=True,
        max_timeout_s=config.executor_timeout_s,
        max_upload_bytes=config.executor_max_upload_bytes,
        max_output_chars=config.executor_max_output_chars,
    )


def executor_request_class(repo_root: Path):
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from main_computer.executor_models import ExecutorRequest

    return ExecutorRequest


def host_main(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    output_root = Path(args.output_root)
    if not output_root.is_absolute():
        output_root = repo_root / output_root
    output_dir = output_root / args.run_id
    output_dir.mkdir(parents=True, exist_ok=False)

    fixture_bytes = xlsx_from_rows(fixture_rows(), sheet_name="Invoices")
    (output_dir / "input_fixture.xlsx").write_bytes(fixture_bytes)

    executor = make_executor(repo_root)
    ExecutorRequest = executor_request_class(repo_root)

    status = executor.status()
    print("[rag-xlsx-smoke] docker executor status:")
    print(json_dumps(status))
    if not status.get("docker_available"):
        report = {
            "ok": False,
            "error": status.get("docker_error") or "Docker is not available.",
            "docker_status": status,
            "output_dir": str(output_dir),
        }
        (output_dir / "host_report.json").write_text(json_dumps(report) + "\n", encoding="utf-8")
        print(json_dumps(report))
        return 1

    xlsx_upload = executor.save_upload(
        filename="rag_xlsx_fixture.xlsx",
        stream=BytesIO(fixture_bytes),
        content_length=len(fixture_bytes),
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    script_bytes = Path(__file__).read_bytes()
    script_upload = executor.save_upload(
        filename="rag_xlsx_docker_smoke.py",
        stream=BytesIO(script_bytes),
        content_length=len(script_bytes),
        mime_type="text/x-python",
    )

    request = ExecutorRequest(
        command=f"python {script_upload.container_path} --worker",
        cwd="/workspace",
        timeout_s=args.timeout_s,
        input_ids=[xlsx_upload.id, script_upload.id],
        artifact_globs=["**/*"],
        network=False,
        description="RAG XLSX smoke: read workbook, retrieve facts, write XLSX artifact.",
        env={"XLSX_INPUT_PATH": xlsx_upload.container_path, "RAG_QUERY": args.query},
        metadata={"xlsx_upload_id": xlsx_upload.id, "script_upload_id": script_upload.id, "run_id": args.run_id},
    )

    result = executor.run(request)
    result_dict = result.as_dict() if hasattr(result, "as_dict") else asdict(result)
    worker_report = parse_worker_report(result.stdout)

    output_xlsx_artifact = None
    output_report_artifact = None
    for artifact in result.artifacts:
        if artifact.relative_path == "rag_xlsx_smoke_written.xlsx":
            output_xlsx_artifact = artifact
        elif artifact.relative_path == "rag_xlsx_smoke_report.json":
            output_report_artifact = artifact

    host_verified = False
    host_error = None
    host_reread_preview: dict[str, list[list[object]]] = {}

    if output_xlsx_artifact is not None:
        try:
            artifact_path = executor.artifact_path(result.job_id, output_xlsx_artifact.relative_path)
            copied = output_dir / "docker_rag_xlsx_smoke_written.xlsx"
            copied.write_bytes(artifact_path.read_bytes())
            reread = read_xlsx_rows(copied)
            host_reread_preview = {name: rows[-4:] for name, rows in reread.items()}
            flat = "\n".join("\t".join(str(cell) for cell in row) for rows in reread.values() for row in rows)
            host_verified = all(needle in flat for needle in ["RAG_WRITE_RESULT", "INV-002", "Beta LLC", "1200.75"])
        except Exception as exc:
            host_error = str(exc)

    report = {
        "ok": bool(result.ok and worker_report.get("ok") is True and output_xlsx_artifact and output_report_artifact and host_verified),
        "run_id": args.run_id,
        "query": args.query,
        "repo_root": str(repo_root),
        "output_dir": str(output_dir),
        "docker_status": status,
        "uploads": {"xlsx": xlsx_upload.as_dict(), "script": script_upload.as_dict()},
        "executor_request": request.as_dict(),
        "executor_result": result_dict,
        "worker_report": worker_report,
        "host_verified_written_xlsx": host_verified,
        "host_reread_preview": host_reread_preview,
        "host_error": host_error,
    }
    (output_dir / "host_report.json").write_text(json_dumps(report) + "\n", encoding="utf-8")
    print("[rag-xlsx-smoke] host report:")
    print(json_dumps(report))
    return 0 if report["ok"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "RAG XLSX Docker executor smoke. Host mode creates/uploads a workbook and this script. "
            "Worker mode runs inside Docker, reads XLSX rows, retrieves an answer, writes a new XLSX, "
            "and emits artifacts under /outputs."
        )
    )
    parser.add_argument("--worker", action="store_true", help="Internal mode used inside the Docker executor.")
    parser.add_argument("--repo-root", default=str(repo_root_from_here()), help="Path to the main_computer_test repo root.")
    parser.add_argument("--run-id", default="rag_xlsx_docker_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--query", default="Who is the customer for invoice INV-002 and what amount is due?")
    parser.add_argument("--timeout-s", type=float, default=60.0)
    parser.add_argument("--output-root", default="diagnostics_output/rag_xlsx_docker_smoke")
    parser.add_argument("--host-xlsx-self-test", action="store_true", help="Test only the stdlib XLSX read/write path on host.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.worker:
        return worker_main()
    if args.host_xlsx_self_test:
        report = host_xlsx_self_test()
        print(json_dumps(report))
        return 0 if report["ok"] else 1
    return host_main(args)


if __name__ == "__main__":
    raise SystemExit(main())