from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
GAME_ROOT = ROOT / "game_projects" / "webgl-demo"
GAME_TOOLS = GAME_ROOT / "tools"
SMOKE = GAME_TOOLS / "space_captain_differentiable_smoke.py"
REPLAY = GAME_TOOLS / "space_captain_snapshot_replay.py"
LIVE_FIXTURE = ROOT / "tests" / "fixtures" / "space_captain_live_votes_v1.json"


def _run(*extra: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SMOKE), *extra],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


def test_captain_ordered_plan_contract_smoke() -> None:
    result = _run()
    assert result["ok"] is True
    checks = result["checks"]
    assert checks["captainTemporalUnitIsOneModelCall"] is True
    assert checks["oneClefHeadForwardPerCaptainTimepoint"] is True
    assert checks["independentJacketJudgmentsBatchedInSingleCall"] is True
    assert checks["groundedNaturalLanguageJudgmentsUsed"] is True
    assert checks["compactSharedSemanticContextUsed"] is True
    assert checks["assessmentUsesHierarchicalCausalGates"] is True
    assert checks["assessmentBindsKnownPhysicalScopeBeforeModelOpinion"] is True
    assert checks["assessmentScopeMatrixIsExact"] is True
    assert checks["childDecisionsBlendModelEvidenceWithCommittedPlan"] is True
    assert checks["symbolicPlannerLabelsHiddenFromModelChoicePrompt"] is True
    assert checks["callTimeCharacterizedAsPrimaryTemporalUnit"] is True
    assert checks["decisionOrderBuildsPlanTopDown"] is True
    assert checks["firstActionsInheritFullPlan"] is True
    assert checks["laterActionsReferencePriorActionOutcome"] is True
    assert checks["assessmentsFollowActions"] is True
    assert checks["normalAssessmentPreservesPlan"] is True
    assert checks["actionObstructionTriggersActionOnlyReplan"] is True
    assert checks["higherLayersPersistAcrossActionReplan"] is True
    assert checks["actionSequenceBuildsOnPriorActions"] is True
    assert checks["jacketPlanPathsRemainDistinct"] is True
    assert checks["cleanDecisionLayerMatchesExpectedJacketOrState"] is True
    assert checks["policyStableUnderTenPercentIncoherence"] is True
    assert checks["expectedDecisionSurvivesInjectedIncoherence"] is True
    assert checks["captainDecisionCannotMutatePhysicsDirectly"] is True
    assert checks["physicsCreatesResidualAgainstCaptainAction"] is True
    assert checks["referenceEpisodeReplaysDeterministically"] is True
    assert result["failedModelDiagnostics"] == []
    assert result["modelDiagnostics"]["rawCleanDecisionLayerMatchesExpectedJacketOrState"] is True


