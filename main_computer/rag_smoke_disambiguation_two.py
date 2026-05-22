from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
from pathlib import Path
import random
import re
from typing import Any

from main_computer.config import MainComputerConfig
from main_computer.models import ChatMessage
from main_computer.providers import LLMProvider, OllamaProvider, OpenAIProvider


WHICH_ARE_SYSTEM_PROMPT = """You are a RAG "which are" selector.

You receive exactly two pieces of information:
1. TEXT: ordered numbered blocks.
2. USER PROMPT: a question asking which blocks should move to a second-pass decision.

Return JSON only with this shape:
{"blocks":[1,2]}

Rules:
- Your job is candidate recall, not final judgment.
- Return exactly two blocks when the prompt says there are exactly two complete-looking candidates.
- Include both candidates even if one has a subtle implementation defect.
- Do not solve the subtle defect in this first pass.
- Exclude obvious fragments, notes, outlines, section-only snippets, and tests.
- A complete-looking standalone candidate has imports plus parse_args, read_csv_rows, validate_row_widths, count_blank_cells, build_report, write_report, and main.
- Put the strongest real-looking block first if you can tell, but returning both close candidates is more important than ordering.
- If no block satisfies or nearly satisfies the prompt, return {"blocks":[]}.
"""


DISAMBIGUATION_TWO_SYSTEM_PROMPT = """You are a RAG disambiguation judge.

You receive:
1. A decision rubric.
2. Exactly two candidate things selected for a second pass.
3. The original first-pass prompt and first-pass answer as context.

Answer the question: "Which of these two things is the real one - and how sure are you?"

Return JSON only with this shape:
{"real_block":3,"confidence":0.87,"why":"short evidence"}

Rules:
- real_block must be one of the ORIGINAL BLOCK numbers listed in VALID ANSWERS.
- Never answer with a block number that is not in VALID ANSWERS.
- Decide by inspecting the candidate code, not titles or general completeness.
- Locate count_blank_cells in each candidate.
- The real candidate counts whitespace-only CSV cells as blank by stripping the cell before comparing.
- A candidate that checks only cell == "" is the near-miss and must lose.
- The why field should mention the decisive expression you observed.
- Keep why under 180 characters.
"""


DISAMBIGUATION_AUDIT_RUBRIC = """Decision rubric for this smoke case:
- Both candidates are complete-looking standalone Python implementations.
- Both contain parse_args, read_csv_rows, validate_row_widths, count_blank_cells, build_report, write_report, and main.
- The only intended tie-breaker is blank-cell behavior.
- Real behavior: whitespace-only cells such as "   " count as blank.
- Accept a candidate whose count_blank_cells strips before testing blankness, such as cell.strip() == "" or not cell.strip().
- Reject a candidate whose count_blank_cells only checks cell == ""; it misses whitespace-only cells.
"""


DEFAULT_TARGET_QUESTION = (
    "Which numbered blocks are the two complete-looking standalone candidate implementations to send to "
    "a second-pass disambiguation? There are exactly two such candidates. Do not choose the winner yet. "
    "Return both block numbers only. A complete-looking candidate has imports plus parse_args, read_csv_rows, "
    "validate_row_widths, count_blank_cells, build_report, write_report, and main."
)


DEFAULT_DISAMBIGUATION_QUESTION = "Which of these two things is the real one - and how sure are you?"


