from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "tools" / "space_captain_clef_backend.py"
LIVE_SMOKE = ROOT / "tools" / "space_captain_live_clef_smoke.py"
CONTRACT_SMOKE = ROOT / "tools" / "space_captain_differentiable_smoke.py"
CLEF_SMOKE = ROOT / "tools" / "nanojev_three_backbone_clef_sized_live_train_smoke.py"
TRAIN_SCRIPT = ROOT / "tools" / "nanojev_three_backbone_clef_tinystories_consensus_pairwise_train.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_live_clef_tools_have_help_without_loading_cuda() -> None:
    for script in (BACKEND, LIVE_SMOKE):
        proc = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr or proc.stdout


def test_live_backend_batches_independent_judgments_in_one_model_call() -> None:
    source = BACKEND.read_text(encoding="utf-8")
    assert "nanojev_three_backbone_clef_tinystories_consensus_pairwise_train.py" in source
    assert '"head.safetensors"' in source
    assert '"tinystories.safetensors"' in source
    assert 'task="captain_pairwise"' in source
    assert "_extract_bundle_batch" in source
    assert "_common_prefix_length" in source
    assert "_repeat_legacy_cache" in source
    assert "_repeat_cache" in source
    assert "batch_repeat_interleave" in source
    assert "_cached_suffix_position_kwargs" in source
    assert "past_key_values=cache" in source
    assert "sharedPrefixCacheUsed" in source
    assert "sharedPrefixCandidateTokensByBackbone" in source
    assert "sharedPrefixCacheFailureByBackbone" in source
    assert "_batched_head_forward" in source
    assert "key_padding_mask=~memory_valid" in source
    assert '"provider": "live-tinystories-clef"' in source
    assert '"captainModelCallCount": 1' in source
    assert '"clefHeadForwardCount": 1' in source
    assert '"batchingMode": "independent-pairwise-questions"' in source
    assert '"evidenceExecutionModeRequested"' in source
    assert '"evidenceExecutionModeActual"' in source
    assert 'evidence_execution_mode == "prefix-cache"' in source
    assert 'evidence_execution_mode != "full-batch"' in source
    assert '"schema": "game.captainDecisionResponse.v6"' in source
    assert "_ensemble_question" not in source
    assert "Answer A or B:" in source
    assert "optionAText" in source
    assert "optionBText" in source
    assert 'compact-shared-context-v2' in source
    assert 'shared_context' in source
    assert '"semanticChoicePromptMode": "compact-shared-context-a-b-v2"' in source
    assert 'ObjectPath(choice_prompt, " A")' in source
    assert 'ObjectPath(choice_prompt, " B")' in source
    assert "unsupported intent" not in source
    assert "build_layer_tap_head" in source
    assert "load_head_state_with_layer_taps" in source
    assert "TINYSTORIES_RESIDUAL_LAYERS" in source
    assert "TINYSTORIES_RESIDUAL_SOURCE_HIDDEN_SIZE" in source
    assert "residual_source_memory" in source
    assert "tinystories_residual" in source
    assert "output_hidden_states=residual_tap" in source
    assert "_continuation_predictor_mean" in source
    assert '"headLoadMode"' in source
    assert '"tinystoriesEvidenceMode": "final-layer-4-plus-residual-layers-1-2"' in source



def test_live_backend_selects_residual_layers_and_keeps_final_anchor_separate() -> None:
    backend = _load_module("space_captain_backend_layer_tap_test", BACKEND)
    model = object.__new__(backend.CaptainClefModel)

    class TrainContract:
        TRAINABLE_LABEL = "tinystories"
        TINYSTORIES_RESIDUAL_LAYERS = (1, 2)
        TINYSTORIES_FINAL_LAYER = 4
        TINYSTORIES_BASE_HIDDEN_SIZE = 3

    class Bundle:
        label = "tinystories"

    class Output:
        pass

    model.train = TrainContract
    output = Output()
    output.hidden_states = tuple(
        torch.full((2, 5, 3), float(index)) for index in range(5)
    )
    output.last_hidden_state = torch.full((2, 5, 3), 99.0)
    residual, final_hidden = model._residual_hidden_states_from_output(Bundle(), output)
    assert len(residual) == 2
    assert torch.equal(residual[0], output.hidden_states[1])
    assert torch.equal(residual[1], output.hidden_states[2])
    assert final_hidden is output.last_hidden_state


