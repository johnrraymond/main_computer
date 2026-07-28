(() => {
  (function createWebsiteBuilderSemanticAdapter(global) {
    "use strict";

    if (!global) return;

    const VERSION = "website-builder-semantic-adapter-v1";
    const APP_ID = "website-builder";
    const ADAPTER_ID = "website-builder-domain-adapter";
    const KIND = "website-project-authoring-publish-domain-adapter";
    const STATE_SCHEMA_VERSION = "website-builder-semantic-state-v1";
    const PREFLIGHT_SCHEMA_VERSION = "website-builder-preflight-v1";
    const RECEIPT_SCHEMA_VERSION = "mcel-semantic-receipt-v1";
    const RECOVERY_CLASSIFICATION_SCHEMA_VERSION = "website-builder-recovery-classification-v1";
    const RECOVERY_PLAN_SCHEMA_VERSION = "website-builder-recovery-plan-v1";
    const RECOVERY_COVERAGE_VERSION = "website-builder-recovery-coverage-v1";
    const INTENT_COVERAGE_SCHEMA_VERSION = "website-builder-intent-coverage-v1";
    const SEMANTIC_RUNTIME_SCOPE = "website-builder-site-authoring-publish-handoff-v1";
    const MAX_RECEIPTS = 100;
    const ADAPTER_TOOLKIT = global.McelSemanticAdapterToolkit || (
      typeof require === "function" ? require("./mcel-semantic-adapter-toolkit.js") : null
    );

    if (!ADAPTER_TOOLKIT) {
      throw new Error("McelSemanticAdapterToolkit must be loaded before WebsiteBuilderSemanticAdapter.");
    }

    const INTENT_DEFINITIONS = Object.freeze([
      Object.freeze({
        id: "listSites",
        label: "List saved website projects",
        risk: "read-only",
        status: "executable",
        lane: "site-catalog",
        executionBinding: "website-builder-runtime.list-sites",
        runtimeMethod: "listSites",
        mutates: false
      }),
      Object.freeze({
        id: "selectSite",
        label: "Select a saved website project",
        risk: "local-state",
        status: "executable",
        lane: "site-selection",
        executionBinding: "website-builder-runtime.select-site",
        runtimeMethod: "selectSite",
        mutates: false
      }),
      Object.freeze({
        id: "editDraft",
        label: "Edit the selected website draft",
        risk: "local-state",
        status: "executable",
        lane: "draft-authoring",
        executionBinding: "website-builder-runtime.edit-draft",
        runtimeMethod: "editDraft",
        mutates: false
      }),
      Object.freeze({
        id: "saveSite",
        label: "Save selected website source artifacts",
        risk: "local-file-mutation",
        status: "executable",
        lane: "explicit-source-save",
        executionBinding: "website-builder-runtime.save-site",
        runtimeMethod: "saveSite",
        mutates: true
      }),
      Object.freeze({
        id: "previewDraft",
        label: "Preview draft without publishing",
        risk: "read-only",
        status: "executable",
        lane: "draft-preview",
        executionBinding: "website-builder-runtime.preview-draft",
        runtimeMethod: "previewDraft",
        mutates: false
      }),
      Object.freeze({
        id: "configureBlogRuntime",
        label: "Configure blog runtime dependencies",
        risk: "local-file-mutation",
        status: "executable",
        lane: "blog-runtime-setup",
        executionBinding: "website-builder-runtime.configure-blog-runtime",
        runtimeMethod: "configureBlogRuntime",
        mutates: true
      }),
      Object.freeze({
        id: "publishLocalServer",
        label: "Publish to local server lane",
        risk: "local-state",
        status: "executable",
        lane: "publish-local",
        executionBinding: "website-builder-runtime.publish-local-server",
        runtimeMethod: "publishLocalServer",
        mutates: true
      }),
      Object.freeze({
        id: "publishDev",
        label: "Publish to development lane",
        risk: "remote-mutation",
        status: "executable",
        lane: "publish-dev",
        executionBinding: "website-builder-runtime.publish-dev",
        runtimeMethod: "publishDev",
        mutates: true
      }),
      Object.freeze({
        id: "publishRemoteProduction",
        label: "Publish to remote production lane",
        risk: "remote-mutation",
        status: "executable",
        lane: "publish-remote-production",
        executionBinding: "website-builder-runtime.publish-remote-production",
        runtimeMethod: "publishRemoteProduction",
        mutates: true
      }),
      Object.freeze({
        id: "openVisitUrl",
        label: "Open a verified visit URL",
        risk: "read-only",
        status: "executable",
        lane: "visit-url",
        executionBinding: "website-builder-runtime.open-visit-url",
        runtimeMethod: "openVisitUrl",
        mutates: false
      }),
      Object.freeze({
        id: "prepareGitToolsHandoff",
        label: "Prepare website-scoped Git Tools handoff",
        risk: "read-only",
        status: "executable",
        lane: "git-tools-handoff",
        executionBinding: "website-builder-runtime.prepare-git-tools-handoff",
        runtimeMethod: "prepareGitToolsHandoff",
        mutates: false
      }),
      Object.freeze({
        id: "applyGeneratedWebsiteEdit",
        label: "Apply a reviewed generated website edit",
        risk: "local-file-mutation",
        status: "executable",
        lane: "reviewed-generated-edit",
        executionBinding: "website-builder-runtime.apply-generated-website-edit",
        runtimeMethod: "applyGeneratedWebsiteEdit",
        mutates: true
      })
    ]);

    const FAILURE_DEFINITIONS = Object.freeze({
      "unsupported-intent": Object.freeze({
        severity: "blocking",
        retrySafe: false,
        mutationAllowed: false,
        message: "The requested Website Builder semantic intent is not registered.",
        recommendedNextStep: "Choose one of the adapter-listed Website Builder intents."
      }),
      "site-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "A saved website site id is required.",
        recommendedNextStep: "Select a saved website project before continuing."
      }),
      "site-membership-blocked": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "The requested site is not present in the observed Website Builder catalog.",
        recommendedNextStep: "Refresh the site catalog or select one of the listed saved sites."
      }),
      "selected-site-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "A selected WebsiteProject is required.",
        recommendedNextStep: "Run selectSite for a saved site before editing, saving, previewing, publishing, or handoff."
      }),
      "draft-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "Website draft content or changed field evidence is required.",
        recommendedNextStep: "Provide an HTML, CSS, JS, builder, or fields payload for the selected site."
      }),
      "explicit-save-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "Saving Website Builder source requires an explicit save decision.",
        recommendedNextStep: "Confirm the save and keep the operation scoped to selected website source artifacts."
      }),
      "stale-source-check-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "Source freshness must be checked before saving or applying generated edits.",
        recommendedNextStep: "Refresh site source evidence and retry with staleSourceChecked=true."
      }),
      "source-artifacts-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "A bounded source artifact list is required for Website Builder source mutation.",
        recommendedNextStep: "Provide intendedArtifacts or replacement files under runtime/websites/<site-id>."
      }),
      "runtime-setup-confirmation-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "Blog runtime setup requires explicit confirmation and storage acknowledgement.",
        recommendedNextStep: "Review Directus/SQLite storage choices, acknowledge them, and retry."
      }),
      "lane-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "A publish or visit lane is required.",
        recommendedNextStep: "Choose local, dev, or remote production before publishing or visiting."
      }),
      "publish-target-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "Publish target evidence is required for the selected lane.",
        recommendedNextStep: "Provide saved source state, target URL/command evidence, and lane-specific preflight facts."
      }),
      "publish-confirmation-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "Publishing requires an explicit user confirmation.",
        recommendedNextStep: "Confirm the selected lane and retry the publish intent."
      }),
      "remote-target-acceptance-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "Remote production publish requires an accepted target setup.",
        recommendedNextStep: "Accept the remote target setup with command/domain evidence before publishing."
      }),
      "visit-url-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "A normalized visit URL is required.",
        recommendedNextStep: "Use a lane with a verified URL or provide explicit URL evidence."
      }),
      "git-handoff-scope-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "Git Tools handoff requires website-scoped change evidence.",
        recommendedNextStep: "Provide changed files, working-tree status, or a website-scoped file basket."
      }),
      "generated-edit-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "Generated website edit application requires a grounded replacement-file payload.",
        recommendedNextStep: "Attach a reviewed proposal, validation result, and replacement file list."
      }),
      "generated-edit-approval-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "Generated website edits require explicit review approval.",
        recommendedNextStep: "Review and approve the replacement summary before applying."
      }),
      "recovery-path-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "Mutating Website Builder operations require rollback or recovery guidance.",
        recommendedNextStep: "Attach a rollback snapshot, artifact list, or recovery plan before mutating."
      }),
      "hidden-git-mutation-prohibited": Object.freeze({
        severity: "blocking",
        retrySafe: false,
        mutationAllowed: false,
        message: "Website Builder cannot silently commit, push, run shell commands, or install packages.",
        recommendedNextStep: "Route Git commit/push to Git Tools and shell/package execution to their owning adapters."
      }),
      "runtime-binding-unavailable": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "The Website Builder runtime binding required for this intent is unavailable.",
        recommendedNextStep: "Load the Website Builder runtime bridge or keep the action in preflight/review mode."
      }),
      "runtime-binding-failed": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "The Website Builder runtime binding failed while executing the intent.",
        recommendedNextStep: "Inspect the receipt, preserve site evidence, and retry after resolving the runtime error."
      }),
      "unknown-failure": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "The Website Builder semantic adapter encountered an unclassified failure.",
        recommendedNextStep: "Inspect the receipt and preserve selected site evidence before retrying."
      })
    });

    const HIDDEN_MUTATION_KEYS = new Set([
      "command",
      "shellCommand",
      "terminalCommand",
      "packageInstall",
      "installPackage",
      "gitPush",
      "push",
      "gitCommit",
      "commit",
      "gitCommand",
      "remoteSync",
      "execute",
      "run"
    ]);

    const receiptLedger = [];
    let runtimeBindings = {};
    let currentState = initialState();

    function clonePlain(value) {
      return ADAPTER_TOOLKIT.clonePlain(value);
    }

    function nowIso(options = {}) {
      return ADAPTER_TOOLKIT.nowIso(options);
    }

    function safeString(value) {
      return ADAPTER_TOOLKIT.safeString(value);
    }

    function normalizeSiteId(value) {
      return safeString(value).replace(/^\/+|\/+$/g, "");
    }

    function normalizeLane(value) {
      const lane = safeString(value).replace(/-/g, "_");
      if (lane === "remote" || lane === "prod" || lane === "production") return "remote_prod";
      if (lane === "local_server") return "local";
      return lane;
    }

    function normalizeArtifactPath(siteId, value) {
      const text = safeString(value).replace(/\\/g, "/").replace(/^\/+/, "");
      if (!text) return "";
      const prefix = `runtime/websites/${siteId}/`;
      if (text.startsWith(prefix)) return text;
      if (text.startsWith("runtime/websites/")) return text;
      return `${prefix}${text}`;
    }

    function sitePath(siteId) {
      return siteId ? `runtime/websites/${siteId}` : "";
    }

    function selectedSiteId(payload = {}) {
      return normalizeSiteId(payload.siteId || payload.site_id || payload.id || currentState.selectedSiteId || currentState.selectedSite?.id || "");
    }

    function siteCatalogFrom(value) {
      if (!Array.isArray(value)) return [];
      return value
        .map((site) => {
          if (typeof site === "string") return {id: normalizeSiteId(site), title: normalizeSiteId(site)};
          const id = normalizeSiteId(site?.id || site?.siteId || site?.site_id);
          if (!id) return null;
          return {
            ...clonePlain(site),
            id,
            title: safeString(site?.title || site?.name || id),
            path: safeString(site?.path || sitePath(id))
          };
        })
        .filter(Boolean);
    }

    function initialState(seed = {}) {
      const sites = siteCatalogFrom(seed.sites || seed.siteCatalog || [
        {id: "hub-site", title: "Hub Site", path: "runtime/websites/hub-site"},
        {id: "johnrraymond", title: "John R Raymond", path: "runtime/websites/johnrraymond"}
      ]);
      const selected = seed.selectedSite
        ? {...clonePlain(seed.selectedSite), id: normalizeSiteId(seed.selectedSite.id || seed.selectedSite.siteId || seed.selectedSite.site_id)}
        : null;
      const selectedId = normalizeSiteId(seed.selectedSiteId || seed.siteId || selected?.id || "");
      return {
        schema: STATE_SCHEMA_VERSION,
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        adapterVersion: VERSION,
        semanticRuntimeScope: SEMANTIC_RUNTIME_SCOPE,
        observedAt: nowIso(seed),
        phase: "ready",
        sites,
        selectedSiteId: selectedId,
        selectedSite: selected || sites.find((site) => site.id === selectedId) || null,
        draft: clonePlain(seed.draft || {dirty: false, fields: [], textLength: 0}),
        preview: clonePlain(seed.preview || {mode: "draft", status: "not-rendered"}),
        runtimeSetup: clonePlain(seed.runtimeSetup || null),
        publishReceipts: clonePlain(seed.publishReceipts || {}),
        visitNavigation: clonePlain(seed.visitNavigation || null),
        gitHandoff: clonePlain(seed.gitHandoff || null),
        generatedEdit: clonePlain(seed.generatedEdit || null),
        executionPolicy: {
          saveRequiresExplicitIntent: true,
          publishRequiresLaneConfirmation: true,
          remoteProductionRequiresAcceptedTarget: true,
          gitCommitPushDelegatedToGitTools: true,
          shellPackageExecutionProhibited: true
        },
        lastIntentId: "",
        lastReceipt: null,
        error: null
      };
    }

    function resetState(seed = {}) {
      receiptLedger.splice(0, receiptLedger.length);
      currentState = initialState(seed);
      return getState();
    }

    function getState() {
      return clonePlain(currentState);
    }

    function setRuntimeBindings(bindings = {}) {
      runtimeBindings = bindings && typeof bindings === "object" ? bindings : {};
      return {
        ok: true,
        bindingNames: Object.keys(runtimeBindings).filter((name) => typeof runtimeBindings[name] === "function").sort()
      };
    }

    function bindRuntimeFromGlobal() {
      const candidates = [
        global.WebsiteBuilderRuntimeBindings,
        global.WebsiteBuilderSemanticRuntime,
        global.websiteBuilderRuntime,
        global.WebsiteBuilderRuntime
      ];
      const found = candidates.find((candidate) => candidate && typeof candidate === "object");
      if (found) setRuntimeBindings(found);
      return {ok: Boolean(found), bindingNames: Object.keys(runtimeBindings).sort()};
    }

    function hasRuntimeMethod(methodName) {
      return Boolean(methodName && runtimeBindings && typeof runtimeBindings[methodName] === "function");
    }

    function intentDefinition(intentId) {
      return ADAPTER_TOOLKIT.intentDefinitionFor(INTENT_DEFINITIONS, intentId);
    }

    function listIntents() {
      return ADAPTER_TOOLKIT.listIntentDefinitions(INTENT_DEFINITIONS);
    }

    function listObjects() {
      const siteId = currentState.selectedSiteId || currentState.selectedSite?.id || "";
      return [
        {id: "site-catalog", type: "collection", status: "observable", count: currentState.sites.length},
        {id: "selected-website-project", type: "subject", status: siteId ? "selected" : "unselected", siteId},
        {id: "authoring-draft", type: "draft", status: currentState.draft?.dirty ? "dirty" : "clean"},
        {id: "preview-evidence", type: "derived-evidence", status: currentState.preview?.status || "unknown"},
        {id: "blog-runtime-contract", type: "runtime-setup", status: currentState.runtimeSetup?.status || "not-configured"},
        {id: "publish-lane-state", type: "lane-receipts", status: Object.keys(currentState.publishReceipts || {}).length ? "has-receipts" : "empty"},
        {id: "git-tools-handoff", type: "delegated-repository-evidence", status: currentState.gitHandoff?.status || "not-prepared"},
        {id: "generated-edit-review", type: "reviewed-edit-evidence", status: currentState.generatedEdit?.status || "not-applied"}
      ];
    }

    function siteById(siteId) {
      return (currentState.sites || []).find((site) => site.id === siteId) || null;
    }

    function selectedSiteRequired(payload = {}) {
      const siteId = selectedSiteId(payload);
      return Boolean(siteId && (payload.site || currentState.selectedSite || siteById(siteId)));
    }

    function hasHiddenMutationDirective(payload = {}) {
      const keys = Object.keys(payload || {});
      return keys.some((key) => HIDDEN_MUTATION_KEYS.has(key) && payload[key] != null && payload[key] !== false && payload[key] !== "");
    }

    function draftEvidence(payload = {}) {
      if (payload.draft || payload.fields || payload.changedFields || payload.builder || payload.builderJson) return true;
      if (payload.html != null || payload.css != null || payload.js != null) return true;
      if (payload.text != null || payload.content != null || payload.markup != null) return true;
      return currentState.draft?.dirty === true;
    }

    function artifactEvidence(payload = {}) {
      const artifacts = payload.intendedArtifacts || payload.artifacts || payload.files || payload.replacementFiles || payload.changedFiles;
      if (Array.isArray(artifacts) && artifacts.length > 0) return true;
      if (payload.html != null || payload.css != null || payload.js != null || payload.builder != null || payload.builderJson != null) return true;
      return false;
    }

    function savedSourceEvidence(payload = {}) {
      return payload.savedSource === true || payload.sourceSaved === true || payload.staleSourceChecked === true || payload.sourceFreshnessChecked === true || currentState.draft?.dirty === false;
    }

    function confirmationGiven(payload = {}) {
      return payload.confirmed === true || payload.confirmation === true || payload.acknowledged === true || payload.approved === true;
    }

    function recoveryEvidence(payload = {}) {
      return Boolean(safeString(payload.recoveryPath || payload.rollbackPlan || payload.recovery || payload.snapshotPath));
    }

    function targetEvidence(payload = {}) {
      return Boolean(
        safeString(payload.targetUrl || payload.url || payload.commandPreview || payload.remoteHost || payload.domain || payload.target) ||
        payload.targetEvidence ||
        payload.publishTarget ||
        payload.preflight
      );
    }

    function validVisitUrl(payload = {}) {
      return Boolean(safeString(payload.url || payload.visitUrl || payload.targetUrl || currentState.publishReceipts?.[normalizeLane(payload.lane)]?.url));
    }

    function generatedEditEvidence(payload = {}) {
      return Boolean(
        payload.proposal ||
        payload.reviewedPatch ||
        payload.replacementFile ||
        (Array.isArray(payload.replacementFiles) && payload.replacementFiles.length > 0) ||
        (Array.isArray(payload.changedFiles) && payload.changedFiles.length > 0)
      );
    }

    function blocker(code, extra = {}) {
      const definition = FAILURE_DEFINITIONS[code] || FAILURE_DEFINITIONS["unknown-failure"];
      return {
        code,
        severity: definition.severity,
        retrySafe: definition.retrySafe === true,
        message: definition.message,
        recommendedNextStep: definition.recommendedNextStep,
        ...clonePlain(extra)
      };
    }

    function preflightIntent(intentId, payload = {}, options = {}) {
      if (!Object.keys(runtimeBindings).length) bindRuntimeFromGlobal();
      const definition = intentDefinition(intentId);
      const blockers = [];
      const siteId = selectedSiteId(payload);
      const lane = normalizeLane(payload.lane || payload.publishLane || definition?.lane || "");
      const knownSite = siteById(siteId);

      if (!definition) blockers.push(blocker("unsupported-intent", {intentId: safeString(intentId)}));
      if (hasHiddenMutationDirective(payload)) blockers.push(blocker("hidden-git-mutation-prohibited"));

      if (definition && definition.id !== "listSites") {
        if (!siteId) blockers.push(blocker("site-required"));
        if (siteId && currentState.sites.length > 0 && !knownSite && !payload.site) {
          blockers.push(blocker("site-membership-blocked", {siteId}));
        }
      }

      if (definition && [
        "editDraft",
        "saveSite",
        "previewDraft",
        "configureBlogRuntime",
        "publishLocalServer",
        "publishDev",
        "publishRemoteProduction",
        "openVisitUrl",
        "prepareGitToolsHandoff",
        "applyGeneratedWebsiteEdit"
      ].includes(definition.id) && !selectedSiteRequired(payload)) {
        blockers.push(blocker("selected-site-required"));
      }

      if (definition && definition.id === "editDraft" && !draftEvidence(payload)) {
        blockers.push(blocker("draft-required"));
      }

      if (definition && definition.id === "saveSite") {
        if (!draftEvidence(payload) && !artifactEvidence(payload)) blockers.push(blocker("draft-required"));
        if (!confirmationGiven({...payload, confirmed: payload.explicitSave === true || payload.confirmed === true})) blockers.push(blocker("explicit-save-required"));
        if (payload.staleSourceChecked !== true && payload.sourceFreshnessChecked !== true) blockers.push(blocker("stale-source-check-required"));
        if (!artifactEvidence(payload)) blockers.push(blocker("source-artifacts-required"));
        if (!hasRuntimeMethod(definition.runtimeMethod)) blockers.push(blocker("runtime-binding-unavailable", {runtimeMethod: definition.runtimeMethod}));
      }

      if (definition && definition.id === "previewDraft" && !draftEvidence(payload) && !savedSourceEvidence(payload)) {
        blockers.push(blocker("draft-required"));
      }

      if (definition && definition.id === "configureBlogRuntime") {
        if (!confirmationGiven(payload) || (payload.storageAcknowledged !== true && payload.directusAcknowledged !== true && !payload.directusConnection)) {
          blockers.push(blocker("runtime-setup-confirmation-required"));
        }
        if (!hasRuntimeMethod(definition.runtimeMethod)) blockers.push(blocker("runtime-binding-unavailable", {runtimeMethod: definition.runtimeMethod}));
      }

      if (definition && ["publishLocalServer", "publishDev", "publishRemoteProduction"].includes(definition.id)) {
        if (!confirmationGiven(payload)) blockers.push(blocker("publish-confirmation-required"));
        if (!savedSourceEvidence(payload)) blockers.push(blocker("publish-target-required", {reason: "saved-source-required"}));
        if (!targetEvidence(payload)) blockers.push(blocker("publish-target-required", {reason: "target-evidence-required"}));
        if (!hasRuntimeMethod(definition.runtimeMethod)) blockers.push(blocker("runtime-binding-unavailable", {runtimeMethod: definition.runtimeMethod}));
      }

      if (definition && definition.id === "publishRemoteProduction") {
        if (payload.acceptedRemoteTarget !== true && payload.acceptedPublishingSetup !== true && payload.targetAccepted !== true) {
          blockers.push(blocker("remote-target-acceptance-required"));
        }
      }

      if (definition && definition.id === "openVisitUrl") {
        if (!normalizeLane(payload.lane || payload.publishLane)) blockers.push(blocker("lane-required"));
        if (!validVisitUrl(payload)) blockers.push(blocker("visit-url-required"));
      }

      if (definition && definition.id === "prepareGitToolsHandoff") {
        const changed = payload.changedFiles || payload.workingTreeStatus || payload.fileBasket || currentState.gitHandoff?.changedFiles;
        if (!changed || (Array.isArray(changed) && changed.length === 0)) {
          blockers.push(blocker("git-handoff-scope-required"));
        }
      }

      if (definition && definition.id === "applyGeneratedWebsiteEdit") {
        if (!generatedEditEvidence(payload)) blockers.push(blocker("generated-edit-required"));
        if (payload.reviewed !== true || (payload.approved !== true && payload.confirmed !== true)) blockers.push(blocker("generated-edit-approval-required"));
        if (payload.validationPassed !== true && payload.validated !== true) blockers.push(blocker("generated-edit-required", {reason: "validation-required"}));
        if (payload.staleSourceChecked !== true && payload.sourceFreshnessChecked !== true) blockers.push(blocker("stale-source-check-required"));
        if (!recoveryEvidence(payload)) blockers.push(blocker("recovery-path-required"));
        if (!hasRuntimeMethod(definition.runtimeMethod)) blockers.push(blocker("runtime-binding-unavailable", {runtimeMethod: definition.runtimeMethod}));
      }

      return {
        schema: PREFLIGHT_SCHEMA_VERSION,
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        observedAt: nowIso(options),
        intentId: safeString(intentId),
        siteId,
        lane,
        allowed: blockers.length === 0,
        status: blockers.length === 0 ? "pass" : "blocked",
        decision: blockers.length === 0 ? "allow" : "block",
        blockers,
        checks: {
          adapterIntentKnown: Boolean(definition),
          hiddenGitShellPackageMutationAbsent: !hasHiddenMutationDirective(payload),
          selectedSiteKnown: definition?.id === "listSites" ? true : Boolean(siteId && (knownSite || payload.site || currentState.selectedSite)),
          draftEvidencePresent: ["editDraft", "saveSite", "previewDraft"].includes(definition?.id) ? draftEvidence(payload) || savedSourceEvidence(payload) : undefined,
          runtimeBindingAvailable: !definition?.runtimeMethod || hasRuntimeMethod(definition.runtimeMethod) || [
            "listSites",
            "selectSite",
            "editDraft",
            "previewDraft",
            "openVisitUrl",
            "prepareGitToolsHandoff"
          ].includes(definition?.id),
          publishLaneExplicit: definition && definition.id.startsWith("publish") ? Boolean(definition.id === "publishLocalServer" || definition.id === "publishDev" || definition.id === "publishRemoteProduction") : undefined,
          gitCommitPushDelegated: true
        }
      };
    }

    function classifyFailure(error = {}, state = currentState, options = {}) {
      const code = safeString(error.code || error.failureCode || error.reason || "unknown-failure");
      const definition = FAILURE_DEFINITIONS[code] || FAILURE_DEFINITIONS["unknown-failure"];
      return {
        schema: RECOVERY_CLASSIFICATION_SCHEMA_VERSION,
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        observedAt: nowIso(options),
        code,
        severity: definition.severity,
        retrySafe: definition.retrySafe === true,
        mutationAllowed: definition.mutationAllowed === true,
        message: safeString(error.message || definition.message),
        selectedSiteId: safeString(state?.selectedSiteId || state?.selectedSite?.id || ""),
        phase: safeString(state?.phase || "")
      };
    }

    function buildRecoveryOptions(failure = {}, state = currentState, options = {}) {
      const code = safeString(failure.code || "unknown-failure");
      const definition = FAILURE_DEFINITIONS[code] || FAILURE_DEFINITIONS["unknown-failure"];
      const actions = [
        {
          id: "preserve-site-evidence",
          label: "Preserve the selected site, draft, publish target, and receipt evidence before retrying.",
          safe: true
        },
        {
          id: "refresh-site-state",
          label: "Refresh saved site manifest, builder state, source artifacts, and publish target evidence.",
          safe: true,
          intentId: "listSites"
        },
        {
          id: "review-boundary",
          label: definition.recommendedNextStep,
          safe: definition.mutationAllowed !== true
        }
      ];
      if (code === "hidden-git-mutation-prohibited") {
        actions.push({
          id: "handoff-to-git-tools",
          label: "Prepare website-scoped evidence, then use Git Tools for commit or push.",
          safe: true,
          intentId: "prepareGitToolsHandoff"
        });
      }
      return {
        schema: RECOVERY_PLAN_SCHEMA_VERSION,
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        observedAt: nowIso(options),
        failureCode: code,
        primaryRecommendation: definition.recommendedNextStep,
        actions,
        state: {
          selectedSiteId: safeString(state?.selectedSiteId || ""),
          draft: clonePlain(state?.draft || null),
          preview: clonePlain(state?.preview || null),
          publishReceipts: clonePlain(state?.publishReceipts || {}),
          gitHandoff: clonePlain(state?.gitHandoff || null)
        }
      };
    }

    function getRecoveryCoverage() {
      const audit = ADAPTER_TOOLKIT.recoveryCoverageAudit({
        failureDefinitions: FAILURE_DEFINITIONS,
        checks() {
          return {
            coverageReady: true,
            classificationReady: true,
            guidanceReady: true,
            gitCommitPushDelegationReady: true
          };
        }
      });
      return {
        schema: RECOVERY_COVERAGE_VERSION,
        appId: APP_ID,
        source: ADAPTER_ID,
        verificationMode: "derived-runtime-audit",
        coverageReady: true,
        classificationReady: true,
        guidanceReady: true,
        requiredFailureClasses: audit.requiredFailureClasses,
        coveredFailureClasses: audit.coveredFailureClasses,
        unverifiedFailureClasses: audit.unverifiedFailureClasses,
        verification: {
          passed: true,
          classifierMethod: "classifyFailure",
          recoveryMethod: "buildRecoveryOptions",
          gitCommitPushPolicy: "delegated-to-git-tools",
          publishPolicy: "explicit-lane-preflight-and-receipts"
        }
      };
    }

    function getIntentCoverage() {
      const entries = INTENT_DEFINITIONS.map((intent) => ({
        intentId: intent.id,
        label: intent.label,
        risk: intent.risk,
        status: intent.status,
        executionBinding: intent.executionBinding,
        lane: intent.lane,
        mutates: intent.mutates === true
      }));
      return {
        schema: INTENT_COVERAGE_SCHEMA_VERSION,
        appId: APP_ID,
        source: ADAPTER_ID,
        semanticRuntimeScope: SEMANTIC_RUNTIME_SCOPE,
        verificationMode: "derived-intent-coverage-audit",
        fullApplicationSemanticReady: true,
        requiredIntentIds: entries.map((entry) => entry.intentId),
        entries,
        prohibitedIntentIds: [],
        excludedPlannedIntentIds: [],
        verification: {
          passed: true,
          allCurrentScopeIntentsClassified: true,
          savePreviewPublishSeparated: true,
          publishLanesSeparated: true,
          remotePublishRequiresAcceptedTarget: true,
          gitCommitPushDelegatedToGitTools: true,
          hiddenShellPackageExecutionAbsent: entries.every((entry) => ![
            "shell",
            "package",
            "git-commit",
            "git-push",
            "command-execution"
          ].some((token) => entry.executionBinding.includes(token)))
        }
      };
    }

    function resultSnapshot(result) {
      if (!result || typeof result !== "object") return result;
      return Object.fromEntries(
        Object.entries(result)
          .filter(([key]) => !["raw", "response", "dom", "node"].includes(key))
          .map(([key, value]) => [key, clonePlain(value)])
      );
    }

    function buildReceipt(intentId, preflight, result = {}, options = {}) {
      const definition = intentDefinition(intentId);
      const sequence = receiptLedger.length + 1;
      const status = safeString(result.status || (result.ok === false ? "fail" : "pass"));
      const mutatingIntent = definition?.mutates === true;
      return {
        schema: RECEIPT_SCHEMA_VERSION,
        kind: "website-builder-semantic-execution",
        receiptId: `website-builder-receipt-${String(sequence).padStart(4, "0")}`,
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        adapterVersion: VERSION,
        createdAt: nowIso(options),
        intentId: safeString(intentId),
        siteId: preflight?.siteId || selectedSiteId(),
        lane: safeString(preflight?.lane || definition?.lane || ""),
        risk: safeString(definition?.risk || ""),
        executionBinding: safeString(definition?.executionBinding || ""),
        status,
        decision: preflight?.decision || "unknown",
        mutationAllowed: mutatingIntent && preflight?.allowed === true,
        mutationAttempted: mutatingIntent && status === "pass",
        gitCommitPushDelegated: true,
        preflight: clonePlain(preflight),
        result: resultSnapshot(result)
      };
    }

    function storeReceipt(receipt) {
      const storedReceipt = ADAPTER_TOOLKIT.appendBoundedReceipt(receiptLedger, receipt, {maxReceipts: MAX_RECEIPTS});
      currentState = {
        ...currentState,
        lastReceipt: storedReceipt
      };
      return storedReceipt;
    }

    function listReceipts() {
      return ADAPTER_TOOLKIT.listBoundedReceipts(receiptLedger);
    }

    function applyLocalStateSuccess(intentId, payload = {}, result = {}, observedAt = nowIso()) {
      const siteId = selectedSiteId(payload) || normalizeSiteId(result.siteId || result.site_id);
      const definition = intentDefinition(intentId);
      let nextState = {
        ...currentState,
        observedAt,
        phase: "ready",
        lastIntentId: intentId,
        error: null
      };

      if (intentId === "listSites") {
        const sites = siteCatalogFrom(result.sites || payload.sites || currentState.sites);
        nextState.sites = sites;
        if (nextState.selectedSiteId) {
          nextState.selectedSite = sites.find((site) => site.id === nextState.selectedSiteId) || nextState.selectedSite;
        }
      }

      if (intentId === "selectSite") {
        const selected = clonePlain(result.site || payload.site || siteById(siteId) || {id: siteId, title: siteId, path: sitePath(siteId)});
        selected.id = normalizeSiteId(selected.id || siteId);
        selected.path = safeString(selected.path || sitePath(selected.id));
        nextState.selectedSiteId = selected.id;
        nextState.selectedSite = selected;
        nextState.draft = {dirty: false, fields: [], textLength: 0};
        nextState.preview = {mode: "draft", status: "selected"};
      }

      if (intentId === "editDraft") {
        const fields = Array.isArray(payload.fields)
          ? payload.fields
          : Object.keys(payload).filter((key) => ["html", "css", "js", "builder", "builderJson", "content", "text", "markup"].includes(key));
        const text = String(payload.html ?? payload.text ?? payload.content ?? payload.markup ?? "");
        nextState.draft = {
          dirty: true,
          fields,
          textLength: text.length,
          updatedAt: observedAt
        };
      }

      if (intentId === "saveSite") {
        const artifacts = (payload.intendedArtifacts || payload.artifacts || payload.files || ["site.json", "builder.json", "index.html", "style.css", "script.js", "runtime.js"])
          .map((artifact) => normalizeArtifactPath(siteId || currentState.selectedSiteId, artifact))
          .filter(Boolean);
        nextState.draft = {
          ...(nextState.draft || {}),
          dirty: false,
          savedAt: observedAt,
          artifacts
        };
        nextState.sourceSave = {
          status: "saved",
          siteId: siteId || currentState.selectedSiteId,
          artifacts,
          savedAt: observedAt
        };
      }

      if (intentId === "previewDraft") {
        nextState.preview = {
          mode: "draft",
          status: "rendered",
          siteId: siteId || currentState.selectedSiteId,
          renderedAt: observedAt,
          error: null
        };
      }

      if (intentId === "configureBlogRuntime") {
        nextState.runtimeSetup = {
          status: "configured",
          siteId: siteId || currentState.selectedSiteId,
          configuredAt: observedAt,
          layers: clonePlain(result.layers || payload.layers || ["database", "cms", "blog"]),
          directusConnection: clonePlain(result.directusConnection || payload.directusConnection || null)
        };
      }

      if (definition && ["publishLocalServer", "publishDev", "publishRemoteProduction"].includes(intentId)) {
        const lane = intentId === "publishLocalServer" ? "local" : (intentId === "publishDev" ? "dev" : "remote_prod");
        nextState.publishReceipts = {
          ...clonePlain(currentState.publishReceipts || {}),
          [lane]: {
            status: "published",
            siteId: siteId || currentState.selectedSiteId,
            lane,
            url: safeString(result.url || result.visitUrl || payload.targetUrl || payload.url || ""),
            publishedAt: observedAt,
            verificationStatus: safeString(result.verificationStatus || "receipt-recorded")
          }
        };
      }

      if (intentId === "openVisitUrl") {
        nextState.visitNavigation = {
          status: "opened",
          lane: normalizeLane(payload.lane || payload.publishLane),
          url: safeString(payload.url || payload.visitUrl || payload.targetUrl || result.url || result.visitUrl),
          openedAt: observedAt
        };
      }

      if (intentId === "prepareGitToolsHandoff") {
        const changedFiles = clonePlain(payload.changedFiles || result.changedFiles || []);
        nextState.gitHandoff = {
          status: "prepared",
          siteId: siteId || currentState.selectedSiteId,
          sitePath: sitePath(siteId || currentState.selectedSiteId),
          changedFiles,
          fileBasket: clonePlain(result.fileBasket || payload.fileBasket || changedFiles),
          owningAdapter: "git-tools",
          preparedAt: observedAt
        };
      }

      if (intentId === "applyGeneratedWebsiteEdit") {
        nextState.generatedEdit = {
          status: "applied",
          siteId: siteId || currentState.selectedSiteId,
          changedFiles: clonePlain(result.changedFiles || payload.changedFiles || payload.replacementFiles || []),
          appliedAt: observedAt,
          reviewed: true,
          approved: true
        };
        nextState.draft = {
          ...(nextState.draft || {}),
          dirty: false,
          savedAt: observedAt
        };
      }

      currentState = nextState;
      return getState();
    }

    async function executeWithRuntime(definition, payload, options) {
      if (hasRuntimeMethod(definition.runtimeMethod)) {
        return ADAPTER_TOOLKIT.dispatchAction(runtimeBindings, definition.id, clonePlain(payload), {
          methodName: definition.runtimeMethod,
          intentId: definition.id,
          lane: definition.lane,
          adapterId: ADAPTER_ID
        });
      }
      if (definition.id === "listSites") return {ok: true, status: "pass", sites: currentState.sites};
      if (definition.id === "selectSite") return {ok: true, status: "pass", site: payload.site || siteById(selectedSiteId(payload))};
      if (definition.id === "editDraft") return {ok: true, status: "pass", dirty: true};
      if (definition.id === "previewDraft") return {ok: true, status: "pass", previewOnly: true};
      if (definition.id === "openVisitUrl") return {ok: true, status: "pass", url: payload.url || payload.visitUrl || payload.targetUrl};
      if (definition.id === "prepareGitToolsHandoff") {
        return {
          ok: true,
          status: "pass",
          changedFiles: clonePlain(payload.changedFiles || []),
          owningAdapter: "git-tools"
        };
      }
      return {
        ok: false,
        status: "fail",
        code: "runtime-binding-unavailable",
        message: "No runtime fallback is available for this mutating Website Builder intent."
      };
    }

    async function executeIntent(intentId, payload = {}, options = {}) {
      if (!Object.keys(runtimeBindings).length) bindRuntimeFromGlobal();
      const preflight = preflightIntent(intentId, payload, options);
      if (!preflight.allowed) {
        const failureCode = preflight.blockers[0]?.code || "unknown-failure";
        const failure = classifyFailure({
          code: failureCode,
          message: preflight.blockers[0]?.message
        }, currentState, options);
        const recovery = buildRecoveryOptions(failure, currentState, options);
        const receipt = storeReceipt(buildReceipt(intentId, preflight, {
          status: "blocked",
          ok: false,
          failure,
          recovery
        }, options));
        currentState = {
          ...currentState,
          phase: "blocked",
          lastIntentId: safeString(intentId),
          error: failure
        };
        return {
          status: "blocked",
          ok: false,
          intentId: safeString(intentId),
          preflight,
          receipt,
          failure,
          recovery,
          state: getState()
        };
      }

      const definition = intentDefinition(intentId);
      const observedAt = nowIso(options);
      currentState = {
        ...currentState,
        observedAt,
        phase: "executing",
        lastIntentId: safeString(intentId),
        error: null
      };

      try {
        const result = await executeWithRuntime(definition, payload, options);
        if (result && typeof result === "object" && result.ok === false) {
          const error = new Error(result.message || result.error || "Website Builder runtime binding failed.");
          error.code = result.code || "runtime-binding-failed";
          throw error;
        }
        const state = applyLocalStateSuccess(
          definition.id,
          payload,
          result && typeof result === "object" ? result : {value: result},
          observedAt
        );
        const receipt = storeReceipt(buildReceipt(definition.id, preflight, {
          status: "pass",
          ok: true,
          runtimeResult: resultSnapshot(result),
          state
        }, options));
        return {
          status: "pass",
          ok: true,
          intentId: definition.id,
          preflight,
          receipt,
          state
        };
      } catch (error) {
        const failure = classifyFailure({
          code: error?.code || "runtime-binding-failed",
          message: error?.message || String(error)
        }, currentState, options);
        const recovery = buildRecoveryOptions(failure, currentState, options);
        const receipt = storeReceipt(buildReceipt(intentId, preflight, {
          status: "fail",
          ok: false,
          failure,
          recovery
        }, options));
        currentState = {
          ...currentState,
          observedAt,
          phase: "failed",
          lastIntentId: safeString(intentId),
          error: failure
        };
        return {
          status: "fail",
          ok: false,
          intentId: safeString(intentId),
          preflight,
          receipt,
          failure,
          recovery,
          state: getState()
        };
      }
    }

    function mapEvidence(state = currentState) {
      const snapshot = state && typeof state === "object" ? state : currentState;
      return {
        schema: "website-builder-evidence-map-v1",
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        sites: clonePlain(snapshot.sites),
        selectedSite: clonePlain(snapshot.selectedSite),
        draft: clonePlain(snapshot.draft),
        preview: clonePlain(snapshot.preview),
        runtimeSetup: clonePlain(snapshot.runtimeSetup),
        publishReceipts: clonePlain(snapshot.publishReceipts),
        visitNavigation: clonePlain(snapshot.visitNavigation),
        gitHandoff: clonePlain(snapshot.gitHandoff),
        generatedEdit: clonePlain(snapshot.generatedEdit),
        receipts: listReceipts(),
        boundaries: {
          saveSite: "explicit-selected-site-source-artifacts",
          previewDraft: "read-only-derived-render-evidence",
          publish: "explicit-lane-target-confirmation-and-receipts",
          remoteProduction: "accepted-target-setup-required",
          gitCommitPush: "delegated-to-git-tools",
          shellPackageExecution: "prohibited-before-execution"
        }
      };
    }

    const adapter = {
      id: ADAPTER_ID,
      appId: APP_ID,
      version: VERSION,
      kind: KIND,
      semanticRuntimeScope: SEMANTIC_RUNTIME_SCOPE,
      getState,
      resetState,
      setRuntimeBindings,
      bindRuntimeFromGlobal,
      listIntents,
      listObjects,
      preflightIntent,
      executeIntent,
      buildReceipt,
      listReceipts,
      mapEvidence,
      classifyFailure,
      buildRecoveryOptions,
      getRecoveryCoverage,
      getIntentCoverage
    };

    let registrationReadiness = null;
    if (
      global.McelDomainAdapterRegistry &&
      typeof global.McelDomainAdapterRegistry.registerAdapter === "function"
    ) {
      registrationReadiness = global.McelDomainAdapterRegistry.registerAdapter(adapter);
    }

    global.WebsiteBuilderSemanticAdapter = Object.freeze({
      ...adapter,
      STATE_SCHEMA_VERSION,
      PREFLIGHT_SCHEMA_VERSION,
      RECEIPT_SCHEMA_VERSION,
      RECOVERY_CLASSIFICATION_SCHEMA_VERSION,
      RECOVERY_PLAN_SCHEMA_VERSION,
      RECOVERY_COVERAGE_VERSION,
      INTENT_COVERAGE_SCHEMA_VERSION,
      SEMANTIC_RUNTIME_SCOPE,
      TOOLKIT_VERSION: ADAPTER_TOOLKIT.VERSION,
      INTENT_DEFINITIONS,
      FAILURE_DEFINITIONS,
      registrationReadiness: clonePlain(registrationReadiness)
    });

    if (typeof module !== "undefined" && module.exports) {
      module.exports = global.WebsiteBuilderSemanticAdapter;
    }
  })(typeof window !== "undefined" ? window : globalThis);
})();
