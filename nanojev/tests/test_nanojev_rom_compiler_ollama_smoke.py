from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import unittest
from typing import Any

from tools import nanojev_rom_compiler_ollama_smoke as smoke


class RomInterfaceCompilerSmokeTests(unittest.TestCase):
    def test_implicit_consensus_fixture_passes(self) -> None:
        payload = smoke._fixture_program(
            input_names=["report_a", "report_b", "report_c"],
            output_name="outlier",
            objective="consensus_outlier_or_none",
            relations=3,
        )
        _, grade = smoke.grade_response(smoke.CASES["implicit_consensus"], json.dumps(payload))
        self.assertTrue(grade.ok, grade.as_dict())

    def test_implicit_question_rejects_invented_source_binding(self) -> None:
        payload = smoke._fixture_program(
            input_names=["report_a", "report_b", "report_c"],
            output_name="outlier",
            objective="consensus_outlier_or_none",
            relations=3,
        )
        payload["inputs"][0]["binding"] = {"status": "BOUND", "source": "runtime.state.validator_a"}
        _, grade = smoke.grade_response(smoke.CASES["implicit_consensus"], json.dumps(payload))
        self.assertFalse(grade.ok)
        self.assertFalse(grade.checks["binding_honesty_unbound"])

    def test_explicit_sources_must_be_preserved_exactly(self) -> None:
        payload = smoke._fixture_program(
            input_names=["proof", "validator_state"],
            output_name="is_valid",
            objective="validate_proof",
            bound_sources={"proof": "guardian.last_proof", "validator_state": "validator_a.state"},
        )
        _, grade = smoke.grade_response(smoke.CASES["explicit_sources"], json.dumps(payload))
        self.assertTrue(grade.ok, grade.as_dict())

        payload["inputs"][0]["binding"]["source"] = "guardian.proof"
        _, grade = smoke.grade_response(smoke.CASES["explicit_sources"], json.dumps(payload))
        self.assertFalse(grade.ok)
        self.assertFalse(grade.checks["bound_source_guardian.last_proof"])

    def test_compiler_must_not_answer(self) -> None:
        payload = smoke._fixture_program(
            input_names=["report_a", "report_b", "report_c"],
            output_name="outlier",
            objective="consensus_outlier_or_none",
            relations=3,
        )
        payload["decision"] = "C"
        _, grade = smoke.grade_response(smoke.CASES["implicit_consensus"], json.dumps(payload))
        self.assertFalse(grade.ok)
        self.assertFalse(grade.checks["decision_null"])

    def test_consensus_requires_three_relation_locals(self) -> None:
        payload = smoke._fixture_program(
            input_names=["report_a", "report_b", "report_c"],
            output_name="outlier",
            objective="consensus_outlier_or_none",
            relations=3,
        )
        payload["locals"] = []
        _, grade = smoke.grade_response(smoke.CASES["implicit_consensus"], json.dumps(payload))
        self.assertFalse(grade.ok)
        self.assertFalse(grade.checks["relation_locals_declared"])

    def test_program_must_bind_every_inferred_input(self) -> None:
        payload = smoke._fixture_program(
            input_names=["current_loss", "incumbent_loss", "current_accuracy", "incumbent_accuracy"],
            output_name="should_promote",
            objective="promote_model",
        )
        payload["program"] = [
            item for item in payload["program"]
            if not (item.get("op") == "BIND" and item.get("input") == "incumbent_accuracy")
        ]
        _, grade = smoke.grade_response(smoke.CASES["implicit_promotion"], json.dumps(payload))
        self.assertFalse(grade.ok)
        self.assertFalse(grade.checks["program_binds_inputs"])

    def test_self_spec_flat_and_structured_ops_are_graded_separately(self) -> None:
        base = {
            "rom_spec": {
                "protocol": "ROM-INTERFACE",
                "version": 2,
                "paired_ops": dict(smoke.PAIRED_OPS),
                "port_schema": {"input": ["name", "type", "role", "binding"]},
                "binding_rules": ["unnamed sources remain UNBOUND"],
                "program_invariants": ["BEGIN first", "END last"],
            }
        }

        flat = json.loads(json.dumps(base))
        flat["rom_spec"]["ops"] = sorted(smoke.ROM_OPS)
        _, flat_grade = smoke.grade_response(smoke.CASES["self_spec_flat_ops"], json.dumps(flat))
        self.assertTrue(flat_grade.ok, flat_grade.as_dict())
        self.assertTrue(flat_grade.checks["ops_format_flat"])

        structured = json.loads(json.dumps(base))
        structured["rom_spec"]["ops"] = [
            {"op": op, "description": f"description for {op}"}
            for op in sorted(smoke.ROM_OPS)
        ]
        _, structured_grade = smoke.grade_response(
            smoke.CASES["self_spec_structured_ops"],
            json.dumps(structured),
        )
        self.assertTrue(structured_grade.ok, structured_grade.as_dict())
        self.assertTrue(structured_grade.checks["ops_format_structured"])

        _, wrong_flat = smoke.grade_response(smoke.CASES["self_spec_flat_ops"], json.dumps(structured))
        self.assertFalse(wrong_flat.ok)
        self.assertFalse(wrong_flat.checks["ops_format_flat"])

        _, wrong_structured = smoke.grade_response(
            smoke.CASES["self_spec_structured_ops"],
            json.dumps(flat),
        )
        self.assertFalse(wrong_structured.ok)
        self.assertFalse(wrong_structured.checks["ops_format_structured"])

    def test_self_spec_pair_contract_is_still_required(self) -> None:
        payload = {
            "rom_spec": {
                "protocol": "ROM-INTERFACE",
                "version": 2,
                "paired_ops": dict(smoke.PAIRED_OPS),
                "ops": sorted(smoke.ROM_OPS),
                "port_schema": {"input": ["name", "type", "role", "binding"]},
                "binding_rules": ["unnamed sources remain UNBOUND"],
                "program_invariants": ["BEGIN first", "END last"],
            }
        }
        payload["rom_spec"]["paired_ops"]["OPEN"] = "DONE"
        _, grade = smoke.grade_response(smoke.CASES["self_spec_flat_ops"], json.dumps(payload))
        self.assertFalse(grade.ok)
        self.assertFalse(grade.checks["pair_open"])

    def test_ab_comparison_prefers_quality_before_speed(self) -> None:
        results = [
            {
                "case": "self_spec_flat_ops",
                "ok": True,
                "grade": {"score": 1.0, "passed_checks": 16, "total_checks": 16},
                "elapsed_s": 120.0,
                "response_chars": 1000,
                "stream_summary": {"eval_count": 400},
            },
            {
                "case": "self_spec_structured_ops",
                "ok": False,
                "grade": {"score": 0.95, "passed_checks": 15, "total_checks": 16},
                "elapsed_s": 80.0,
                "response_chars": 1500,
                "stream_summary": {"eval_count": 500},
            },
        ]
        comparison = smoke.build_self_spec_ab_comparison(results)
        self.assertIsNotNone(comparison)
        assert comparison is not None
        self.assertEqual(comparison["quality_winner"], "flat")
        self.assertEqual(comparison["latency_winner"], "structured")
        self.assertEqual(comparison["overall_winner"], "flat")

    def test_suite_persists_exact_question_and_results_with_fake_ollama(self) -> None:
        def caller(**kwargs: Any) -> tuple[str, dict[str, Any]]:
            prompt = kwargs["payload"]["prompt"]
            if prompt == smoke.CASES["implicit_consensus"].prompt:
                payload = smoke._fixture_program(
                    input_names=["report_a", "report_b", "report_c"],
                    output_name="outlier",
                    objective="consensus_outlier_or_none",
                    relations=3,
                )
            else:
                raise AssertionError("unexpected prompt")
            Path(kwargs["raw_path"]).write_text('{"done":true}\n', encoding="utf-8")
            return json.dumps(payload), {"done": True, "eval_count": 10}

        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(
                model="fake-model",
                url="http://127.0.0.1:11434/api/generate",
                cases=["implicit_consensus"],
                out_root=tmp,
                run_id="test-run",
                num_predict=400,
                keep_alive="1m",
                timeout_s=0,
                quiet=True,
            )
            rc = smoke.run_suite(args, caller=caller)
            self.assertEqual(rc, 0)
            report = json.loads((Path(tmp) / "test-run" / "report.json").read_text(encoding="utf-8"))
            self.assertTrue(report["ok"])
            self.assertEqual(report["rom_version"], 2)
            case_dir = Path(tmp) / "test-run" / "cases" / "implicit_consensus"
            self.assertEqual(
                (case_dir / "user_prompt.txt").read_text(encoding="utf-8").strip(),
                smoke.CASES["implicit_consensus"].prompt,
            )
            self.assertTrue((case_dir / "system_prompt.txt").exists())
            self.assertTrue((case_dir / "response.txt").exists())
            self.assertTrue((case_dir / "parsed.json").exists())
            self.assertTrue((case_dir / "grade.json").exists())
            self.assertTrue((case_dir / "raw_stream.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
