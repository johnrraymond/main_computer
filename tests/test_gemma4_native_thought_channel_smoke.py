from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "gemma4_native_thought_channel_smoke_v2.py"
)
SPEC = importlib.util.spec_from_file_location(
    "gemma4_native_thought_channel_smoke_under_test",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
smoke = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = smoke
SPEC.loader.exec_module(smoke)


def good_inspection() -> smoke.ModelInspection:
    return smoke.ModelInspection(
        requested_model="gemma4:26b",
        ollama_version="0.20.7",
        listed_name="gemma4:26b",
        digest=(
            "5571076f3d70050487b26b341705799e"
            "0ab29b808164f90d20d4cf84f699d251"
        ),
        family="gemma4",
        parameter_size="25.8B",
        quantization_level="Q4_K_M",
        capabilities=["completion", "vision", "tools", "thinking"],
        template_sha256="template-hash",
        api_show_template_markers_present={
            "<|turn>": False,
            "<turn|>": False,
            "<|think|>": False,
            "<|channel>": False,
            "<channel|>": False,
            "<|tool_response>": False,
        },
        modelfile_sha256="modelfile-hash",
    )


def test_missing_api_show_markers_are_diagnostic_not_provenance_failure() -> None:
    inspection = good_inspection()
    failures = smoke.provenance_failures(
        inspection=inspection,
        expected_digest_prefix=smoke.DEFAULT_EXPECTED_DIGEST_PREFIX,
        allow_digest_drift=False,
        allow_custom_model=False,
    )
    assert failures == []


def test_digest_family_and_capabilities_remain_provenance_gates() -> None:
    inspection = good_inspection()
    inspection.digest = "bad-digest"
    inspection.family = "not-gemma4"
    inspection.capabilities = ["completion"]

    failures = smoke.provenance_failures(
        inspection=inspection,
        expected_digest_prefix=smoke.DEFAULT_EXPECTED_DIGEST_PREFIX,
        allow_digest_drift=False,
        allow_custom_model=False,
    )

    assert any("digest" in item for item in failures)
    assert any("family" in item for item in failures)
    assert any("'thinking'" in item for item in failures)
    assert any("'tools'" in item for item in failures)


def test_inspection_records_hidden_renderer_markers_as_warning(monkeypatch) -> None:
    def fake_http_json(*, method, url, payload, timeout):
        del method, payload, timeout
        if url.endswith("/api/version"):
            return {"version": "0.20.7"}
        if url.endswith("/api/tags"):
            return {
                "models": [
                    {
                        "name": "gemma4:26b",
                        "digest": (
                            "5571076f3d70050487b26b341705799e"
                            "0ab29b808164f90d20d4cf84f699d251"
                        ),
                    }
                ]
            }
        if url.endswith("/api/show"):
            return {
                "details": {
                    "family": "gemma4",
                    "parameter_size": "25.8B",
                    "quantization_level": "Q4_K_M",
                },
                "capabilities": ["completion", "vision", "tools", "thinking"],
                "template": "{{ .Prompt }}",
                "modelfile": "FROM gemma4:26b",
                "system": "",
            }
        raise AssertionError(url)

    monkeypatch.setattr(smoke, "http_json", fake_http_json)
    inspection = smoke.inspect_model(
        base_url="http://localhost:11434",
        model="gemma4:26b",
        timeout=1.0,
    )

    assert inspection.ollama_version == "0.20.7"
    assert not any(inspection.api_show_template_markers_present.values())
    assert any("diagnostic only" in warning for warning in inspection.warnings)
    assert smoke.provenance_failures(
        inspection=inspection,
        expected_digest_prefix=smoke.DEFAULT_EXPECTED_DIGEST_PREFIX,
        allow_digest_drift=False,
        allow_custom_model=False,
    ) == []


def test_attack_cases_use_native_gemma4_tokens() -> None:
    prompts = smoke.emit_prompts()
    combined = "\n".join(
        row["user"]
        for case_id, row in prompts.items()
        if case_id != "clean_control"
    )
    for token in (
        "<|think|>",
        "<|channel>thought",
        "<channel|>",
        "<|turn>",
        "<turn|>",
        "<|tool_call>",
        "<|tool_response>",
    ):
        assert token in combined


def test_deterministic_self_test_passes() -> None:
    report = smoke.self_test()
    assert report["ok"] is True
