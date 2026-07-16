(() => {
  (function createGitToolsSemanticAdapter(global) {
    "use strict";

    if (!global) return;

    const VERSION = "git-tools-semantic-adapter-governed-push-v7";
    const APP_ID = "git-tools";
    const ADAPTER_ID = "git-tools-domain-adapter";
    const STATE_SCHEMA_VERSION = "git-tools-semantic-state-v1";
    const PREFLIGHT_SCHEMA_VERSION = "git-tools-preflight-v1";
    const RECEIPT_SCHEMA_VERSION = "mcel-semantic-receipt-v1";
    const CONFIRMATION_SCHEMA_VERSION = "git-tools-confirmation-v1";
    const PUSH_EXECUTION_SCHEMA_VERSION = "git-tools-push-execution-v1";
    const RECOVERY_CLASSIFICATION_SCHEMA_VERSION = "git-tools-recovery-classification-v1";
    const RECOVERY_PLAN_SCHEMA_VERSION = "git-tools-recovery-plan-v1";
    const RECOVERY_COVERAGE_VERSION = "git-tools-recovery-coverage-v2";
    const INTENT_COVERAGE_SCHEMA_VERSION = "git-tools-intent-coverage-v1";
    const KIND = "governed-push-execution-recovery-domain-adapter";
    const RECEIPT_STORAGE_KEY = "mcel.git-tools.preflight-receipts.v1";
    const MAX_RECEIPTS = 50;
    const DEFAULT_MAX_STATE_AGE_MS = 120000;

    const INTENT_DEFINITIONS = Object.freeze([
      Object.freeze({
        id: "refreshStatus",
        label: "Refresh repository status",
        risk: "safe-read",
        mutates: false,
        requiresGitRepo: false,
        requiresRemote: false,
        preflightRequired: false
      }),
      Object.freeze({
        id: "inspectWorkingTree",
        label: "Inspect working tree",
        risk: "safe-read",
        mutates: false,
        requiresGitRepo: true,
        requiresRemote: false,
        preflightRequired: false
      }),
      Object.freeze({
        id: "inspectRemotes",
        label: "Inspect configured remotes",
        risk: "safe-read",
        mutates: false,
        requiresGitRepo: true,
        requiresRemote: false,
        preflightRequired: false
      }),
      Object.freeze({
        id: "inspectPatchInventory",
        label: "Inspect patch inventory",
        risk: "safe-read",
        mutates: false,
        requiresGitRepo: true,
        requiresRemote: false,
        preflightRequired: false
      }),
      Object.freeze({
        id: "preparePush",
        label: "Prepare push preflight",
        risk: "publish-preflight",
        mutates: false,
        requiresGitRepo: true,
        requiresRemote: true,
        preflightRequired: true
      }),
      Object.freeze({
        id: "pushCurrentBranch",
        label: "Push current branch",
        risk: "publish-mutation",
        mutates: true,
        requiresGitRepo: true,
        requiresRemote: true,
        preflightRequired: true
      }),
      Object.freeze({
        id: "runManualCommand",
        label: "Run manual Git command",
        risk: "arbitrary-command-execution",
        mutates: "potential",
        requiresGitRepo: true,
        requiresRemote: false,
        preflightRequired: true
      })
    ]);

    const RECOVERY_FAILURE_DEFINITIONS = Object.freeze({
      "status-backend-unavailable": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        refreshRequired: true,
        mutationAllowed: false,
        message: "The Git status backend is unavailable.",
        recommendedNextStep: "Retry the governed status refresh after confirming the backend is reachable.",
        prohibitedActions: Object.freeze(["pushCurrentBranch", "runManualCommand"])
      }),
      "status-refresh-failed": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        refreshRequired: true,
        mutationAllowed: false,
        message: "The governed repository status refresh failed.",
        recommendedNextStep: "Retry the governed status refresh and inspect the returned backend error.",
        prohibitedActions: Object.freeze(["pushCurrentBranch", "runManualCommand"])
      }),
      "state-not-observed": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        refreshRequired: true,
        mutationAllowed: false,
        message: "Repository state has not been observed.",
        recommendedNextStep: "Execute a governed status refresh before evaluating risky Git actions.",
        prohibitedActions: Object.freeze(["pushCurrentBranch", "runManualCommand"])
      }),
      "state-stale": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        refreshRequired: true,
        mutationAllowed: false,
        message: "Repository state is stale.",
        recommendedNextStep: "Refresh repository state and rerun preflight against the new state fingerprint.",
        prohibitedActions: Object.freeze(["pushCurrentBranch", "runManualCommand"])
      }),
      "not-a-git-repository": Object.freeze({
        severity: "blocking",
        retrySafe: false,
        refreshRequired: false,
        mutationAllowed: false,
        message: "The selected path is not a confirmed Git repository.",
        recommendedNextStep: "Select or initialize the intended repository, then execute a governed status refresh.",
        prohibitedActions: Object.freeze(["pushCurrentBranch", "preparePush", "runManualCommand"])
      }),
      "remote-missing": Object.freeze({
        severity: "blocking",
        retrySafe: false,
        refreshRequired: false,
        mutationAllowed: false,
        message: "No Git remote is configured for publication.",
        recommendedNextStep: "Inspect remotes and configure the intended remote outside the semantic runtime before retrying.",
        prohibitedActions: Object.freeze(["pushCurrentBranch", "preparePush", "forcePush"])
      }),
      "remote-divergence-unknown": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        refreshRequired: true,
        mutationAllowed: false,
        message: "Ahead and behind state is unknown.",
        recommendedNextStep: "Refresh remote-aware status and inspect the branch relationship before publication.",
        prohibitedActions: Object.freeze(["pushCurrentBranch", "forcePush"])
      }),
      "remote-behind": Object.freeze({
        severity: "blocking",
        retrySafe: false,
        refreshRequired: true,
        mutationAllowed: false,
        message: "The local branch is behind its remote.",
        recommendedNextStep: "Inspect remote commits and choose an explicit integration strategy before publishing.",
        prohibitedActions: Object.freeze(["pushCurrentBranch", "forcePush"])
      }),
      "remote-diverged": Object.freeze({
        severity: "blocking",
        retrySafe: false,
        refreshRequired: true,
        mutationAllowed: false,
        message: "Local and remote history have diverged.",
        recommendedNextStep: "Inspect ahead/behind commits and choose an explicit merge or rebase recovery path.",
        prohibitedActions: Object.freeze(["pushCurrentBranch", "forcePush"])
      }),
      "nothing-to-publish": Object.freeze({
        severity: "informational",
        retrySafe: false,
        refreshRequired: false,
        mutationAllowed: false,
        message: "There are no local commits to publish.",
        recommendedNextStep: "Inspect the working tree and commit intended changes before running push preflight again.",
        prohibitedActions: Object.freeze(["pushCurrentBranch"])
      }),
      "working-tree-dirty": Object.freeze({
        severity: "warning",
        retrySafe: false,
        refreshRequired: false,
        mutationAllowed: false,
        message: "The working tree contains uncommitted changes.",
        recommendedNextStep: "Inspect, commit, stash, or discard the intended changes before relying on publish results.",
        prohibitedActions: Object.freeze(["forcePush"])
      }),
      "execution-binding-unavailable": Object.freeze({
        severity: "blocking",
        retrySafe: false,
        refreshRequired: false,
        mutationAllowed: false,
        message: "No governed execution binding exists for this intent.",
        recommendedNextStep: "Use an available observation or preflight intent; do not fall back to an ungoverned command path.",
        prohibitedActions: Object.freeze(["pushCurrentBranch", "runManualCommand", "forcePush"])
      }),
      "manual-command-policy-block": Object.freeze({
        severity: "blocking",
        retrySafe: false,
        refreshRequired: false,
        mutationAllowed: false,
        message: "Arbitrary Git command execution is outside the adapter policy.",
        recommendedNextStep: "Choose a named governed intent or perform the operation outside MCEL with explicit review.",
        prohibitedActions: Object.freeze(["runManualCommand"])
      }),
      "branch-unpublishable": Object.freeze({
        severity: "blocking",
        retrySafe: false,
        refreshRequired: false,
        mutationAllowed: false,
        message: "The current HEAD is not attached to a publishable named branch.",
        recommendedNextStep: "Select or create the intended branch, then refresh repository state.",
        prohibitedActions: Object.freeze(["pushCurrentBranch", "forcePush"])
      }),
      "unsupported-intent": Object.freeze({
        severity: "blocking",
        retrySafe: false,
        refreshRequired: false,
        mutationAllowed: false,
        message: "The requested Git intent is not supported by this adapter.",
        recommendedNextStep: "Choose a registered intent and inspect adapter capabilities.",
        prohibitedActions: Object.freeze(["runManualCommand"])
      }),
      "confirmation-declined": Object.freeze({
        severity: "informational",
        retrySafe: true,
        refreshRequired: false,
        mutationAllowed: false,
        message: "The governed push confirmation was declined.",
        recommendedNextStep: "Review the target and run push preflight again only when publication is intended.",
        prohibitedActions: Object.freeze(["forcePush"])
      }),
      "preflight-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        refreshRequired: true,
        mutationAllowed: false,
        message: "A current stored confirmation-required push preflight receipt is required.",
        recommendedNextStep: "Refresh repository state and run governed push preflight again.",
        prohibitedActions: Object.freeze(["pushCurrentBranch", "forcePush"])
      }),
      "preflight-expired": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        refreshRequired: true,
        mutationAllowed: false,
        message: "The push preflight receipt expired before execution.",
        recommendedNextStep: "Refresh repository state and create a new push preflight receipt.",
        prohibitedActions: Object.freeze(["pushCurrentBranch", "forcePush"])
      }),
      "state-changed-after-preflight": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        refreshRequired: true,
        mutationAllowed: false,
        message: "Repository state changed after push preflight.",
        recommendedNextStep: "Review the refreshed state and run preflight again before confirming.",
        prohibitedActions: Object.freeze(["pushCurrentBranch", "forcePush"])
      }),
      "push-backend-failed": Object.freeze({
        severity: "blocking",
        retrySafe: false,
        refreshRequired: true,
        mutationAllowed: false,
        message: "The Local Gitea push backend reported a failure.",
        recommendedNextStep: "Refresh status, inspect the execution receipt and backend result, then resolve the reported failure before retrying.",
        prohibitedActions: Object.freeze(["pushCurrentBranch", "forcePush"])
      }),
      "post-push-state-refresh-failed": Object.freeze({
        severity: "warning",
        retrySafe: true,
        refreshRequired: true,
        mutationAllowed: false,
        message: "The push completed, but post-push repository state could not be verified.",
        recommendedNextStep: "Run a governed status refresh before attempting another publication.",
        prohibitedActions: Object.freeze(["pushCurrentBranch", "forcePush"])
      }),
      "unknown-failure": Object.freeze({
        severity: "blocking",
        retrySafe: false,
        refreshRequired: true,
        mutationAllowed: false,
        message: "The semantic runtime could not classify this failure.",
        recommendedNextStep: "Refresh state, inspect the source receipt, and keep mutations disabled.",
        prohibitedActions: Object.freeze(["pushCurrentBranch", "runManualCommand", "forcePush"])
      })
    });

    const SOURCE_CODE_TO_FAILURE_CLASS = Object.freeze({
      "status-api-unavailable": "status-backend-unavailable",
      "git-status-request-failed": "status-refresh-failed",
      "git-status-failed": "status-refresh-failed",
      "state-error": "status-refresh-failed",
      "state-not-observed": "state-not-observed",
      "state-loading": "state-not-observed",
      "state-stale": "state-stale",
      "state-timestamp-invalid": "state-stale",
      "not-a-git-repository": "not-a-git-repository",
      "remote-missing": "remote-missing",
      "remote-divergence-unknown": "remote-divergence-unknown",
      "remote-behind": "remote-behind",
      "remote-diverged": "remote-diverged",
      "nothing-to-publish": "nothing-to-publish",
      "working-tree-dirty": "working-tree-dirty",
      "execution-binding-unavailable": "execution-binding-unavailable",
      "manual-command-policy-block": "manual-command-policy-block",
      "branch-unpublishable": "branch-unpublishable",
      "unsupported-intent": "unsupported-intent",
      "confirmation-declined": "confirmation-declined",
      "preflight-required": "preflight-required",
      "preflight-expired": "preflight-expired",
      "state-changed-after-preflight": "state-changed-after-preflight",
      "push-backend-failed": "push-backend-failed",
      "post-push-state-refresh-failed": "post-push-state-refresh-failed"
    });


    function clonePlain(value) {
      if (value == null || typeof value !== "object") return value;
      if (Array.isArray(value)) return value.map(clonePlain);
      return Object.fromEntries(
        Object.entries(value)
          .filter(([, entry]) => typeof entry !== "function")
          .map(([key, entry]) => [key, clonePlain(entry)])
      );
    }

    function integerOr(value, fallback = 0) {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? Math.trunc(parsed) : fallback;
    }

    function nullableInteger(value) {
      if (value === null || value === undefined || value === "") return null;
      const parsed = Number(value);
      return Number.isFinite(parsed) ? Math.trunc(parsed) : null;
    }

    function initialState(repoDir = ".") {
      return {
        schemaVersion: STATE_SCHEMA_VERSION,
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        adapterVersion: VERSION,
        source: "uninitialized",
        observedAt: "",
        phase: "uninitialized",
        ok: null,
        repoDir: String(repoDir || "."),
        gitRoot: "",
        isGitRepo: null,
        hasHead: null,
        branch: "unknown",
        ahead: null,
        behind: null,
        dirty: null,
        changedCount: 0,
        untrackedCount: 0,
        shortStatus: "",
        recentCommits: [],
        remotes: [],
        patching: {
          available: false,
          counts: {
            incoming: 0,
            applied: 0,
            archive: 0,
            dryRuns: 0
          }
        },
        capabilities: {},
        error: null
      };
    }

    let currentState = initialState();
    let receiptSequence = 0;
    let receiptLedger = [];

    function selectedRepoDir(options = {}) {
      const explicit = String(options.repoDir ?? options.repo_dir ?? "").trim();
      if (explicit) return explicit;
      const input = global.document?.querySelector?.("#git-project-path");
      const selected = String(input?.value || "").trim();
      return selected || currentState.repoDir || ".";
    }

    function normalizeRemotes(remotes) {
      if (!Array.isArray(remotes)) return [];
      return remotes
        .map((remote) => ({
          name: String(remote?.name || "").trim(),
          fetch: String(remote?.fetch || "").trim(),
          push: String(remote?.push || "").trim()
        }))
        .filter((remote) => remote.name);
    }

    function normalizePatchCounts(patching) {
      const counts = patching?.counts || {};
      return {
        incoming: integerOr(counts.incoming),
        applied: integerOr(counts.applied),
        archive: integerOr(counts.archive),
        dryRuns: integerOr(counts.dry_runs ?? counts.dryRuns)
      };
    }

    function normalizeStatus(payload = {}, options = {}) {
      const repoDir = String(
        payload.repo_dir ??
        options.repoDir ??
        options.repo_dir ??
        currentState.repoDir ??
        "."
      );
      const ok = payload.ok === true;
      const isGitRepo = payload.is_git_repo === true;
      const phase = ok
        ? (isGitRepo ? "ready" : "not-a-repository")
        : "error";
      const patching = payload.patching || {};

      return {
        ...initialState(repoDir),
        source: String(options.source || "git-tools-status-api"),
        observedAt: String(options.observedAt || new Date().toISOString()),
        phase,
        ok,
        repoDir,
        gitRoot: String(payload.git_root || ""),
        isGitRepo,
        hasHead: payload.has_head === true,
        branch: String(payload.branch || (isGitRepo ? "detached-or-unknown" : "unknown")),
        ahead: nullableInteger(payload.ahead),
        behind: nullableInteger(payload.behind),
        dirty: isGitRepo ? Boolean(payload.dirty) : null,
        changedCount: integerOr(payload.changed_count),
        untrackedCount: integerOr(payload.untracked_count),
        shortStatus: String(payload.short_status || ""),
        recentCommits: Array.isArray(payload.recent_commits)
          ? payload.recent_commits.map((entry) => String(entry))
          : [],
        remotes: normalizeRemotes(payload.remotes),
        patching: {
          available: patching.ok === true,
          counts: normalizePatchCounts(patching)
        },
        capabilities: clonePlain(payload.capabilities || {}),
        error: ok
          ? null
          : {
              code: isGitRepo ? "git-status-failed" : "not-a-git-repository",
              message: String(payload.error || "Git status could not be derived.")
            }
      };
    }

    function getState() {
      return clonePlain(currentState);
    }

    function setFailureState(error, options = {}) {
      const repoDir = selectedRepoDir(options);
      currentState = {
        ...initialState(repoDir),
        source: String(options.source || "git-tools-status-api"),
        observedAt: new Date().toISOString(),
        phase: "error",
        ok: false,
        error: {
          code: String(options.code || "git-status-request-failed"),
          message: String(error?.message || error || "Git status request failed.")
        }
      };
      return getState();
    }

    async function refreshState(options = {}) {
      const api = options.api || global.GitToolsStatusApi;
      const repoDir = selectedRepoDir(options);

      if (!api || typeof api.fetchStatus !== "function") {
        return setFailureState(
          new Error("GitToolsStatusApi.fetchStatus is unavailable."),
          {
            repoDir,
            source: "git-tools-status-api-unavailable",
            code: "status-api-unavailable"
          }
        );
      }

      currentState = {
        ...currentState,
        repoDir,
        source: "git-tools-status-api",
        phase: "loading",
        error: null
      };

      try {
        const payload = await api.fetchStatus({repoDir});
        currentState = normalizeStatus(payload, {
          repoDir,
          source: "git-tools-status-api",
          observedAt: options.observedAt
        });
        return getState();
      } catch (error) {
        return setFailureState(error, {
          repoDir,
          source: "git-tools-status-api",
          code: "git-status-request-failed"
        });
      }
    }

    function repositoryStateLabel(state) {
      if (state.phase !== "ready") return state.phase;
      if (state.dirty) return "dirty";
      return "clean";
    }

    function listObjects(state = getState()) {
      const safeState = state && typeof state === "object" ? state : getState();
      return [
        {
          id: "repository",
          kind: "git-repository",
          identity: safeState.gitRoot || safeState.repoDir,
          state: repositoryStateLabel(safeState),
          attributes: {
            isGitRepo: safeState.isGitRepo,
            hasHead: safeState.hasHead
          }
        },
        {
          id: "branch",
          kind: "git-branch",
          identity: safeState.branch || "unknown",
          state: {
            ahead: safeState.ahead,
            behind: safeState.behind
          }
        },
        {
          id: "working-tree",
          kind: "git-working-tree",
          identity: safeState.gitRoot || safeState.repoDir,
          state: {
            dirty: safeState.dirty,
            changedCount: safeState.changedCount,
            untrackedCount: safeState.untrackedCount
          }
        },
        {
          id: "remotes",
          kind: "git-remote-collection",
          identity: "configured remotes",
          state: clonePlain(safeState.remotes || [])
        },
        {
          id: "patch-inventory",
          kind: "patch-inventory",
          identity: "new_patch inventory",
          state: clonePlain(safeState.patching || {})
        }
      ];
    }

    function intentAvailability(definition, state) {
      if (definition.id !== "refreshStatus" && state.phase === "uninitialized") {
        return {
          available: false,
          blockedReason: "Repository state has not been observed."
        };
      }
      if (definition.requiresGitRepo && state.isGitRepo !== true) {
        return {
          available: false,
          blockedReason: "A confirmed Git repository state is required."
        };
      }
      if (definition.requiresRemote && !(state.remotes || []).length) {
        return {
          available: false,
          blockedReason: "A configured remote is required."
        };
      }
      if (definition.preflightRequired) {
        const executable = intentCoverageStatus(definition) === "executable";
        return {
          available: true,
          blockedReason: executable
            ? "A successful preflight and explicit confirmation are required before execution."
            : "Execution remains disabled; run preflight for an evidence-backed decision."
        };
      }
      return {
        available: true,
        blockedReason: ""
      };
    }

    function intentCoverageStatus(definition) {
      if (["refreshStatus", "pushCurrentBranch"].includes(definition.id)) return "executable";
      if (definition.id === "runManualCommand") return "prohibited";
      if (definition.preflightRequired === true) return "preflight-only";
      return "declared-only";
    }

    function intentExecutionBinding(definition, status = intentCoverageStatus(definition)) {
      if (definition.id === "refreshStatus" && status === "executable") {
        return "git-tools-status-api.fetchStatus";
      }
      if (definition.id === "pushCurrentBranch" && status === "executable") {
        return "git-tools-server-panel.serverPushLocal";
      }
      if (status === "preflight-only") return "mcel-preflight";
      if (status === "prohibited") return "policy-prohibited";
      return "not-registered";
    }

    function getIntentCoverage() {
      const entries = INTENT_DEFINITIONS.map((definition) => {
        const status = intentCoverageStatus(definition);
        return {
          intentId: definition.id,
          label: definition.label,
          risk: definition.risk,
          mutates: definition.mutates,
          status,
          executable: status === "executable",
          preflightAvailable: status === "preflight-only",
          prohibited: status === "prohibited",
          executionBinding: intentExecutionBinding(definition, status),
          complete: ["executable", "prohibited"].includes(status)
        };
      });
      const requiredIntentIds = entries.map((entry) => entry.intentId);
      const executableIntentIds = entries
        .filter((entry) => entry.status === "executable")
        .map((entry) => entry.intentId);
      const preflightOnlyIntentIds = entries
        .filter((entry) => entry.status === "preflight-only")
        .map((entry) => entry.intentId);
      const declaredOnlyIntentIds = entries
        .filter((entry) => entry.status === "declared-only")
        .map((entry) => entry.intentId);
      const prohibitedIntentIds = entries
        .filter((entry) => entry.status === "prohibited")
        .map((entry) => entry.intentId);
      const incompleteIntentIds = entries
        .filter((entry) => entry.complete !== true)
        .map((entry) => entry.intentId);
      const safeReadEntries = entries.filter((entry) => entry.risk === "safe-read");
      const safeReadComplete = safeReadEntries.every(
        (entry) => entry.status === "executable"
      );
      const fullApplicationSemanticReady = incompleteIntentIds.length === 0;
      const governedPublishExecutable = entries.some(
        (entry) =>
          entry.intentId === "pushCurrentBranch" &&
          entry.status === "executable"
      );
      const semanticRuntimeScope = governedPublishExecutable
        ? "governed-publish-partial"
        : (
          safeReadComplete
            ? "safe-read-complete"
            : "safe-read-partial"
        );
      const checks = {
        everyIntentClassified: entries.length === INTENT_DEFINITIONS.length,
        uniqueIntentIds:
          new Set(requiredIntentIds).size === requiredIntentIds.length,
        executionBindingsDeclared: entries.every(
          (entry) => Boolean(entry.executionBinding)
        ),
        safeReadScopeDerived: safeReadEntries.length > 0,
        fullReadinessDerived:
          fullApplicationSemanticReady === (incompleteIntentIds.length === 0)
      };

      return {
        schemaVersion: INTENT_COVERAGE_SCHEMA_VERSION,
        source: "git-tools-intent-coverage-audit-v1",
        verificationMode: "derived-intent-coverage-audit",
        semanticRuntimeScope,
        fullApplicationSemanticReady,
        requiredIntentIds,
        classifiedIntentIds: requiredIntentIds.slice(),
        executableIntentIds,
        preflightOnlyIntentIds,
        declaredOnlyIntentIds,
        prohibitedIntentIds,
        incompleteIntentIds,
        counts: {
          total: entries.length,
          executable: executableIntentIds.length,
          preflightOnly: preflightOnlyIntentIds.length,
          declaredOnly: declaredOnlyIntentIds.length,
          prohibited: prohibitedIntentIds.length,
          blocked: declaredOnlyIntentIds.length + prohibitedIntentIds.length
        },
        entries,
        verification: {
          passed: Object.values(checks).every(Boolean),
          checks
        }
      };
    }

    function listIntents(state = getState()) {
      const safeState = state && typeof state === "object" ? state : getState();
      return INTENT_DEFINITIONS.map((definition) => {
        const availability = intentAvailability(definition, safeState);
        const semanticStatus = intentCoverageStatus(definition);
        const executable = semanticStatus === "executable";
        return {
          ...definition,
          semanticStatus,
          available: availability.available,
          blockedReason:
            availability.blockedReason ||
            (
              semanticStatus === "prohibited"
                ? "This intent is explicitly prohibited by MCEL policy."
                : (
                  executable
                    ? ""
                    : "No governed execution binding is registered for this intent."
                )
            ),
          executable,
          executionBinding: intentExecutionBinding(definition, semanticStatus),
          receiptRequired: true,
          receiptAvailable: typeof buildReceipt === "function"
        };
      });
    }

    function normalizeIntentId(intentOrId) {
      if (typeof intentOrId === "object" && intentOrId) {
        return String(intentOrId.id || intentOrId.intentId || "").trim();
      }
      return String(intentOrId || "").trim();
    }

    function definitionFor(intentOrId) {
      const intentId = normalizeIntentId(intentOrId);
      return INTENT_DEFINITIONS.find((entry) => entry.id === intentId) || null;
    }

    function canonicalStateSnapshot(state) {
      return {
        schemaVersion: state.schemaVersion || STATE_SCHEMA_VERSION,
        phase: state.phase || "unknown",
        observedAt: state.observedAt || "",
        repoDir: state.repoDir || ".",
        gitRoot: state.gitRoot || "",
        isGitRepo: state.isGitRepo === true,
        hasHead: state.hasHead === true,
        branch: state.branch || "unknown",
        ahead: nullableInteger(state.ahead),
        behind: nullableInteger(state.behind),
        dirty: state.dirty === null || state.dirty === undefined ? null : Boolean(state.dirty),
        changedCount: integerOr(state.changedCount),
        untrackedCount: integerOr(state.untrackedCount),
        remotes: normalizeRemotes(state.remotes)
      };
    }

    function stableStringify(value) {
      if (value == null || typeof value !== "object") return JSON.stringify(value);
      if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
      const keys = Object.keys(value).sort();
      return `{${keys.map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(",")}}`;
    }

    function simpleHash(value) {
      const text = String(value || "");
      let hash = 2166136261;
      for (let index = 0; index < text.length; index += 1) {
        hash ^= text.charCodeAt(index);
        hash = Math.imul(hash, 16777619);
      }
      return `fnv1a-${(hash >>> 0).toString(16).padStart(8, "0")}`;
    }

    function stateFingerprint(state) {
      return simpleHash(stableStringify(canonicalStateSnapshot(state)));
    }

    function stateContentFingerprint(state) {
      const snapshot = canonicalStateSnapshot(state);
      delete snapshot.observedAt;
      return simpleHash(stableStringify(snapshot));
    }

    function normalizeIntentParameters(intentOrId, input = {}) {
      const intentId = normalizeIntentId(intentOrId);
      const safeInput = input && typeof input === "object" ? input : {};
      if (intentId !== "pushCurrentBranch" && intentId !== "preparePush") {
        return {};
      }
      return {
        repoDir: String(
          safeInput.repoDir ??
          safeInput.repo_dir ??
          ""
        ).trim(),
        remote: String(
          safeInput.remote ??
          safeInput.targetRemote ??
          ""
        ).trim(),
        owner: String(safeInput.owner ?? "").trim(),
        repo: String(safeInput.repo ?? safeInput.repoName ?? "").trim(),
        protocol: String(safeInput.protocol ?? "http").trim().toLowerCase() || "http",
        switchOrigin: safeInput.switchOrigin === true || safeInput.switch_origin === true
      };
    }

    function receiptStorage() {
      try {
        const storage = global.localStorage;
        if (
          storage &&
          typeof storage.getItem === "function" &&
          typeof storage.setItem === "function"
        ) {
          return storage;
        }
      } catch (_error) {
        // Storage can be unavailable in private/sandboxed browser contexts.
      }
      return null;
    }

    function loadReceipts() {
      const storage = receiptStorage();
      if (!storage) return receiptLedger;
      try {
        const parsed = JSON.parse(storage.getItem(RECEIPT_STORAGE_KEY) || "[]");
        if (Array.isArray(parsed)) {
          receiptLedger = parsed.slice(-MAX_RECEIPTS).map(clonePlain);
        }
      } catch (_error) {
        receiptLedger = [];
      }
      return receiptLedger;
    }

    function persistReceipts() {
      const storage = receiptStorage();
      if (!storage) return false;
      try {
        storage.setItem(RECEIPT_STORAGE_KEY, JSON.stringify(receiptLedger.slice(-MAX_RECEIPTS)));
        return true;
      } catch (_error) {
        return false;
      }
    }

    function listReceipts(options = {}) {
      loadReceipts();
      const intentId = normalizeIntentId(options.intentId || options.intent || "");
      const receipts = intentId
        ? receiptLedger.filter((receipt) => receipt.intentId === intentId)
        : receiptLedger;
      return clonePlain(receipts);
    }

    function storedPushPreflightReceipt(preflightResult) {
      const suppliedReceipt = preflightResult?.receipt;
      if (!suppliedReceipt?.receiptId) return null;
      loadReceipts();
      const stored = receiptLedger.find(
        (receipt) => receipt.receiptId === suppliedReceipt.receiptId
      );
      if (!stored) return null;
      const suppliedParameters = normalizeIntentParameters(
        "pushCurrentBranch",
        preflightResult.parameters || {}
      );
      const storedParameters = normalizeIntentParameters(
        "pushCurrentBranch",
        stored.parameters || {}
      );
      const requiredMatches = [
        stored.kind === "preflight-decision-receipt",
        stored.intentId === "pushCurrentBranch",
        stored.decision === "confirm",
        stored.status === "confirmation-required",
        String(stored.preflightId || "") === String(preflightResult.preflightId || ""),
        String(stored.expiresAt || "") === String(preflightResult.expiresAt || ""),
        String(stored.stateFingerprint || "") === String(preflightResult.stateFingerprint || ""),
        String(stored.stateContentFingerprint || "") ===
          String(preflightResult.stateContentFingerprint || ""),
        JSON.stringify(storedParameters) === JSON.stringify(suppliedParameters)
      ];
      return requiredMatches.every(Boolean) ? clonePlain(stored) : null;
    }

    function clearReceipts() {
      receiptLedger = [];
      const storage = receiptStorage();
      if (storage) {
        try {
          storage.removeItem(RECEIPT_STORAGE_KEY);
        } catch (_error) {
          // The in-memory ledger has still been cleared.
        }
      }
      return [];
    }

    function nowIso(options = {}) {
      const supplied = options.now;
      if (supplied instanceof Date) return supplied.toISOString();
      if (typeof supplied === "string" && supplied.trim()) {
        const parsed = new Date(supplied);
        if (Number.isFinite(parsed.getTime())) return parsed.toISOString();
      }
      if (typeof supplied === "number" && Number.isFinite(supplied)) {
        return new Date(supplied).toISOString();
      }
      return new Date().toISOString();
    }

    function addBlocker(blockers, code, message, details = {}) {
      blockers.push({
        code,
        message,
        ...clonePlain(details)
      });
    }

    function addWarning(warnings, code, message, details = {}) {
      warnings.push({
        code,
        message,
        ...clonePlain(details)
      });
    }

    function normalizeFailureSource(input, state = getState()) {
      const safeInput = input && typeof input === "object" ? input : {};
      const sourceCode = String(
        (typeof input === "string" ? input : "") ||
        safeInput.failureClass ||
        safeInput.code ||
        safeInput.error?.code ||
        safeInput.blockers?.[0]?.details?.stateErrorCode ||
        safeInput.blockers?.[0]?.stateErrorCode ||
        safeInput.blockers?.[0]?.code ||
        safeInput.warnings?.[0]?.code ||
        state?.error?.code ||
        ""
      ).trim();
      const sourceMessage = String(
        safeInput.message ||
        safeInput.error?.message ||
        safeInput.blockers?.[0]?.message ||
        safeInput.warnings?.[0]?.message ||
        state?.error?.message ||
        ""
      ).trim();
      return {
        sourceCode,
        sourceMessage,
        sourceReceiptId: String(safeInput.receiptId || safeInput.sourceReceiptId || ""),
        sourceIntentId: String(safeInput.intentId || ""),
        sourceKind: String(safeInput.kind || "")
      };
    }

    function classifyFailure(input, state = getState(), options = {}) {
      const safeState = state && typeof state === "object" ? state : getState();
      const source = normalizeFailureSource(input, safeState);
      const mappedClass =
        SOURCE_CODE_TO_FAILURE_CLASS[source.sourceCode] ||
        (RECOVERY_FAILURE_DEFINITIONS[source.sourceCode] ? source.sourceCode : "");
      const failureClass = mappedClass || (
        source.sourceCode || safeState.phase === "error"
          ? "unknown-failure"
          : "none"
      );
      const definition = failureClass === "none"
        ? {
            severity: "none",
            retrySafe: true,
            refreshRequired: false,
            mutationAllowed: true,
            message: "No recovery-relevant failure was classified.",
            recommendedNextStep: "No recovery action is required.",
            prohibitedActions: []
          }
        : RECOVERY_FAILURE_DEFINITIONS[failureClass] ||
          RECOVERY_FAILURE_DEFINITIONS["unknown-failure"];
      const classifiedAt = nowIso(options);
      return {
        schemaVersion: RECOVERY_CLASSIFICATION_SCHEMA_VERSION,
        classificationId: `${APP_ID}-recovery-classification-${Date.parse(classifiedAt) || Date.now()}-${receiptSequence + 1}`,
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        adapterVersion: VERSION,
        sourceCode: source.sourceCode || "none",
        sourceMessage: source.sourceMessage,
        sourceReceiptId: source.sourceReceiptId,
        sourceIntentId: source.sourceIntentId,
        sourceKind: source.sourceKind,
        failureClass,
        known: failureClass === "none" || Boolean(RECOVERY_FAILURE_DEFINITIONS[failureClass]),
        severity: definition.severity,
        retrySafe: definition.retrySafe === true,
        refreshRequired: definition.refreshRequired === true,
        mutationAllowed: definition.mutationAllowed === true,
        message: source.sourceMessage || definition.message,
        recommendedNextStep: definition.recommendedNextStep,
        prohibitedActions: clonePlain(definition.prohibitedActions || []),
        classifiedAt,
        stateFingerprint: stateFingerprint(safeState)
      };
    }

    function recoveryOptionsForClass(failureClass) {
      const sharedRefresh = {
        intentId: "refreshStatus",
        label: "Execute governed status refresh",
        kind: "governed-execution",
        executable: true,
        safe: true
      };
      const optionsByClass = {
        "status-backend-unavailable": [
          sharedRefresh,
          {intentId: "inspectBackend", label: "Inspect status backend availability", kind: "inspection", executable: false, safe: true}
        ],
        "status-refresh-failed": [
          sharedRefresh,
          {intentId: "viewSourceReceipt", label: "Inspect the failed execution receipt", kind: "inspection", executable: false, safe: true}
        ],
        "state-not-observed": [sharedRefresh],
        "state-stale": [sharedRefresh],
        "not-a-git-repository": [
          {intentId: "selectRepository", label: "Select the intended repository", kind: "human-action", executable: false, safe: true},
          sharedRefresh
        ],
        "remote-missing": [
          {intentId: "inspectRemotes", label: "Inspect configured remotes", kind: "inspection", executable: false, safe: true},
          {intentId: "configureRemote", label: "Configure the intended remote outside MCEL", kind: "human-action", executable: false, safe: false}
        ],
        "remote-divergence-unknown": [
          sharedRefresh,
          {intentId: "inspectAheadBehind", label: "Inspect ahead and behind state", kind: "inspection", executable: false, safe: true}
        ],
        "remote-behind": [
          sharedRefresh,
          {intentId: "inspectAheadBehind", label: "Inspect remote commits", kind: "inspection", executable: false, safe: true},
          {intentId: "chooseIntegrationStrategy", label: "Choose merge or rebase explicitly", kind: "human-action", executable: false, safe: false}
        ],
        "remote-diverged": [
          sharedRefresh,
          {intentId: "inspectAheadBehind", label: "Inspect local and remote commits", kind: "inspection", executable: false, safe: true},
          {intentId: "exportPatch", label: "Export local changes before integration", kind: "human-action", executable: false, safe: true}
        ],
        "nothing-to-publish": [
          {intentId: "inspectWorkingTree", label: "Inspect working-tree changes", kind: "inspection", executable: false, safe: true},
          {intentId: "commitChanges", label: "Commit intended changes outside MCEL", kind: "human-action", executable: false, safe: false}
        ],
        "working-tree-dirty": [
          {intentId: "inspectWorkingTree", label: "Inspect working-tree changes", kind: "inspection", executable: false, safe: true},
          {intentId: "commitOrStash", label: "Commit or stash intended changes outside MCEL", kind: "human-action", executable: false, safe: false}
        ],
        "execution-binding-unavailable": [
          {intentId: "inspectAvailableIntents", label: "Inspect registered semantic intents", kind: "inspection", executable: false, safe: true}
        ],
        "manual-command-policy-block": [
          {intentId: "inspectAvailableIntents", label: "Choose a named governed intent", kind: "inspection", executable: false, safe: true}
        ],
        "branch-unpublishable": [
          {intentId: "selectBranch", label: "Select or create the intended branch", kind: "human-action", executable: false, safe: false},
          sharedRefresh
        ],
        "unsupported-intent": [
          {intentId: "inspectAvailableIntents", label: "Inspect registered semantic intents", kind: "inspection", executable: false, safe: true}
        ],
        "confirmation-declined": [
          {intentId: "reviewPushTarget", label: "Review repository, branch, and Local Gitea target", kind: "inspection", executable: false, safe: true}
        ],
        "preflight-required": [
          sharedRefresh,
          {intentId: "preparePush", label: "Run governed push preflight", kind: "preflight", executable: false, safe: true}
        ],
        "preflight-expired": [
          sharedRefresh,
          {intentId: "preparePush", label: "Create a new push preflight receipt", kind: "preflight", executable: false, safe: true}
        ],
        "state-changed-after-preflight": [
          sharedRefresh,
          {intentId: "reviewPushDecision", label: "Review the new push decision", kind: "inspection", executable: false, safe: true}
        ],
        "push-backend-failed": [
          sharedRefresh,
          {intentId: "viewSourceReceipt", label: "Inspect the failed push execution receipt", kind: "inspection", executable: false, safe: true},
          {intentId: "inspectGitServer", label: "Inspect Local Gitea and remote configuration", kind: "inspection", executable: false, safe: true}
        ],
        "post-push-state-refresh-failed": [
          sharedRefresh,
          {intentId: "viewSourceReceipt", label: "Inspect the completed push receipt", kind: "inspection", executable: false, safe: true}
        ],
        "unknown-failure": [
          sharedRefresh,
          {intentId: "viewSourceReceipt", label: "Inspect the source receipt", kind: "inspection", executable: false, safe: true}
        ]
      };
      return clonePlain(optionsByClass[failureClass] || optionsByClass["unknown-failure"]);
    }

    function buildRecoveryOptions(failureOrInput, state = getState(), options = {}) {
      const safeState = state && typeof state === "object" ? state : getState();
      const failure = failureOrInput?.schemaVersion === RECOVERY_CLASSIFICATION_SCHEMA_VERSION
        ? clonePlain(failureOrInput)
        : classifyFailure(failureOrInput, safeState, options);
      const generatedAt = nowIso(options);
      const recoveryOptions = failure.failureClass === "none"
        ? []
        : recoveryOptionsForClass(failure.failureClass);
      return {
        schemaVersion: RECOVERY_PLAN_SCHEMA_VERSION,
        recoveryPlanId: `${APP_ID}-recovery-plan-${Date.parse(generatedAt) || Date.now()}-${receiptSequence + 1}`,
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        adapterVersion: VERSION,
        sourceReceiptId: failure.sourceReceiptId || "",
        sourceIntentId: failure.sourceIntentId || "",
        failureClass: failure.failureClass,
        severity: failure.severity,
        retrySafe: failure.retrySafe === true,
        refreshRequired: failure.refreshRequired === true,
        mutationAllowed: failure.mutationAllowed === true,
        recommendedNextStep: failure.recommendedNextStep,
        options: recoveryOptions,
        prohibitedActions: clonePlain(failure.prohibitedActions || []),
        generatedAt,
        stateFingerprint: failure.stateFingerprint || stateFingerprint(safeState)
      };
    }

    function decorateReceiptWithRecovery(receipt, state = getState(), options = {}) {
      const failure = classifyFailure(
        {
          ...clonePlain(receipt),
          sourceReceiptId: receipt?.receiptId || ""
        },
        state,
        options
      );
      const recovery = failure.failureClass === "none"
        ? null
        : buildRecoveryOptions(failure, state, options);
      return {
        ...receipt,
        recoveryClassified: true,
        failure: failure.failureClass === "none" ? null : failure,
        recovery
      };
    }

    function recoveryDefinitionComplete(definition) {
      return Boolean(
        definition &&
        typeof definition.severity === "string" &&
        definition.severity.trim() &&
        typeof definition.retrySafe === "boolean" &&
        typeof definition.refreshRequired === "boolean" &&
        typeof definition.mutationAllowed === "boolean" &&
        typeof definition.message === "string" &&
        definition.message.trim() &&
        typeof definition.recommendedNextStep === "string" &&
        definition.recommendedNextStep.trim() &&
        Array.isArray(definition.prohibitedActions)
      );
    }

    function recoveryOptionComplete(option) {
      return Boolean(
        option &&
        typeof option.intentId === "string" &&
        option.intentId.trim() &&
        typeof option.label === "string" &&
        option.label.trim() &&
        typeof option.kind === "string" &&
        option.kind.trim() &&
        typeof option.executable === "boolean" &&
        typeof option.safe === "boolean"
      );
    }

    function getRecoveryCoverage() {
      const requiredFailureClasses = Array.from(
        new Set(Object.values(SOURCE_CODE_TO_FAILURE_CLASS))
      ).sort();
      const invalidSourceMappings = Object.entries(SOURCE_CODE_TO_FAILURE_CLASS)
        .filter(([, failureClass]) => !RECOVERY_FAILURE_DEFINITIONS[failureClass])
        .map(([sourceCode, failureClass]) => ({sourceCode, failureClass}));
      const missingDefinitions = requiredFailureClasses
        .filter((failureClass) => !RECOVERY_FAILURE_DEFINITIONS[failureClass]);
      const invalidDefinitions = requiredFailureClasses
        .filter((failureClass) => {
          const definition = RECOVERY_FAILURE_DEFINITIONS[failureClass];
          return definition && !recoveryDefinitionComplete(definition);
        });
      const missingGuidance = requiredFailureClasses
        .filter((failureClass) => recoveryOptionsForClass(failureClass).length === 0);
      const invalidGuidance = requiredFailureClasses
        .filter((failureClass) => {
          const options = recoveryOptionsForClass(failureClass);
          return options.length > 0 && options.some((option) => !recoveryOptionComplete(option));
        });
      const fallbackReady = Boolean(
        recoveryDefinitionComplete(RECOVERY_FAILURE_DEFINITIONS["unknown-failure"]) &&
        recoveryOptionsForClass("unknown-failure").length > 0 &&
        recoveryOptionsForClass("unknown-failure").every(recoveryOptionComplete)
      );
      const unverifiedFailureClasses = Array.from(new Set([
        ...missingDefinitions,
        ...invalidDefinitions,
        ...missingGuidance,
        ...invalidGuidance,
        ...invalidSourceMappings.map((entry) => entry.failureClass)
      ])).sort();
      const coveredFailureClasses = requiredFailureClasses
        .filter((failureClass) => !unverifiedFailureClasses.includes(failureClass));
      const proofChecks = {
        sourceMappingsValid: invalidSourceMappings.length === 0,
        definitionsComplete:
          missingDefinitions.length === 0 &&
          invalidDefinitions.length === 0,
        guidanceComplete:
          missingGuidance.length === 0 &&
          invalidGuidance.length === 0,
        unknownFallbackReady: fallbackReady,
        requiredClassesCovered:
          requiredFailureClasses.length > 0 &&
          coveredFailureClasses.length === requiredFailureClasses.length
      };
      const verificationPassed = Object.values(proofChecks).every(Boolean);
      return {
        version: RECOVERY_COVERAGE_VERSION,
        source: "git-tools-recovery-coverage-audit-v1",
        verificationMode: "derived-runtime-audit",
        classificationReady: proofChecks.definitionsComplete,
        guidanceReady: proofChecks.guidanceComplete,
        coverageReady: verificationPassed,
        requiredFailureClasses,
        coveredFailureClasses,
        unverifiedFailureClasses,
        missingDefinitions,
        invalidDefinitions,
        missingGuidance,
        invalidGuidance,
        invalidSourceMappings,
        fallbackFailureClass: "unknown-failure",
        verification: {
          passed: verificationPassed,
          checks: proofChecks
        },
        reason: verificationPassed
          ? "Every supported failure class has a complete definition, mapped source code, and structured recovery guidance."
          : "Recovery coverage audit found incomplete or unmapped failure guidance."
      };
    }

    function evaluatePreflight(intentOrId, state = getState(), options = {}) {
      const definition = definitionFor(intentOrId);
      const safeState = state && typeof state === "object" ? clonePlain(state) : getState();
      const evaluatedAt = nowIso(options);
      const blockers = [];
      const warnings = [];
      const intentId = normalizeIntentId(intentOrId);
      const maxStateAgeMs = Number.isFinite(Number(options.maxStateAgeMs))
        ? Math.max(0, Number(options.maxStateAgeMs))
        : DEFAULT_MAX_STATE_AGE_MS;
      const parameters = normalizeIntentParameters(
        definition?.id || intentId,
        options.parameters || options
      );

      if (!definition) {
        addBlocker(
          blockers,
          "unsupported-intent",
          `Git Tools does not define the intent "${intentId || "unknown"}".`
        );
      } else if (definition.id === "refreshStatus") {
        // Refresh is always semantically valid and uses the governed status binding.
      } else if (definition.id === "runManualCommand") {
        addBlocker(
          blockers,
          "manual-command-policy-block",
          "Arbitrary Git command execution is outside this adapter's policy."
        );
      } else {
        let prerequisitesReady = true;

        if (safeState.phase === "uninitialized" || !safeState.observedAt) {
          addBlocker(blockers, "state-not-observed", "Repository state must be refreshed before this intent can proceed.");
          prerequisitesReady = false;
        } else if (safeState.phase === "loading") {
          addBlocker(blockers, "state-loading", "Repository state is still loading.");
          prerequisitesReady = false;
        } else if (safeState.phase === "error") {
          addBlocker(
            blockers,
            "state-error",
            safeState.error?.message || "Repository state could not be derived.",
            {stateErrorCode: safeState.error?.code || "unknown"}
          );
          prerequisitesReady = false;
        }

        if (prerequisitesReady && definition.preflightRequired) {
          const observedTime = new Date(safeState.observedAt).getTime();
          const evaluatedTime = new Date(evaluatedAt).getTime();
          if (!Number.isFinite(observedTime)) {
            addBlocker(blockers, "state-timestamp-invalid", "The repository state timestamp is invalid.");
            prerequisitesReady = false;
          } else if (evaluatedTime - observedTime > maxStateAgeMs) {
            addBlocker(
              blockers,
              "state-stale",
              "Repository state is stale; refresh before evaluating a risky intent.",
              {
                observedAt: safeState.observedAt,
                maxStateAgeMs
              }
            );
            prerequisitesReady = false;
          }
        }

        if (prerequisitesReady && definition.requiresGitRepo && safeState.isGitRepo !== true) {
          addBlocker(blockers, "not-a-git-repository", "A confirmed Git repository is required.");
          prerequisitesReady = false;
        }

        if (prerequisitesReady && definition.requiresRemote && !(safeState.remotes || []).length) {
          addBlocker(blockers, "remote-missing", "A configured remote is required.");
          prerequisitesReady = false;
        }

        if (
          prerequisitesReady &&
          definition.requiresRemote &&
          parameters.remote &&
          !(safeState.remotes || []).some(
            (remote) => String(remote?.name || "").trim() === parameters.remote
          )
        ) {
          addBlocker(
            blockers,
            "remote-missing",
            `The required remote "${parameters.remote}" is not configured.`,
            {remote: parameters.remote}
          );
          prerequisitesReady = false;
        }
      }

      if (
        definition &&
        (definition.id === "preparePush" || definition.id === "pushCurrentBranch") &&
        blockers.length === 0
      ) {
        const branch = String(safeState.branch || "").trim().toLowerCase();
        if (
          safeState.hasHead !== true ||
          !branch ||
          branch === "unknown" ||
          branch.includes("detached")
        ) {
          addBlocker(blockers, "branch-unpublishable", "A named branch with a confirmed HEAD is required.");
        } else if (safeState.ahead === null || safeState.behind === null) {
          addBlocker(
            blockers,
            "remote-divergence-unknown",
            "Ahead/behind state is unknown; refresh remote-aware status before publication."
          );
        } else if (safeState.behind > 0) {
          addBlocker(
            blockers,
            safeState.ahead > 0 ? "remote-diverged" : "remote-behind",
            safeState.ahead > 0
              ? "Local and remote history have diverged."
              : "The local branch is behind its remote.",
            {
              ahead: safeState.ahead,
              behind: safeState.behind
            }
          );
        } else if (safeState.ahead <= 0) {
          addBlocker(blockers, "nothing-to-publish", "There are no local commits to publish.");
        }

        if (safeState.dirty === true) {
          addWarning(
            warnings,
            "working-tree-dirty",
            "Uncommitted working-tree changes will not be included in the push.",
            {
              changedCount: safeState.changedCount,
              untrackedCount: safeState.untrackedCount
            }
          );
        }
      }

      let decision = "allow";
      if (blockers.length) {
        decision = "block";
      } else if (definition?.id === "pushCurrentBranch") {
        decision = "confirm";
      }

      return {
        schemaVersion: PREFLIGHT_SCHEMA_VERSION,
        preflightId: `${APP_ID}-preflight-${Date.parse(evaluatedAt) || Date.now()}-${receiptSequence + 1}`,
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        adapterVersion: VERSION,
        intentId: definition?.id || intentId || "unknown",
        intentLabel: definition?.label || "Unknown intent",
        risk: definition?.risk || "unknown",
        mutates: definition?.mutates ?? "unknown",
        evaluatedAt,
        expiresAt: definition?.preflightRequired
          ? new Date((Date.parse(evaluatedAt) || Date.now()) + maxStateAgeMs).toISOString()
          : "",
        stateObservedAt: safeState.observedAt || "",
        stateFingerprint: stateFingerprint(safeState),
        stateContentFingerprint: stateContentFingerprint(safeState),
        parameters,
        decision,
        allowed: decision === "allow",
        blocked: decision === "block",
        confirmationRequired: decision === "confirm",
        blockers,
        warnings,
        executionAvailable: Boolean(
          definition && intentCoverageStatus(definition) === "executable"
        ),
        executionBinding: definition
          ? intentExecutionBinding(definition)
          : "not-registered",
        state: canonicalStateSnapshot(safeState)
      };
    }

    function receiptStatusFor(preflight) {
      if (preflight.decision === "block") return "blocked";
      if (preflight.decision === "confirm") return "confirmation-required";
      return "allowed";
    }

    function storeReceipt(receipt, options = {}) {
      if (options.store === false) return clonePlain(receipt);
      loadReceipts();
      receiptLedger.push(receipt);
      receiptLedger = receiptLedger.slice(-MAX_RECEIPTS);
      persistReceipts();
      return clonePlain(receipt);
    }

    function buildReceipt(preflight, options = {}) {
      if (!preflight || typeof preflight !== "object") {
        throw new TypeError("A preflight decision is required to build a receipt.");
      }
      receiptSequence += 1;
      const createdAt = nowIso({now: options.now || preflight.evaluatedAt});
      const receipt = {
        schemaVersion: RECEIPT_SCHEMA_VERSION,
        receiptId: `${APP_ID}-receipt-${Date.parse(createdAt) || Date.now()}-${receiptSequence}`,
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        adapterVersion: VERSION,
        kind: "preflight-decision-receipt",
        intentId: String(preflight.intentId || "unknown"),
        risk: String(preflight.risk || "unknown"),
        status: receiptStatusFor(preflight),
        decision: String(preflight.decision || "block"),
        createdAt,
        preflightId: String(preflight.preflightId || ""),
        expiresAt: String(preflight.expiresAt || ""),
        stateObservedAt: String(preflight.stateObservedAt || ""),
        stateFingerprint: String(preflight.stateFingerprint || ""),
        stateContentFingerprint: String(preflight.stateContentFingerprint || ""),
        parameters: clonePlain(preflight.parameters || {}),
        blockers: clonePlain(preflight.blockers || []),
        warnings: clonePlain(preflight.warnings || []),
        executionAttempted: false,
        executionBinding: String(preflight.executionBinding || "not-registered"),
        recoveryClassified: false
      };
      const decoratedReceipt = decorateReceiptWithRecovery(
        receipt,
        preflight.state || getState(),
        options
      );

      return storeReceipt(decoratedReceipt, options);
    }

    function buildExecutionReceipt(preflight, stateBefore, stateAfter, options = {}) {
      receiptSequence += 1;
      const createdAt = nowIso({
        now: options.completedAt || options.now || stateAfter?.observedAt
      });
      const executionSucceeded =
        stateAfter?.phase !== "error" &&
        stateAfter?.ok !== false;
      const beforeFingerprint = stateFingerprint(stateBefore || initialState());
      const afterFingerprint = stateFingerprint(stateAfter || initialState());
      const receipt = {
        schemaVersion: RECEIPT_SCHEMA_VERSION,
        receiptId: `${APP_ID}-receipt-${Date.parse(createdAt) || Date.now()}-${receiptSequence}`,
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        adapterVersion: VERSION,
        kind: "action-execution-receipt",
        intentId: "refreshStatus",
        risk: "safe-read",
        status: executionSucceeded ? "succeeded" : "failed",
        decision: "allow",
        createdAt,
        startedAt: String(options.startedAt || preflight?.evaluatedAt || createdAt),
        completedAt: createdAt,
        preflightId: String(preflight?.preflightId || ""),
        stateObservedAt: String(stateAfter?.observedAt || ""),
        stateFingerprint: afterFingerprint,
        stateBeforeFingerprint: beforeFingerprint,
        stateAfterFingerprint: afterFingerprint,
        blockers: clonePlain(preflight?.blockers || []),
        warnings: clonePlain(preflight?.warnings || []),
        executionAttempted: true,
        executionBinding: "git-tools-status-api.fetchStatus",
        result: {
          status: executionSucceeded ? "succeeded" : "failed",
          phase: String(stateAfter?.phase || "unknown"),
          source: String(stateAfter?.source || "git-tools-status-api"),
          repoDir: String(stateAfter?.repoDir || stateBefore?.repoDir || "."),
          gitRoot: String(stateAfter?.gitRoot || ""),
          branch: String(stateAfter?.branch || "unknown")
        },
        error: clonePlain(stateAfter?.error || null),
        recoveryClassified: false
      };
      const decoratedReceipt = decorateReceiptWithRecovery(
        receipt,
        stateAfter || stateBefore || getState(),
        options
      );

      return storeReceipt(decoratedReceipt, options);
    }

    function buildConfirmationReceipt(preflightResult, options = {}) {
      if (!preflightResult || typeof preflightResult !== "object") {
        throw new TypeError("A push preflight result is required for confirmation.");
      }
      receiptSequence += 1;
      const accepted = options.accepted === true;
      const createdAt = nowIso({
        now: options.confirmedAt || options.now || new Date().toISOString()
      });
      const preflightReceipt = preflightResult.receipt || null;
      const receipt = {
        schemaVersion: RECEIPT_SCHEMA_VERSION,
        confirmationSchemaVersion: CONFIRMATION_SCHEMA_VERSION,
        receiptId: `${APP_ID}-receipt-${Date.parse(createdAt) || Date.now()}-${receiptSequence}`,
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        adapterVersion: VERSION,
        kind: "confirmation-decision-receipt",
        intentId: "pushCurrentBranch",
        risk: "publish-mutation",
        status: accepted ? "confirmed" : "cancelled",
        decision: accepted ? "confirm" : "decline",
        createdAt,
        confirmedAt: accepted ? createdAt : "",
        preflightId: String(preflightResult.preflightId || ""),
        preflightReceiptId: String(preflightReceipt?.receiptId || ""),
        parentReceiptId: String(preflightReceipt?.receiptId || ""),
        stateObservedAt: String(preflightResult.stateObservedAt || ""),
        stateFingerprint: String(preflightResult.stateFingerprint || ""),
        stateContentFingerprint: String(preflightResult.stateContentFingerprint || ""),
        parameters: clonePlain(preflightResult.parameters || {}),
        blockers: accepted
          ? []
          : [{
              code: "confirmation-declined",
              message: "The user declined the governed Local Gitea push confirmation."
            }],
        warnings: clonePlain(preflightResult.warnings || []),
        executionAttempted: false,
        executionBinding: "git-tools-server-panel.serverPushLocal",
        result: {
          status: accepted ? "confirmed" : "cancelled",
          accepted
        },
        recoveryClassified: false
      };
      const decoratedReceipt = decorateReceiptWithRecovery(
        receipt,
        preflightResult.state || getState(),
        options
      );
      return storeReceipt(decoratedReceipt, options);
    }

    function buildPushGuardReceipt(
      preflightResult,
      confirmationReceipt,
      state,
      blockers,
      options = {}
    ) {
      receiptSequence += 1;
      const createdAt = nowIso({
        now: options.completedAt || options.now || new Date().toISOString()
      });
      const preflightReceipt = preflightResult?.receipt || null;
      const safeState = state && typeof state === "object" ? state : getState();
      const receipt = {
        schemaVersion: RECEIPT_SCHEMA_VERSION,
        pushExecutionSchemaVersion: PUSH_EXECUTION_SCHEMA_VERSION,
        receiptId: `${APP_ID}-receipt-${Date.parse(createdAt) || Date.now()}-${receiptSequence}`,
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        adapterVersion: VERSION,
        kind: "action-execution-receipt",
        intentId: "pushCurrentBranch",
        risk: "publish-mutation",
        status: "blocked",
        decision: "block",
        createdAt,
        preflightId: String(preflightResult?.preflightId || ""),
        preflightReceiptId: String(preflightReceipt?.receiptId || ""),
        confirmationReceiptId: String(confirmationReceipt?.receiptId || ""),
        parentReceiptId: String(
          confirmationReceipt?.receiptId ||
          preflightReceipt?.receiptId ||
          ""
        ),
        stateObservedAt: String(safeState.observedAt || ""),
        stateFingerprint: stateFingerprint(safeState),
        stateContentFingerprint: stateContentFingerprint(safeState),
        parameters: clonePlain(preflightResult?.parameters || options.parameters || {}),
        blockers: clonePlain(blockers || []),
        warnings: clonePlain(preflightResult?.warnings || []),
        executionAttempted: false,
        executionBinding: "git-tools-server-panel.serverPushLocal",
        result: {
          status: "blocked"
        },
        error: null,
        recoveryClassified: false
      };
      const decoratedReceipt = decorateReceiptWithRecovery(
        receipt,
        safeState,
        options
      );
      return storeReceipt(decoratedReceipt, options);
    }

    function buildPushExecutionReceipt(
      preflightResult,
      confirmationReceipt,
      stateBefore,
      stateAfter,
      backendResult,
      backendError,
      options = {}
    ) {
      receiptSequence += 1;
      const createdAt = nowIso({
        now: options.completedAt || options.now || new Date().toISOString()
      });
      const preflightReceipt = preflightResult?.receipt || null;
      const backendSucceeded = backendResult?.ok === true && !backendError;
      const postRefreshFailed =
        stateAfter?.phase === "error" ||
        stateAfter?.ok === false;
      const warnings = [
        ...clonePlain(preflightResult?.warnings || [])
      ];
      if (backendSucceeded && postRefreshFailed) {
        warnings.push({
          code: "post-push-state-refresh-failed",
          message: "The push completed, but post-push repository state could not be verified."
        });
      }
      const error = backendSucceeded
        ? null
        : {
            code: "push-backend-failed",
            message: String(
              backendError?.message ||
              backendResult?.error ||
              "The Local Gitea push backend reported a failure."
            )
          };
      const receipt = {
        schemaVersion: RECEIPT_SCHEMA_VERSION,
        pushExecutionSchemaVersion: PUSH_EXECUTION_SCHEMA_VERSION,
        receiptId: `${APP_ID}-receipt-${Date.parse(createdAt) || Date.now()}-${receiptSequence}`,
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        adapterVersion: VERSION,
        kind: "action-execution-receipt",
        intentId: "pushCurrentBranch",
        risk: "publish-mutation",
        status: backendSucceeded ? "succeeded" : "failed",
        decision: "execute",
        createdAt,
        startedAt: String(options.startedAt || createdAt),
        completedAt: createdAt,
        preflightId: String(preflightResult?.preflightId || ""),
        preflightReceiptId: String(preflightReceipt?.receiptId || ""),
        confirmationReceiptId: String(confirmationReceipt?.receiptId || ""),
        parentReceiptId: String(
          confirmationReceipt?.receiptId ||
          preflightReceipt?.receiptId ||
          ""
        ),
        stateObservedAt: String(stateAfter?.observedAt || ""),
        stateFingerprint: stateFingerprint(stateAfter || stateBefore || initialState()),
        stateContentFingerprint: stateContentFingerprint(
          stateAfter || stateBefore || initialState()
        ),
        stateBeforeFingerprint: stateFingerprint(stateBefore || initialState()),
        stateAfterFingerprint: stateFingerprint(stateAfter || initialState()),
        parameters: clonePlain(preflightResult?.parameters || options.parameters || {}),
        blockers: [],
        warnings,
        executionAttempted: true,
        executionBinding: "git-tools-server-panel.serverPushLocal",
        result: {
          status: backendSucceeded ? "succeeded" : "failed",
          backendOk: backendResult?.ok === true,
          operationId: String(backendResult?.operation?.id || ""),
          operationStatus: String(backendResult?.operation?.status || ""),
          remote: String(
            preflightResult?.parameters?.remote ||
            backendResult?.remote ||
            ""
          ),
          branch: String(stateBefore?.branch || "unknown"),
          postRefreshPhase: String(stateAfter?.phase || "unknown"),
          backend: clonePlain(backendResult || null)
        },
        error,
        recoveryClassified: false
      };
      const decoratedReceipt = decorateReceiptWithRecovery(
        receipt,
        stateAfter || stateBefore || getState(),
        options
      );
      return storeReceipt(decoratedReceipt, options);
    }

    function preflightIntent(intentOrId, state = getState(), options = {}) {
      const preflight = evaluatePreflight(intentOrId, state, options);
      const receipt = buildReceipt(preflight, options);
      return {
        ...preflight,
        receipt
      };
    }

    function blockedExecutionResult(intentOrId, state, options = {}) {
      const preflight = evaluatePreflight(intentOrId, state, options);
      const blocker = {
        code: "execution-binding-unavailable",
        message: "No governed execution binding is registered for this intent."
      };
      const blockedPreflight = {
        ...preflight,
        decision: "block",
        allowed: false,
        blocked: true,
        confirmationRequired: false,
        blockers: [
          ...clonePlain(preflight.blockers || []),
          blocker
        ],
        executionAvailable: false,
        executionBinding: "not-registered"
      };
      const receipt = buildReceipt(blockedPreflight, options);
      return {
        ...blockedPreflight,
        receipt,
        executionAttempted: false,
        stateBefore: canonicalStateSnapshot(state)
      };
    }

    async function executeRefreshIntent(options = {}) {
      const stateBefore = getState();
      const preflight = evaluatePreflight("refreshStatus", stateBefore, options);
      const startedAt = nowIso({now: options.startedAt || options.now});
      const stateAfter = await refreshState({
        ...options,
        observedAt: options.observedAt || options.completedAt || options.now
      });
      const receipt = buildExecutionReceipt(
        preflight,
        stateBefore,
        stateAfter,
        {
          ...options,
          startedAt
        }
      );

      return {
        schemaVersion: "git-tools-execution-result-v1",
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        adapterVersion: VERSION,
        intentId: "refreshStatus",
        status: receipt.status,
        executionAttempted: true,
        executionBinding: receipt.executionBinding,
        preflight,
        receipt,
        stateBefore: canonicalStateSnapshot(stateBefore),
        stateAfter: clonePlain(stateAfter),
        error: clonePlain(receipt.error)
      };
    }

    async function executePushIntent(options = {}) {
      const suppliedPreflight = options.preflight;
      const stateAtRequest = getState();
      const parameters = normalizeIntentParameters(
        "pushCurrentBranch",
        suppliedPreflight?.parameters || options.parameters || options
      );

      const verifiedPreflightReceipt = storedPushPreflightReceipt(
        suppliedPreflight
      );
      if (
        !suppliedPreflight ||
        suppliedPreflight.intentId !== "pushCurrentBranch" ||
        suppliedPreflight.decision !== "confirm" ||
        !verifiedPreflightReceipt
      ) {
        const fallbackPreflight = suppliedPreflight || evaluatePreflight(
          "pushCurrentBranch",
          stateAtRequest,
          {
            ...options,
            parameters
          }
        );
        const receipt = buildPushGuardReceipt(
          fallbackPreflight,
          null,
          stateAtRequest,
          [{
            code: "preflight-required",
            message: "A current stored confirmation-required push preflight receipt is required."
          }],
          {
            ...options,
            parameters
          }
        );
        return {
          schemaVersion: "git-tools-execution-result-v1",
          appId: APP_ID,
          adapterId: ADAPTER_ID,
          adapterVersion: VERSION,
          intentId: "pushCurrentBranch",
          status: "blocked",
          decision: "block",
          executionAttempted: false,
          executionBinding: receipt.executionBinding,
          blockers: clonePlain(receipt.blockers || []),
          warnings: clonePlain(receipt.warnings || []),
          preflight: clonePlain(fallbackPreflight),
          confirmationReceipt: null,
          receipt,
          stateBefore: canonicalStateSnapshot(stateAtRequest),
          stateAfter: canonicalStateSnapshot(stateAtRequest),
          error: null
        };
      }

      const confirmationReceipt = buildConfirmationReceipt(
        suppliedPreflight,
        {
          ...options,
          accepted: options.confirmation?.accepted === true,
          confirmedAt:
            options.confirmation?.confirmedAt ||
            options.confirmedAt ||
            options.now
        }
      );

      if (options.confirmation?.accepted !== true) {
        return {
          schemaVersion: "git-tools-execution-result-v1",
          appId: APP_ID,
          adapterId: ADAPTER_ID,
          adapterVersion: VERSION,
          intentId: "pushCurrentBranch",
          status: "cancelled",
          decision: "decline",
          executionAttempted: false,
          executionBinding: "git-tools-server-panel.serverPushLocal",
          preflight: clonePlain(suppliedPreflight),
          confirmationReceipt,
          receipt: confirmationReceipt,
          stateBefore: canonicalStateSnapshot(stateAtRequest),
          stateAfter: canonicalStateSnapshot(stateAtRequest),
          error: null
        };
      }

      const executionClock = String(
        options.revalidationNow ||
        options.now ||
        new Date().toISOString()
      );
      const expiry = Date.parse(String(suppliedPreflight.expiresAt || ""));
      if (Number.isFinite(expiry) && Date.parse(executionClock) > expiry) {
        const receipt = buildPushGuardReceipt(
          suppliedPreflight,
          confirmationReceipt,
          stateAtRequest,
          [{
            code: "preflight-expired",
            message: "The push preflight receipt expired before execution."
          }],
          options
        );
        return {
          schemaVersion: "git-tools-execution-result-v1",
          appId: APP_ID,
          adapterId: ADAPTER_ID,
          adapterVersion: VERSION,
          intentId: "pushCurrentBranch",
          status: "blocked",
          decision: "block",
          executionAttempted: false,
          executionBinding: receipt.executionBinding,
          blockers: clonePlain(receipt.blockers || []),
          warnings: clonePlain(receipt.warnings || []),
          preflight: clonePlain(suppliedPreflight),
          confirmationReceipt,
          receipt,
          stateBefore: canonicalStateSnapshot(stateAtRequest),
          stateAfter: canonicalStateSnapshot(stateAtRequest),
          error: null
        };
      }

      const revalidatedState = await refreshState({
        ...options,
        repoDir:
          parameters.repoDir ||
          suppliedPreflight.state?.repoDir ||
          stateAtRequest.repoDir,
        observedAt: options.revalidatedAt || executionClock
      });
      const revalidatedPreflight = evaluatePreflight(
        "pushCurrentBranch",
        revalidatedState,
        {
          ...options,
          now: executionClock,
          parameters
        }
      );
      const blockers = clonePlain(revalidatedPreflight.blockers || []);
      const originalContentFingerprint = String(
        suppliedPreflight.stateContentFingerprint ||
        stateContentFingerprint(suppliedPreflight.state || stateAtRequest)
      );
      if (
        originalContentFingerprint !==
        revalidatedPreflight.stateContentFingerprint
      ) {
        blockers.unshift({
          code: "state-changed-after-preflight",
          message: "Repository state changed after push preflight; confirmation cannot be reused.",
          expectedStateContentFingerprint: originalContentFingerprint,
          actualStateContentFingerprint:
            revalidatedPreflight.stateContentFingerprint
        });
      }

      if (revalidatedPreflight.decision !== "confirm" || blockers.length) {
        const receipt = buildPushGuardReceipt(
          suppliedPreflight,
          confirmationReceipt,
          revalidatedState,
          blockers.length
            ? blockers
            : [{
                code: "state-changed-after-preflight",
                message: "Repository state no longer satisfies the confirmed push preflight."
              }],
          options
        );
        return {
          schemaVersion: "git-tools-execution-result-v1",
          appId: APP_ID,
          adapterId: ADAPTER_ID,
          adapterVersion: VERSION,
          intentId: "pushCurrentBranch",
          status: "blocked",
          decision: "block",
          executionAttempted: false,
          executionBinding: receipt.executionBinding,
          blockers: clonePlain(receipt.blockers || []),
          warnings: clonePlain(receipt.warnings || []),
          preflight: clonePlain(suppliedPreflight),
          revalidatedPreflight: clonePlain(revalidatedPreflight),
          confirmationReceipt,
          receipt,
          stateBefore: canonicalStateSnapshot(stateAtRequest),
          stateAfter: canonicalStateSnapshot(revalidatedState),
          error: null
        };
      }

      if (typeof options.executeBinding !== "function") {
        const receipt = buildPushGuardReceipt(
          suppliedPreflight,
          confirmationReceipt,
          revalidatedState,
          [{
            code: "execution-binding-unavailable",
            message: "The governed Local Gitea push execution binding is unavailable."
          }],
          options
        );
        return {
          schemaVersion: "git-tools-execution-result-v1",
          appId: APP_ID,
          adapterId: ADAPTER_ID,
          adapterVersion: VERSION,
          intentId: "pushCurrentBranch",
          status: "blocked",
          decision: "block",
          executionAttempted: false,
          executionBinding: receipt.executionBinding,
          blockers: clonePlain(receipt.blockers || []),
          warnings: clonePlain(receipt.warnings || []),
          preflight: clonePlain(suppliedPreflight),
          revalidatedPreflight: clonePlain(revalidatedPreflight),
          confirmationReceipt,
          receipt,
          stateBefore: canonicalStateSnapshot(stateAtRequest),
          stateAfter: canonicalStateSnapshot(revalidatedState),
          error: null
        };
      }

      const startedAt = nowIso({now: options.startedAt || executionClock});
      let backendResult = null;
      let backendError = null;
      try {
        backendResult = await options.executeBinding({
          schemaVersion: PUSH_EXECUTION_SCHEMA_VERSION,
          appId: APP_ID,
          adapterId: ADAPTER_ID,
          intentId: "pushCurrentBranch",
          parameters: clonePlain(parameters),
          preflightReceiptId: suppliedPreflight.receipt.receiptId,
          confirmationReceiptId: confirmationReceipt.receiptId,
          state: canonicalStateSnapshot(revalidatedState),
          stateFingerprint: revalidatedPreflight.stateFingerprint,
          stateContentFingerprint:
            revalidatedPreflight.stateContentFingerprint
        });
      } catch (error) {
        backendError = error;
      }

      const postState = await refreshState({
        ...options,
        repoDir:
          parameters.repoDir ||
          suppliedPreflight.state?.repoDir ||
          revalidatedState.repoDir,
        observedAt:
          options.postObservedAt ||
          options.completedAt ||
          new Date().toISOString()
      });
      const receipt = buildPushExecutionReceipt(
        suppliedPreflight,
        confirmationReceipt,
        revalidatedState,
        postState,
        backendResult,
        backendError,
        {
          ...options,
          startedAt,
          parameters
        }
      );

      return {
        schemaVersion: "git-tools-execution-result-v1",
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        adapterVersion: VERSION,
        intentId: "pushCurrentBranch",
        status: receipt.status,
        decision: receipt.decision,
        executionAttempted: true,
        executionBinding: receipt.executionBinding,
        preflight: clonePlain(suppliedPreflight),
        revalidatedPreflight: clonePlain(revalidatedPreflight),
        confirmationReceipt,
        receipt,
        backendResult: clonePlain(backendResult),
        stateBefore: canonicalStateSnapshot(revalidatedState),
        stateAfter: clonePlain(postState),
        error: clonePlain(receipt.error)
      };
    }

    async function executeIntent(intentOrId, options = {}) {
      const intentId = normalizeIntentId(intentOrId);
      if (intentId === "refreshStatus") {
        return executeRefreshIntent(options);
      }
      if (intentId === "pushCurrentBranch") {
        return executePushIntent(options);
      }
      return blockedExecutionResult(intentOrId, getState(), options);
    }

    function mapEvidence(state = getState()) {
      const safeState = state && typeof state === "object" ? state : getState();
      const evidence = [];
      if (safeState.observedAt) {
        evidence.push(
          {
            evidenceId: "git-tools-repository-state",
            kind: "state-snapshot",
            source: safeState.source,
            observedAt: safeState.observedAt,
            authoritative: safeState.source === "git-tools-status-api",
            receiptBacked: false,
            claims: {
              repoDir: safeState.repoDir,
              gitRoot: safeState.gitRoot,
              branch: safeState.branch,
              dirty: safeState.dirty,
              ahead: safeState.ahead,
              behind: safeState.behind,
              changedCount: safeState.changedCount,
              untrackedCount: safeState.untrackedCount
            }
          },
          {
            evidenceId: "git-tools-remote-state",
            kind: "state-snapshot",
            source: safeState.source,
            observedAt: safeState.observedAt,
            authoritative: safeState.source === "git-tools-status-api",
            receiptBacked: false,
            claims: {
              remoteCount: (safeState.remotes || []).length,
              remotes: clonePlain(safeState.remotes || [])
            }
          }
        );
      }

      listReceipts().forEach((receipt) => {
        evidence.push({
          evidenceId: receipt.receiptId,
          kind: receipt.kind || "semantic-receipt",
          source: ADAPTER_ID,
          observedAt: receipt.createdAt,
          authoritative: true,
          receiptBacked: true,
          receiptId: receipt.receiptId,
          claims: {
            intentId: receipt.intentId,
            decision: receipt.decision,
            status: receipt.status,
            stateFingerprint: receipt.stateFingerprint,
            blockerCodes: (receipt.blockers || []).map((blocker) => blocker.code),
            warningCodes: (receipt.warnings || []).map((warning) => warning.code),
            executionAttempted: receipt.executionAttempted === true,
            executionBinding: receipt.executionBinding || "not-registered",
            resultStatus: receipt.result?.status || "",
            preflightReceiptId: receipt.preflightReceiptId || "",
            confirmationReceiptId: receipt.confirmationReceiptId || "",
            parentReceiptId: receipt.parentReceiptId || "",
            parameters: clonePlain(receipt.parameters || {}),
            recoveryClassified: receipt.recoveryClassified === true,
            failureClass: receipt.failure?.failureClass || "",
            recoveryOptionIds: (receipt.recovery?.options || []).map((option) => option.intentId),
            prohibitedActions: clonePlain(receipt.recovery?.prohibitedActions || [])
          }
        });
      });

      return evidence;
    }

    function resetState(options = {}) {
      currentState = initialState(selectedRepoDir(options));
      return getState();
    }

    const adapter = Object.freeze({
      id: ADAPTER_ID,
      appId: APP_ID,
      version: VERSION,
      kind: KIND,
      getState,
      refreshState,
      listObjects,
      listIntents,
      getIntentCoverage,
      preflightIntent,
      executeIntent,
      buildReceipt,
      buildConfirmationReceipt,
      classifyFailure,
      buildRecoveryOptions,
      getRecoveryCoverage,
      mapEvidence
    });

    let registrationReadiness = null;
    if (global.McelDomainAdapterRegistry?.registerAdapter) {
      registrationReadiness = global.McelDomainAdapterRegistry.registerAdapter(adapter);
    }

    loadReceipts();

    global.GitToolsSemanticAdapter = Object.freeze({
      ...adapter,
      STATE_SCHEMA_VERSION,
      PREFLIGHT_SCHEMA_VERSION,
      RECEIPT_SCHEMA_VERSION,
      CONFIRMATION_SCHEMA_VERSION,
      PUSH_EXECUTION_SCHEMA_VERSION,
      RECOVERY_CLASSIFICATION_SCHEMA_VERSION,
      RECOVERY_PLAN_SCHEMA_VERSION,
      RECOVERY_COVERAGE_VERSION,
      INTENT_COVERAGE_SCHEMA_VERSION,
      INTENT_DEFINITIONS,
      RECOVERY_FAILURE_DEFINITIONS,
      DEFAULT_MAX_STATE_AGE_MS,
      normalizeStatus,
      evaluatePreflight,
      stateContentFingerprint,
      buildExecutionReceipt,
      buildPushExecutionReceipt,
      listReceipts,
      clearReceipts,
      resetState,
      registrationReadiness: clonePlain(registrationReadiness)
    });

    if (typeof module !== "undefined" && module.exports) {
      module.exports = global.GitToolsSemanticAdapter;
    }
  })(typeof window !== "undefined" ? window : globalThis);
})();
