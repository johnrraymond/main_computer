(() => {
  (function createGitToolsSemanticAdapter(global) {
    "use strict";

    if (!global) return;

    const VERSION = "git-tools-semantic-adapter-preflight-v2";
    const APP_ID = "git-tools";
    const ADAPTER_ID = "git-tools-domain-adapter";
    const STATE_SCHEMA_VERSION = "git-tools-semantic-state-v1";
    const PREFLIGHT_SCHEMA_VERSION = "git-tools-preflight-v1";
    const RECEIPT_SCHEMA_VERSION = "mcel-semantic-receipt-v1";
    const KIND = "preflight-domain-adapter";
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
        return {
          available: true,
          blockedReason: "Execution remains disabled; run preflight for an evidence-backed decision."
        };
      }
      return {
        available: true,
        blockedReason: ""
      };
    }

    function listIntents(state = getState()) {
      const safeState = state && typeof state === "object" ? state : getState();
      return INTENT_DEFINITIONS.map((definition) => {
        const availability = intentAvailability(definition, safeState);
        return {
          ...definition,
          available: availability.available,
          blockedReason: availability.blockedReason,
          executable: false,
          executionBinding: "not-registered",
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

      if (!definition) {
        addBlocker(
          blockers,
          "unsupported-intent",
          `Git Tools does not define the intent "${intentId || "unknown"}".`
        );
      } else if (definition.id === "refreshStatus") {
        // Refresh is always semantically valid, although execution remains unbound.
      } else {
        if (safeState.phase === "uninitialized" || !safeState.observedAt) {
          addBlocker(blockers, "state-not-observed", "Repository state must be refreshed before this intent can proceed.");
        } else if (safeState.phase === "loading") {
          addBlocker(blockers, "state-loading", "Repository state is still loading.");
        } else if (safeState.phase === "error") {
          addBlocker(
            blockers,
            "state-error",
            safeState.error?.message || "Repository state could not be derived.",
            {stateErrorCode: safeState.error?.code || "unknown"}
          );
        }

        if (definition?.requiresGitRepo && safeState.isGitRepo !== true) {
          addBlocker(blockers, "not-a-git-repository", "A confirmed Git repository is required.");
        }

        if (definition?.requiresRemote && !(safeState.remotes || []).length) {
          addBlocker(blockers, "remote-missing", "A configured remote is required.");
        }

        if (definition?.preflightRequired && safeState.observedAt) {
          const observedTime = new Date(safeState.observedAt).getTime();
          const evaluatedTime = new Date(evaluatedAt).getTime();
          if (!Number.isFinite(observedTime)) {
            addBlocker(blockers, "state-timestamp-invalid", "The repository state timestamp is invalid.");
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
          }
        }
      }

      if (definition?.id === "runManualCommand") {
        addBlocker(
          blockers,
          "manual-command-policy-block",
          "Arbitrary Git command execution is outside this adapter's policy."
        );
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
        }

        if (safeState.ahead === null || safeState.behind === null) {
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
        stateObservedAt: safeState.observedAt || "",
        stateFingerprint: stateFingerprint(safeState),
        decision,
        allowed: decision === "allow",
        blocked: decision === "block",
        confirmationRequired: decision === "confirm",
        blockers,
        warnings,
        executionAvailable: false,
        executionBinding: "not-registered",
        state: canonicalStateSnapshot(safeState)
      };
    }

    function receiptStatusFor(preflight) {
      if (preflight.decision === "block") return "blocked";
      if (preflight.decision === "confirm") return "confirmation-required";
      return "allowed";
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
        stateObservedAt: String(preflight.stateObservedAt || ""),
        stateFingerprint: String(preflight.stateFingerprint || ""),
        blockers: clonePlain(preflight.blockers || []),
        warnings: clonePlain(preflight.warnings || []),
        executionAttempted: false,
        executionBinding: "not-registered",
        recoveryClassified: false
      };

      if (options.store !== false) {
        loadReceipts();
        receiptLedger.push(receipt);
        receiptLedger = receiptLedger.slice(-MAX_RECEIPTS);
        persistReceipts();
      }

      return clonePlain(receipt);
    }

    function preflightIntent(intentOrId, state = getState(), options = {}) {
      const preflight = evaluatePreflight(intentOrId, state, options);
      const receipt = buildReceipt(preflight, options);
      return {
        ...preflight,
        receipt
      };
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
          kind: "preflight-decision-receipt",
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
            blockerCodes: receipt.blockers.map((blocker) => blocker.code),
            warningCodes: receipt.warnings.map((warning) => warning.code),
            executionAttempted: false
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
      preflightIntent,
      buildReceipt,
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
      INTENT_DEFINITIONS,
      DEFAULT_MAX_STATE_AGE_MS,
      normalizeStatus,
      evaluatePreflight,
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
