var McelApplicationBrowserScenarioRunner = (() => {
  const CONTRACT_VERSION = "mcel.application-browser-scenario-runner.v1";

  function clonePlain(value) {
    if (value === undefined) return undefined;
    return JSON.parse(JSON.stringify(value));
  }

  function stableStringify(value) {
    if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
    if (value && typeof value === "object") {
      return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(",")}}`;
    }
    return JSON.stringify(value);
  }

  function stableHash(value) {
    const text = stableStringify(value);
    let hash = 0x811c9dc5;
    for (let index = 0; index < text.length; index += 1) {
      hash ^= text.charCodeAt(index);
      hash = Math.imul(hash, 0x01000193) >>> 0;
    }
    return `fnv1a32:${hash.toString(16).padStart(8, "0")}`;
  }

  function fail(code, message, detail = {}) {
    const error = new Error(message);
    error.code = code;
    error.detail = clonePlain(detail);
    throw error;
  }

  function safeString(value) {
    return typeof value === "string" ? value.trim() : "";
  }

  function readPath(value, path) {
    const parts = safeString(path).split(".").filter(Boolean);
    let current = value;
    for (const part of parts) {
      if (current == null) return undefined;
      if (Array.isArray(current) && part === "length") current = current.length;
      else current = current[part];
    }
    return current;
  }

  function exactNode(root, nodeId) {
    const matches = Array.from(root.querySelectorAll(`[data-mcel-node-id="${CSS.escape(nodeId)}"]`));
    if (root.getAttribute?.("data-mcel-node-id") === nodeId) matches.unshift(root);
    if (matches.length !== 1) {
      fail("MCEL_BROWSER_SCENARIO_NODE_IDENTITY_INVALID", `Expected one semantic node ${nodeId}; found ${matches.length}.`, {nodeId, matchCount: matches.length});
    }
    return matches[0];
  }

  function itemRow(root, collectionNodeId, key) {
    const host = exactNode(root, collectionNodeId);
    return Array.from(host.children).find((element) => element.getAttribute?.("data-mcel-collection-key") === String(key)) || null;
  }

  function itemControl(root, key, intentId) {
    const row = itemRow(root, "contract-workbench.items", key);
    if (!row) return null;
    return row.querySelector(`[data-mcel-item-intent="${CSS.escape(intentId)}"]`);
  }

  function collectionSnapshot(mount) {
    const host = exactNode(mount.root, "contract-workbench.items");
    return Array.from(host.children).map((row) => ({
      key: row.getAttribute("data-mcel-collection-key") || "",
      runtimeNodeId: row.getAttribute("data-mcel-runtime-node-id") || "",
      name: row.querySelector("[data-mcel-item-field='name']")?.textContent || "",
      category: row.querySelector("[data-mcel-item-field='category']")?.textContent || "",
      quantity: row.querySelector("[data-mcel-item-field='quantity']")?.value || "",
      quoteStatus: row.querySelector("[data-mcel-item-field='quote-status']")?.textContent || "",
      quoteAmount: row.querySelector("[data-mcel-item-field='quote-amount']")?.textContent || "",
      controls: Array.from(row.querySelectorAll("[data-mcel-item-intent]")).map((entry) => entry.getAttribute("data-mcel-item-intent") || "").sort()
    }));
  }

  function normalizedExpectedField(field, value) {
    if (field === "quantity") return String(value);
    if (field === "quoteAmount") return `$${Number(value || 0)}`;
    return String(value ?? "");
  }

  function collectionMatchesView(mount, declaration) {
    const expected = readPath(mount.readViewState(), declaration.compareToStatePath) || [];
    const actual = collectionSnapshot(mount);
    const fields = declaration.fields || {};
    if (actual.length !== expected.length) return false;
    return actual.every((row, index) => {
      const item = expected[index];
      if (String(readPath(item, declaration.keyPath)) !== row.key) return false;
      if (declaration.requireOrderMatch === true && String(readPath(expected[index], declaration.keyPath)) !== row.key) return false;
      const fieldsMatch = Object.entries(fields).every(([field, itemPath]) => row[field] === normalizedExpectedField(field, readPath(item, itemPath)));
      const controls = [...(declaration.requireItemControls || [])].sort();
      return fieldsMatch && controls.every((intentId) => row.controls.includes(intentId));
    });
  }

  function latestReceipt(mount) {
    return mount.readLastResult()?.receipt || null;
  }

  function receiptVisible(mount) {
    const text = exactNode(mount.root, "contract-workbench.latest-receipt").textContent || "";
    try {
      return JSON.parse(text);
    } catch (_error) {
      return null;
    }
  }

  function sleep(milliseconds) {
    return new Promise((resolve) => setTimeout(resolve, Math.max(0, Number(milliseconds) || 0)));
  }

  async function waitFor(predicate, options = {}) {
    const timeout = Number(options.timeout || 3000);
    const interval = Number(options.interval || 10);
    const started = performance.now();
    let lastError = null;
    while (performance.now() - started < timeout) {
      try {
        const result = predicate();
        if (result) return result;
      } catch (error) {
        lastError = error;
      }
      await sleep(interval);
    }
    if (lastError) throw lastError;
    fail("MCEL_BROWSER_SCENARIO_TIMEOUT", options.message || "Browser scenario condition did not become true.", {timeout});
  }

  function operationPayload(when = {}) {
    const payload = {...clonePlain(when.payload || {})};
    if (when.itemKey) payload.contractId = when.itemKey;
    if (when.itemField && typeof when.itemField === "object") Object.assign(payload, clonePlain(when.itemField));
    return payload;
  }

  async function dispatchOperation(mount, operation = {}, fallbackScope = "given") {
    const operationId = safeString(operation.operationId) || `${mount.application.id}:${fallbackScope}:${safeString(operation.intentId)}:${mount.application.revision}`;
    return await mount.dispatch(safeString(operation.intentId), clonePlain(operation.payload || {}), {
      operationId,
      expectedRevision: Number.isSafeInteger(operation.expectedRevision) ? operation.expectedRevision : mount.application.revision
    });
  }

  async function applyGiven(host, mount, scenario) {
    const given = scenario.given || {};
    host.observationHarness?.reset?.();
    host.observationHarness?.enqueueQuotePlans?.(given.quotePlans || {});
    const results = [];
    for (const [index, operation] of (given.operations || []).entries()) {
      results.push(await dispatchOperation(mount, operation, `given-${scenario.id}-${index}`));
    }
    return results;
  }

  function evaluateCommonExpectation(scenario, mount, result, before) {
    const expect = scenario.expect || {};
    const afterState = mount.readState();
    const checks = {};
    if (expect.operationStatus) checks.operationStatus = result?.status === expect.operationStatus;
    if (expect.code) checks.code = result?.code === expect.code || result?.receipt?.code === expect.code || result?.receipt?.violation?.code === expect.code;
    if (expect.canonicalStateUnchanged === true) checks.canonicalStateUnchanged = stableStringify(afterState) === stableStringify(before.state);
    if (Number.isInteger(expect.itemCountDelta)) checks.itemCountDelta = afterState.contracts.length - before.state.contracts.length === expect.itemCountDelta;
    if (expect.stableKey) checks.stableKey = afterState.contracts.some((entry) => entry.id === expect.stableKey) && Boolean(itemRow(mount.root, "contract-workbench.items", expect.stableKey));
    if (expect.keyedItemAbsent === true) checks.keyedItemAbsent = !afterState.contracts.some((entry) => entry.id === scenario.when?.itemKey) && !itemRow(mount.root, "contract-workbench.items", scenario.when?.itemKey);
    if (expect.visibleQuantity !== undefined) {
      const row = itemRow(mount.root, "contract-workbench.items", scenario.when?.itemKey);
      checks.visibleQuantity = row?.querySelector("[data-mcel-item-field='quantity']")?.value === String(expect.visibleQuantity);
    }
    if (expect.conditionalValidationVisible === true) {
      const validation = exactNode(mount.root, "contract-workbench.validation");
      checks.conditionalValidationVisible = Boolean(validation.textContent?.trim()) && validation.textContent.includes(result?.receipt?.message || "");
    }
    if (expect.collectionMatchesDerivedState === true) {
      const declaration = mount.observation.observations.find((entry) => entry.kind === "collection");
      checks.collectionMatchesDerivedState = Boolean(declaration) && collectionMatchesView(mount, declaration);
    }
    if (expect.provisionalStateClosed === true) {
      checks.provisionalStateClosed = Object.keys(mount.readProvisionalState().quoteProgress || {}).length === 0;
    }
    if (Number.isInteger(expect.canonicalItemCount)) {
      checks.canonicalItemCount = afterState.contracts.length === expect.canonicalItemCount;
    }
    if (expect.collectionEmpty === true) {
      checks.collectionEmpty = collectionSnapshot(mount).length === 0;
    }
    if (expect.emptyStateVisible === true) {
      const emptyState = exactNode(mount.root, "contract-workbench.empty-state");
      checks.emptyStateVisible = Boolean(emptyState.textContent?.trim());
    }
    if (Number.isInteger(expect.revisionDelta)) {
      checks.revisionDelta = mount.application.revision - before.revision === expect.revisionDelta;
    }
    return checks;
  }

  async function runStandardScenario(host, scenario, sequence) {
    const mount = await host.createIsolatedMount({instanceId: `browser-${sequence}`});
    try {
      await applyGiven(host, mount, scenario);
      const before = {
        revision: mount.application.revision,
        state: clonePlain(mount.readState()),
        local: clonePlain(mount.readLocalState()),
        provisional: clonePlain(mount.readProvisionalState())
      };
      const when = scenario.when || {};
      let result = null;
      const observations = {};

      if (when.localState) {
        mount.updateLocalState(clonePlain(when.localState));
        result = {status: "observed", ok: true, code: "APPLICATION_LOCAL_STATE_UPDATED"};
      } else if (safeString(when.intentId) === "request-quote") {
        const promise = mount.dispatch("request-quote", operationPayload(when), {
          operationId: `${scenario.id}:quote`,
          expectedRevision: mount.application.revision
        });
        const progress = await waitFor(() => {
          const value = mount.readProvisionalState().quoteProgress?.[when.itemKey];
          return value?.status === "running" ? clonePlain(value) : null;
        }, {message: `${scenario.id} did not expose provisional progress.`});
        const row = itemRow(mount.root, "contract-workbench.items", when.itemKey);
        observations.provisional = progress;
        observations.visibleProgress = row?.querySelector("[data-mcel-item-field='quote-status']")?.textContent || "";
        observations.revisionDuringProgress = mount.application.revision;
        result = await promise;
      } else if (safeString(when.intentId) === "cancel-quote" && scenario.given?.activeOperation) {
        const active = scenario.given.activeOperation;
        const activePromise = mount.dispatch(active.intentId, {contractId: active.itemKey}, {
          operationId: `${scenario.id}:active`,
          expectedRevision: mount.application.revision
        });
        await waitFor(() => mount.readProvisionalState().quoteProgress?.[active.itemKey]?.status === "running", {
          message: `${scenario.id} did not start its cancellable operation.`
        });
        const beforeCancelState = clonePlain(mount.readState());
        result = await mount.dispatch("cancel-quote", {contractId: when.itemKey}, {
          operationId: `${scenario.id}:cancel`,
          expectedRevision: mount.application.revision
        });
        observations.activeResult = await activePromise;
        observations.beforeCancelState = beforeCancelState;
      } else {
        const options = {
          operationId: safeString(when.reuseOperationId) || `${scenario.id}:${safeString(when.intentId)}`,
          expectedRevision: Number.isSafeInteger(when.expectedRevision) ? when.expectedRevision : mount.application.revision
        };
        result = await mount.dispatch(safeString(when.intentId), operationPayload(when), options);
      }

      const checks = evaluateCommonExpectation(scenario, mount, result, before);
      if (scenario.expect?.provisionalEventsVisibleBeforeCommit === true) {
        checks.provisionalEventsVisibleBeforeCommit = Boolean(observations.provisional) && observations.visibleProgress.startsWith("running") && observations.revisionDuringProgress === before.revision;
      }
      if (scenario.expect?.oneCanonicalCommit === true) checks.oneCanonicalCommit = mount.application.revision === before.revision + 1;
      if (scenario.expect?.canonicalStateUnchanged === true && observations.beforeCancelState) checks.canonicalStateUnchanged = stableStringify(mount.readState()) === stableStringify(observations.beforeCancelState);
      const visible = receiptVisible(mount);
      const acceptableReceipts = [result?.receipt, observations.activeResult?.receipt].filter(Boolean);
      checks.visibleReceipt = acceptableReceipts.length
        ? acceptableReceipts.some((receipt) => visible?.operationId === receipt.operationId && visible?.status === receipt.status)
        : true;
      const passed = Object.values(checks).every((value) => value === true);
      return {
        id: scenario.id,
        status: passed ? "pass" : "fail",
        passed,
        operationResult: clonePlain(result),
        checks,
        before,
        after: {
          revision: mount.application.revision,
          state: clonePlain(mount.readState()),
          local: clonePlain(mount.readLocalState()),
          provisional: clonePlain(mount.readProvisionalState()),
          collection: collectionSnapshot(mount),
          receipt: clonePlain(latestReceipt(mount))
        },
        observations
      };
    } finally {
      host.disposeIsolatedMount(mount);
    }
  }

  async function runSupersessionScenario(host, scenario, sequence) {
    const mount = await host.createIsolatedMount({instanceId: `browser-${sequence}`});
    try {
      await applyGiven(host, mount, scenario);
      const beforeRevision = mount.application.revision;
      const contractId = scenario.when.itemKey;
      const first = mount.dispatch("request-quote", {contractId}, {operationId: `${scenario.id}:older`, expectedRevision: beforeRevision});
      await waitFor(() => mount.readProvisionalState().quoteProgress?.[contractId]?.status === "running");
      const second = mount.dispatch("request-quote", {contractId}, {operationId: `${scenario.id}:latest`, expectedRevision: mount.application.revision});
      const [older, latest] = await Promise.all([first, second]);
      const checks = {
        olderOperationStatus: older.status === scenario.expect.olderOperationStatus,
        latestOperationStatus: latest.status === scenario.expect.latestOperationStatus,
        oneCanonicalCommit: mount.application.revision === beforeRevision + 1,
        latestValueCommitted: mount.readState().contracts.find((entry) => entry.id === contractId)?.quoteAmount === 150,
        provisionalStateClosed: Object.keys(mount.readProvisionalState().quoteProgress || {}).length === 0
      };
      return {
        id: scenario.id,
        status: Object.values(checks).every(Boolean) ? "pass" : "fail",
        passed: Object.values(checks).every(Boolean),
        checks,
        operationResults: [clonePlain(older), clonePlain(latest)],
        after: {revision: mount.application.revision, state: clonePlain(mount.readState()), collection: collectionSnapshot(mount)}
      };
    } finally {
      host.disposeIsolatedMount(mount);
    }
  }

  async function runParallelScenario(host, scenario, sequence) {
    const mount = await host.createIsolatedMount({instanceId: `browser-${sequence}`});
    try {
      await applyGiven(host, mount, scenario);
      const beforeRevision = mount.application.revision;
      const itemKeys = scenario.when.itemKeys || [];
      const promises = itemKeys.map((contractId, index) => mount.dispatch("request-quote", {contractId}, {
        operationId: `${scenario.id}:${index}`,
        expectedRevision: mount.application.revision
      }));
      await waitFor(() => itemKeys.every((key) => mount.readProvisionalState().quoteProgress?.[key]?.status === "running"));
      const results = await Promise.all(promises);
      const checks = {
        operationStatus: results.every((entry) => entry.status === scenario.expect.operationStatus),
        independentItemKeys: itemKeys.every((key) => mount.readState().contracts.find((entry) => entry.id === key)?.quoteStatus === "quoted"),
        canonicalCommitCount: mount.application.revision - beforeRevision === scenario.expect.canonicalCommitCount,
        provisionalStateClosed: Object.keys(mount.readProvisionalState().quoteProgress || {}).length === 0
      };
      return {
        id: scenario.id,
        status: Object.values(checks).every(Boolean) ? "pass" : "fail",
        passed: Object.values(checks).every(Boolean),
        checks,
        operationResults: clonePlain(results),
        after: {revision: mount.application.revision, state: clonePlain(mount.readState()), collection: collectionSnapshot(mount)}
      };
    } finally {
      host.disposeIsolatedMount(mount);
    }
  }

  async function runMultiInstanceScenario(host, scenario, sequence) {
    const left = await host.createIsolatedMount({instanceId: `browser-${sequence}-left`});
    const right = await host.createIsolatedMount({instanceId: `browser-${sequence}-right`});
    try {
      host.observationHarness?.reset?.();
      host.observationHarness?.enqueueQuotePlan?.("contract-1", [
        {delayMs: 0, event: {type: "quote.started", expected: 1}},
        {delayMs: 200, event: {type: "quote.received", report: {amount: 100, source: "isolation"}}}
      ]);
      left.updateLocalState({draftName: "Left only", filterText: "left"});
      await left.dispatch("add-contract", {name: "Left", quantity: 3, category: "services"}, {operationId: `${scenario.id}:left-add`});
      const active = left.dispatch("request-quote", {contractId: "contract-1"}, {operationId: `${scenario.id}:left-quote`});
      await waitFor(() => left.readProvisionalState().quoteProgress?.["contract-1"]?.status === "running");
      const leftEvidence = left.application.exportEvidence();
      const rightEvidence = right.application.exportEvidence();
      const leftRow = itemRow(left.root, "contract-workbench.items", "contract-1");
      const rightRows = collectionSnapshot(right);
      const checks = {
        isolatedCanonicalState: left.readState().contracts.length === 1 && right.readState().contracts.length === 0,
        isolatedLocalState: left.readLocalState().draftName === "Left only" && right.readLocalState().draftName === "",
        isolatedProvisionalState: Boolean(left.readProvisionalState().quoteProgress?.["contract-1"]) && Object.keys(right.readProvisionalState().quoteProgress || {}).length === 0,
        isolatedOperationLedgers: leftEvidence.scm.appliedOperationIds.length > rightEvidence.scm.appliedOperationIds.length,
        isolatedReceipts: left.readLastResult()?.operationId !== right.readLastResult()?.operationId && right.readLastResult() === null,
        isolatedRoots: left.root !== right.root && left.root.dataset.mcelInstanceRoot !== right.root.dataset.mcelInstanceRoot && Boolean(leftRow) && rightRows.length === 0
      };
      left.unmount();
      const activeResult = await active;
      checks.unmountClosedActiveOperation = ["cancelled", "superseded"].includes(activeResult.status) && Object.keys(right.readProvisionalState().quoteProgress || {}).length === 0;
      const passed = Object.values(checks).every(Boolean);
      return {
        id: scenario.id,
        status: passed ? "pass" : "fail",
        passed,
        checks,
        leftEvidence: clonePlain(leftEvidence),
        rightEvidence: clonePlain(rightEvidence),
        activeResult: clonePlain(activeResult),
        rootIds: [left.root.dataset.mcelInstanceRoot, right.root.dataset.mcelInstanceRoot]
      };
    } finally {
      host.disposeIsolatedMount(left);
      host.disposeIsolatedMount(right);
    }
  }

  function observationCoverage(host, scenarioResults) {
    const declarations = host.observation?.observations || [];
    const checks = [];
    const primary = host.mount;
    declarations.forEach((declaration) => {
      let passed = false;
      let detail = {};
      if (declaration.kind === "property") {
        const actual = exactNode(primary.root, declaration.semanticNodeId)[declaration.property];
        const expected = readPath(primary.readViewState(), declaration.compareToStatePath);
        passed = String(actual) === String(expected);
        detail = {actual, expected};
      } else if (declaration.kind === "conditional") {
        passed = scenarioResults.some((entry) => entry.checks?.conditionalValidationVisible === true || entry.checks?.keyedItemAbsent === true || entry.checks?.collectionMatchesDerivedState === true);
      } else if (declaration.kind === "collection") {
        passed = scenarioResults.some((entry) => entry.after?.collection?.length > 0) && scenarioResults.filter((entry) => entry.after?.collection).every((entry) => {
          if (!entry.after.collection.length) return true;
          return entry.after.collection.every((row) => (declaration.requireItemControls || []).every((intentId) => row.controls.includes(intentId)));
        });
      } else if (declaration.kind === "provisional") {
        passed = scenarioResults.some((entry) => entry.checks?.provisionalEventsVisibleBeforeCommit === true);
      } else if (declaration.kind === "receipt") {
        passed = scenarioResults.every((entry) => entry.checks?.visibleReceipt !== false);
      } else if (declaration.kind === "multi-instance") {
        passed = scenarioResults.some((entry) => entry.id === "contract-workbench.acceptance.multi-instance" && entry.passed === true);
      }
      checks.push({id: declaration.id, kind: declaration.kind, passed, detail});
    });
    return checks;
  }

  function surfaceConformance(host, semanticSurfacePass) {
    const root = host.mount.root;
    const surface = host.mount.surface;
    const layout = host.mount.layout;
    const rect = (element) => {
      const box = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return {
        left: box.left,
        top: box.top,
        right: box.right,
        bottom: box.bottom,
        width: box.width,
        height: box.height,
        visible: style.display !== "none" && style.visibility !== "hidden" && box.width > 0 && box.height > 0
      };
    };
    const surfaceRoots = Array.from(document.querySelectorAll("[data-mcel-surface-id]"))
      .filter((element) => element.getAttribute("data-mcel-surface-id") === surface.surfaceId);
    const rootBox = rect(root);
    const controls = Array.from(root.querySelectorAll("[data-mcel-intent-id]"));
    const controlBoxes = controls.map((element) => ({
      nodeId: element.getAttribute("data-mcel-node-id") || "",
      intentId: element.getAttribute("data-mcel-intent-id") || "",
      ...rect(element)
    }));
    const controlsUsable = controlBoxes.length > 0 && controlBoxes.every((box) => box.visible && box.width >= 44 && box.height >= 44);
    const controlsOverlap = controlBoxes.some((left, index) => controlBoxes.slice(index + 1).some((right) =>
      left.left < right.right && left.right > right.left && left.top < right.bottom && left.bottom > right.top
    ));
    const horizontalOverflow = document.documentElement.scrollWidth > innerWidth + 1 || root.scrollWidth > root.clientWidth + 1;
    const requiredRegionIds = (surface.regions || []).map((region) => region.id);
    const declaredLayoutRegions = Object.keys(layout.regions || {});
    const layoutComplete = requiredRegionIds.every((id) => declaredLayoutRegions.includes(id));
    const runtimeOwnershipPass = surfaceRoots.length === 1 && surfaceRoots[0] === root && rootBox.visible;
    const runtimeVisualFitPass = rootBox.visible && rootBox.width <= innerWidth + 1 && !horizontalOverflow && controlsUsable && !controlsOverlap;
    const layers = [
      {id: "semantic-surface", status: semanticSurfacePass ? "pass" : "fail"},
      {id: "layout-grammar", status: layoutComplete ? "pass" : "fail"},
      {id: "runtime-ownership", status: runtimeOwnershipPass ? "pass" : "fail"},
      {id: "runtime-visual-fit", status: runtimeVisualFitPass ? "pass" : "fail"},
      {id: "diagnostic-no-throw", status: "pass"}
    ];
    const failedLayerIds = layers.filter((entry) => entry.status !== "pass").map((entry) => entry.id);
    return {
      contractVersion: "mcel.app-surface-conformance.v1",
      appId: host.appId,
      surfaceId: surface.surfaceId,
      status: failedLayerIds.length ? "fail" : "pass",
      valid: failedLayerIds.length === 0,
      conformanceRequired: true,
      requiredLayerIds: layers.map((entry) => entry.id),
      requiredLayerStatuses: Object.fromEntries(layers.map((entry) => [entry.id, entry.status])),
      layers,
      failedLayerIds,
      unavailableLayerIds: [],
      measurements: {viewport: {width: innerWidth, height: innerHeight, deviceScaleFactor: window.devicePixelRatio}, root: rootBox, surfaceRootCount: surfaceRoots.length, controlBoxes, controlsUsable, controlsOverlap, horizontalOverflow, requiredRegionIds, declaredLayoutRegions}
    };
  }

  async function run(host, request = {}) {
    if (!host?.ready || !host.mount || !host.acceptance || !host.observation) {
      fail("MCEL_BROWSER_SCENARIO_HOST_INVALID", "A ready application package host with acceptance and observation contracts is required.");
    }
    if (host.observation.currentStatus !== "scenario-linked") {
      fail("MCEL_BROWSER_SCENARIO_CONTRACT_NOT_EXECUTABLE", "Observation contract is not scenario-linked.", {currentStatus: host.observation.currentStatus});
    }
    if (!Array.isArray(host.acceptance.scenarios) || !host.acceptance.scenarios.length) {
      fail("MCEL_BROWSER_SCENARIO_ACCEPTANCE_EMPTY", "Acceptance contract has no browser scenarios.");
    }
    const results = [];
    let sequence = 1;
    for (const scenario of host.acceptance.scenarios) {
      let result;
      if (scenario.when?.mountInstances) result = await runMultiInstanceScenario(host, scenario, sequence);
      else if (scenario.when?.overlap) result = await runSupersessionScenario(host, scenario, sequence);
      else if (Array.isArray(scenario.when?.itemKeys)) result = await runParallelScenario(host, scenario, sequence);
      else result = await runStandardScenario(host, scenario, sequence);
      results.push(result);
      sequence += 1;
    }
    const coverage = observationCoverage(host, results);
    const allScenariosPass = results.every((entry) => entry.passed === true);
    const allObservationsPass = coverage.every((entry) => entry.passed === true);
    const surface = surfaceConformance(host, allScenariosPass && allObservationsPass);
    const capturedAt = safeString(request.capturedAt) || new Date().toISOString();
    const report = {
      schema: "mcel.application-browser-scenario-observation.v1",
      status: allScenariosPass && allObservationsPass && surface.valid ? "pass" : "fail",
      ok: allScenariosPass && allObservationsPass && surface.valid,
      capturedAt,
      appId: host.appId,
      repositoryFingerprint: safeString(request.repositoryFingerprint),
      packageFingerprint: host.record.fingerprint,
      runtimeProjectionFingerprint: host.record.runtimeProjection.fingerprint,
      catalogFingerprint: host.manifest.source.catalogFingerprint,
      surfaceId: host.mount.surface.surfaceId,
      operationId: `${host.appId}.browser-scenario-suite`,
      intentId: "browser-scenario-suite",
      scenarioCount: results.length,
      passedScenarioCount: results.filter((entry) => entry.passed).length,
      failedScenarioCount: results.filter((entry) => !entry.passed).length,
      scenarioResults: results,
      observationCoverage: coverage,
      comparison: {
        stateMatches: allScenariosPass,
        receiptMatches: results.every((entry) => entry.checks?.visibleReceipt !== false),
        surfaceMatches: allObservationsPass
      },
      canonicalState: clonePlain(host.mount.readState()),
      canonicalStateFingerprint: stableHash(host.mount.readState()),
      multiInstanceProof: clonePlain(results.find((entry) => entry.id === "contract-workbench.acceptance.multi-instance") || null),
      browser: clonePlain(request.browser || null),
      viewport: clonePlain(request.viewport || null)
    };
    return Object.freeze({
      operationResult: {
        kind: "mcel-application-browser-scenario-result",
        appId: host.appId,
        operationId: report.operationId,
        intentId: report.intentId,
        status: report.status,
        ok: report.ok,
        scenarioCount: report.scenarioCount,
        passedScenarioCount: report.passedScenarioCount,
        failedScenarioCount: report.failedScenarioCount
      },
      observation: Object.freeze({...report, observationFingerprint: stableHash(report)}),
      surfaceConformance: Object.freeze(surface)
    });
  }

  return Object.freeze({CONTRACT_VERSION, run});
})();

if (typeof window !== "undefined") window.McelApplicationBrowserScenarioRunner = McelApplicationBrowserScenarioRunner;
if (typeof module !== "undefined" && module.exports) module.exports = McelApplicationBrowserScenarioRunner;
