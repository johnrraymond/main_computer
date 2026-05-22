from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from typing import Any

from main_computer.rag_retriever import DeterministicRagRetriever, RagRetrieverConfig


AHBE_REFERENCE_DOC = """~doc id=rag-smoke-001 title="Ahbe Seed"

#entity id=orbix kind=tool
name: "Orbix"
color: "blue"
owner: "Mira"
rule: "Only Mira may unlock Orbix after sunset."

#entity id=velin kind=place
name: "Velin Archive"
floor: 7
access: "requires a brass token"

#event id=e17 date=2042-03-18
actor: @orbix
place: @velin
summary: "Orbix was stored in the Velin Archive on floor 7."

#qa id=q1
ask: "Who may unlock Orbix after sunset?"
answer: "Only Mira may unlock Orbix after sunset."

#qa id=q2
ask: "Where was Orbix stored?"
answer: "Orbix was stored in the Velin Archive on floor 7."
"""


SMOKE_PROMPT = (
    "Using only the Ahbe document, answer: Who owns Orbix, where was it stored, "
    "and what is required to access that place?"
)

SMOKE_QUERIES = [
    "Orbix owner Mira",
    "Orbix stored Velin Archive floor 7",
    "Velin Archive brass token access",
    "Ahbe qa answer Orbix",
]

AHBE_RELATIVE_PATH = "knowledge/ahbe_seed.ahbe"


@dataclass(frozen=True)
class AhbeRecord:
    kind: str
    attrs: dict[str, Any]
    fields: dict[str, Any]
    line: int

    @property
    def record_id(self) -> str:
        return str(self.attrs.get("id", ""))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "attrs": self.attrs,
            "fields": self.fields,
            "line": self.line,
        }


@dataclass(frozen=True)
class AhbeDocument:
    attrs: dict[str, Any]
    records: list[AhbeRecord]

    def to_dict(self) -> dict[str, Any]:
        return {
            "attrs": self.attrs,
            "records": [record.to_dict() for record in self.records],
        }

    def by_id(self) -> dict[str, AhbeRecord]:
        return {
            record.record_id: record
            for record in self.records
            if record.record_id
        }


@dataclass(frozen=True)
class AhbeRagSmokeReport:
    ok: bool
    run_id: str
    scenario: str
    output_dir: str
    report_path: str
    corpus_dir: str
    ahbe_path: str
    prompt: str
    queries: list[str]
    retrieved_paths: list[str]
    chunk_paths: list[str]
    expected_answer: str
    parsed_document: dict[str, Any]
    retrieval: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    strict: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _default_run_id() -> str:
    return "rag_ahbe_" + datetime.now().strftime("%Y%m%d_%H%M%S")


def _json_dumps(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_dumps(data) + "\n", encoding="utf-8")


def _parse_value(raw: str) -> Any:
    text = raw.strip()
    if not text:
        raise ValueError("Ahbe value is empty")
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return bytes(text[1:-1], "utf-8").decode("unicode_escape")
    if text.startswith("@"):
        ref = text[1:].strip()
        if not ref:
            raise ValueError("Ahbe reference is missing an id")
        return {"ref": ref}
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


_ATTR_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*)=(\"(?:\\.|[^\"])*\"|[^ \t]+)")


def _parse_attrs(raw: str) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    index = 0
    while index < len(raw):
        while index < len(raw) and raw[index].isspace():
            index += 1
        if index >= len(raw):
            break
        match = _ATTR_RE.match(raw, index)
        if not match:
            raise ValueError(f"Invalid Ahbe attribute syntax near: {raw[index:]!r}")
        key = match.group(1)
        attrs[key] = _parse_value(match.group(2))
        index = match.end()
    return attrs


def parse_ahbe_document(text: str) -> AhbeDocument:
    """Parse the tiny Ahbe subset used by the smoke fixture.

    Supported syntax:
      * ~doc key=value...
      * #type key=value...
      * key: value fields under the most recent record
      * quoted strings, integers/floats, bare strings, and @id references
    """

    doc_attrs: dict[str, Any] | None = None
    records: list[AhbeRecord] = []
    current: AhbeRecord | None = None

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("~doc"):
            if doc_attrs is not None:
                raise ValueError(f"Line {line_number}: duplicate ~doc header")
            doc_attrs = _parse_attrs(line[len("~doc"):])
            continue

        if line.startswith("#"):
            rest = line[1:].strip()
            if not rest:
                raise ValueError(f"Line {line_number}: missing Ahbe record type")
            kind, _, attrs_text = rest.partition(" ")
            current = AhbeRecord(kind=kind, attrs=_parse_attrs(attrs_text), fields={}, line=line_number)
            records.append(current)
            continue

        if ":" in line:
            if current is None:
                raise ValueError(f"Line {line_number}: field appears before any record")
            key, value = line.split(":", 1)
            key = key.strip()
            if not key:
                raise ValueError(f"Line {line_number}: field key is empty")
            current.fields[key] = _parse_value(value)
            continue

        raise ValueError(f"Line {line_number}: unrecognized Ahbe syntax: {raw_line!r}")

    if doc_attrs is None:
        raise ValueError("Ahbe document is missing a ~doc header")
    return AhbeDocument(attrs=doc_attrs, records=records)


