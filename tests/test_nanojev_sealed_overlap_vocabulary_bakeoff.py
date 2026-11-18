"""Offline-only vocabulary-growth bakeoff tests: no Qwen, Ollama, or networking."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_sealed_overlap_information_smoke as smoke
from tools import nanojev_sealed_overlap_generalization_frob as factorial
from tools import nanojev_sealed_overlap_vocabulary_bakeoff as lab


def fixture_baseline(tmp_path: Path, space: dict):
    axes = [d["id"] for d in space["dimensions"]]
    vocab = [{"id": f"v{i+1:04d}",
              "description": f"Diagnostic semantic entry {i+1}: {space['dimensions'][i%8]['definition']}",
              "dimensions": [axes[i % 8]]} for i in range(24)]
    base = {"schema": smoke.SCHEMA,
            "status": "COMPLETE_DIAGNOSTIC_NO_SEAL_NO_PROMOTION",
            "human_space_sha256": smoke.seal(space),
            "candidate_vocabulary_sha256": smoke.seal(vocab),
            "candidate_vocabulary_approved_or_sealed": False,
            "selected_vocabulary_size": 24,
            "human_dimensions": axes,
            "calibration": {"train_count": 90, "selection_count": 24,
                            "final_test_count": 36, "seed": 41},
            "model": {"frozen": True, "backbone": smoke.MODEL_ID},
            "measurement": {"ridge": 0.5}, "vocabulary": vocab}
    base_path = tmp_path / "v2.json"
    base_path.write_text(json.dumps(base), encoding="utf-8")
    old = {"schema": factorial.SCHEMA,
           "v2_report_sha256": sha256(base_path.read_bytes()).hexdigest(),
           "human_space_sha256": smoke.seal(space),
           "vocabulary_sha256": smoke.seal(vocab),
           "vocabulary_unchanged_no_ollama_calls": True,
           "cells": {}}
    for cell in ("known_paraphrase", "novel_paraphrase"):
        old["cells"][cell] = {"per_case": [
            {"dimensions": ["obligation", "time"],
             "vocabulary_predictions": ["time", "resource"]},
            {"dimensions": ["authority", "exclusion"],
             "vocabulary_predictions": ["authority", "consequence"]}]}
    old_path = tmp_path / "factorial.json"
    old_path.write_text(json.dumps(old), encoding="utf-8")
    return base_path, old_path, base


def fake_encoder(space):
    phrases = [[*d["phrases"], *factorial.read_paraphrases(factorial.DEFAULT_PARAPHRASES, space)[d["id"]],
                *lab.read_growth_probes(lab.DEFAULT_PROBES, space)["selection"][d["id"]],
                *lab.read_growth_probes(lab.DEFAULT_PROBES, space)["final"][d["id"]],
                d["definition"]] for d in space["dimensions"]]
    def encode(texts):
        arr = np.zeros((len(texts), len(phrases) + 1), dtype=float)
        for i, line in enumerate(texts):
            for j, options in enumerate(phrases):
                arr[i,j] = 1.0 if any(t in line for t in options) else 0.0
            arr[i,-1] = .01
        return arr
    return encode


def fake_proposer(space, existing, count, focus, **kwargs):
    axes = [d["id"] for d in space["dimensions"]]
    return [{"id": f"v{len(existing)+i+1:04d}",
             "description": f"Additional unique semantic description number {len(existing)+i+1}: {space['dimensions'][(len(existing)+i)%8]['definition']}",
             "dimensions": [axes[(len(existing)+i)%8]]} for i in range(count)]


def args_for(tmp_path, base_path, old_path, *extra):
    return lab.parser().parse_args(["--baseline-report", str(base_path),
                                    "--factorial-report", str(old_path),
                                    "--output", str(tmp_path / "growth.json"),
                                    "--null-trials", "19", "--bootstrap-trials", "39", *extra])


def test_bakeoff_new_probes_and_combinations_are_strictly_held_out(tmp_path):
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    _, _, baseline = fixture_baseline(tmp_path, space)
    probes = lab.read_growth_probes(lab.DEFAULT_PROBES, space)
    train, cases, design = lab.build_new_cases(space, baseline, probes, seed=226,
                                                 count_five=12, count_six=6)
    assert len(train) == 90
    assert design["selection_final_combinations_disjoint"]
    assert design["never_used_in_old_one_to_four_dimension_trials"]
    assert design["selection_final_paraphrase_pools_disjoint"]
    assert design["samples_per_wording_per_phase"] == 36
    all_prompts = []
    for phase in lab.PHASES:
        for wording in lab.WORDINGS:
            rows = cases[phase][wording]
            assert len(rows) == 36
            assert {len(r["dimensions"]) for r in rows} == {5, 6}
            all_prompts.extend(r["prompt"] for r in rows)
    assert len(all_prompts) == len(set(all_prompts)) == 144
    assert [r["dimensions"] for r in cases["final"]["familiar"]] == [r["dimensions"] for r in cases["final"]["paraphrase"]]


def test_altered_prior_source_refuses_to_drive_vocab_generation(tmp_path):
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    baseline_path, prior_path, baseline = fixture_baseline(tmp_path, space)
    result = lab.read_failure_focus(prior_path, baseline, baseline_path, space)
    assert result["per_axis"]["obligation"]["false_negative"] == 2
    assert result["per_axis"]["resource"]["false_positive"] == 2
    obj = json.loads(prior_path.read_text(encoding="utf-8"))
    obj["vocabulary_sha256"] = "tampered"
    prior_path.write_text(json.dumps(obj), encoding="utf-8")
    with pytest.raises(ValueError, match="NOT_MATCHING"):
        lab.read_failure_focus(prior_path, baseline, baseline_path, space)


def test_bad_growth_probe_leakage_rejected(tmp_path):
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    obj = json.loads(lab.DEFAULT_PROBES.read_text(encoding="utf-8"))
    obj["final"]["authority"][1] = obj["selection"]["authority"][0]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(obj), encoding="utf-8")
    with pytest.raises(ValueError, match="LEXICAL_LEAKAGE"):
        lab.read_growth_probes(path, space)


@pytest.mark.parametrize("sizes", ["36,48", "24,24,36", "24,18", "24,161", "24,36,30", "not-integers"])
def test_bakeoff_rejects_wrong_sizes(sizes):
    with pytest.raises(ValueError, match="SIZES_"):
        lab.parse_sizes(sizes, 24)


def test_candidate_generation_rejects_missing_or_unauthorized_terms():
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    initial = [{"id": "v0001", "description": "An authorized decision determines access", "dimensions": ["authority"]}]
    def partial(space, existing, count, focus, **kwargs):
        return []
    with pytest.raises(ValueError, match="INCOMPLETE_OLLAMA_VOCABULARY"):
        lab.grow_vocabulary(space, initial, 2, {}, proposer=partial,
                            attempts=2, model="fake", base_url="fake", timeout=1)
    def malicious(space, existing, count, focus, **kwargs):
        return [{"id": "v0002", "description": "Malformed description with unknown axis", "dimensions": ["nonexistent"]}]
    with pytest.raises(ValueError, match="UNAUTHORIZED_DIMENSION"):
        lab.grow_vocabulary(space, initial, 2, {}, proposer=malicious,
                            attempts=1, model="fake", base_url="fake", timeout=1)


def test_selection_winner_is_independent_of_final_results():
    def arm(n, score):
        return {"entries":n, "selection_score":score, "cells":{"final_paraphrase": {"exact_semantic_pattern_accuracy": 0.0}}}
    arms = [arm(24, .4), arm(36, .49), arm(48, .50), arm(72, .49)]
    assert lab.choose_size(arms, min_gain=.02)["selected_entries"] == 36
    arms[-1]["cells"]["final_paraphrase"]["exact_semantic_pattern_accuracy"] = 1.0
    assert lab.choose_size(arms, min_gain=.02)["selected_entries"] == 36
    assert lab.choose_size([arm(24,.4), arm(36,.41)], min_gain=.02)["selected_entries"] == 24


def test_paired_effect_detects_improvement():
    rows = [{"group_id": f"g{i}"} for i in range(12) for _ in range(2)]
    truth = np.array([[i%2, (i//2)%2] for i in range(12) for _ in range(2)],dtype=np.int8)
    b = truth.copy()
    b[:,0] = 1-b[:,0]
    result = lab.paired_improvement(truth, b, truth, rows, seed=13, trials=39)
    assert result["mean_axis_accuracy_delta"] == .5
    assert result["exact_pattern_accuracy_delta"] == 1.0
    assert result["macro_information_bits_delta"] == 0.0  # anticorrelation also preserves MI


def test_end_to_end_offline_bakeoff_writes_correct_report(tmp_path):
    space = smoke.load_space(smoke.SPACE_DEFAULT)
    baseline_path, old_path, baseline = fixture_baseline(tmp_path, space)
    args = args_for(tmp_path, baseline_path, old_path, "--sizes", "24,36,48")
    result = lab.run(args, proposer=fake_proposer, encoder=fake_encoder(space))
    assert result["status"].startswith("COMPLETE_VOCABULARY_GROWTH")
    assert result["acceptance"]["never_authorizes_contract_seal"] is True
    assert result["model"]["frozen"] is True
    assert result["experiment"]["selection_final_combinations_disjoint"] is True
    assert result["experiment"]["selection_final_paraphrase_pools_disjoint"] is True
    assert result["source"]["baseline_vocabulary_sha256"] == baseline["candidate_vocabulary_sha256"]
    assert [x["entries"] for x in result["arms"]] == [24,36,48]
    assert len(result["all_candidate_vocabulary_unapproved_unsealed"]) == 48
    assert result["arms"][0]["new_entries_vs_baseline"] == 0
    for arm in result["arms"]:
        assert arm["cells"]["selection_familiar"]["sample_count"] == 36
        assert arm["cells"]["final_paraphrase"]["sample_count"] == 36
        assert "macro_information_bits_delta" in arm["paired_vs_24"]["final_paraphrase"]
    assert json.loads(args.output.read_text(encoding="utf-8")) == result
    with pytest.raises(FileExistsError, match="OUTPUT_EXISTS"):
        lab.run(args, proposer=fake_proposer, encoder=fake_encoder(space))


def test_provider_http_500_preserves_error_body_and_request_diagnostics(monkeypatch):
    import io
    from urllib.error import HTTPError

    space = smoke.load_space(smoke.SPACE_DEFAULT)
    existing = fake_proposer(space, [], 96, {"axes_ranked_by_paraphrase_errors": []})
    focus = {"axes_ranked_by_paraphrase_errors": [d["id"] for d in space["dimensions"]]}
    sent = []

    def http_failure(request, timeout):
        payload = json.loads(request.data)
        prompt = payload["messages"][0]["content"]
        sent.append(prompt)
        raise HTTPError(request.full_url, 500, "Internal Server Error", {},
                        io.BytesIO(b'{"error":"model runner out of memory"}'))

    monkeypatch.setattr(lab, "urlopen", http_failure)
    with pytest.raises(lab.OllamaProviderHTTPError, match="model runner out of memory") as exc:
        lab.ollama_targeted_propose(space, existing, 16, focus,
                                   model="local-model", base_url="http://127.0.0.1:11434", timeout=2)
    assert exc.value.status == 500
    assert exc.value.requested == 16
    assert exc.value.prompt_chars == len(sent[0])
    assert "Current vocabulary contains 96 entries" in sent[0]
    assert "model runner out of memory" in str(exc.value)
    # The existing vocabulary is verified locally; it is never sent wholesale.
    refs = lab.bounded_existing_descriptions(existing, focus["axes_ranked_by_paraphrase_errors"])
    assert len(refs) <= 36
    assert existing[-1]["description"] in refs
    assert existing[0]["description"] in refs
    assert "model runner out of memory" not in sent[0]
