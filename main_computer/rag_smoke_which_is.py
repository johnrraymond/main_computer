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


WHICH_IS_SYSTEM_PROMPT = """You are a RAG "which is" selector.

You receive exactly two pieces of information:
1. TEXT: ordered numbered blocks.
2. USER PROMPT: a question asking which block satisfies a precise condition.

Return only one base-10 integer: the number of the single best block.
Do not explain. Do not return JSON. Do not include punctuation.
If no block satisfies the prompt, return 0.
"""


DEFAULT_WHICH_IS_QUESTION = (
    "Which numbered block is the complete standalone Python implementation that includes every "
    "required procedure: parse CLI arguments, read CSV rows, validate row widths, count blank "
    "cells, write a JSON report, and expose main()? Answer with only the block number."
)


@dataclass(frozen=True)
class WhichIsBlock:
    index: int
    title: str
    content: str
    is_answer: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WhichIsCase:
    case_id: str
    seed: int
    question: str
    blocks: list[WhichIsBlock]
    expected_index: int

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

    def user_message(self) -> str:
        return "\n\n".join(
            [
                "TEXT:",
                self.numbered_text().rstrip(),
                "USER PROMPT:",
                self.question,
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "seed": self.seed,
            "question": self.question,
            "expected_index": self.expected_index,
            "blocks": [block.to_dict() for block in self.blocks],
        }


@dataclass(frozen=True)
class WhichIsAttemptReport:
    case_id: str
    seed: int
    expected_index: int
    raw_answer: str
    parsed_index: int | None
    ok: bool
    prompt_chars: int
    block_count: int
    model: str
    provider: str
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RagWhichIsSmokeReport:
    ok: bool
    run_id: str
    scenario: str
    output_dir: str
    report_path: str
    attempts: list[WhichIsAttemptReport]
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    strict: bool = True
    seed: int = 8128

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["attempts"] = [attempt.to_dict() for attempt in self.attempts]
        data["attempt_count"] = len(self.attempts)
        data["passed_count"] = sum(1 for attempt in self.attempts if attempt.ok)
        data["accuracy"] = data["passed_count"] / len(self.attempts) if self.attempts else 0.0
        return data


def default_run_id() -> str:
    return "rag_which_is_" + datetime.now().strftime("%Y%m%d_%H%M%S")


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


def _full_code_block() -> str:
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
    return sum(1 for row in rows for cell in row if not cell.strip())


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
            "CLI argument parsing procedure only",
            """# PROCEDURE: parse CLI arguments
# Purpose: define the input CSV path and output JSON path.
# This fragment intentionally stops after argument parsing.
import argparse


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Audit a CSV and write a JSON report.")
    parser.add_argument("input_csv")
    parser.add_argument("output_json")
    return parser.parse_args(argv)
""",
        ),
        (
            "CSV loading procedure only",
            """# PROCEDURE: read CSV rows
# Purpose: open the CSV file with newline handling and UTF-8 decoding.
# This block does not validate, summarize, write, or expose main().
import csv
from pathlib import Path


def read_csv_rows(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))
""",
        ),
        (
            "Row width validation procedure only",
            """# PROCEDURE: validate row widths
# Purpose: compare every row with the header width and report mismatches.
# This is only one validation section, not a standalone program.
def validate_row_widths(rows: list[list[str]]) -> list[dict[str, int]]:
    if not rows:
        return []
    expected = len(rows[0])
    problems = []
    for line_number, row in enumerate(rows, start=1):
        if len(row) != expected:
            problems.append({"line": line_number, "expected": expected, "actual": len(row)})
    return problems
""",
        ),
        (
            "Blank-cell counting procedure only",
            """# PROCEDURE: count blank cells
# Purpose: count cells that are empty after whitespace trimming.
# This fragment cannot read files or write the report by itself.
def count_blank_cells(rows: list[list[str]]) -> int:
    return sum(1 for row in rows for cell in row if not cell.strip())
""",
        ),
        (
            "Report construction procedure only",
            """# PROCEDURE: build the JSON-compatible report dictionary
# Purpose: collect row count, column count, blank-cell count, duplicate headers,
# and row-width errors. This depends on helper functions defined elsewhere.
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
""",
        ),
        (
            "JSON writing procedure only",
            """# PROCEDURE: write JSON report
# Purpose: serialize an already-built report to disk with stable formatting.
# This fragment has no CSV reader, validator, report builder, or main function.
import json
from pathlib import Path


def write_report(path: Path, report: dict[str, object]) -> None:
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
""",
        ),
        (
            "High-level checklist, not code",
            """# PROCEDURE CHECKLIST
# 1. Parse input_csv and output_json command-line arguments.
# 2. Read all CSV rows.
# 3. Validate each row width against the header width.
# 4. Count blank cells.
# 5. Build and write a JSON audit report.
# 6. Wire the procedures through main().
# This is descriptive text only; it is not executable Python code.
""",
        ),
        (
            "Near-miss glue code missing helper implementations",
            """# PROCEDURE: main glue only
# This block looks like an entry point, but all important helpers are missing.
from pathlib import Path


def main(argv=None) -> int:
    args = parse_args(argv)
    rows = read_csv_rows(Path(args.input_csv))
    write_report(Path(args.output_json), build_report(rows))
    return 0
""",
        ),
    ]


