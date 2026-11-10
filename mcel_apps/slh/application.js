"use strict";

// SLH's first semantic application. It declares observations and user actions,
// not HTML, widget hierarchy, CSS geometry, or implicit administrator authority.
// See pretty_docs/secure-local-hub-administrator-manual.md for user intentions.
const mcel = require("@mcel/app");

module.exports = mcel.defineApp(
  {id: "slh", title: "Secure Local Hub", semanticVersion: "1"},
  dsl => {
    const text = dsl.field.text();
    const acknowledgement = dsl.field.boolean();
    const healthRow = dsl.field.record({id: text, subject: text, state: text, source: text});
    const workerRow = dsl.field.record({id: text, label: text, state: text, models: text,
      assignment: text, marketplace: text, source: text});
    const overviewShape = dsl.field.record({
      serviceState: text, initializationState: text, organizationState: text,
      administratorEnrollmentState: text, organizationEstablishedState: text,
      organizationEndpoint: text, organizationNetworkState: text,
      organizationTlsState: text, localCredentialState: text, managementEndpoint: text,
      nextStep: text, networkGuidance: text,
      observationState: text, observedAt: text, source: text, explanation: text
    });
    const workerSummaryShape = dsl.field.record({
      observationState: text, explanation: text, observedAt: text, source: text,
      enrolledCount: text, connectedCount: text
    });
    const healthRows = dsl.field.list(healthRow, {key: "id"});
    const workerRows = dsl.field.list(workerRow, {key: "id"});
    const overview = dsl.state.derived("overview", overviewShape);
    const checks = dsl.state.derived("health-checks", healthRows);
    const workers = dsl.state.derived("workers", workerRows);
    const workerSummary = dsl.state.derived("worker-summary", workerSummaryShape);
    const selectedWorker = dsl.state.rendererLocal("selected-worker", text, {initial: ""});
    // Identities are ordinary LMS-SLH keys. Their selection grants no organization role.
    const identityRow = dsl.field.record({deviceId:text,label:text,fingerprint:text,defaultStatus:text});
    const identityRows = dsl.field.list(identityRow,{key:"deviceId"});
    const identitySummaryShape = dsl.field.record({state:text,defaultDeviceId:text,explanation:text});
    const identities = dsl.state.derived("local-identities",identityRows);
    const identitySummary = dsl.state.derived("local-identity-status",identitySummaryShape);
    const adminDevice = dsl.state.rendererLocal("selected-local-identity", text, {initial: ""});
    const mutationShape = dsl.field.record({outcome: text, message: text, deviceId: text});
    const mutationResult = dsl.state.derived("worker-operation-result", mutationShape);
    const setupShape = dsl.field.record({outcome: text, message: text, deviceId: text});
    const setupResult = dsl.state.derived("first-run-result", setupShape);
    const view = dsl.state.rendererLocal("view", text.oneOf(["overview", "worker-bank"]), {initial: "overview"});

    // Only the fixed, bounded same-origin observation adapter is admitted.
    // Device enrollment, capacity policy writes and SEH activation are NOT
    // declared, so they cannot be invoked through this generated application.
    const observer = dsl.capability.external("slh-observer", {
      risk: "external-read",
      description: "Read already-running SLH loopback service state without starting or modifying it"
    });
    observer.operation("overview", "observeSlhOverview", {result: overviewShape});
    observer.operation("health", "observeSlhHealth", {result: healthRows});
    observer.operation("workers", "observeSlhWorkers", {result: workerRows});
    observer.operation("worker-summary", "observeSlhWorkerSummary", {result: workerSummaryShape});
    observer.operation("identities", "observeSlhIdentities", {result: identityRows});
    observer.operation("identity-status", "observeSlhIdentityStatus", {result: identitySummaryShape});

    const refreshOverview = dsl.intent.capabilityRequest("refresh-overview", observer,
      {operation: "overview", resultTo: overview});
    const refreshHealth = dsl.intent.capabilityRequest("refresh-health", observer,
      {operation: "health", resultTo: checks});
    const refreshWorkers = dsl.intent.capabilityRequest("refresh-workers", observer,
      {operation: "workers", resultTo: workers});
    const refreshWorkerSummary = dsl.intent.capabilityRequest("refresh-worker-summary", observer,
      {operation: "worker-summary", resultTo: workerSummary});
    const refreshIdentities = dsl.intent.capabilityRequest("refresh-identities", observer,
      {operation:"identities",resultTo:identities});
    const refreshIdentityStatus = dsl.intent.capabilityRequest("refresh-identity-status", observer,
      {operation:"identity-status",resultTo:identitySummary});
    const chooseIdentity = dsl.intent.selectFromCollection("choose-identity",identities,adminDevice);
    const admin = dsl.capability.external("slh-worker-admin", {
      risk: "consequential", description: "Authenticated, audited Worker identity management"
    });
    admin.operation("rename", "renameSlhWorker", {inputs: {label: text, deviceId: text},
      result: mutationShape, risk: "consequential"});
    // First-run capabilities are explicitly consequential; the viewport never
    // simulates success or writes the SLH authority directly.
    const setup = dsl.capability.external("slh-first-run", {
      risk: "consequential", description: "Local operator-authorized SLH startup, bootstrap and organization activation"
    });
    setup.operation("start", "startSlhLocal", {result: setupShape, risk: "consequential"});
    setup.operation("bootstrap", "bootstrapSlhAdministrator", {
      inputs: {adminId: text, adminName: text, deviceId: text, acknowledgeP256Only: acknowledgement},
      result: setupShape, risk: "consequential"
    });
    setup.operation("configure", "configureSlhOrganization", {
      inputs: {adminDeviceId: text, host: text, apiPort: text, workerPort: text},
      result: setupShape, risk: "consequential"
    });
    setup.operation("activate", "activateSlhOrganization", {
      inputs: {adminDeviceId: text}, result: setupShape, risk: "consequential"
    });
    setup.operation("logout", "logoutSlhAdministrator", {
      result: setupShape, risk: "consequential"
    });
    setup.operation("login", "loginSlhAdministrator", {
      inputs: {adminDeviceId: text}, result: setupShape, risk: "consequential"
    });
    const startLocal = dsl.intent.capabilityRequest("start-local", setup,
      {operation: "start", resultTo: setupResult});
    const bootstrap = dsl.intent.capabilityRequest("bootstrap-administrator", setup,
      {operation: "bootstrap", inputs: {adminId: text, adminName: text, acknowledgeP256Only: acknowledgement},
        arguments: {deviceId:adminDevice}, resultTo: setupResult});
    const configure = dsl.intent.capabilityRequest("configure-organization", setup,
      {operation: "configure", inputs: {host: text, apiPort: text, workerPort: text},
       arguments: {adminDeviceId:adminDevice}, resultTo: setupResult});
    const activate = dsl.intent.capabilityRequest("activate-organization", setup,
      {operation: "activate", arguments: {adminDeviceId:adminDevice}, resultTo: setupResult});
    const login = dsl.intent.capabilityRequest("login-administrator", setup,
      {operation: "login", arguments: {adminDeviceId:adminDevice},
        confirmationRequired: false, resultTo: setupResult});
    const logout = dsl.intent.capabilityRequest("logout-administrator", setup,
      {operation: "logout", resultTo: setupResult});
    const chooseWorker = dsl.intent.selectFromCollection("choose-worker", workers, selectedWorker);
    const renameWorker = dsl.intent.capabilityRequest("rename-worker", admin, {
      operation: "rename", inputs: {label: text}, arguments: {deviceId: selectedWorker},
      resultTo: mutationResult
    });
    const selectView = dsl.intent.selectView("select-view", view);
    const isView = equals => ({state: view, equals});
    // Backend observations, not click history, establish the next valid step.
    const observed = (path, equals, fresh=false) =>
      ({state: overview, path, equals, ...(fresh?{maxAgeSeconds:60}:{})});
    const whenAll = (...clauses) => ({all:clauses});
    const currentLocal = observed("serviceState","RUNNING",true);
    const initialized = observed("initializationState","INITIALIZED");
    const orgRunning = observed("organizationState","running");
    const administratorEnrolled = observed("administratorEnrollmentState","ENROLLED");
    const established = observed("organizationEstablishedState","ESTABLISHED");
    const localTls = observed("organizationTlsState","VERIFIED_LOCAL");
    const authenticated = {state:workerSummary,path:"observationState",equals:"AUTHENTICATED"};
    const authRequired = {state:workerSummary,path:"observationState",equals:"AUTH_REQUIRED"};
    const haveIdentity = {state:identitySummary,path:"state",equals:"AVAILABLE"};
    // Login Administrator is the single entry for both an unestablished local
    // organization and an already enrolled one. The backend, not this UI,
    // decides whether initial enrollment is permitted.
    const canLogin = whenAll(currentLocal,authRequired,haveIdentity);
    const canOperate = whenAll(currentLocal,initialized,orgRunning,established,
      administratorEnrolled,localTls,authenticated);
    const surface = dsl.surface.define("slh", {purpose: "monitor", root:
      dsl.surface.region("semantic-source", {children: [
        dsl.surface.action("select-view", {intent: selectView}),
        dsl.surface.action("refresh-overview", {intent: refreshOverview}),
        dsl.surface.action("refresh-identities", {intent:refreshIdentities}),
        dsl.surface.text("local-identity-status",{value:identitySummary,when:isView("overview")}),
        dsl.surface.collection("available-local-identities",{
          key:"deviceId",fields:["label","deviceId","defaultStatus"],state:identities,
          select:chooseIdentity,when:isView("overview")}),
        dsl.surface.text("selected-local-identity",{value:adminDevice,when:isView("overview")}),
        dsl.surface.action("start-local", {intent: startLocal,
          when: whenAll(observed("serviceState","UNREACHABLE",true),
            observed("observationState","UNAVAILABLE"))}),
        dsl.surface.form("configure-organization", {fields: {host: text,
          apiPort: text, workerPort: text}, defaults: {host: "127.0.0.1", apiPort: "8899",
          workerPort: "8898"}, intent: configure,
          when: whenAll(isView("overview"),currentLocal,haveIdentity,initialized,established,
            administratorEnrolled,observed("organizationState","not_configured"))}),
        dsl.surface.action("activate-organization", {intent: activate,
          when: whenAll(isView("overview"),currentLocal,haveIdentity,initialized,established,
            administratorEnrolled,observed("organizationState","disabled"))}),
        dsl.surface.action("login-administrator", {intent: login,when:canLogin}),
        dsl.surface.action("logout-administrator", {intent: logout,when:authenticated}),
        dsl.surface.text("first-run-result", {value: setupResult, when: isView("overview")}),
        dsl.surface.action("refresh-health", {intent: refreshHealth}),
        dsl.surface.text("service-status", {value: overview, when: isView("overview")}),
        dsl.surface.collection("health-checks", {
          key: "id", fields: ["subject", "state", "source"], state: checks, when: isView("overview")
        }),
        dsl.surface.action("refresh-workers", {intent: refreshWorkers}),
        dsl.surface.action("refresh-worker-summary", {intent: refreshWorkerSummary}),
        dsl.surface.text("worker-inventory-availability", {value: workerSummary, when: isView("worker-bank")}),
        dsl.surface.collection("worker-bank", {
          key: "id", fields: ["label", "state", "models", "assignment", "marketplace"],
          state: workers, select: chooseWorker, when: isView("worker-bank")
        }),
        dsl.surface.text("selected-worker", {value: selectedWorker, when: isView("worker-bank")}),
        dsl.surface.form("rename-worker", {fields: {label: text}, intent: renameWorker,
          when: whenAll(isView("worker-bank"),canOperate)}),
        dsl.surface.text("worker-operation-result", {value: mutationResult, when: isView("worker-bank")}),
        dsl.surface.receipt("observation-evidence")
      ]})
    });
    return {states: [overview, checks, workers, workerSummary, identities, identitySummary, view, selectedWorker, adminDevice, mutationResult, setupResult],
      capabilities: [observer, admin, setup],
      intents: [refreshOverview, refreshHealth, refreshWorkers, refreshWorkerSummary,
        refreshIdentities,refreshIdentityStatus,chooseIdentity,
        selectView, chooseWorker, renameWorker, startLocal, bootstrap, configure, activate, login, logout],
      surfaces: [surface]};
  }
);
