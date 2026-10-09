from __future__ import annotations

import json
from types import SimpleNamespace

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "nanojev_rom_template_execution_smoke.py"
SPEC = importlib.util.spec_from_file_location("nanojev_rom_template_execution_smoke", MODULE_PATH)
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def fixtures():
    structured = {
        "rom_version": 2,
        "inputs": [{"name": "report_a"}, {"name": "report_b"}, {"name": "report_c"}],
        "outputs": [{"name": "outlier"}],
        "locals": [],
        "program": [
            {"op": "BEGIN"},
            {"op": "BIND", "object": "A", "input": "report_a"},
            {"op": "BIND", "object": "B", "input": "report_b"},
            {"op": "BIND", "object": "C", "input": "report_c"},
            {"op": "RELATE", "left": "A", "right": "B"},
            {"op": "RESOLVE", "output": "outlier"},
            {"op": "COMMIT", "output": "outlier"},
            {"op": "END"},
        ],
    }
    flat = {
        **{k: v for k, v in structured.items() if k != "program"},
        "program": ["BEGIN", "BIND A <- report_a", "BIND B <- report_b", "BIND C <- report_c", "RELATE A B", "RESOLVE outlier", "COMMIT outlier", "END"],
    }
    return flat, structured


def test_recover_both_program_serializations():
    flat, structured = fixtures()
    fr = smoke.recover_template(flat, "flat")
    sr = smoke.recover_template(structured, "structured")
    assert fr["usable"] is True
    assert sr["usable"] is True
    assert fr["runtime_port_map"] == sr["runtime_port_map"] == {
        "candidate_a": "report_a",
        "candidate_b": "report_b",
        "candidate_c": "report_c",
    }


def test_style_drift_is_diagnostic_not_fatal():
    flat, _ = fixtures()
    result = smoke.recover_template(flat, "structured")
    assert result["usable"] is True
    assert result["style_compliant"] is False
    assert result["observed_style"] == "flat"


def test_build_payload_reuses_each_template_four_times():
    flat, structured = fixtures()
    compile_results = {
        "flat": {"style": "flat", "parsed": flat, "recovery": smoke.recover_template(flat, "flat")},
        "structured": {"style": "structured", "parsed": structured, "recovery": smoke.recover_template(structured, "structured")},
    }
    payload, meta = smoke.build_nanojev_payload(compile_results)
    assert len(payload["states"]) == 12
    assert len(meta) == 12
    assert sum(1 for m in meta.values() if m["arm"] == "flat") == 4
    assert sum(1 for m in meta.values() if m["arm"] == "structured") == 4
    assert sum(1 for m in meta.values() if m["arm"] == "baseline") == 4


def test_rotations_have_a_b_c_none_gold():
    assert [case["gold"] for case in smoke.BINDING_CASES] == [
        "candidate_c", "candidate_b", "candidate_a", "none"
    ]


def test_analysis_prefers_accuracy_then_gold_probability():
    rows = []
    for arm, correct, gold_p in (
        ("baseline", 3, 0.60),
        ("flat", 4, 0.55),
        ("structured", 4, 0.70),
    ):
        for i in range(4):
            rows.append({
                "arm": arm,
                "correct": i < correct,
                "gold_probability": gold_p,
                "top_margin": 0.2,
                "state_chars": 1000,
            })
    analysis = smoke.analyze_rows(rows)
    assert analysis["downstream_winner"] == "structured"
    assert analysis["arms"]["flat"]["accuracy"] == 1.0
    assert analysis["arms"]["structured"]["accuracy"] == 1.0


def test_self_test():
    smoke._self_test()


def _catalog_fixture(style: str):
    mix = smoke.discover_training_mix()
    templates = []
    for task in mix["tasks"]:
        contract = smoke.TASK_FAMILY_CONTRACTS[task]
        inputs = [
            {"name": name, "binding": {"status": "UNBOUND", "source": None}}
            for name in contract["suggested_inputs"]
        ]
        if task == "consensus" and style == "flat":
            program = [
                "BEGIN", "BIND A <- candidate_a", "BIND B <- candidate_b",
                "BIND C <- candidate_c", "RELATE A B", "RELATE A C", "RELATE B C",
                "OBJECTIVE consensus", "RESOLVE outlier", "COMMIT outlier", "END",
            ]
        elif task == "consensus":
            program = [
                {"op": "BEGIN"},
                {"op": "BIND", "object": "A", "input": "candidate_a"},
                {"op": "BIND", "object": "B", "input": "candidate_b"},
                {"op": "BIND", "object": "C", "input": "candidate_c"},
                {"op": "RELATE", "left": "A", "right": "B"},
                {"op": "RELATE", "left": "A", "right": "C"},
                {"op": "RELATE", "left": "B", "right": "C"},
                {"op": "OBJECTIVE", "name": "consensus"},
                {"op": "RESOLVE", "output": "outlier"},
                {"op": "COMMIT", "output": "outlier"},
                {"op": "END"},
            ]
        elif style == "flat":
            program = [
                "BEGIN",
                *[f"BIND X{i} <- {name}" for i, name in enumerate(contract["suggested_inputs"])],
                f"OBJECTIVE {task}",
                f"RESOLVE {contract['suggested_output']}",
                f"COMMIT {contract['suggested_output']}",
                "END",
            ]
        else:
            program = [
                {"op": "BEGIN"},
                *[
                    {"op": "BIND", "object": f"X{i}", "input": name}
                    for i, name in enumerate(contract["suggested_inputs"])
                ],
                {"op": "OBJECTIVE", "name": task},
                {"op": "RESOLVE", "output": contract["suggested_output"]},
                {"op": "COMMIT", "output": contract["suggested_output"]},
                {"op": "END"},
            ]
        templates.append({
            "task": task,
            "rom_version": 2,
            "inputs": inputs,
            "outputs": [{"name": contract["suggested_output"]}],
            "locals": [],
            "program": program,
            "unresolved": [],
            "decision": None,
        })
    return mix, {"catalog_version": 1, "templates": templates}