def _require_record(document: AhbeDocument, record_id: str) -> AhbeRecord:
    record = document.by_id().get(record_id)
    if record is None:
        raise ValueError(f"Ahbe record id not found: {record_id}")
    return record


def _ref_id(value: Any) -> str:
    if isinstance(value, dict) and isinstance(value.get("ref"), str):
        return value["ref"]
    raise ValueError(f"Expected Ahbe @id reference, got: {value!r}")


def build_expected_answer(document: AhbeDocument) -> str:
    orbix = _require_record(document, "orbix")
    event = _require_record(document, "e17")
    place = _require_record(document, _ref_id(event.fields["place"]))

    owner = str(orbix.fields["owner"])
    place_name = str(place.fields["name"])
    floor = place.fields["floor"]
    access = str(place.fields["access"])

    return (
        f"Orbix is owned by {owner}. It was stored in the {place_name} on floor {floor}. "
        f"Access to the {place_name} {access}."
    )


def validate_reference_document(document: AhbeDocument) -> list[str]:
    failures: list[str] = []
    by_id = document.by_id()

    for record_id in ("orbix", "velin", "e17", "q1", "q2"):
        if record_id not in by_id:
            failures.append(f"Missing required Ahbe record id={record_id}.")

    if failures:
        return failures

    orbix = by_id["orbix"]
    event = by_id["e17"]
    place = by_id["velin"]
    q1 = by_id["q1"]
    q2 = by_id["q2"]

    if orbix.kind != "entity" or orbix.attrs.get("kind") != "tool":
        failures.append("id=orbix must be an entity of kind=tool.")
    if place.kind != "entity" or place.attrs.get("kind") != "place":
        failures.append("id=velin must be an entity of kind=place.")
    if _ref_id(event.fields.get("actor")) != "orbix":
        failures.append("event e17 actor must reference @orbix.")
    if _ref_id(event.fields.get("place")) != "velin":
        failures.append("event e17 place must reference @velin.")
    if q1.fields.get("answer") != orbix.fields.get("rule"):
        failures.append("q1 answer must match the Orbix unlock rule.")
    if q2.fields.get("answer") != event.fields.get("summary"):
        failures.append("q2 answer must match the event summary.")
    if build_expected_answer(document) != (
        "Orbix is owned by Mira. It was stored in the Velin Archive on floor 7. "
        "Access to the Velin Archive requires a brass token."
    ):
        failures.append("Composed expected answer does not match the Ahbe seed facts.")

    return failures


def write_fixture_corpus(corpus_dir: Path) -> Path:
    ahbe_path = corpus_dir / AHBE_RELATIVE_PATH
    ahbe_path.parent.mkdir(parents=True, exist_ok=True)
    ahbe_path.write_text(AHBE_REFERENCE_DOC, encoding="utf-8")

    decoy_path = corpus_dir / "knowledge" / "decoy.txt"
    decoy_path.write_text(
        "Decoy note: a red tool named Obrex is in a basement and has no brass token rule.\n",
        encoding="utf-8",
    )
    return ahbe_path


