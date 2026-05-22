from __future__ import annotations

from collections.abc import Sequence
import json
import re
from pathlib import Path

from main_computer.models import ChatMessage, ChatResponse
from main_computer.rag_smoke_which_is import (
    WHICH_IS_SYSTEM_PROMPT,
    build_messages,
    generate_which_is_case,
    parse_args,
    parse_numeric_answer,
    run_rag_smoke_which_is,
)


class FakeWhichIsProvider:
    name = "fake"
    model = "which-is"

    def __init__(self, *, answer_format: str = "raw") -> None:
        self.calls: list[list[ChatMessage]] = []
        self.answer_format = answer_format

    def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        self.calls.append(list(messages))
        user_text = "\n\n".join(message.content for message in messages if message.role == "user")
        required = {
            "def parse_args(",
            "def read_csv_rows(",
            "def validate_row_widths(",
            "def count_blank_cells(",
            "def build_report(",
            "def write_report(",
            "def main(",
            "if __name__ == \"__main__\"",
        }
        for match in re.finditer(r"\[BLOCK (\d+)\](.*?)(?=\n\n\[BLOCK |\Z)", user_text, flags=re.DOTALL):
            block_number = int(match.group(1))
            block_text = match.group(2)
            if required.issubset(set(token for token in required if token in block_text)):
                content = str(block_number)
                if self.answer_format == "json":
                    content = json.dumps({"block_number": block_number})
                return ChatResponse(content=content, provider=self.name, model=self.model)
        return ChatResponse(content="0", provider=self.name, model=self.model)


def test_generate_which_is_case_contains_one_answer_and_numbered_text() -> None:
    case = generate_which_is_case(seed=99, fragment_count=7)

    assert len(case.blocks) == 8
    assert sum(1 for block in case.blocks if block.is_answer) == 1
    assert case.expected_index in {block.index for block in case.blocks}
    text = case.numbered_text()
    assert "[BLOCK 1]" in text
    assert "[/BLOCK 8]" in text
    assert "USER PROMPT" not in text

    messages = build_messages(case)
    assert messages[0].content == WHICH_IS_SYSTEM_PROMPT
    assert "TEXT:" in messages[1].content
    assert "USER PROMPT:" in messages[1].content
    assert str(case.expected_index) not in case.question


def test_parse_numeric_answer_accepts_strict_and_repairable_numeric_forms() -> None:
    assert parse_numeric_answer("3", max_index=8) == 3
    assert parse_numeric_answer("  0  ", max_index=8) == 0
    assert parse_numeric_answer('{"block_number": "4"}', max_index=8) == 4
    assert parse_numeric_answer("The answer is block 5.", max_index=8) == 5
    assert parse_numeric_answer("block 5 or 6", max_index=8) is None
    assert parse_numeric_answer("99", max_index=8) is None
    assert parse_numeric_answer("", max_index=8) is None


def test_run_rag_smoke_which_is_writes_cases_and_report(tmp_path: Path) -> None:
    provider = FakeWhichIsProvider(answer_format="json")

    report = run_rag_smoke_which_is(
        provider=provider,
        output_root=tmp_path,
        run_id="which_is_test",
        seed=1200,
        case_count=2,
        fragment_count=7,
    )

    assert report.ok
    assert len(provider.calls) == 2
    assert all(attempt.ok for attempt in report.attempts)
    assert Path(report.report_path).exists()
    assert (Path(report.output_dir) / "cases.json").exists()
    assert (Path(report.output_dir) / "which_is_01.txt").exists()

    payload = json.loads(Path(report.report_path).read_text(encoding="utf-8"))
    assert payload["scenario"] == "which-is"
    assert payload["attempt_count"] == 2
    assert payload["accuracy"] == 1.0


def test_parse_args_defaults_to_fast_smoke() -> None:
    args = parse_args([])

    assert args.case_count == 3
    assert args.fragment_count == 7
    assert args.seed == 8128
