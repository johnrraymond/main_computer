(function (global) {
  "use strict";

  const SOURCE_FILE = "main_computer/web/applications/scripts/git-tools-project-workflow.js";
  const LEGACY_SOURCE_FILE = "main_computer/web/applications/scripts/task-manager.js";

  const EVIDENCE_STEP_IDS = new Set([
    "find_repository_root",
    "measure_dirty_state",
    "make_cleanup_plan",
    "classify_changed_files",
    "find_blocking_problems",
    "rank_cleanup_risk",
    "explain_each_dirty_item",
    "compare_to_remote_state",
    "find_nested_repositories",
    "find_generated_artifacts",
    "inspect_configured_remotes",
    "list_saved_states",
    "refresh_action_log",
  ]);

  const ATTENTION_STEP_IDS = new Set([
    "choose_correct_repository_root",
    "stop_until_repository_is_clear",
    "show_merge_conflicts",
    "open_conflict_for_manual_fix",
    "abort_merge_or_rebase",
    "initial-snapshot-required",
  ]);

  const USER_ACTION_KINDS = new Set([
    "repository",
    "safety",
    "preserve",
    "ignore",
    "cleanup",
    "conflict",
    "workflow",
    "remote",
    "execution",
  ]);

  const COMMIT_CARD_STEP_IDS = new Set([
    "prepare_commit_snapshot",
    "create_initial_snapshot",
    "record_current_work_as_commit",
    "start_tracking_real_work",
  ]);

  const WIZARD_HIDDEN_ACTION_IDS = new Set([
    "save_current_state",
    "push_current_branch_to_local_server",
    "inspect_configured_remotes",
    "remove_untracked_generated_files",
  ]);

  const WIZARD_HIDDEN_ACTION_LABELS = new Set([
    "save current state",
    "push current branch to local server",
    "push to local gitea",
    "inspect configured remotes",
    "remove generated untracked files",
  ]);

  const GITIGNORE_REVIEW_IDS = new Set([
    "update_gitignore_before_initial_commit",
    "ignore_generated_files",
    "ignore_selected_paths",
    "ignore_local_environment_files",
    "ignore_debug_output",
    "separate_real_work_from_noise",
  ]);

  const GITIGNORE_REVIEW_LABELS = new Set([
    "clean up .gitignore before first commit",
    ".gitignore review",
    "gitignore review",
    "ignore generated files",
    "ignore selected paths",
    "ignore local environment files",
    "ignore debug output",
    "separate real work from noise",
  ]);

  const SECRETS_FILTER_LABELS = new Set([
    "secrets / filter",
    "security / secrets",
    "review security / secrets",
  ]);

  function setValues(values = []) {
    if (values instanceof Set) return values;
    return new Set(Array.isArray(values) ? values : []);
  }

  function stepId(step = {}) {
    return String(step.id || "").trim();
  }

  function stepKind(step = {}) {
    return String(step.kind || "").trim();
  }

  function actionKey(step = {}, scope = "wizard") {
    return `${scope}:${step.id || "step"}:${Number(step.order || 0)}`;
  }

  function firstActionableWizardStep(wizard = {}) {
    const steps = Array.isArray(wizard.steps) ? wizard.steps : [];
    return steps.find((step) => !["succeeded", "skipped", "blocked"].includes(step.state || "") && !step.locked) || steps[0] || null;
  }

  function humanizeToken(value = "") {
    return String(value || "")
      .replace(/[_-]+/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .split(" ")
      .filter(Boolean)
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ");
  }

  function weightForWizardStep(step = {}, data = {}) {
    const project = data.project || {};
    const git = data.git || {};
    const dirty = data.dirty_plan || {};
    let weight = 60 - Number(step.order || 0);
    const id = `${step.id || ""} ${step.label || ""}`.toLowerCase();
    if (!git.is_git_repo) weight += 25;
    if (!git.has_head && (id.includes("initial snapshot") || id.includes("initial_snapshot") || id.includes("first git commit") || id.includes("prepare_commit_snapshot") || id.includes("take snapshot") || id.includes("commit") || id.includes("gitignore"))) weight += 40;
    if (id.includes("prepare_commit_snapshot") || id.includes("take snapshot")) weight += 16;
    if (id.includes("secrets_filter") || id.includes("secrets / filter")) weight += 18;
    if (id.includes("push")) weight -= 10;
    if (id.includes("find repository root")) weight += 8;
    if (id.includes("save current state")) weight += 24;
    if (id.includes("classify")) weight += 6;
    if (id.includes("start tracking")) weight += 18;
    if (id.includes("ignore generated")) weight += 14;
    if (id.includes("inspect configured remotes")) weight += 4;
    if (id.includes("push")) weight += 8;
    if (project.locked && !id.includes("find repository root") && !id.includes("inspect configured remotes") && !id.includes("classify")) weight += 2;
    if (Number(dirty.dirty_score || 0) >= 20) weight += 4;
    return Math.max(weight, 1);
  }

  function remoteStepIsCurrentlyRequired(data = {}, hooks = {}) {
    const wizard = data.wizard || {};
    const steps = Array.isArray(wizard.steps) ? wizard.steps : [];
    const statusLabel = typeof hooks.actionStatusLabel === "function" ? hooks.actionStatusLabel : () => "idle";
    const keyForStep = typeof hooks.actionKey === "function" ? hooks.actionKey : actionKey;
    return steps.some((step = {}) => {
      const id = stepId(step);
      if (id === "inspect_configured_remotes") return false;
      if (!id.includes("remote") && !id.includes("gitea") && !id.includes("server") && !id.includes("push")) return false;
      const key = keyForStep(step, "wizard");
      return statusLabel(key) !== "completed";
    });
  }

  function stepIsReadOnlyEvidence(step = {}, data = {}, hooks = {}) {
    const id = stepId(step);
    const kind = stepKind(step);
    if (step.locked || step.destructive || step.safe === false) return false;
    if (id === "inspect_configured_remotes") return !remoteStepIsCurrentlyRequired(data, hooks);
    if (EVIDENCE_STEP_IDS.has(id)) return true;
    if (kind === "analysis") return true;
    return false;
  }

  function stepIsUserAction(step = {}, data = {}, hooks = {}) {
    if (stepIsReadOnlyEvidence(step, data, hooks)) return false;
    const id = stepId(step);
    const kind = stepKind(step);
    if (ATTENTION_STEP_IDS.has(id)) return true;
    if (step.locked || step.destructive || step.safe === false) return true;
    if (USER_ACTION_KINDS.has(kind)) return true;
    return ["blocked", "ready", "running", "planned"].includes(step.state || "");
  }

  function stepBlockedReason(step = {}, data = {}, hooks = {}) {
    const project = data.project || {};
    const git = data.git || {};
    const id = `${step.id || ""} ${step.label || ""}`.toLowerCase();
    if (git.is_git_repo && git.has_head === false && id.includes("push")) {
      return "Waiting for prerequisite: Has HEAD.";
    }
    if (project.locked && stepIsUserAction(step, data, hooks) && !stepIsReadOnlyEvidence(step, data, hooks)) {
      return "Project is locked; unlock only when you intend to mutate state.";
    }
    if (Array.isArray(step.requires) && step.requires.length) {
      return `Waiting for prerequisite: ${step.requires.map(humanizeToken).join(", ")}.`;
    }
    if (step.locked) return "Locked until the prerequisite safety step is complete.";
    if (step.destructive) return "Destructive action; save current state before running.";
    if (step.state === "blocked") return "Blocked by current repository state.";
    return "";
  }

  function classifyWizardStep(step = {}, data = {}, actionKeyValue = "", hooks = {}) {
    const statusLabel = typeof hooks.actionStatusLabel === "function" ? hooks.actionStatusLabel : () => "idle";
    const status = actionKeyValue ? statusLabel(actionKeyValue) : "idle";
    if (step.state === "completed" || step.completed) {
      return {
        lane: "satisfied",
        tone: "complete",
        reason: step.gitignore_success?.message || "Prerequisite already satisfied.",
        showRunner: false,
        status,
      };
    }
    if (status === "completed") {
      return {
        lane: "completed",
        tone: "complete",
        reason: "Already completed in this browser session.",
        showRunner: false,
        status,
      };
    }
    if (["queued", "running"].includes(status)) {
      return {
        lane: "ready_action",
        tone: "actionable",
        reason: "This action is already active.",
        showRunner: true,
        status,
      };
    }
    if (stepIsReadOnlyEvidence(step, data, hooks)) {
      return {
        lane: "evidence",
        tone: "informative",
        reason: "Read-only evidence; it does not require the user to unblock the workflow.",
        showRunner: false,
        status,
      };
    }
    if (ATTENTION_STEP_IDS.has(stepId(step))) {
      return {
        lane: "attention",
        tone: "blocking",
        reason: "Requires a user decision before the workflow can safely continue.",
        showRunner: false,
        status,
      };
    }
    const blockedReason = stepBlockedReason(step, data, hooks);
    if (step.locked || step.destructive) {
      return {
        lane: "destructive_locked",
        tone: "blocking",
        reason: blockedReason || "Locked or destructive action.",
        showRunner: true,
        status,
      };
    }
    if (blockedReason || step.state === "blocked") {
      return {
        lane: "waiting_action",
        tone: "blocking",
        reason: blockedReason || "Waiting for a prerequisite.",
        showRunner: true,
        status,
      };
    }
    if (stepIsUserAction(step, data, hooks)) {
      return {
        lane: "ready_action",
        tone: "actionable",
        reason: "Actionable: the user must make a decision or run this to move the process forward.",
        showRunner: true,
        status,
      };
    }
    return {
      lane: "evidence",
      tone: "informative",
      reason: "Context only.",
      showRunner: false,
      status,
    };
  }

  function toneForWizardStep(step = {}, data = {}, hooks = {}) {
    return classifyWizardStep(step, data, "", hooks).tone;
  }

  function stepIsCommitCard(step = {}) {
    return Boolean(step.commit_review) || COMMIT_CARD_STEP_IDS.has(stepId(step));
  }

  function visibleStepLabel(step = {}, hooks = {}) {
    const label = String(step.label || "Step").trim() || "Step";
    const commitTitle = typeof hooks.commitCardTitle === "function" ? hooks.commitCardTitle(step) : "";
    if (!commitTitle || /commit|snapshot/i.test(label)) return label;
    return `${label} — ${commitTitle}`;
  }

  function normalizedWizardLabel(value = "") {
    return String(value || "")
      .replace(/^\s*\d+\.\s*/, "")
      .trim()
      .replace(/\s+/g, " ")
      .toLowerCase();
  }

  function wizardStepMatches(step = {}, ids = new Set(), labels = new Set(), hooks = {}) {
    const id = stepId(step);
    const idSet = setValues(ids);
    const labelSet = setValues(labels);
    if (idSet.has(id)) return true;
    const visibleLabel = normalizedWizardLabel(visibleStepLabel(step, hooks));
    const rawLabel = normalizedWizardLabel(step.label || "");
    return labelSet.has(visibleLabel) || labelSet.has(rawLabel);
  }

  function wizardStepShouldHideInActionQueue(step = {}, hooks = {}) {
    return wizardStepMatches(
      step,
      WIZARD_HIDDEN_ACTION_IDS,
      WIZARD_HIDDEN_ACTION_LABELS,
      hooks
    );
  }

  function wizardStepIsGitignoreReviewCandidate(step = {}, hooks = {}) {
    return wizardStepMatches(
      step,
      GITIGNORE_REVIEW_IDS,
      GITIGNORE_REVIEW_LABELS,
      hooks
    );
  }

  function wizardStepIsSecretsFilterCandidate(step = {}, hooks = {}) {
    const id = stepId(step);
    if (id === "secrets_filter") return true;
    const label = normalizedWizardLabel(step.label || visibleStepLabel(step, hooks));
    return SECRETS_FILTER_LABELS.has(label);
  }

  function normalizeSecretsFilterStep(step = {}) {
    return {
      ...step,
      id: "secrets_filter",
      label: "Review Security / Secrets",
      why: step.why || "Check selected files for API keys, usernames, credentials, tokens, private keys, generated artifacts, and risky content before committing.",
      kind: step.kind || "safety",
    };
  }

  function uniqueStrings(...groups) {
    const seen = new Set();
    const values = [];
    groups.flat().forEach((item) => {
      const value = String(item || "").trim();
      if (!value || seen.has(value)) return;
      seen.add(value);
      values.push(value);
    });
    return values;
  }

  function wizardStepPaths(step = {}) {
    return Array.isArray(step.paths) ? step.paths : [];
  }

  function wizardIgnoreRules(step = {}, key = "ignore_rules") {
    if (Array.isArray(step[key])) return step[key];
    const groups = step.ignore_rule_groups || {};
    if (key === "ignore_rules" && Array.isArray(groups.safe)) return groups.safe;
    if (key === "questionable_ignore_rules" && Array.isArray(groups.questionable)) return groups.questionable;
    return [];
  }

  function mergeGitignoreReviewSteps(steps = []) {
    const candidates = steps.filter(Boolean);
    if (!candidates.length) return null;
    const firstCommitStep = candidates.find((step) => stepId(step) === "update_gitignore_before_initial_commit") || null;
    const generatedStep = candidates.find((step) => stepId(step) === "ignore_generated_files" || stepId(step) === "ignore_debug_output") || null;
    const selectedPathsStep = candidates.find((step) => stepId(step) === "ignore_selected_paths" || stepId(step) === "separate_real_work_from_noise") || null;
    const localEnvStep = candidates.find((step) => stepId(step) === "ignore_local_environment_files") || null;
    const base = firstCommitStep || generatedStep || selectedPathsStep || localEnvStep || candidates[0];
    const generatedPaths = generatedStep ? wizardStepPaths(generatedStep) : [];
    const localEnvPaths = localEnvStep ? wizardStepPaths(localEnvStep) : [];
    const uniquePaths = uniqueStrings(
      ...candidates.map((step) => wizardStepPaths(step)),
      ...candidates.map((step) => Array.isArray(step.affected_paths) ? step.affected_paths : [])
    ).sort();
    const generatedPathSet = new Set(generatedPaths);
    const sharedPathCount = localEnvPaths.filter((path) => generatedPathSet.has(path)).length;
    const safeRules = uniqueStrings(...candidates.map((step) => wizardIgnoreRules(step, "ignore_rules")));
    const questionableRules = uniqueStrings(...candidates.map((step) => wizardIgnoreRules(step, "questionable_ignore_rules")));
    const safePaths = uniqueStrings(...candidates.map((step) => Array.isArray(step.safe_paths) ? step.safe_paths : []));
    const questionablePaths = uniqueStrings(...candidates.map((step) => Array.isArray(step.questionable_paths) ? step.questionable_paths : []));
    return {
      ...base,
      label: ".gitignore review",
      why: [
        "Generated/debug files and local environment files appear to be untracked noise.",
        "Review the combined candidate list and add appropriate patterns to .gitignore or local excludes.",
      ].join(" "),
      paths: uniquePaths,
      affected_paths: uniquePaths,
      safe_paths: safePaths,
      questionable_paths: questionablePaths,
      ignore_rules: safeRules,
      questionable_ignore_rules: questionableRules,
      ignore_rule_groups: {
        ...(base.ignore_rule_groups || {}),
        safe: safeRules,
        questionable: questionableRules,
      },
      gitignore_review_summary: {
        generated_path_count: generatedPaths.length,
        local_environment_path_count: localEnvPaths.length,
        unique_path_count: uniquePaths.length,
        shared_path_count: sharedPathCount,
      },
      gitignore_path_summary: `Paths (${uniquePaths.length}; ${generatedPaths.length} generated, ${localEnvPaths.length} local/env, ${sharedPathCount} overlap)`,
      uiReason: "Combined .gitignore review card: generated/debug and local environment candidates are shown together.",
      tone: "actionable",
      uiLane: "ready_action",
      weight: Math.max(...candidates.map((step) => Number(step.weight || 0)), Number(base.weight || 0)),
      showRunner: candidates.some((step) => step.showRunner !== false),
    };
  }

  function wizardDisplayActions(actions = [], hooks = {}) {
    const isCommitCard = typeof hooks.isCommitCard === "function" ? hooks.isCommitCard : stepIsCommitCard;
    const gitignoreCandidates = actions.filter((step) => wizardStepIsGitignoreReviewCandidate(step, hooks));
    const mergedGitignoreReview = mergeGitignoreReviewSteps(gitignoreCandidates);
    const secretsFilterStep = actions.find((step) => wizardStepIsSecretsFilterCandidate(step, hooks)) || null;
    let insertedGitignoreReview = false;
    let insertedSecretsFilter = false;

    const displayActions = actions.reduce((displayActions, step) => {
      if (wizardStepShouldHideInActionQueue(step, hooks)) return displayActions;

      if (wizardStepIsGitignoreReviewCandidate(step, hooks)) {
        if (!insertedGitignoreReview && mergedGitignoreReview) {
          displayActions.push(mergedGitignoreReview);
          insertedGitignoreReview = true;
        }
        return displayActions;
      }

      if (wizardStepIsSecretsFilterCandidate(step, hooks)) {
        if (!insertedSecretsFilter) {
          displayActions.push(normalizeSecretsFilterStep(step));
          insertedSecretsFilter = true;
        }
        return displayActions;
      }

      displayActions.push(step);
      return displayActions;
    }, []);

    if (!insertedSecretsFilter && secretsFilterStep) {
      const insertAt = displayActions.findIndex((step) => isCommitCard(step));
      const normalized = normalizeSecretsFilterStep(secretsFilterStep);
      if (insertAt >= 0) {
        displayActions.splice(insertAt, 0, normalized);
      } else {
        displayActions.push(normalized);
      }
    }

    return displayActions;
  }

  function buildReadinessReport() {
    return {
      ready: true,
      ownerApp: "git-tools",
      sourceFile: SOURCE_FILE,
      legacySourceFile: LEGACY_SOURCE_FILE,
      ownershipStatus: "project-workflow-boundary-extracted",
      contracts: [
        "wizard action visibility is not owned by Task Manager",
        "gitignore review steps are merged by Git Tools workflow semantics",
        "first-commit and normal .gitignore steps stay visible as a review pane",
        "secrets review steps are normalized before commit cards",
        "step classification uses controller-style policy instead of render-only labels",
      ],
    };
  }

  global.GitToolsProjectWorkflow = Object.freeze({
    SOURCE_FILE,
    LEGACY_SOURCE_FILE,
    EVIDENCE_STEP_IDS,
    ATTENTION_STEP_IDS,
    USER_ACTION_KINDS,
    COMMIT_CARD_STEP_IDS,
    WIZARD_HIDDEN_ACTION_IDS,
    WIZARD_HIDDEN_ACTION_LABELS,
    GITIGNORE_REVIEW_IDS,
    GITIGNORE_REVIEW_LABELS,
    SECRETS_FILTER_LABELS,
    actionKey,
    firstActionableWizardStep,
    humanizeToken,
    weightForWizardStep,
    stepId,
    stepKind,
    remoteStepIsCurrentlyRequired,
    stepIsReadOnlyEvidence,
    stepIsUserAction,
    stepBlockedReason,
    classifyWizardStep,
    toneForWizardStep,
    stepIsCommitCard,
    visibleStepLabel,
    normalizedWizardLabel,
    wizardStepMatches,
    wizardStepShouldHideInActionQueue,
    wizardStepIsGitignoreReviewCandidate,
    wizardStepIsSecretsFilterCandidate,
    normalizeSecretsFilterStep,
    uniqueStrings,
    wizardStepPaths,
    wizardIgnoreRules,
    mergeGitignoreReviewSteps,
    wizardDisplayActions,
    buildReadinessReport,
  });
})(typeof globalThis !== "undefined" ? globalThis : window);