def test_captain_snapshots_form_persistent_parent_child_decision_chain() -> None:
    result = _run("--include-call-snapshots")
    snapshots = result["callSnapshots"]
    assert len(snapshots) == 27
    by_jacket: dict[str, list[dict]] = {}
    for row in snapshots:
        by_jacket.setdefault(row["jacketId"], []).append(row)
        assert row["intervalSeconds"] == 1
        assert row["primaryTemporalUnit"] == "captain-model-call"
        assert row["questionCount"] == 20
        assert row["independentJudgmentCount"] == 20
        assert row["batchingMode"] == "independent-pairwise-questions"
        assert row["semanticQuestionMode"] == "grounded-compact-pairwise-v2"
        assert row["semanticContextMode"] == "compact-shared-context-v2"
        assert row["sharedContextChars"] <= 520
        assert row["meanQuestionTextChars"] <= 140
        assert row["semanticChoicePromptMode"] == "compact-shared-context-a-b-v2"
        assert row["optionOrderBalanced"] is True
        assert row["captainModelCallCount"] == 1
        assert row["clefHeadForwardCount"] == 1
        assert len(row["incoherentJudgmentIndexes"]) == 2
        assert len(row["questionIds"]) == 20
        assert len(row["cleanAnswerChoices"]) == 20
        assert len(row["noisyAnswerChoices"]) == 20
        assert row["cleanMatchesExpected"] is True
        assert row["stableUnderNoise"] is True
        assert row["recognizable"] is True
        assert len(row["answersSha256"]) == 64
        assert len(row["requestSha256"]) == 64
        assert row["checkpointId"] == "smoke.tinystories-clef.reference"

    expected_layers = [
        "appraisal", "priority", "posture", "subgoal", "action",
        "assessment", "action", "assessment", "action",
    ]
    expected_targets = {
        "captain.jacket.guardian": ["protect", "guard", "maintain-range", "hold"],
        "captain.jacket.hunter": ["press", "pursue", "close-range", "close"],
        "captain.jacket.survivor": ["preserve", "disengage", "open-range", "withdraw"],
    }
    for jacket_id, rows in by_jacket.items():
        assert [row["decisionLayer"] for row in rows] == expected_layers
        assert [rows[index]["planCoherenceTarget"] for index in (1, 2, 3, 4)] == expected_targets[jacket_id]
        first_action = rows[4]
        first_assessment = rows[5]
        second_action = rows[6]
        obstruction_assessment = rows[7]
        third_action = rows[8]

        assert first_action["parentDecisionId"] == first_action["planBefore"]["subgoal"]["decisionId"]
        assert len(first_action["inheritedPlanDecisionIds"]) == 5
        assert first_assessment["parentDecisionId"] == first_action["decisionId"]
        assert first_assessment["actualChoice"] == "continue-plan"
        assert first_assessment["invalidatedLayers"] == []

        assert second_action["previousActionDecisionId"] == first_action["decisionId"]
        assert second_action["previousOutcomeSha256"]
        assert obstruction_assessment["parentDecisionId"] == second_action["decisionId"]
        assert obstruction_assessment["actualChoice"] == "revise-action"
        assert set(obstruction_assessment["assessmentGateVotes"]) == {"action-usable", "subgoal-valid", "posture-valid"}
        assert obstruction_assessment["assessmentGateVotes"]["action-usable"]["source"] == "physics"
        assert obstruction_assessment["assessmentGateVotes"]["action-usable"]["no"] > obstruction_assessment["assessmentGateVotes"]["action-usable"]["yes"]
        assert obstruction_assessment["assessmentGateVotes"]["subgoal-valid"]["yes"] > obstruction_assessment["assessmentGateVotes"]["subgoal-valid"]["no"]
        assert obstruction_assessment["assessmentGateVotes"]["posture-valid"]["yes"] > obstruction_assessment["assessmentGateVotes"]["posture-valid"]["no"]
        assert obstruction_assessment["assessmentAuthoritativeChoice"] == "revise-action"
        assert obstruction_assessment["invalidatedLayers"] == ["action"]
        assert obstruction_assessment["higherLayerDecisionIdsBefore"] == obstruction_assessment["higherLayerDecisionIdsAfter"]

        assert third_action["previousActionDecisionId"] == second_action["decisionId"]
        assert third_action["parentDecisionId"] == first_action["parentDecisionId"]
        assert third_action["previousOutcomeSha256"]

    metrics = result["metrics"]
    assert metrics["requiredDecisionLayerPrefix"] == expected_layers
    assert metrics["firstActionStepIndex"] == 4
    assert metrics["firstAssessmentStepIndex"] == 5
    assert metrics["actionOnlyReplanAssessmentStepIndex"] == 7
    assert metrics["primaryTemporalUnit"] == "captain-model-call"
    assert metrics["primaryTemporalUnitMs"] > 0
    assert metrics["steadyCallLatencyP95Ms"] > 0