@dataclass(frozen=True)
class DisambiguationBlock:
    index: int
    title: str
    content: str
    role: str = "fragment"

    @property
    def is_real(self) -> bool:
        return self.role == "real"

    @property
    def is_near_miss(self) -> bool:
        return self.role == "near_miss"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DisambiguationCase:
    case_id: str
    seed: int
    question: str
    disambiguation_question: str
    blocks: list[DisambiguationBlock]
    expected_real_index: int
    expected_near_miss_index: int

    @property
    def expected_close_indices(self) -> list[int]:
        return [block.index for block in self.blocks if block.role in {"real", "near_miss"}]

    def numbered_text(self) -> str:
        rendered: list[str] = []
        for block in self.blocks:
            rendered.extend(
                [
                    f"[BLOCK {block.index}]",
                    f"TITLE: {block.title}",
                    block.content.rstrip(),
                    f"[/BLOCK {block.index}]",
                ]
            )
        return "\n\n".join(rendered) + "\n"

    def which_are_user_message(self) -> str:
        return "\n\n".join(
            [
                "TEXT:",
                self.numbered_text().rstrip(),
                "USER PROMPT:",
                self.question,
            ]
        )

    def selected_candidate_text(self, candidate_indices: Sequence[int]) -> str:
        by_index = {block.index: block for block in self.blocks}
        chunks: list[str] = []
        for position, block_index in enumerate(candidate_indices, start=1):
            block = by_index[block_index]
            chunks.extend(
                [
                    f"[THING {position}: ORIGINAL BLOCK {block.index}]",
                    f"TITLE: {block.title}",
                    block.content.rstrip(),
                    f"[/THING {position}: ORIGINAL BLOCK {block.index}]",
                ]
            )
        return "\n\n".join(chunks) + "\n"

    def disambiguation_user_message(self, *, candidate_indices: Sequence[int], first_selector_raw_answer: str) -> str:
        valid_answers = ", ".join(str(index) for index in candidate_indices)
        return "\n\n".join(
            [
                "USER PROMPT:",
                self.disambiguation_question,
                f"VALID ANSWERS: [{valid_answers}]",
                "DECISION RUBRIC:",
                DISAMBIGUATION_AUDIT_RUBRIC.strip(),
                "TWO CANDIDATE THINGS:",
                self.selected_candidate_text(candidate_indices).rstrip(),
                "FIRST-PASS CONTEXT:",
                "The first pass selected candidate block numbers for this two-way comparison. "
                "Use it only as context; the final real_block must come from VALID ANSWERS.",
                "FIRST-PASS USER PROMPT:",
                self.question,
                "FIRST-PASS RAW ANSWER:",
                first_selector_raw_answer.strip() or "<empty>",
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "seed": self.seed,
            "question": self.question,
            "disambiguation_question": self.disambiguation_question,
            "expected_real_index": self.expected_real_index,
            "expected_near_miss_index": self.expected_near_miss_index,
            "expected_close_indices": self.expected_close_indices,
            "blocks": [block.to_dict() for block in self.blocks],
        }


@dataclass(frozen=True)
class ParsedDisambiguation:
    real_block: int | None
    confidence: float | None
    why: str | None = None
    invalid_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DisambiguationAttemptReport:
    case_id: str
    seed: int
    expected_real_index: int
    expected_near_miss_index: int
    expected_close_indices: list[int]
    which_are_raw_answer: str
    parsed_which_are_indices: list[int]
    used_candidate_indices: list[int]
    oracle_candidates_used: bool
    disambiguation_raw_answer: str
    parsed_real_index: int | None
    parsed_confidence: float | None
    ok: bool
    which_are_prompt_chars: int
    disambiguation_prompt_chars: int
    block_count: int
    model: str
    provider: str
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RagDisambiguationTwoSmokeReport:
    ok: bool
    run_id: str
    scenario: str
    output_dir: str
    report_path: str
    attempts: list[DisambiguationAttemptReport]
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    strict: bool = False
    seed: int = 90210

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["attempts"] = [attempt.to_dict() for attempt in self.attempts]
        data["attempt_count"] = len(self.attempts)
        data["passed_count"] = sum(1 for attempt in self.attempts if attempt.ok)
        data["accuracy"] = data["passed_count"] / len(self.attempts) if self.attempts else 0.0
        data["which_are_close_pair_count"] = sum(
            1 for attempt in self.attempts if set(attempt.parsed_which_are_indices) == set(attempt.expected_close_indices)
        )
        return data


def default_run_id() -> str:
    return "rag_disambiguation_two_" + datetime.now().strftime("%Y%m%d_%H%M%S")


def _json_dumps(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_dumps(data) + "\n", encoding="utf-8")


def _provider_from_config(config: MainComputerConfig) -> LLMProvider:
    if config.provider == "ollama":
        return OllamaProvider(
            model=config.model,
            base_url=config.ollama_base_url,
            timeout_s=config.ollama_timeout_s,
            fallback=config.fallback,
        )
    if config.provider == "openai":
        return OpenAIProvider(model=config.model, base_url=config.openai_base_url, fallback=config.fallback)
    raise ValueError(f"Unknown provider: {config.provider}")


def _real_full_code_block() -> str:
    return """# CSV audit script
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Audit a CSV and write a JSON report.")
    parser.add_argument("input_csv")
    parser.add_argument("output_json")
    return parser.parse_args(argv)


def read_csv_rows(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


def validate_row_widths(rows: list[list[str]]) -> list[dict[str, int]]:
    if not rows:
        return []
    expected = len(rows[0])
    problems = []
    for line_number, row in enumerate(rows, start=1):
        if len(row) != expected:
            problems.append(
                {
                    "line": line_number,
                    "expected": expected,
                    "actual": len(row),
                }
            )
    return problems


def count_blank_cells(rows: list[list[str]]) -> int:
    return sum(1 for row in rows for cell in row if cell.strip() == "")


def build_report(rows: list[list[str]]) -> dict[str, object]:
    header = rows[0] if rows else []
    duplicate_headers = sorted({name for name in header if header.count(name) > 1})
    return {
        "row_count": max(0, len(rows) - 1),
        "column_count": len(header),
        "blank_cell_count": count_blank_cells(rows),
        "duplicate_header_names": duplicate_headers,
        "row_width_errors": validate_row_widths(rows),
    }


def write_report(path: Path, report: dict[str, object]) -> None:
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\\n", encoding="utf-8")


def main(argv=None) -> int:
    args = parse_args(argv)
    rows = read_csv_rows(Path(args.input_csv))
    write_report(Path(args.output_json), build_report(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""


def _near_miss_full_code_block() -> str:
    return """# CSV audit script
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Audit a CSV and write a JSON report.")
    parser.add_argument("input_csv")
    parser.add_argument("output_json")
    return parser.parse_args(argv)


def read_csv_rows(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


def validate_row_widths(rows: list[list[str]]) -> list[dict[str, int]]:
    if not rows:
        return []
    expected = len(rows[0])
    problems = []
    for line_number, row in enumerate(rows, start=1):
        if len(row) != expected:
            problems.append(
                {
                    "line": line_number,
                    "expected": expected,
                    "actual": len(row),
                }
            )
    return problems


def count_blank_cells(rows: list[list[str]]) -> int:
    return sum(1 for row in rows for cell in row if cell == "")


def build_report(rows: list[list[str]]) -> dict[str, object]:
    header = rows[0] if rows else []
    duplicate_headers = sorted({name for name in header if header.count(name) > 1})
    return {
        "row_count": max(0, len(rows) - 1),
        "column_count": len(header),
        "blank_cell_count": count_blank_cells(rows),
        "duplicate_header_names": duplicate_headers,
        "row_width_errors": validate_row_widths(rows),
    }


def write_report(path: Path, report: dict[str, object]) -> None:
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\\n", encoding="utf-8")


def main(argv=None) -> int:
    args = parse_args(argv)
    rows = read_csv_rows(Path(args.input_csv))
    write_report(Path(args.output_json), build_report(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""


def _fragment_blocks() -> list[tuple[str, str]]:
    return [
        (
            "CSV procedure notes",
            """# CSV AUDIT PROCEDURE NOTES
# Parse CLI arguments.
# Read rows with csv.reader.
# Validate row widths.
# Count blank cells.
# Write JSON and expose main().
# This is a design note, not executable code.
""",
        ),
        (
            "Argument parser section",
            """import argparse


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Audit a CSV and write a JSON report.")
    parser.add_argument("input_csv")
    parser.add_argument("output_json")
    return parser.parse_args(argv)
""",
        ),
        (
            "CSV reader and width validator",
            """import csv
from pathlib import Path


def read_csv_rows(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


def validate_row_widths(rows: list[list[str]]) -> list[dict[str, int]]:
    if not rows:
        return []
    expected = len(rows[0])
    return [
        {"line": line_number, "expected": expected, "actual": len(row)}
        for line_number, row in enumerate(rows, start=1)
        if len(row) != expected
    ]
""",
        ),
        (
            "Report builder only",
            """def build_report(rows: list[list[str]]) -> dict[str, object]:
    header = rows[0] if rows else []
    duplicate_headers = sorted({name for name in header if header.count(name) > 1})
    return {
        "row_count": max(0, len(rows) - 1),
        "column_count": len(header),
        "blank_cell_count": count_blank_cells(rows),
        "duplicate_header_names": duplicate_headers,
        "row_width_errors": validate_row_widths(rows),
    }
""",
        ),
        (
            "Writer and main glue only",
            """import json
from pathlib import Path


def write_report(path: Path, report: dict[str, object]) -> None:
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\\n", encoding="utf-8")


def main(argv=None) -> int:
    args = parse_args(argv)
    rows = read_csv_rows(Path(args.input_csv))
    write_report(Path(args.output_json), build_report(rows))
    return 0
""",
        ),
        (
            "Almost standalone missing row-width reporting",
            """from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Audit a CSV and write a JSON report.")
    parser.add_argument("input_csv")
    parser.add_argument("output_json")
    return parser.parse_args(argv)


def read_csv_rows(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


def count_blank_cells(rows: list[list[str]]) -> int:
    return sum(1 for row in rows for cell in row if not cell.strip())


def build_report(rows: list[list[str]]) -> dict[str, object]:
    header = rows[0] if rows else []
    return {
        "row_count": max(0, len(rows) - 1),
        "column_count": len(header),
        "blank_cell_count": count_blank_cells(rows),
    }


def write_report(path: Path, report: dict[str, object]) -> None:
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\\n", encoding="utf-8")


def main(argv=None) -> int:
    args = parse_args(argv)
    rows = read_csv_rows(Path(args.input_csv))
    write_report(Path(args.output_json), build_report(rows))
    return 0
""",
        ),
        (
            "Standalone text report implementation",
            """from __future__ import annotations

import argparse
import csv
from pathlib import Path


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Audit a CSV and write a text report.")
    parser.add_argument("input_csv")
    parser.add_argument("output_txt")
    return parser.parse_args(argv)


def read_csv_rows(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


def count_blank_cells(rows: list[list[str]]) -> int:
    return sum(1 for row in rows for cell in row if not cell.strip())


def main(argv=None) -> int:
    args = parse_args(argv)
    rows = read_csv_rows(Path(args.input_csv))
    Path(args.output_txt).write_text(f"blank cells: {count_blank_cells(rows)}\\n", encoding="utf-8")
    return 0
""",
        ),
        (
            "Validator unit-test sketch",
            """def test_validate_row_widths_reports_mismatches():
    rows = [["a", "b"], ["1"], ["2", "3", "4"]]
    assert validate_row_widths(rows) == [
        {"line": 2, "expected": 2, "actual": 1},
        {"line": 3, "expected": 2, "actual": 3},
    ]
""",
        ),
    ]


def generate_disambiguation_case(
    *,
    seed: int = 90210,
    case_id: str | None = None,
    fragment_count: int = 3,
    question: str = DEFAULT_TARGET_QUESTION,
    disambiguation_question: str = DEFAULT_DISAMBIGUATION_QUESTION,
) -> DisambiguationCase:
    if fragment_count < 1:
        raise ValueError("fragment_count must be at least 1")
    fragments = _fragment_blocks()
    if fragment_count > len(fragments):
        raise ValueError(f"fragment_count must be no greater than {len(fragments)}")

    rng = random.Random(seed)
    selected = rng.sample(fragments, fragment_count)
    candidates: list[tuple[str, str, str]] = [(title, block_text, "fragment") for title, block_text in selected]
    candidates.append(("Candidate implementation", _real_full_code_block(), "real"))
    candidates.append(("Candidate implementation", _near_miss_full_code_block(), "near_miss"))
    rng.shuffle(candidates)

    blocks = [
        DisambiguationBlock(index=index, title=title, content=block_text.rstrip() + "\n", role=role)
        for index, (title, block_text, role) in enumerate(candidates, start=1)
    ]
    real_indices = [block.index for block in blocks if block.is_real]
    near_miss_indices = [block.index for block in blocks if block.is_near_miss]
    if len(real_indices) != 1 or len(near_miss_indices) != 1:
        raise RuntimeError("generated case must contain exactly one real block and one near-miss block")

    return DisambiguationCase(
        case_id=case_id or f"disambiguation_two_{seed}",
        seed=seed,
        question=question,
        disambiguation_question=disambiguation_question,
        blocks=blocks,
        expected_real_index=real_indices[0],
        expected_near_miss_index=near_miss_indices[0],
    )


def build_which_are_messages(case: DisambiguationCase) -> list[ChatMessage]:
    return [
        ChatMessage(role="system", content=WHICH_ARE_SYSTEM_PROMPT),
        ChatMessage(role="user", content=case.which_are_user_message()),
    ]


def build_disambiguation_messages(
    case: DisambiguationCase,
    *,
    candidate_indices: Sequence[int],
    first_selector_raw_answer: str,
) -> list[ChatMessage]:
    if len(candidate_indices) != 2:
        raise ValueError("disambiguation requires exactly two candidate indices")
    valid_indices = {block.index for block in case.blocks}
    bad_indices = [index for index in candidate_indices if index not in valid_indices]
    if bad_indices:
        raise ValueError(f"candidate indices are not in the case: {bad_indices}")

    return [
        ChatMessage(role="system", content=DISAMBIGUATION_TWO_SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content=case.disambiguation_user_message(
                candidate_indices=candidate_indices,
                first_selector_raw_answer=first_selector_raw_answer,
            ),
        ),
    ]


def _dedupe_in_order(values: Sequence[int]) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _int_from_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        return int(value.strip())
    return None


def _extract_json_payload(raw: str) -> Any | None:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            return None
    return None


def _collect_ints_from_jsonish(value: Any) -> list[int]:
    if isinstance(value, (list, tuple)):
        result: list[int] = []
        for item in value:
            integer = _int_from_value(item)
            if integer is not None:
                result.append(integer)
            elif isinstance(item, dict):
                result.extend(_collect_ints_from_jsonish(item))
        return result
    if isinstance(value, dict):
        for key in ("blocks", "block_numbers", "answers", "indices", "candidates", "candidate_blocks"):
            if key in value:
                return _collect_ints_from_jsonish(value[key])
        for key in ("block", "block_number", "answer", "index", "real_block"):
            if key in value:
                integer = _int_from_value(value[key])
                return [integer] if integer is not None else []
    integer = _int_from_value(value)
    return [integer] if integer is not None else []


def parse_which_are_answer(raw_answer: str, *, max_index: int) -> list[int]:
    raw = str(raw_answer or "").strip()
    if not raw:
        return []

    values: list[int] = []
    data = _extract_json_payload(raw)

    if data is not None:
        values = _collect_ints_from_jsonish(data)

    if not values:
        bracket_match = re.search(r"\[([^\]]*)\]", raw)
        if bracket_match:
            values = [int(match.group(0)) for match in re.finditer(r"\b\d+\b", bracket_match.group(1))]

    if not values:
        values = [int(match.group(0)) for match in re.finditer(r"\b\d+\b", raw)]

    return _dedupe_in_order([value for value in values if 1 <= value <= max_index])


def _parse_confidence_from_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        confidence = float(value)
        if 0.0 <= confidence <= 1.0:
            return confidence
        if 1.0 < confidence <= 100.0:
            return confidence / 100.0
        return None
    if isinstance(value, str):
        text = value.strip()
        percent = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%", text)
        if percent:
            return _parse_confidence_from_value(float(percent.group(1)))
        try:
            return _parse_confidence_from_value(float(text))
        except ValueError:
            return None
    return None


def _parse_confidence_from_text(raw: str) -> float | None:
    for pattern in (
        r"\bconfidence\s*[:=]\s*(\d+(?:\.\d+)?\s*%?)",
        r"\bsure\s*[:=]?\s*(\d+(?:\.\d+)?\s*%?)",
        r"\b(\d+(?:\.\d+)?)\s*%\s*(?:sure|confidence|confident)",
    ):
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if match:
            confidence = _parse_confidence_from_value(match.group(1))
            if confidence is not None:
                return confidence
    return None


def parse_disambiguation_answer(
    raw_answer: str,
    *,
    max_index: int,
    candidate_indices: Sequence[int],
) -> ParsedDisambiguation:
    raw = str(raw_answer or "").strip()
    if not raw:
        return ParsedDisambiguation(real_block=None, confidence=None, invalid_reason="empty answer")

    candidate_set = set(candidate_indices)
    real_block: int | None = None
    confidence: float | None = None
    why: str | None = None

    data = _extract_json_payload(raw)

    if isinstance(data, dict):
        for key in ("real_block", "block", "block_number", "answer", "index"):
            integer = _int_from_value(data.get(key))
            if integer is not None:
                real_block = integer
                break

        if real_block is None:
            for key in ("thing", "candidate", "choice"):
                integer = _int_from_value(data.get(key))
                if integer is not None and 1 <= integer <= len(candidate_indices):
                    real_block = candidate_indices[integer - 1]
                    break

        for key in ("confidence", "certainty", "sure", "score"):
            confidence = _parse_confidence_from_value(data.get(key))
            if confidence is not None:
                break

        reason = data.get("why") or data.get("reason") or data.get("explanation")
        if isinstance(reason, str):
            why = reason.strip()[:240] or None

    elif isinstance(data, int):
        real_block = data

    if real_block is None:
        thing_match = re.search(r"\bthing\s*([12])\b", raw, flags=re.IGNORECASE)
        if thing_match:
            real_block = candidate_indices[int(thing_match.group(1)) - 1]

    if real_block is None:
        block_match = re.search(r"\bblock\s*(\d+)\b", raw, flags=re.IGNORECASE)
        if block_match:
            real_block = int(block_match.group(1))

    if real_block is None and re.fullmatch(r"\d+", raw):
        real_block = int(raw)

    if confidence is None:
        confidence = _parse_confidence_from_text(raw)

    if real_block is None:
        return ParsedDisambiguation(real_block=None, confidence=confidence, why=why, invalid_reason="no parseable real_block")
    if real_block not in candidate_set:
        return ParsedDisambiguation(
            real_block=None,
            confidence=confidence,
            why=why,
            invalid_reason=f"parsed block {real_block} is outside the two valid candidates {list(candidate_indices)}",
        )

    return ParsedDisambiguation(real_block=real_block, confidence=confidence, why=why)



def choose_two_candidates_for_disambiguation(
    *,
    parsed_which_are_indices: Sequence[int],
    expected_real_index: int,
    expected_near_miss_index: int,
    max_index: int,
) -> tuple[list[int], bool]:
    parsed = _dedupe_in_order([index for index in parsed_which_are_indices if 1 <= index <= max_index])
    expected_set = {expected_real_index, expected_near_miss_index}
    if expected_set.issubset(set(parsed)):
        pair = [index for index in parsed if index in expected_set]
        return pair[:2], False
    return [expected_real_index, expected_near_miss_index], True


def run_disambiguation_case(case: DisambiguationCase, provider: LLMProvider) -> DisambiguationAttemptReport:
    which_are_messages = build_which_are_messages(case)
    which_are_response = provider.chat(which_are_messages)
    parsed_which_are_indices = parse_which_are_answer(which_are_response.content, max_index=len(case.blocks))

    candidate_indices, oracle_candidates_used = choose_two_candidates_for_disambiguation(
        parsed_which_are_indices=parsed_which_are_indices,
        expected_real_index=case.expected_real_index,
        expected_near_miss_index=case.expected_near_miss_index,
        max_index=len(case.blocks),
    )

    disambiguation_messages = build_disambiguation_messages(
        case,
        candidate_indices=candidate_indices,
        first_selector_raw_answer=which_are_response.content,
    )
    disambiguation_response = provider.chat(disambiguation_messages)
    parsed = parse_disambiguation_answer(
        disambiguation_response.content,
        max_index=len(case.blocks),
        candidate_indices=candidate_indices,
    )

    warning = None
    if oracle_candidates_used:
        warning = (
            "which_are did not return both close candidates; used generated real/near-miss pair "
            "only to measure the second-pass discriminator"
        )
    elif set(parsed_which_are_indices) != set(case.expected_close_indices):
        warning = "which_are returned extra candidates beyond the generated close pair"
    elif parsed.confidence is None:
        warning = "disambiguation answer did not include a parseable confidence"
    elif parsed.invalid_reason:
        warning = parsed.invalid_reason

    provider_name = disambiguation_response.provider or which_are_response.provider
    model_name = disambiguation_response.model or which_are_response.model

    return DisambiguationAttemptReport(
        case_id=case.case_id,
        seed=case.seed,
        expected_real_index=case.expected_real_index,
        expected_near_miss_index=case.expected_near_miss_index,
        expected_close_indices=case.expected_close_indices,
        which_are_raw_answer=which_are_response.content,
        parsed_which_are_indices=parsed_which_are_indices,
        used_candidate_indices=list(candidate_indices),
        oracle_candidates_used=oracle_candidates_used,
        disambiguation_raw_answer=disambiguation_response.content,
        parsed_real_index=parsed.real_block,
        parsed_confidence=parsed.confidence,
        ok=parsed.real_block == case.expected_real_index,
        which_are_prompt_chars=sum(len(message.content) for message in which_are_messages),
        disambiguation_prompt_chars=sum(len(message.content) for message in disambiguation_messages),
        block_count=len(case.blocks),
        model=model_name,
        provider=provider_name,
        warning=warning,
    )


def run_rag_smoke_disambiguation_two(
    *,
    provider: LLMProvider | None = None,
    output_root: Path | str | None = None,
    run_id: str | None = None,
    seed: int = 90210,
    case_count: int = 3,
    fragment_count: int = 3,
    strict: bool = False,
    dump_cases: bool = True,
) -> RagDisambiguationTwoSmokeReport:
    if case_count < 1:
        raise ValueError("case_count must be at least 1")

    if provider is None:
        provider = _provider_from_config(MainComputerConfig.from_env())

    run_id = run_id or default_run_id()
    output_dir = Path(output_root or Path("diagnostics_output") / "rag_disambiguation_two_runs") / run_id
    output_dir.mkdir(parents=True, exist_ok=False)

    cases = [
        generate_disambiguation_case(
            seed=seed + index,
            case_id=f"disambiguation_two_{index + 1:02d}",
            fragment_count=fragment_count,
        )
        for index in range(case_count)
    ]

    if dump_cases:
        _write_json(output_dir / "cases.json", [case.to_dict() for case in cases])
        for case in cases:
            (output_dir / f"{case.case_id}_which_are.txt").write_text(
                case.which_are_user_message() + "\n",
                encoding="utf-8",
            )

    attempts = [run_disambiguation_case(case, provider) for case in cases]

    if dump_cases:
        case_by_id = {case.case_id: case for case in cases}
        for attempt in attempts:
            case = case_by_id[attempt.case_id]
            (output_dir / f"{case.case_id}_disambiguation_two.txt").write_text(
                case.disambiguation_user_message(
                    candidate_indices=attempt.used_candidate_indices,
                    first_selector_raw_answer=attempt.which_are_raw_answer,
                )
                + "\n",
                encoding="utf-8",
            )

    warnings = [f"{attempt.case_id}: {attempt.warning}" for attempt in attempts if attempt.warning]
    failures = [
        (
            f"{attempt.case_id}: expected real block {attempt.expected_real_index}, "
            f"got {attempt.parsed_real_index!r} from {attempt.disambiguation_raw_answer!r}"
        )
        for attempt in attempts
        if not attempt.ok
    ]
    ok = not failures and (not strict or not warnings)

    report_path = output_dir / "report.json"
    report = RagDisambiguationTwoSmokeReport(
        ok=ok,
        run_id=run_id,
        scenario="disambiguation-two-things",
        output_dir=str(output_dir),
        report_path=str(report_path),
        attempts=attempts,
        warnings=warnings,
        failures=failures,
        strict=strict,
        seed=seed,
    )
    _write_json(report_path, report.to_dict())
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the RAG two-candidate disambiguation smoke test.")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Optional diagnostics output root. Defaults to diagnostics_output/rag_disambiguation_two_runs.",
    )
    parser.add_argument("--run-id", default=None, help="Optional deterministic run id.")
    parser.add_argument("--seed", type=int, default=90210, help="Base seed for generated block ordering.")
    parser.add_argument("--case-count", type=int, default=3, help="Number of generated disambiguation cases to run.")
    parser.add_argument(
        "--fragment-count",
        type=int,
        default=3,
        help="Number of non-answer fragments per case, excluding the real and near-miss candidates.",
    )
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures.")
    parser.add_argument("--dump-json", action="store_true", help="Print the full JSON report.")
    parser.add_argument("--quiet", action="store_true", help="Suppress concise diagnostics.")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_rag_smoke_disambiguation_two(
        output_root=args.output_root,
        run_id=args.run_id,
        seed=args.seed,
        case_count=args.case_count,
        fragment_count=args.fragment_count,
        strict=args.strict,
    )
    if args.dump_json:
        print(_json_dumps(report.to_dict()))
    elif not args.quiet:
        passed_count = sum(1 for attempt in report.attempts if attempt.ok)
        close_pair_count = sum(
            1 for attempt in report.attempts if set(attempt.parsed_which_are_indices) == set(attempt.expected_close_indices)
        )
        print(f"Scenario: {report.scenario}")
        print(f"Run: {report.run_id}")
        print(f"Output: {report.output_dir}")
        print(f"Report: {report.report_path}")
        print(f"Attempts: {passed_count}/{len(report.attempts)} passed")
        disambiguation_count = sum(1 for attempt in report.attempts if attempt.parsed_real_index == attempt.expected_real_index)
        print(f"Which-are close pairs: {close_pair_count}/{len(report.attempts)}")
        print(f"Disambiguation decisions: {disambiguation_count}/{len(report.attempts)}")
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