def test_live_backend_hides_symbolic_planner_ids_from_model_choice_prompt() -> None:
    backend = _load_module("space_captain_backend_semantic_test", BACKEND)

    class ObjectPath:
        def __init__(self, prompt, answer):
            self.prompt = prompt
            self.answer = answer

    class ObjectCandidate:
        def __init__(self, candidate_id, paths):
            self.candidate_id = candidate_id
            self.paths = paths

    class ObjectQuestion:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class ObjectiveApi:
        pass

    ObjectiveApi.ObjectPath = ObjectPath
    ObjectiveApi.ObjectCandidate = ObjectCandidate
    ObjectiveApi.ObjectQuestion = ObjectQuestion

    model = object.__new__(backend.CaptainClefModel)
    model.objective_api = ObjectiveApi
    shared_context = (
        "Doctrine: Seize initiative; pressure the contact unless survival forbids it. "
        "Parent: Take risk to seize tactical initiative. State: intent=uncertain."
    )
    question = model._question({
        "id": "posture.q01",
        "text": "Lens=initiative. Which alternative better fits the current command posture?",
        "optionA": "pursue",
        "optionB": "disengage",
        "optionAText": "Pressure the contact and restrict its freedom.",
        "optionBText": "Increase separation and preserve a safe exit.",
        "semanticMode": "grounded-compact-pairwise-v2",
    }, shared_context)
    assert [candidate.candidate_id for candidate in question.candidates] == ["pursue", "disengage"]
    prompt = question.candidates[0].paths[0].prompt
    assert shared_context in prompt
    assert "A: Pressure the contact and restrict its freedom." in prompt
    assert "B: Increase separation and preserve a safe exit." in prompt
    assert "pursue" not in prompt.lower()
    assert "disengage" not in prompt.lower()
    assert question.candidates[0].paths[0].answer == " A"
    assert question.candidates[1].paths[0].answer == " B"



def test_shared_prefix_cache_helpers_are_batch_safe() -> None:
    backend = _load_module("space_captain_backend_prefix_test", BACKEND)
    model = object.__new__(backend.CaptainClefModel)
    model.torch = torch
    assert model._common_prefix_length([[1, 2, 3, 4], [1, 2, 8], [1, 2, 9, 10]]) == 2
    cache = ((torch.arange(12).reshape(1, 2, 3, 2), torch.ones(1, 2, 3, 2)),)
    repeated = model._repeat_legacy_cache(cache, 4)
    assert repeated[0][0].shape[0] == 4
    assert repeated[0][1].shape[0] == 4
    for index in range(4):
        assert torch.equal(repeated[0][0][index], cache[0][0][0])

    class NativeCache:
        def __init__(self):
            self.tensor = torch.arange(6).reshape(1, 3, 2)

        def batch_repeat_interleave(self, repeats: int):
            self.tensor = self.tensor.repeat_interleave(repeats, dim=0)
            return None

    native = NativeCache()
    native_repeated = model._repeat_cache(native, 4)
    assert native_repeated is native
    assert native.tensor.shape[0] == 4

def test_batched_head_is_numerically_equivalent_to_independent_head_calls() -> None:
    backend = _load_module("space_captain_backend_test", BACKEND)
    smoke = _load_module("space_captain_clef_smoke_test", CLEF_SMOKE)
    Head = smoke.build_head_class()
    torch.manual_seed(42)
    head = Head(
        {"qwen": 4, "pythia": 3, "tinystories": 5},
        width=8,
        routing_layers=1,
        layers=1,
        heads=2,
        feedforward=16,
        fusion_feedforward=20,
    ).eval()
    model = object.__new__(backend.CaptainClefModel)
    model.torch = torch
    model.head = head
    model.train = type("TrainContract", (), {"TRAINABLE_LABEL": "tinystories"})

    def evidence_row(hidden: int, memory_tokens: int, seed: int) -> dict:
        generator = torch.Generator().manual_seed(seed)

        def rand(*shape: int):
            return torch.randn(*shape, generator=generator)

        memory = rand(memory_tokens, hidden)
        return {
            "memory": memory,
            "option_context": rand(2, hidden),
            "option_predictor": rand(2, hidden),
            "option_terminal": rand(2, hidden),
            "option_question": rand(2, hidden),
            "option_lexical": rand(2, hidden),
            "option_logp": rand(2),
            "global": memory.mean(dim=0),
        }

    rows = {
        "qwen": [evidence_row(4, 3, 1), evidence_row(4, 5, 2)],
        "pythia": [evidence_row(3, 4, 3), evidence_row(3, 2, 4)],
        "tinystories": [evidence_row(5, 2, 5), evidence_row(5, 6, 6)],
    }
    packed = {label: model._pack_evidence_batch(batch) for label, batch in rows.items()}
    with torch.no_grad():
        batched = model._batched_head_forward(packed)
        independent = torch.stack([
            head({label: rows[label][index] for label in rows})
            for index in range(2)
        ])
    assert torch.allclose(batched, independent, rtol=1e-5, atol=1e-5)