def run_ahbe_rag_smoke(
    *,
    repo_dir: Path,
    output_root: Path | None = None,
    run_id: str | None = None,
    strict: bool = True,
    verbose: bool = True,
) -> AhbeRagSmokeReport:
    run_id = run_id or _default_run_id()
    output_dir = (output_root or (repo_dir / "diagnostics_output" / "rag_runs")) / run_id
    output_dir.mkdir(parents=True, exist_ok=False)

    corpus_dir = output_dir / "corpus"
    ahbe_path = write_fixture_corpus(corpus_dir)

    warnings: list[str] = []
    failures: list[str] = []

    document = parse_ahbe_document(AHBE_REFERENCE_DOC)
    failures.extend(validate_reference_document(document))
    expected_answer = build_expected_answer(document)

    retriever = DeterministicRagRetriever(
        RagRetrieverConfig(
            repo_dir=corpus_dir,
            max_context_chars=12_000,
            max_candidates=8,
            max_chunks=4,
        )
    )
    retrieval = retriever.retrieve(SMOKE_QUERIES, extra_paths=[AHBE_RELATIVE_PATH])
    retrieved_paths = [candidate.path for candidate in retrieval.candidates]
    chunk_paths = [chunk.path for chunk in retrieval.chunks]
    context_text = "\n".join(chunk.content for chunk in retrieval.chunks)

    if AHBE_RELATIVE_PATH not in retrieved_paths:
        failures.append(f"Ahbe seed file was not selected as a retrieval candidate: {AHBE_RELATIVE_PATH}")
    if AHBE_RELATIVE_PATH not in chunk_paths:
        failures.append(f"Ahbe seed file did not produce a context chunk: {AHBE_RELATIVE_PATH}")

    required_needles = [
        "Orbix",
        "Mira",
        "Velin Archive",
        "floor 7",
        "requires a brass token",
        "Only Mira may unlock Orbix after sunset.",
    ]
    missing_needles = [needle for needle in required_needles if needle not in context_text]
    if missing_needles:
        failures.append("Retrieved context is missing expected Ahbe facts: " + ", ".join(missing_needles))

    if retrieval.used_chars > retrieval.context_budget_chars:
        failures.append(
            f"Retriever exceeded context budget: {retrieval.used_chars}/{retrieval.context_budget_chars} chars"
        )

    if len(retrieved_paths) > 1 and retrieved_paths[0] != AHBE_RELATIVE_PATH:
        warnings.append(
            f"Ahbe seed was retrieved but not ranked first; first candidate was {retrieved_paths[0]!r}."
        )

    if strict and warnings:
        failures.extend(warnings)

    _write_json(output_dir / "parsed_ahbe.json", document.to_dict())
    _write_json(output_dir / "retrieval.json", retrieval.as_dict())
    (output_dir / "expected_answer.txt").write_text(expected_answer + "\n", encoding="utf-8")

    report_path = output_dir / "ahbe_rag_smoke_report.json"
    report = AhbeRagSmokeReport(
        ok=not failures,
        run_id=run_id,
        scenario="ahbe_micro_language_rag_retrieval",
        output_dir=str(output_dir),
        report_path=str(report_path),
        corpus_dir=str(corpus_dir),
        ahbe_path=str(ahbe_path),
        prompt=SMOKE_PROMPT,
        queries=list(SMOKE_QUERIES),
        retrieved_paths=retrieved_paths,
        chunk_paths=chunk_paths,
        expected_answer=expected_answer,
        parsed_document=document.to_dict(),
        retrieval=retrieval.as_dict(),
        warnings=warnings,
        failures=failures,
        strict=strict,
    )
    _write_json(report_path, report.to_dict())

    if verbose:
        print("[rag-ahbe-smoke] validation report:")
        print(
            _json_dumps(
                {
                    "ok": report.ok,
                    "run_id": report.run_id,
                    "scenario": report.scenario,
                    "retrieved_paths": report.retrieved_paths,
                    "chunk_paths": report.chunk_paths,
                    "warnings": report.warnings,
                    "failures": report.failures,
                    "expected_answer": report.expected_answer,
                    "report_path": report.report_path,
                }
            )
        )

    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Ahbe micro-language RAG smoke test.")
    parser.add_argument(
        "--repo-dir",
        type=Path,
        default=Path.cwd(),
        help="Repository root used for diagnostics output. Defaults to current working directory.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Optional diagnostics output root. Defaults to diagnostics_output/rag_runs.",
    )
    parser.add_argument("--run-id", default=None, help="Optional deterministic run id.")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures.")
    parser.add_argument("--quiet", action="store_true", help="Suppress verbose smoke diagnostics.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_ahbe_rag_smoke(
        repo_dir=args.repo_dir,
        output_root=args.output_root,
        run_id=args.run_id,
        strict=args.strict,
        verbose=not args.quiet,
    )

    print(f"Scenario: {report.scenario}")
    print(f"Run: {report.run_id}")
    print(f"Output: {report.output_dir}")
    print(f"Corpus: {report.corpus_dir}")
    print(f"Ahbe seed: {report.ahbe_path}")
    print(f"Report: {report.report_path}")
    print(f"Retrieved paths: {report.retrieved_paths}")
    print(f"Expected answer: {report.expected_answer}")
    print(f"Status: {'passed' if report.ok else 'failed'}")
    if report.warnings:
        print("Warnings:")
        for warning in report.warnings:
            print(f"  - {warning}")
    if report.failures:
        print("Failures:")
        for failure in report.failures:
            print(f"  - {failure}")

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