def generate_which_is_case(
    *,
    seed: int = 8128,
    case_id: str | None = None,
    fragment_count: int = 7,
    question: str = DEFAULT_WHICH_IS_QUESTION,
) -> WhichIsCase:
    if fragment_count < 2:
        raise ValueError("fragment_count must be at least 2")
    fragments = _fragment_blocks()
    if fragment_count > len(fragments):
        raise ValueError(f"fragment_count must be no greater than {len(fragments)}")

    rng = random.Random(seed)
    selected = rng.sample(fragments, fragment_count)
    candidates: list[tuple[str, str, bool]] = [(title, block_text, False) for title, block_text in selected]
    candidates.append(("Candidate implementation", _full_code_block(), True))
    rng.shuffle(candidates)

    blocks = [
        WhichIsBlock(index=index, title=title, content=block_text.rstrip() + "\n", is_answer=is_answer)
        for index, (title, block_text, is_answer) in enumerate(candidates, start=1)
    ]
    expected_indices = [block.index for block in blocks if block.is_answer]
    if len(expected_indices) != 1:
        raise RuntimeError("generated case must contain exactly one answer block")
    return WhichIsCase(
        case_id=case_id or f"which_is_{seed}",
        seed=seed,
        question=question,
        blocks=blocks,
        expected_index=expected_indices[0],
    )


def build_messages(case: WhichIsCase) -> list[ChatMessage]:
    return [
        ChatMessage(role="system", content=WHICH_IS_SYSTEM_PROMPT),
        ChatMessage(role="user", content=case.user_message()),
    ]


def parse_numeric_answer(raw_answer: str, *, max_index: int) -> int | None:
    raw = str(raw_answer or "").strip()
    if not raw:
        return None

    if re.fullmatch(r"[+-]?\d+", raw):
        value = int(raw)
        return value if 0 <= value <= max_index else None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, int):
        return data if 0 <= data <= max_index else None
    if isinstance(data, dict):
        for key in ("answer", "block", "block_number", "index"):
            value = data.get(key)
            if isinstance(value, int) and 0 <= value <= max_index:
                return value
            if isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
                integer = int(value.strip())
                return integer if 0 <= integer <= max_index else None

    numbers = [int(match.group(0)) for match in re.finditer(r"\b\d+\b", raw)]
    unique = sorted(set(numbers))
    if len(unique) == 1 and 0 <= unique[0] <= max_index:
        return unique[0]
    return None


def run_which_is_case(case: WhichIsCase, provider: LLMProvider) -> WhichIsAttemptReport:
    messages = build_messages(case)
    response = provider.chat(messages)
    parsed_index = parse_numeric_answer(response.content, max_index=len(case.blocks))
    warning = None
    if parsed_index is None:
        warning = "model answer did not contain a unique in-range block number"
    elif parsed_index == 0:
        warning = "model returned 0 even though this generated case has one answer block"

    return WhichIsAttemptReport(
        case_id=case.case_id,
        seed=case.seed,
        expected_index=case.expected_index,
        raw_answer=response.content,
        parsed_index=parsed_index,
        ok=parsed_index == case.expected_index,
        prompt_chars=sum(len(message.content) for message in messages),
        block_count=len(case.blocks),
        model=response.model,
        provider=response.provider,
        warning=warning,
    )


def run_rag_smoke_which_is(
    *,
    provider: LLMProvider | None = None,
    output_root: Path | str | None = None,
    run_id: str | None = None,
    seed: int = 8128,
    case_count: int = 3,
    fragment_count: int = 7,
    strict: bool = True,
    dump_cases: bool = True,
) -> RagWhichIsSmokeReport:
    if case_count < 1:
        raise ValueError("case_count must be at least 1")

    if provider is None:
        provider = _provider_from_config(MainComputerConfig.from_env())

    run_id = run_id or default_run_id()
    output_dir = Path(output_root or Path("diagnostics_output") / "rag_which_is_runs") / run_id
    output_dir.mkdir(parents=True, exist_ok=False)

    cases = [
        generate_which_is_case(
            seed=seed + index,
            case_id=f"which_is_{index + 1:02d}",
            fragment_count=fragment_count,
        )
        for index in range(case_count)
    ]

    if dump_cases:
        _write_json(output_dir / "cases.json", [case.to_dict() for case in cases])
        for case in cases:
            (output_dir / f"{case.case_id}.txt").write_text(case.user_message() + "\n", encoding="utf-8")

    attempts = [run_which_is_case(case, provider) for case in cases]
    warnings = [attempt.warning for attempt in attempts if attempt.warning]
    failures = [
        (
            f"{attempt.case_id}: expected block {attempt.expected_index}, "
            f"got {attempt.parsed_index!r} from {attempt.raw_answer!r}"
        )
        for attempt in attempts
        if not attempt.ok
    ]
    ok = not failures and (not strict or not warnings)

    report_path = output_dir / "report.json"
    report = RagWhichIsSmokeReport(
        ok=ok,
        run_id=run_id,
        scenario="which-is",
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
    parser = argparse.ArgumentParser(description="Run the RAG 'which is' numeric block-selection smoke test.")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Optional diagnostics output root. Defaults to diagnostics_output/rag_which_is_runs.",
    )
    parser.add_argument("--run-id", default=None, help="Optional deterministic run id.")
    parser.add_argument("--seed", type=int, default=8128, help="Base seed for generated fragment ordering.")
    parser.add_argument("--case-count", type=int, default=3, help="Number of generated which-is cases to run.")
    parser.add_argument("--fragment-count", type=int, default=7, help="Number of non-answer fragments per case.")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures.")
    parser.add_argument("--dump-json", action="store_true", help="Print the full JSON report.")
    parser.add_argument("--quiet", action="store_true", help="Suppress concise diagnostics.")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_rag_smoke_which_is(
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
        print(f"Scenario: {report.scenario}")
        print(f"Run: {report.run_id}")
        print(f"Output: {report.output_dir}")
        print(f"Report: {report.report_path}")
        print(f"Attempts: {sum(1 for attempt in report.attempts if attempt.ok)}/{len(report.attempts)} passed")
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