def test_batched_head_matches_v3_residual_tap_head_with_nonzero_adapters() -> None:
    backend = _load_module("space_captain_backend_residual_test", BACKEND)
    train = _load_module("space_captain_train_residual_test", TRAIN_SCRIPT)
    torch.manual_seed(123)
    head, _ = train.build_layer_tap_head(
        torch=torch,
        hidden_sizes={"qwen": 4, "pythia": 3, "tinystories": 768},
        head_kwargs={
            "width": 8,
            "routing_layers": 1,
            "layers": 1,
            "heads": 2,
            "feedforward": 16,
            "fusion_feedforward": 20,
        },
    )
    with torch.no_grad():
        for module in head.tinystories_residual.values():
            module.weight.normal_(mean=0.0, std=0.01)
    head.eval()

    model = object.__new__(backend.CaptainClefModel)
    model.torch = torch
    model.head = head
    model.train = train

    def evidence_row(hidden: int, memory_tokens: int, seed: int, *, residual: bool = False) -> dict:
        generator = torch.Generator().manual_seed(seed)

        def rand(*shape: int):
            return torch.randn(*shape, generator=generator)

        memory = rand(memory_tokens, hidden)
        row = {
            "memory": memory,
            "option_context": rand(2, hidden),
            "option_predictor": rand(2, hidden),
            "option_terminal": rand(2, hidden),
            "option_question": rand(2, hidden),
            "option_lexical": rand(2, hidden),
            "option_logp": rand(2),
            "global": memory.mean(dim=0),
        }
        if residual:
            residual_width = train.TINYSTORIES_RESIDUAL_SOURCE_HIDDEN_SIZE
            residual_memory = rand(memory_tokens, residual_width)
            row.update({
                "residual_source_memory": residual_memory,
                "residual_source_option_context": rand(2, residual_width),
                "residual_source_option_predictor": rand(2, residual_width),
                "residual_source_option_terminal": rand(2, residual_width),
                "residual_source_option_question": rand(2, residual_width),
                "residual_source_global": residual_memory.mean(dim=0),
            })
        return row

    rows = {
        "qwen": [evidence_row(4, 3, 11), evidence_row(4, 5, 12)],
        "pythia": [evidence_row(3, 4, 13), evidence_row(3, 2, 14)],
        "tinystories": [
            evidence_row(768, 2, 15, residual=True),
            evidence_row(768, 6, 16, residual=True),
        ],
    }
    packed = {label: model._pack_evidence_batch(batch) for label, batch in rows.items()}
    with torch.no_grad():
        batched = model._batched_head_forward(packed)
        independent = torch.stack([
            head({label: rows[label][index] for label in rows})
            for index in range(2)
        ])
    assert torch.allclose(batched, independent, rtol=1e-5, atol=1e-5)


