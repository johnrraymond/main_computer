from __future__ import annotations

import base64
import json

from main_computer.models import ChatMessage, ChatResponse
from main_computer.rag_assisted_thinking_v2 import (
    RagAssistedThinkingV2Policy,
    choose_tool_plan,
    classify_request_intent,
    diagnose_malformed_control_payload,
    is_self_contained_recreation_benchmark,
    parse_v2_control_payload,
    run_rag_assisted_thinking_v2_request,
    validate_v2_control_payload,
)


def test_v2_classifies_rag_patch_request_as_local_file_change() -> None:
    intent = classify_request_intent("Create a new rag assisted thinking v2 backend script for the repo.")
    assert intent.request_type == "file_change"
    assert intent.needs_local_repo_context is True
    assert intent.requires_file_change is True
    assert intent.direct_mutation_intent is True


def test_v2_fresh_external_request_requires_web_gate() -> None:
    intent = classify_request_intent("What is the latest release version of PackageX today?")
    plan = choose_tool_plan(intent, RagAssistedThinkingV2Policy(web_search_enabled=True))
    assert intent.needs_fresh_external_context is True
    assert "web_search" in plan.allowed_tools
    assert "freshness_check" in plan.required_gates


def test_v2_warns_when_fresh_context_requested_but_web_disabled() -> None:
    intent = classify_request_intent("Look up the current CEO before answering.")
    plan = choose_tool_plan(intent, RagAssistedThinkingV2Policy(web_search_enabled=False))
    assert "web_search" not in plan.allowed_tools
    assert any("web_search_enabled is false" in warning for warning in plan.warnings)


def test_v2_json_parse_fails_closed_without_files() -> None:
    payload, warnings = parse_v2_control_payload("not json at all")
    assert payload["ok"] is False
    assert payload["action"] == "abstain"
    assert payload["files"] == []
    assert warnings


def test_v2_validation_rejects_unallowed_replacement_file() -> None:
    intent = classify_request_intent("Fix main_computer/rag_assisted_thinking.py")
    plan = choose_tool_plan(intent, RagAssistedThinkingV2Policy())
    payload = {
        "ok": True,
        "action": "propose_files",
        "files": [
            {
                "path": "main_computer/other.py",
                "content": "print('bad')\n",
                "evidence_paths": ["main_computer/rag_assisted_thinking.py"],
            }
        ],
    }
    errors = validate_v2_control_payload(
        payload,
        tool_plan=plan,
        allowed_write_paths=("main_computer/rag_assisted_thinking.py",),
        evidence_paths=("main_computer/rag_assisted_thinking.py",),
    )
    assert any("replacement path is not allowed" in error for error in errors)



class StaticJsonProvider:
    name = "static"
    model = "static-json"

    def __init__(self) -> None:
        self.messages: list[ChatMessage] = []

    def chat(self, messages):
        self.messages = list(messages)
        return ChatResponse(
            content=json.dumps(
                {
                    "ok": True,
                    "action": "answer",
                    "summary": "answered",
                    "answer": "print('hello world')",
                    "citations": [],
                    "files": [],
                    "commands": [],
                    "warnings": [],
                }
            ),
            provider=self.name,
            model=self.model,
            metadata={},
        )