def test_discovers_current_training_mix_and_native_plans():
    mix = smoke.discover_training_mix()
    assert mix["tasks"] == [
        "legacy", "mutation", "ast", "consensus", "triad", "dictionary_definition", "english_code"
    ]
    assert mix["train_plan"] == {
        "legacy": 10,
        "mutation": 18,
        "ast": 38,
        "consensus": 28,
        "triad": 18,
        "dictionary_definition": 24,
        "english_code": 24,
    }
    assert mix["dev_plan"] == {
        "legacy": 3,
        "mutation": 6,
        "ast": 10,
        "consensus": 8,
        "triad": 6,
        "dictionary_definition": 7,
        "english_code": 8,
    }


def test_recovers_complete_flat_and_structured_training_mix_catalogs():
    for style in ("flat", "structured"):
        mix, parsed = _catalog_fixture(style)
        recovered = smoke.recover_catalog(parsed, style, mix)
        assert recovered["usable"] is True
        assert list(recovered["task_results"]) == mix["tasks"]
        assert all(item["recovery"]["style_compliant"] for item in recovered["task_results"].values())


def test_training_mix_catalog_cannot_silently_drop_a_task():
    mix, parsed = _catalog_fixture("flat")
    parsed["templates"] = [row for row in parsed["templates"] if row["task"] != "triad"]
    recovered = smoke.recover_catalog(parsed, "flat", mix)
    assert recovered["usable"] is False
    assert recovered["missing_tasks"] == ["triad"]


def test_consensus_execution_reuses_catalog_template_instead_of_recompile():
    mix, parsed = _catalog_fixture("flat")
    recovered = smoke.recover_catalog(parsed, "flat", mix)
    catalog_result = {
        "style": "flat",
        "error": None,
        "elapsed_s": 1.0,
        "response_chars": 100,
        "stream_summary": {"eval_count": 10},
        "parsed": parsed,
        "recovery": recovered,
    }
    compile_result = smoke.consensus_compile_result_from_catalog(catalog_result)
    assert compile_result["parsed"]["task"] == "consensus"
    assert compile_result["recovery"]["usable"] is True



def test_compile_training_mix_catalog_has_live_prompt_and_serialization_suffix(monkeypatch, tmp_path):
    seen = []

    def fake_call(*, payload, url, timeout_s, log, raw_path, stream_label):
        seen.append(payload)
        requested = "structured" if "STRUCTURED PROGRAM" in payload["system"] else "flat"
        _mix, parsed = _catalog_fixture(requested)
        return json.dumps(parsed), {"eval_count": 1, "done": True}

    monkeypatch.setattr(smoke.ollama_stream, "call_ollama_generate_streaming", fake_call)

    class FakeLog:
        def banner(self, _message):
            pass

        def __call__(self, _message):
            pass

    args = SimpleNamespace(
        model="fixture-model",
        ollama_url="http://127.0.0.1:11434/api/generate",
        keep_alive="30m",
        catalog_num_predict=9000,
        timeout_s=0,
    )
    mix = smoke.discover_training_mix()
    flat = smoke.compile_training_mix_catalog(
        style="flat", args=args, out_dir=tmp_path, log=FakeLog(), mix=mix,
    )
    structured = smoke.compile_training_mix_catalog(
        style="structured", args=args, out_dir=tmp_path, log=FakeLog(), mix=mix,
    )
    assert flat["recovery"]["usable"] is True
    assert structured["recovery"]["usable"] is True
    assert "ROM Training-Mix Compiler" in seen[0]["system"]
    assert "FLAT PROGRAM" in seen[0]["system"]
    assert "instruction strings only" in seen[0]["system"]
    assert "ROM Training-Mix Compiler" in seen[1]["system"]
    assert "STRUCTURED PROGRAM" in seen[1]["system"]
    assert "instruction objects only" in seen[1]["system"]