def test_live_wrapper_runs_call_time_characterization_contract() -> None:
    source = LIVE_SMOKE.read_text(encoding="utf-8")
    assert "space_captain_clef_backend.py" in source
    assert "space_captain_differentiable_smoke.py" in source
    assert '"--steps", str(int(args.steps))' in source
    assert '"--decision-interval-seconds", str(float(args.decision_interval_seconds))' in source
    assert '"--questions-per-call", str(int(args.questions_per_call))' in source
    assert '"--speed-ab"' in source
    assert '"--speed-ab-repeats"' in source
    assert '"prefix-cache", "full-batch"' in source
    assert '"full-batch", "prefix-cache"' in source
    assert 'warmupExcludedMs' in source
    assert 'semanticResultsIgnored' in source
    assert 'prefixCacheVsFullBatchP95Ratio' in source
    assert '"--speed-scaling"' in source
    assert '"--speed-scaling-counts"' in source
    assert '"--speed-scaling-repeats"' in source
    assert '"--speed-scaling-modes"' in source
    assert '"model": "T(x)=m*x+b"' in source
    assert 'fixedInvocationMsEstimate' in source
    assert 'slopeMsPerJudgment' in source
    assert 'wallMeanCrossover' in source

    contract = CONTRACT_SMOKE.read_text(encoding="utf-8")
    assert "captainTemporalUnitIsOneModelCall" in contract
    assert "independentJacketJudgmentsBatchedInSingleCall" in contract
    assert "compactSharedSemanticContextUsed" in contract
    assert "assessmentUsesHierarchicalCausalGates" in contract
    assert "assessmentBindsKnownPhysicalScopeBeforeModelOpinion" in contract
    assert "childDecisionsBlendModelEvidenceWithCommittedPlan" in contract
    assert "tenPercentIncoherenceInjectedIntoIndependentJudgments" in contract
    assert "policyStableUnderTenPercentIncoherence" in contract
    assert "decisionOrderBuildsPlanTopDown" in contract
    assert "laterActionsReferencePriorActionOutcome" in contract
    assert "actionObstructionTriggersActionOnlyReplan" in contract
    assert "higherLayersPersistAcrossActionReplan" in contract
    assert "actionSequenceBuildsOnPriorActions" in contract
    assert "steadyCallLatencyP95Ms" in contract
    assert "backendProviderUsedAsRequested" in contract
    assert '"--evidence-execution-mode"' in contract
    assert "evidenceExecutionMode" in contract


def test_speed_ab_timing_summary_uses_interpolated_quantiles() -> None:
    live = _load_module("space_captain_live_speed_ab_test", LIVE_SMOKE)
    summary = live.timing_summary([100.0, 200.0, 300.0, 400.0, 500.0])
    assert summary["sampleCount"] == 5
    assert summary["minMs"] == 100.0
    assert summary["p50Ms"] == 300.0
    assert summary["p95Ms"] == 480.0
    assert summary["meanMs"] == 300.0
    assert summary["maxMs"] == 500.0

def test_speed_scaling_linear_fit_recovers_fixed_and_per_judgment_cost() -> None:
    live = _load_module("space_captain_live_speed_scaling_fit_test", LIVE_SMOKE)
    fit = live.linear_fit([(1, 23.0), (2, 26.0), (4, 32.0), (8, 44.0), (20, 80.0)])
    assert abs(fit["slopeMsPerJudgment"] - 3.0) < 1e-12
    assert abs(fit["interceptMs"] - 20.0) < 1e-12
    assert abs(fit["fixedInvocationMsEstimate"] - 20.0) < 1e-12
    assert fit["rSquared"] == 1.0
    assert fit["linearEnough"] is True


def test_speed_scaling_probe_spans_one_to_twenty_judgments_and_preserves_contract() -> None:
    live = _load_module("space_captain_live_speed_scaling_probe_test", LIVE_SMOKE)
    counts = live.parse_speed_scaling_counts("20,1,4,2,8,12,16,20")
    assert counts == [1, 2, 4, 8, 12, 16, 20]
    health = {"checkpointId": "cycle-test", "checkpointSha256": "abc123"}
    payload = live.speed_scaling_request(health=health, mode="prefix-cache", question_count=20)
    assert payload["checkpoint"]["checkpointId"] == "cycle-test"
    assert payload["execution"]["evidenceMode"] == "prefix-cache"
    assert payload["semanticContext"]["mode"] == "compact-shared-context-v2"
    assert len(payload["questions"]) == 20
    assert all(row["semanticMode"] == "grounded-compact-pairwise-v2" for row in payload["questions"])
    assert all(row["optionA"] != row["optionB"] for row in payload["questions"])


def test_speed_scaling_decomposition_reports_fixed_and_variable_work() -> None:
    live = _load_module("space_captain_live_speed_scaling_decomp_test", LIVE_SMOKE)
    fit = {"interceptMs": 100.0, "slopeMsPerJudgment": 7.5}
    decomposition = live.fit_decomposition(fit, 20)
    assert decomposition["questions"] == 20
    assert decomposition["fixedInvocationMs"] == 100.0
    assert decomposition["variableJudgmentMs"] == 150.0
    assert decomposition["predictedMs"] == 250.0
    assert decomposition["fixedFraction"] == 0.4
    assert decomposition["variableFraction"] == 0.6

