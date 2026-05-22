from __future__ import annotations

import json
from pathlib import Path

from main_computer.rag_ahbe_smoke import (
    AHBE_REFERENCE_DOC,
    AHBE_RELATIVE_PATH,
    build_expected_answer,
    parse_args,
    parse_ahbe_document,
    run_ahbe_rag_smoke,
)


def test_parse_ahbe_document_extracts_records_fields_and_refs() -> None:
    document = parse_ahbe_document(AHBE_REFERENCE_DOC)
    records = document.by_id()

    assert document.attrs["id"] == "rag-smoke-001"
    assert records["orbix"].kind == "entity"
    assert records["orbix"].fields["owner"] == "Mira"
    assert records["velin"].fields["floor"] == 7
    assert records["e17"].fields["actor"] == {"ref": "orbix"}
    assert records["e17"].fields["place"] == {"ref": "velin"}
    assert records["q1"].fields["answer"] == "Only Mira may unlock Orbix after sunset."
    assert build_expected_answer(document) == (
        "Orbix is owned by Mira. It was stored in the Velin Archive on floor 7. "
        "Access to the Velin Archive requires a brass token."
    )


def test_ahbe_rag_smoke_sets_up_fixture_and_retrieves_seed(tmp_path: Path) -> None:
    report = run_ahbe_rag_smoke(
        repo_dir=tmp_path,
        output_root=tmp_path / "rag_runs",
        run_id="ahbe_smoke_test",
        verbose=False,
    )

    assert report.ok
    assert AHBE_RELATIVE_PATH in report.retrieved_paths
    assert AHBE_RELATIVE_PATH in report.chunk_paths
    assert "Orbix is owned by Mira" in report.expected_answer
    assert Path(report.ahbe_path).exists()
    assert Path(report.report_path).exists()

    saved = json.loads(Path(report.report_path).read_text(encoding="utf-8"))
    assert saved["ok"] is True
    assert saved["retrieved_paths"][0] == AHBE_RELATIVE_PATH
    assert saved["parsed_document"]["records"]


def test_parse_args_accepts_quiet_strict_and_run_id() -> None:
    args = parse_args(["--run-id", "fixed", "--strict", "--quiet"])

    assert args.run_id == "fixed"
    assert args.strict is True
    assert args.quiet is True