def test_question_compiler_uses_compact_shared_context_and_causal_assessment() -> None:
    source = SMOKE.read_text(encoding="utf-8")
    assert "const choiceDescriptions" in source
    assert "goalDescriptions" in source
    assert "buildSharedSemanticContext" in source
    assert "compact-shared-context-v2" in source
    assert "grounded-compact-pairwise-v2" in source
    assert "assessmentGateSchedule" in source
    assert "authoritativeAssessmentChoice" in source
    assert "ASSESSMENT_INVALIDATION_MARGIN" in source
    assert "PLAN_COHERENCE_WEIGHT = 3 / 8" in source
    assert "planCoherenceTarget" in source
    assert "action-usable" in source
    assert "subgoal-valid" in source
    assert "posture-valid" in source
    assert "synthesizeAssessment" in source
    assert "sharedContext.length <= 520" in source
    assert "question.text.length <= 140" in source
    assert "repetition % 2 === 0 ? basePair : [basePair[1], basePair[0]]" in source
    assert "Already-committed plan in plain language" not in source
    assert "Previous or active action:" not in source
    assert "failedModelDiagnostics" in source
    assert "ok: failed.length === 0" in source
    assert "cleanAnswerChoices" in source
    assert "noisyAnswerChoices" in source


def test_captain_contract_streams_large_node_program_over_stdin() -> None:
    source = SMOKE.read_text(encoding="utf-8")
    assert '[node, "-", str(gravity), str(project)' in source
    assert 'input=js' in source
    assert 'process.argv.slice(-3)' in source
    assert '[node, "-e", js' not in source


def _load_replay_module():
    spec = importlib.util.spec_from_file_location("space_captain_snapshot_replay_test", REPLAY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_saved_live_fixture_locks_three_eighths_coherence_without_gpu() -> None:
    proc = subprocess.run(
        [
            sys.executable, str(REPLAY), str(LIVE_FIXTURE),
            "--weights", "0.3,0.3333333333333333,0.35,0.375,0.4",
            "--assert-weight", "0.375",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    result = json.loads(proc.stdout)
    by_weight = {round(float(row["coherenceWeight"]), 12): row for row in result["rows"]}
    old = by_weight[round(1.0 / 3.0, 12)]
    locked = by_weight[0.375]
    assert (old["cleanExpected"], old["noisyExpected"], old["stable"]) == (26, 27, 26)
    assert (locked["cleanExpected"], locked["noisyExpected"], locked["stable"]) == (27, 27, 27)
    assert locked["systemOk"] is True
    # Raw model weakness remains visible but does not define system correctness.
    assert locked["rawModelDiagnostics"] == {
        "callsEvaluated": 21,
        "cleanExpected": 15,
        "noisyExpected": 15,
        "stable": 21,
    }


def test_three_eighths_plan_inertia_does_not_override_overwhelming_live_evidence() -> None:
    replay = _load_replay_module()
    borderline, _ = replay.synthesize_vote_scores(
        {"hold": 14, "close": 3, "withdraw": 3}, "withdraw", 3.0 / 8.0
    )
    overwhelming, _ = replay.synthesize_vote_scores(
        {"hold": 18, "close": 1, "withdraw": 1}, "withdraw", 3.0 / 8.0
    )
    assert borderline == "withdraw"
    assert overwhelming == "hold"


def test_exact_coherence_adjusted_tie_prefers_committed_plan_target() -> None:
    replay = _load_replay_module()
    choice, adjusted = replay.synthesize_vote_scores(
        {"maintain-range": 14, "close-range": 4, "open-range": 2},
        "open-range",
        3.0 / 8.0,
    )
    assert adjusted["maintain-range"] == 0.4375
    assert adjusted["open-range"] == 0.4375
    assert choice == "open-range"


def test_exact_tie_without_coherence_target_keeps_deterministic_lexical_fallback() -> None:
    replay = _load_replay_module()
    choice, adjusted = replay.synthesize_vote_scores(
        {"beta": 10, "alpha": 10},
        None,
        3.0 / 8.0,
    )
    assert adjusted["alpha"] == adjusted["beta"]
    assert choice == "alpha"