def test_v2_general_answer_skips_local_rag_context(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'sample'\n", encoding="utf-8")
    provider = StaticJsonProvider()

    result = run_rag_assisted_thinking_v2_request(
        prompt="give me a hello world in python",
        repo_dir=tmp_path,
        provider=provider,
        policy=RagAssistedThinkingV2Policy(verify_before=False, verify_after=False),
    )

    assert result.intent["request_type"] == "general_answer"
    assert result.intent["rag_required"] is False
    assert result.retrieved_context_paths == []
    assert result.rag_result["status"] == "skipped"
    assert result.quality["retrieval_actions"] == ["skipped_no_rag_required"]

    payload = json.loads(provider.messages[-1].content)
    assert payload["rag_context"] == []
    assert payload["rag_summary"]["run_id"].endswith("_no_rag_required")



class BrokenThenRepairProvider:
    name = "broken-then-repair"
    model = "static-json-repair"

    def __init__(self) -> None:
        self.calls: list[list[ChatMessage]] = []

    def chat(self, messages):
        self.calls.append(list(messages))
        if len(self.calls) == 1:
            return ChatResponse(
                content='{"ok": true, "action": "answer", "summary": "broken", "answer": "unterminated"',
                provider=self.name,
                model=self.model,
                metadata={},
            )
        return ChatResponse(
            content=json.dumps(
                {
                    "ok": True,
                    "action": "answer",
                    "summary": "repaired",
                    "answer": "The control-plane JSON was repaired.",
                    "citations": [],
                    "files": [],
                    "commands": [],
                    "warnings": [],
                }
            ),
            provider=self.name,
            model=self.model,
            metadata={},
        )


def test_v2_repairs_malformed_control_json_once(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'sample'\n", encoding="utf-8")
    provider = BrokenThenRepairProvider()

    result = run_rag_assisted_thinking_v2_request(
        prompt="say hello",
        repo_dir=tmp_path,
        provider=provider,
        run_id="json_repair_once",
        policy=RagAssistedThinkingV2Policy(
            verify_before=False,
            verify_after=False,
            json_repair_enabled=True,
        ),
    )

    assert result.ok
    assert result.status == "completed"
    assert result.repair_payload["summary"] == "repaired"
    assert "repaired" in result.repair_payload["answer"]
    assert len(provider.calls) == 2
    assert any("control-plane JSON was repaired" in warning for warning in result.warnings)
    output_dir = tmp_path / "diagnostics_output" / "rag_assisted_thinking_v2_runs" / "json_repair_once"
    assert (output_dir / "json_repair_input.txt").exists()
    assert (output_dir / "json_repair_response.txt").exists()
    assert (output_dir / "json_repair_payload.json").exists()


def test_v2_recovers_replacement_file_from_malformed_large_json() -> None:
    malformed = (
        '{\n'
        '  "ok": true,\n'
        '  "action": "propose_files",\n'
        '  "summary": "candidate",\n'
        '  "files": [\n'
        '    {\n'
        '      "path": "new_patch.py",\n'
        '      "content": "import sys\\nprint("hello from replacement")\\n",\n'
        '      "evidence_paths": ["main_computer/rag_new_patch_recreation_tester_v4.py"]\n'
        '    }\n'
        '  ],\n'
        '  "commands": [],\n'
        '  "warnings": []\n'
        '}\n'
    )

    payload, warnings = parse_v2_control_payload(malformed)

    assert payload["ok"] is True
    assert payload["action"] == "propose_files"
    assert payload["files"][0]["path"] == "new_patch.py"
    assert payload["files"][0]["content"] == 'import sys\nprint("hello from replacement")'
    assert payload["files"][0]["evidence_paths"] == ["main_computer/rag_new_patch_recreation_tester_v4.py"]
    assert payload["files"][0]["recovered_from_malformed_json"] is True
    assert any("recovered deterministically" in warning for warning in warnings)
    assert any("deprecated model JSON repair" in warning for warning in payload["warnings"])


def test_v2_decodes_content_base64_file_payload() -> None:
    encoded = base64.b64encode(b'print("base64 safe")\n').decode("ascii")
    payload, warnings = parse_v2_control_payload(
        json.dumps(
            {
                "ok": True,
                "action": "propose_files",
                "summary": "base64",
                "answer": "",
                "citations": [],
                "files": [
                    {
                        "path": "new_patch.py",
                        "content_base64": encoded,
                        "evidence_paths": [],
                    }
                ],
                "commands": [],
                "warnings": [],
            }
        )
    )

    assert warnings == []
    assert payload["files"][0]["content"] == 'print("base64 safe")\n'
    assert payload["files"][0]["content_encoding"] == "base64"



def test_v2_diagnoses_completed_primary_output_with_no_extractable_file() -> None:
    malformed = (
        '{\n'
        '  "ok": true,\n'
        '  "action": "propose_files",\n'
        '  "files": [\n'
        '    {\n'
        '      "path": "new_patch.py",\n'
        '      "content": "import sys\\nprint("unterminated replacement")\\n"\n'
        '    }\n'
    )
    payload, warnings = parse_v2_control_payload(malformed)
    diagnostics = diagnose_malformed_control_payload(malformed, parse_warnings=warnings)

    assert payload["action"] == "abstain"
    assert warnings
    assert diagnostics["files_key_found"] is True
    assert diagnostics["candidate_paths"] == ["new_patch.py"]
    assert diagnostics["content_key_count"] == 1
    assert diagnostics["deterministic_recovery_file_count"] == 0
    assert diagnostics["likely_failure_reason"] in {
        "missing_content_boundary",
        "deterministic_recovery_returned_no_files",
    }


class AnswerOnlyFileChangeProvider:
    name = "answer-only-file-change"
    model = "static"

    def chat(self, messages):
        return ChatResponse(
            content=json.dumps(
                {
                    "ok": True,
                    "action": "answer",
                    "summary": "explained but did not propose files",
                    "answer": "I described the fix but did not include a replacement file.",
                    "citations": [],
                    "files": [],
                    "commands": [],
                    "warnings": [],
                }
            ),
            provider=self.name,
            model=self.model,
            metadata={},
        )


def test_v2_reports_primary_output_not_extractable_for_file_change(tmp_path) -> None:
    target = tmp_path / "main_computer" / "target.py"
    target.parent.mkdir(parents=True)
    target.write_text("print('old')\n", encoding="utf-8")

    result = run_rag_assisted_thinking_v2_request(
        prompt="Fix main_computer/target.py",
        repo_dir=tmp_path,
        provider=AnswerOnlyFileChangeProvider(),
        run_id="primary_not_extractable",
        policy=RagAssistedThinkingV2Policy(
            verify_before=False,
            verify_after=False,
            json_repair_enabled=False,
            allowed_write_paths=("main_computer/target.py",),
        ),
    )

    assert result.ok is False
    assert result.terminal_fault_type == "primary_output_not_extractable"
    assert result.terminal_fault_source == "primary"
    assert result.partial_content_chars > 0
    assert result.proposed_paths == []
    output_dir = tmp_path / "diagnostics_output" / "rag_assisted_thinking_v2_runs" / "primary_not_extractable"
    traces = json.loads((output_dir / "model_call_traces.json").read_text(encoding="utf-8"))
    assert traces["primary"]["content_chars"] > 0
    assert traces["primary"]["parse_diagnostics_path"] == ""


SELF_CONTAINED_NEW_PATCH_PROMPT = """
This is a benchmark run for the same new_patch.py task.
The benchmark task below is intentionally self-contained.
Do not copy or rely on an existing repository implementation of new_patch.py.
Write a robust, self-contained Python implementation or replacement for a patch-application script named new_patch.py.
"""


def test_detects_self_contained_new_patch_recreation_benchmark() -> None:
    assert is_self_contained_recreation_benchmark(SELF_CONTAINED_NEW_PATCH_PROMPT)


class ProposeFileProvider:
    name = "propose-file"
    model = "static"

    def __init__(self, path: str, content: str, evidence_paths: list[str] | None = None) -> None:
        self.path = path
        self.content = content
        self.evidence_paths = list(evidence_paths or [])

    def chat(self, messages):
        return ChatResponse(
            content=json.dumps(
                {
                    "ok": True,
                    "action": "propose_files",
                    "summary": "proposed",
                    "answer": "proposed replacement",
                    "citations": [],
                    "files": [
                        {
                            "path": self.path,
                            "content": self.content,
                            "evidence_paths": self.evidence_paths,
                        }
                    ],
                    "commands": [],
                    "warnings": [],
                }
            ),
            provider=self.name,
            model=self.model,
            metadata={},
        )


def test_v2_quality_gate_does_not_block_self_contained_new_patch_proposal(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'sample'\n", encoding="utf-8")

    result = run_rag_assisted_thinking_v2_request(
        prompt=SELF_CONTAINED_NEW_PATCH_PROMPT,
        repo_dir=tmp_path,
        provider=ProposeFileProvider("new_patch.py", "print('replacement')\n"),
        run_id="sc_bypass",
        policy=RagAssistedThinkingV2Policy(
            verify_before=False,
            verify_after=False,
            json_repair_enabled=False,
            auto_apply=False,
            allowed_write_paths=("new_patch.py",),
            min_quality_score=2.0,
        ),
    )

    assert result.ok is True
    assert result.status == "completed"
    assert result.proposed_paths == ["new_patch.py"]
    assert result.written_paths == []
    assert not (tmp_path / "new_patch.py").exists()
    assert result.terminal_fault_type != "proposals_blocked_by_retrieval_quality"
    assert result.self_contained_benchmark is True
    assert result.quality_gate_mode == "self_contained_benchmark"
    assert "allowed_write_paths still enforced" in result.quality_gate_bypassed_reasons
    assert any("retrieval quality gate bypassed for self-contained benchmark" in item for item in result.warnings)
    assert any("auto_apply is false" in item for item in result.warnings)

    output_dir = tmp_path / "diagnostics_output" / "rag_assisted_thinking_v2_runs" / "sc_bypass"
    result_json = json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
    assert result_json["self_contained_benchmark"] is True
    assert result_json["quality_gate_mode"] == "self_contained_benchmark"


def test_v2_normal_repo_file_proposal_still_blocked_by_low_retrieval_quality(tmp_path) -> None:
    target = tmp_path / "main_computer" / "target.py"
    target.parent.mkdir(parents=True)
    target.write_text("def old_target():\n    return 'old'\n", encoding="utf-8")

    result = run_rag_assisted_thinking_v2_request(
        prompt="Fix main_computer/target.py so old_target returns a new value.",
        repo_dir=tmp_path,
        provider=ProposeFileProvider(
            "main_computer/target.py",
            "def old_target():\n    return 'new'\n",
            evidence_paths=["main_computer/target.py"],
        ),
        queries=["main_computer/target.py old_target"],
        run_id="repo_block",
        policy=RagAssistedThinkingV2Policy(
            verify_before=False,
            verify_after=False,
            json_repair_enabled=False,
            auto_apply=False,
            allowed_write_paths=("main_computer/target.py",),
            min_quality_score=2.0,
        ),
    )

    assert result.ok is False
    assert result.status == "failed"
    assert result.proposed_paths == []
    assert result.terminal_fault_type == "proposals_blocked_by_retrieval_quality"
    assert result.terminal_fault_source == "retrieval_quality"
    assert result.self_contained_benchmark is False


def test_v2_self_contained_benchmark_still_enforces_allowed_paths(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'sample'\n", encoding="utf-8")

    result = run_rag_assisted_thinking_v2_request(
        prompt=SELF_CONTAINED_NEW_PATCH_PROMPT,
        repo_dir=tmp_path,
        provider=ProposeFileProvider("../new_patch.py", "print('unsafe')\n"),
        run_id="sc_path",
        policy=RagAssistedThinkingV2Policy(
            verify_before=False,
            verify_after=False,
            json_repair_enabled=False,
            auto_apply=False,
            allowed_write_paths=("new_patch.py",),
            min_quality_score=2.0,
        ),
    )

    assert result.ok is False
    assert result.proposed_paths == []
    assert result.written_paths == []
    assert not (tmp_path / "new_patch.py").exists()
    assert result.terminal_fault_type == "control_payload_validation_failed"
    assert result.terminal_fault_source == "validation"
    assert "unsafe" in result.terminal_fault_message.lower()
    assert result.terminal_fault_type != "proposals_blocked_by_retrieval_quality"
