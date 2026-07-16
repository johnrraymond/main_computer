(() => {
  (function createGitToolsSemanticPanel(global) {
    "use strict";

    if (!global) return;

    const VERSION = "git-tools-semantic-panel-v8";
    const ROOT_SELECTOR = "#git-tools-app";
    const PANEL_SELECTOR = "#git-semantic-runtime-panel";
    const PUSH_INTENT_ID = "pushCurrentBranch";

    function clonePlain(value) {
      if (value == null || typeof value !== "object") return value;
      if (Array.isArray(value)) return value.map(clonePlain);
      return Object.fromEntries(
        Object.entries(value)
          .filter(([, entry]) => typeof entry !== "function")
          .map(([key, entry]) => [key, clonePlain(entry)])
      );
    }

    function stateAgeMs(state, now = Date.now()) {
      const observedAt = Date.parse(String(state?.observedAt || ""));
      if (!Number.isFinite(observedAt)) return null;
      return Math.max(0, Number(now) - observedAt);
    }

    function freshnessLabel(state, now = Date.now()) {
      if (!state || state.phase === "uninitialized" || !state.observedAt) return "Not observed";
      if (state.phase === "loading") return "Refreshing";
      if (state.phase === "error") return "Unavailable";
      const age = stateAgeMs(state, now);
      if (age === null) return "Timestamp invalid";
      if (age > 120000) return "Stale";
      return "Fresh";
    }

    function runtimeStatus(readiness) {
      if (readiness?.fullApplicationSemanticReady === true) {
        return "Full semantic coverage";
      }
      if (readiness?.runtimeCoreReady === true) {
        return "Core ready · partial coverage";
      }
      if (
        readiness?.adapterExecutable === true &&
        readiness?.recoveryClassifierPresent === true &&
        readiness?.recoveryReady !== true
      ) {
        return "Safe-read + recovery guidance";
      }
      if (
        readiness?.adapterExecutable === true &&
        readiness?.recoveryReady !== true
      ) {
        return "Safe-read execution";
      }
      if (
        readiness?.actionPlannerReady === true &&
        readiness?.adapterExecutable === false
      ) {
        return "Preflight-only";
      }
      if (readiness?.registryAdapterPresent === true) return "Observation-only";
      return "Unavailable";
    }

    function executionScope(readiness) {
      const count = Number(readiness?.executableIntentCount || 0);
      if (count === 1) return "Refresh only";
      if (count >= 2) return "Refresh + governed push";
      if (readiness?.adapterExecutable === true) return "Refresh only";
      return "No";
    }

    function intentStatusLabel(status) {
      const labels = {
        executable: "Executable",
        "preflight-only": "Preflight only",
        "declared-only": "Declared only",
        prohibited: "Prohibited"
      };
      return labels[String(status || "")] || "Unclassified";
    }

    function semanticScopeLabel(scope) {
      return String(scope || "unclassified")
        .split("-")
        .filter(Boolean)
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(" ");
    }

    function intentCoverageModel(readiness, adapter) {
      const coverage =
        readiness?.intentCoverage ||
        adapter?.getIntentCoverage?.() ||
        null;
      const entries = Array.isArray(coverage?.entries)
        ? coverage.entries.map((entry) => ({
          intentId: String(entry.intentId || "unknown"),
          label: String(entry.label || entry.intentId || "Unknown intent"),
          risk: String(entry.risk || "unclassified"),
          status: String(entry.status || "unclassified"),
          statusLabel: intentStatusLabel(entry.status),
          executionBinding: String(entry.executionBinding || "not-registered")
        }))
        : [];
      const executable = Number(readiness?.executableIntentCount ?? coverage?.counts?.executable ?? 0);
      const preflightOnly = Number(readiness?.preflightOnlyIntentCount ?? coverage?.counts?.preflightOnly ?? 0);
      const declaredOnly = Number(readiness?.declaredOnlyIntentCount ?? coverage?.counts?.declaredOnly ?? 0);
      const prohibited = Number(readiness?.prohibitedIntentCount ?? coverage?.counts?.prohibited ?? 0);
      const total = Number(readiness?.totalIntentCount ?? coverage?.counts?.total ?? entries.length);
      const runtimeCoreReady = readiness?.runtimeCoreReady === true;
      const fullApplicationSemanticReady =
        readiness?.fullApplicationSemanticReady === true;
      const semanticRuntimeScope = String(
        readiness?.semanticRuntimeScope ||
        coverage?.semanticRuntimeScope ||
        "unclassified"
      );
      return {
        runtimeCoreReady,
        runtimeCoreLabel: runtimeCoreReady ? "Ready" : "Not ready",
        fullApplicationSemanticReady,
        applicationCoverageLabel: fullApplicationSemanticReady
          ? "Complete"
          : "Partial",
        semanticRuntimeScope,
        semanticRuntimeScopeLabel: semanticScopeLabel(semanticRuntimeScope),
        executable,
        preflightOnly,
        declaredOnly,
        prohibited,
        blocked: Number(
          readiness?.blockedIntentCount ??
          coverage?.counts?.blocked ??
          (declaredOnly + prohibited)
        ),
        total,
        summary: `${executable} executable · ${preflightOnly} preflight only · ${declaredOnly} declared only · ${prohibited} prohibited`,
        entries
      };
    }

    function intentCoverageText(coverageModel) {
      const entries = coverageModel?.entries || [];
      if (!entries.length) return "No intent-level coverage proof is available.";
      return entries
        .map((entry) => [
          entry.intentId.padEnd(22),
          entry.statusLabel.padEnd(15),
          entry.executionBinding
        ].join(" | "))
        .join("\n");
    }

    function latestReceipt(receipts, intentId = "") {
      const safeReceipts = Array.isArray(receipts) ? receipts : [];
      const filtered = intentId
        ? safeReceipts.filter((receipt) => receipt?.intentId === intentId)
        : safeReceipts;
      return filtered.length ? filtered[filtered.length - 1] : null;
    }

    function decisionLabel(receipt) {
      if (!receipt) return "Not evaluated";
      if (receipt.status === "confirmation-required") return "Confirmation required";
      if (receipt.status === "allowed") return "Allowed for planning";
      if (receipt.status === "blocked") {
        const firstBlocker = receipt.blockers?.[0];
        return firstBlocker?.message
          ? `Blocked: ${firstBlocker.message}`
          : "Blocked";
      }
      if (receipt.status === "succeeded") return "Push succeeded";
      if (receipt.status === "failed") return "Push failed";
      if (receipt.status === "cancelled") return "Confirmation declined";
      if (receipt.status === "confirmed") return "Confirmed";
      return String(receipt.status || receipt.decision || "Unknown");
    }

    function recoveryGuidance(receipt, adapter, state) {
      const coverage = adapter?.getRecoveryCoverage?.() || null;
      if (!receipt) {
        return {
          available: false,
          failureClass: "None",
          severity: "None",
          retrySafe: "Not applicable",
          refreshRequired: "Not applicable",
          nextStep: "Run a governed action or preflight to produce recovery evidence.",
          prohibitedActions: "None",
          sourceReceipt: "None",
          optionLabels: [],
          coverageReady: coverage?.coverageReady === true,
          coverageStatus: coverage?.coverageReady === true
            ? "Verified coverage"
            : "Guidance available; coverage verification pending",
          failure: null,
          recovery: null
        };
      }

      let failure = receipt.failure || null;
      let recovery = receipt.recovery || null;
      try {
        if (!failure && adapter?.classifyFailure) {
          failure = adapter.classifyFailure(receipt, state);
        }
        if (
          failure &&
          failure.failureClass !== "none" &&
          !recovery &&
          adapter?.buildRecoveryOptions
        ) {
          recovery = adapter.buildRecoveryOptions(failure, state);
        }
      } catch (_error) {
        failure = failure || {
          failureClass: "unknown-failure",
          severity: "blocking",
          retrySafe: false,
          refreshRequired: true,
          recommendedNextStep: "Inspect the source receipt and keep mutations disabled."
        };
      }

      if (!failure || failure.failureClass === "none") {
        return {
          available: false,
          failureClass: "None",
          severity: "None",
          retrySafe: "Not required",
          refreshRequired: "No",
          nextStep: "No recovery action is required for the selected receipt.",
          prohibitedActions: "None",
          sourceReceipt: receipt.receiptId || "Unknown",
          optionLabels: [],
          coverageReady: coverage?.coverageReady === true,
          coverageStatus: coverage?.coverageReady === true
            ? "Verified coverage"
            : "Guidance available; coverage verification pending",
          failure: clonePlain(failure),
          recovery: null
        };
      }

      const optionLabels = (recovery?.options || []).map((option) => {
        const suffix = option.executable === true ? " (executable)" : "";
        return `${option.label || option.intentId || "Recovery option"}${suffix}`;
      });
      return {
        available: true,
        failureClass: String(failure.failureClass || "unknown-failure"),
        severity: String(failure.severity || "blocking"),
        retrySafe: failure.retrySafe === true ? "Yes" : "No",
        refreshRequired: failure.refreshRequired === true ? "Yes" : "No",
        nextStep: String(
          recovery?.recommendedNextStep ||
          failure.recommendedNextStep ||
          "Inspect the source receipt."
        ),
        prohibitedActions: (recovery?.prohibitedActions || failure.prohibitedActions || []).join(", ") || "None",
        sourceReceipt: String(recovery?.sourceReceiptId || failure.sourceReceiptId || receipt.receiptId || "Unknown"),
        optionLabels,
        coverageReady: coverage?.coverageReady === true,
        coverageStatus: coverage?.coverageReady === true
          ? "Verified coverage"
          : "Guidance available; coverage verification pending",
        failure: clonePlain(failure),
        recovery: clonePlain(recovery)
      };
    }

    function repositoryLabel(state) {
      if (!state) return "No repository observed";
      return String(state.gitRoot || state.repoDir || "No repository observed");
    }

    function divergenceLabel(state) {
      if (!state || state.ahead === null || state.ahead === undefined ||
          state.behind === null || state.behind === undefined) {
        return "Unknown";
      }
      return `${state.ahead} / ${state.behind}`;
    }

    function buildViewModel(options = {}) {
      const adapter = options.adapter || global.GitToolsSemanticAdapter || null;
      const registry = options.registry || global.McelDomainAdapterRegistry || null;
      const now = options.now ?? Date.now();
      const state = adapter?.getState?.() || null;
      const receipts = adapter?.listReceipts?.() || [];
      const readiness = registry?.evaluateAdapterReadiness?.("git-tools") || null;
      const latestPushReceipt = latestReceipt(receipts, PUSH_INTENT_ID);
      const latestAnyReceipt = latestReceipt(receipts);
      const recovery = recoveryGuidance(latestAnyReceipt, adapter, state);
      const intentCoverage = intentCoverageModel(readiness, adapter);

      return {
        version: VERSION,
        available: Boolean(adapter && registry),
        runtimeStatus: runtimeStatus(readiness),
        freshness: freshnessLabel(state, now),
        repository: repositoryLabel(state),
        branch: String(state?.branch || "Unknown"),
        divergence: divergenceLabel(state),
        pushDecision: decisionLabel(latestPushReceipt),
        receiptCount: receipts.length,
        executionEnabled: executionScope(readiness),
        intentCoverage,
        state: clonePlain(state),
        readiness: clonePlain(readiness),
        latestPushReceipt: clonePlain(latestPushReceipt),
        latestReceipt: clonePlain(latestAnyReceipt),
        recovery
      };
    }

    function receiptText(receipt) {
      if (!receipt) return "No receipt selected.";
      const blockers = (receipt.blockers || [])
        .map((item) => `- ${item.code}: ${item.message}`)
        .join("\n");
      const warnings = (receipt.warnings || [])
        .map((item) => `- ${item.code}: ${item.message}`)
        .join("\n");
      const executionAttempted = receipt.executionAttempted === true;
      const resultStatus = receipt.result?.status || receipt.status || "";
      const errorMessage = receipt.error?.message || "";
      const failure = receipt.failure || null;
      const recovery = receipt.recovery || null;
      const recoveryOptions = (recovery?.options || [])
        .map((option) => `- ${option.intentId}: ${option.label}${option.executable === true ? " (executable)" : ""}`)
        .join("\n");
      const prohibitedActions = (recovery?.prohibitedActions || []).join(", ");
      return [
        `Receipt: ${receipt.receiptId || "unknown"}`,
        `Kind: ${receipt.kind || "semantic-receipt"}`,
        `Intent: ${receipt.intentId || "unknown"}`,
        `Decision: ${receipt.decision || receipt.status || "unknown"}`,
        `Created: ${receipt.createdAt || "unknown"}`,
        `State fingerprint: ${receipt.stateFingerprint || "unknown"}`,
        `State content fingerprint: ${receipt.stateContentFingerprint || "unknown"}`,
        `Preflight receipt: ${receipt.preflightReceiptId || "none"}`,
        `Confirmation receipt: ${receipt.confirmationReceiptId || "none"}`,
        `Parent receipt: ${receipt.parentReceiptId || "none"}`,
        receipt.parameters && Object.keys(receipt.parameters).length
          ? `Parameters: ${JSON.stringify(receipt.parameters)}`
          : "Parameters: none",
        `Execution attempted: ${executionAttempted ? "yes" : "no"}`,
        `Execution binding: ${receipt.executionBinding || "not-registered"}`,
        resultStatus ? `Result: ${resultStatus}` : "Result: not available",
        errorMessage ? `Error: ${errorMessage}` : "Error: none",
        blockers ? `Blockers:\n${blockers}` : "Blockers: none",
        warnings ? `Warnings:\n${warnings}` : "Warnings: none",
        `Recovery classified: ${receipt.recoveryClassified === true ? "yes" : "no"}`,
        `Failure class: ${failure?.failureClass || "none"}`,
        `Severity: ${failure?.severity || "none"}`,
        `Retry safe: ${failure ? (failure.retrySafe === true ? "yes" : "no") : "not applicable"}`,
        `Refresh required: ${failure ? (failure.refreshRequired === true ? "yes" : "no") : "no"}`,
        recovery?.recommendedNextStep
          ? `Recommended next step: ${recovery.recommendedNextStep}`
          : "Recommended next step: none",
        prohibitedActions
          ? `Prohibited actions: ${prohibitedActions}`
          : "Prohibited actions: none",
        recoveryOptions
          ? `Recovery options:\n${recoveryOptions}`
          : "Recovery options: none"
      ].join("\n");
    }

    function elementMap(root) {
      return {
        panel: root?.querySelector?.(PANEL_SELECTOR) || null,
        status: root?.querySelector?.("#git-semantic-runtime-status") || null,
        freshness: root?.querySelector?.("#git-semantic-state-freshness") || null,
        repository: root?.querySelector?.("#git-semantic-repository") || null,
        branch: root?.querySelector?.("#git-semantic-branch") || null,
        divergence: root?.querySelector?.("#git-semantic-divergence") || null,
        decision: root?.querySelector?.("#git-semantic-push-decision") || null,
        receiptCount: root?.querySelector?.("#git-semantic-receipt-count") || null,
        execution: root?.querySelector?.("#git-semantic-execution-enabled") || null,
        runtimeCore: root?.querySelector?.("#git-semantic-runtime-core-ready") || null,
        applicationCoverage: root?.querySelector?.("#git-semantic-application-coverage") || null,
        runtimeScope: root?.querySelector?.("#git-semantic-runtime-scope") || null,
        intentCoverageSummary: root?.querySelector?.("#git-semantic-intent-coverage-summary") || null,
        intentCoverageMatrix: root?.querySelector?.("#git-semantic-intent-coverage-matrix") || null,
        refresh: root?.querySelector?.("#git-semantic-refresh-state") || null,
        preflight: root?.querySelector?.("#git-semantic-run-push-preflight") || null,
        viewReceipt: root?.querySelector?.("#git-semantic-view-latest-receipt") || null,
        clearReceipts: root?.querySelector?.("#git-semantic-clear-receipts") || null,
        recoveryClass: root?.querySelector?.("#git-semantic-recovery-class") || null,
        recoverySeverity: root?.querySelector?.("#git-semantic-recovery-severity") || null,
        recoveryRetrySafe: root?.querySelector?.("#git-semantic-recovery-retry-safe") || null,
        recoveryRefreshRequired: root?.querySelector?.("#git-semantic-recovery-refresh-required") || null,
        recoveryNextStep: root?.querySelector?.("#git-semantic-recovery-next-step") || null,
        recoveryProhibited: root?.querySelector?.("#git-semantic-recovery-prohibited") || null,
        recoverySourceReceipt: root?.querySelector?.("#git-semantic-recovery-source-receipt") || null,
        recoveryCoverage: root?.querySelector?.("#git-semantic-recovery-coverage") || null,
        recoveryOptions: root?.querySelector?.("#git-semantic-recovery-options") || null,
        sourcePush: root?.querySelector?.("#git-server-push-local") || null,
        message: root?.querySelector?.("#git-semantic-runtime-message") || null,
        output: root?.querySelector?.("#git-semantic-receipt-output") || null
      };
    }

    function setText(node, value) {
      if (node) node.textContent = String(value ?? "");
    }

    function setBusy(elements, busy) {
      [elements.refresh, elements.preflight, elements.clearReceipts, elements.sourcePush].forEach((button) => {
        if (button) button.disabled = Boolean(busy);
      });
      if (elements.panel) elements.panel.setAttribute("aria-busy", busy ? "true" : "false");
    }

    function ownSemanticAction(event) {
      event?.preventDefault?.();
      event?.stopPropagation?.();
    }

    function preserveSemanticSupportView(root) {
      const controller =
        root?.__mcelGitToolsLayoutController ||
        global.MainComputerGitToolsLayoutController ||
        null;
      if (!controller?.selectSupport) return false;
      const resolved = controller.resolved || null;
      if (
        resolved?.supportView === "semantics" &&
        resolved?.activeSurface === "support" &&
        resolved?.supportOpen !== false
      ) {
        return true;
      }
      controller.selectSupport("semantics");
      return true;
    }

    function configureSourcePushControl(root) {
      const sourcePush = elementMap(root).sourcePush;
      if (!sourcePush) return null;
      const backendUnavailable =
        sourcePush.dataset.mcelBackendUnavailable === "true";
      const pushable =
        sourcePush.dataset.mcelPushable !== "false";
      sourcePush.dataset.mcelSemanticGate = "governed-execution";
      sourcePush.disabled = backendUnavailable || !pushable;
      sourcePush.setAttribute?.(
        "aria-disabled",
        sourcePush.disabled ? "true" : "false"
      );
      sourcePush.setAttribute?.("aria-describedby", "git-semantic-runtime-message");
      sourcePush.title = backendUnavailable
        ? "Docker is unavailable to the Main Computer backend; Local Gitea push is disabled."
        : (
          !pushable
            ? "A publishable Git HEAD is required."
            : "Runs fresh-state MCEL preflight, explicit confirmation, execution-time revalidation, and governed Local Gitea push."
        );
      return sourcePush;
    }

    function render(root, options = {}) {
      const elements = elementMap(root);
      if (!elements.panel) return null;
      configureSourcePushControl(root);
      const model = buildViewModel(options);

      setText(elements.status, model.runtimeStatus);
      setText(elements.freshness, model.freshness);
      setText(elements.repository, model.repository);
      setText(elements.branch, model.branch);
      setText(elements.divergence, model.divergence);
      setText(elements.decision, model.pushDecision);
      setText(elements.receiptCount, `${model.receiptCount} stored`);
      setText(elements.execution, model.executionEnabled);
      setText(elements.runtimeCore, model.intentCoverage.runtimeCoreLabel);
      setText(elements.applicationCoverage, model.intentCoverage.applicationCoverageLabel);
      setText(elements.runtimeScope, model.intentCoverage.semanticRuntimeScopeLabel);
      setText(elements.intentCoverageSummary, model.intentCoverage.summary);
      setText(elements.intentCoverageMatrix, intentCoverageText(model.intentCoverage));
      setText(elements.recoveryClass, model.recovery.failureClass);
      setText(elements.recoverySeverity, model.recovery.severity);
      setText(elements.recoveryRetrySafe, model.recovery.retrySafe);
      setText(elements.recoveryRefreshRequired, model.recovery.refreshRequired);
      setText(elements.recoveryNextStep, model.recovery.nextStep);
      setText(elements.recoveryProhibited, model.recovery.prohibitedActions);
      setText(elements.recoverySourceReceipt, model.recovery.sourceReceipt);
      setText(elements.recoveryCoverage, model.recovery.coverageStatus);
      setText(
        elements.recoveryOptions,
        model.recovery.optionLabels.length
          ? model.recovery.optionLabels.map((label) => `• ${label}`).join("\n")
          : "No recovery options are required for the selected receipt."
      );
      if (options.receipt !== undefined) {
        setText(elements.output, receiptText(options.receipt));
      } else if (!elements.output?.textContent?.trim()) {
        setText(elements.output, receiptText(model.latestReceipt));
      }

      elements.panel.dataset.semanticRuntimeStatus = model.runtimeStatus
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-");
      elements.panel.dataset.semanticStateFreshness = model.freshness
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-");
      elements.panel.dataset.semanticRuntimeScope =
        model.intentCoverage.semanticRuntimeScope;
      elements.panel.dataset.fullApplicationSemanticReady =
        model.intentCoverage.fullApplicationSemanticReady ? "true" : "false";
      return model;
    }

    function mount(options = {}) {
      const documentObject = options.document || global.document;
      const root = options.root || documentObject?.querySelector?.(ROOT_SELECTOR);
      if (!root) return null;

      const elements = elementMap(root);
      if (!elements.panel) return null;
      if (elements.panel.dataset.semanticPanelBound === VERSION) {
        return root.__mcelGitToolsSemanticPanel || null;
      }

      const adapter = options.adapter || global.GitToolsSemanticAdapter;
      const registry = options.registry || global.McelDomainAdapterRegistry;
      let pushGateInFlight = null;
      elements.panel.dataset.semanticPanelBound = VERSION;
      configureSourcePushControl(root);

      function updateMessage(message) {
        setText(elements.message, message);
      }

      function currentRoot() {
        return documentObject?.querySelector?.(ROOT_SELECTOR) || root;
      }

      function preserveSelection() {
        return preserveSemanticSupportView(currentRoot());
      }

      async function executeRefreshStatus() {
        if (!adapter?.executeIntent) {
          throw new Error("Governed refresh execution is unavailable.");
        }
        return adapter.executeIntent("refreshStatus");
      }

      async function refreshState(event) {
        ownSemanticAction(event);
        preserveSelection();
        if (!adapter?.executeIntent) {
          updateMessage("Governed refresh execution is unavailable.");
          preserveSelection();
          return null;
        }
        setBusy(elements, true);
        updateMessage("Executing governed repository status refresh...");
        try {
          const result = await executeRefreshStatus();
          const state = result?.stateAfter || adapter.getState?.() || null;
          const model = render(root, {
            adapter,
            registry,
            receipt: result?.receipt
          });
          if (result?.status === "failed" || state?.phase === "error") {
            updateMessage(
              result?.error?.message ||
              state?.error?.message ||
              "Repository state could not be refreshed."
            );
          } else {
            updateMessage(
              `Governed refresh succeeded for ${model?.branch || "unknown branch"}. Receipt ${result?.receipt?.receiptId || "not available"}. No Git mutation was executed.`
            );
          }
          return result;
        } catch (error) {
          updateMessage(error?.message || String(error));
          return null;
        } finally {
          setBusy(elements, false);
          preserveSelection();
        }
      }

      function runPushPreflight(event) {
        ownSemanticAction(event);
        preserveSelection();
        if (!adapter?.preflightIntent) {
          updateMessage("Push preflight is unavailable.");
          preserveSelection();
          return null;
        }
        const result = adapter.preflightIntent(PUSH_INTENT_ID, adapter.getState());
        render(root, {adapter, registry, receipt: result.receipt});
        updateMessage(
          `Push preflight: ${result.decision}. Receipt ${result.receipt?.receiptId || "not available"}. No Git mutation was executed.`
        );
        preserveSelection();
        return result;
      }

      async function requestGovernedPush(event, requestOptions = {}) {
        ownSemanticAction(event);
        preserveSelection();
        if (pushGateInFlight) return pushGateInFlight;

        pushGateInFlight = (async () => {
          setBusy(elements, true);
          updateMessage("Refreshing repository state before governed Local Gitea push preflight...");
          try {
            if (!adapter?.preflightIntent || !adapter?.executeIntent) {
              updateMessage("Push blocked: the MCEL governed execution adapter is unavailable. No Git mutation was executed.");
              return {
                decision: "block",
                status: "blocked",
                executionAttempted: false,
                blockers: [{
                  code: "semantic-preflight-unavailable",
                  message: "The MCEL governed execution adapter is unavailable."
                }]
              };
            }

            const parameters = clonePlain(
              requestOptions.parameters ||
              requestOptions.payload ||
              {}
            );
            const refreshResult = await executeRefreshStatus();
            const state =
              refreshResult?.stateAfter ||
              adapter.getState?.() ||
              null;
            preserveSelection();

            const preflight = adapter.preflightIntent(
              PUSH_INTENT_ID,
              state || adapter.getState?.(),
              {parameters}
            );
            render(root, {adapter, registry, receipt: preflight.receipt});
            const preflightReceiptId =
              preflight.receipt?.receiptId ||
              "not available";

            if (preflight.decision !== "confirm") {
              updateMessage(
                `Push blocked by MCEL preflight. Receipt ${preflightReceiptId}. No Git mutation was executed.`
              );
              return {
                ...preflight,
                sourceControlId: String(
                  requestOptions.sourceControl?.id ||
                  requestOptions.sourceControlId ||
                  "git-server-push-local"
                ),
                executionAttempted: false
              };
            }

            const remote =
              parameters.remote ||
              preflight.parameters?.remote ||
              "local-gitea";
            const branch = state?.branch || "unknown";
            const repository =
              state?.gitRoot ||
              state?.repoDir ||
              "unknown repository";
            const ahead =
              state?.ahead === null || state?.ahead === undefined
                ? "unknown"
                : state.ahead;
            const dirtyWarning = state?.dirty === true
              ? "\n\nWarning: uncommitted working-tree changes are not included in this push."
              : "";
            const prompt = [
              "Push the current committed HEAD to Local Gitea?",
              "",
              `Repository: ${repository}`,
              `Branch: ${branch}`,
              `Remote: ${remote}`,
              `Commits ahead: ${ahead}`,
              "",
              `Preflight receipt: ${preflightReceiptId}`,
              dirtyWarning
            ].join("\n");
            const confirmAction =
              requestOptions.confirm ||
              global.confirm;
            const accepted =
              typeof confirmAction === "function"
                ? Boolean(confirmAction(prompt))
                : false;

            updateMessage(
              accepted
                ? "Push confirmed. Revalidating repository state immediately before execution..."
                : "Push confirmation declined. No Git mutation was executed."
            );

            const execution = await adapter.executeIntent(
              PUSH_INTENT_ID,
              {
                preflight,
                confirmation: {
                  accepted,
                  confirmedAt: new Date().toISOString(),
                  prompt
                },
                parameters,
                executeBinding: requestOptions.executePush,
                api: requestOptions.statusApi
              }
            );
            render(root, {
              adapter,
              registry,
              receipt: execution.receipt
            });

            const receiptId =
              execution.receipt?.receiptId ||
              "not available";
            if (execution.status === "succeeded") {
              updateMessage(
                `Governed Local Gitea push succeeded. Receipt ${receiptId}. Post-push state refresh completed.`
              );
            } else if (execution.status === "failed") {
              updateMessage(
                `Governed Local Gitea push failed. Receipt ${receiptId}. Inspect recovery guidance before retrying.`
              );
            } else if (execution.status === "cancelled") {
              updateMessage(
                `Push confirmation declined. Receipt ${receiptId}. No Git mutation was executed.`
              );
            } else {
              updateMessage(
                `Push blocked during execution-time revalidation. Receipt ${receiptId}. No Git mutation was executed.`
              );
            }

            return {
              ...execution,
              sourceControlId: String(
                requestOptions.sourceControl?.id ||
                requestOptions.sourceControlId ||
                "git-server-push-local"
              )
            };
          } catch (error) {
            updateMessage(
              `Push blocked because governed execution failed before a successful backend result: ${error?.message || String(error)}.`
            );
            return {
              decision: "block",
              status: "blocked",
              executionAttempted: false,
              blockers: [{
                code: "semantic-preflight-failed",
                message: String(error?.message || error || "Governed push failed.")
              }]
            };
          } finally {
            setBusy(elements, false);
            preserveSelection();
            configureSourcePushControl(currentRoot());
            pushGateInFlight = null;
          }
        })();

        return pushGateInFlight;
      }

      function viewLatestReceipt(event) {
        ownSemanticAction(event);
        preserveSelection();
        const receipt = latestReceipt(adapter?.listReceipts?.() || []);
        setText(elements.output, receiptText(receipt));
        updateMessage(receipt ? `Showing ${receipt.receiptId}.` : "No stored receipt is available.");
        preserveSelection();
        return receipt;
      }

      function clearReceipts(event) {
        ownSemanticAction(event);
        preserveSelection();
        adapter?.clearReceipts?.();
        render(root, {adapter, registry, receipt: null});
        updateMessage("Semantic receipts cleared. No Git mutation was executed.");
        preserveSelection();
        return [];
      }

      elements.refresh?.addEventListener?.("click", refreshState);
      elements.preflight?.addEventListener?.("click", runPushPreflight);
      elements.viewReceipt?.addEventListener?.("click", viewLatestReceipt);
      elements.clearReceipts?.addEventListener?.("click", clearReceipts);

      const controller = Object.freeze({
        version: VERSION,
        render: () => render(root, {adapter, registry}),
        refreshState,
        runPushPreflight,
        requestGovernedPush,
        viewLatestReceipt,
        clearReceipts
      });
      root.__mcelGitToolsSemanticPanel = controller;
      render(root, {adapter, registry});
      return controller;
    }

    async function interceptPushControl(event, options = {}) {
      ownSemanticAction(event);
      const documentObject = options.document || global.document;
      const root = options.root || documentObject?.querySelector?.(ROOT_SELECTOR);
      const controller = root?.__mcelGitToolsSemanticPanel || mount({
        document: documentObject,
        root,
        adapter: options.adapter,
        registry: options.registry
      });
      if (!controller?.requestGovernedPush) {
        return {
          decision: "block",
          status: "blocked",
          executionAttempted: false,
          blockers: [{
            code: "semantic-panel-unavailable",
            message: "The MCEL semantic panel could not be mounted."
          }]
        };
      }
      return controller.requestGovernedPush(event, options);
    }

    const api = Object.freeze({
      version: VERSION,
      buildViewModel,
      freshnessLabel,
      decisionLabel,
      recoveryGuidance,
      receiptText,
      configureSourcePushControl,
      interceptPushControl,
      mount
    });

    global.GitToolsSemanticPanel = api;

    const documentObject = global.document;
    if (documentObject?.readyState === "loading") {
      documentObject.addEventListener("DOMContentLoaded", () => mount(), {once: true});
    } else {
      mount();
    }

    if (typeof module !== "undefined" && module.exports) {
      module.exports = api;
    }
  })(typeof window !== "undefined" ? window : globalThis);
})();
