/* Optional, independent diagnostic integrations for the Applications host.
 * No remote scripts or telemetry are loaded automatically.
 * APIs: window.MCELDiagnosticPlugins.list/enable/disable/run/findings/configure
 */
(() => {
  "use strict";
  if (window.MCELDiagnosticPlugins) return;
  const definitions = new Map();
  const running = new Map();
  const configs = new Map();
  const findings = [];
  const MAX_FINDINGS = 300;
  const startup = {armedAt: new Date().toISOString(), states: {}, ready: null};
  const normalizeError = (error) => String(error && (error.stack || error.message) || error).slice(0, 1200);
  function emit(id, code, severity, detail, extra = {}) {
    const item = {pluginId:id, code, severity, detail:String(detail).slice(0, 2000), timestamp:new Date().toISOString(), ...extra};
    findings.push(item);
    if (findings.length > MAX_FINDINGS) findings.splice(0, findings.length - MAX_FINDINGS);
    window.dispatchEvent(new CustomEvent("mcel-diagnostic-plugin-finding", {detail:item}));
    return item;
  }
  function register(spec) {
    if (!spec || !spec.id || definitions.has(spec.id)) throw new Error("Duplicate/invalid MCEL diagnostics plugin");
    definitions.set(spec.id, Object.freeze(spec));
  }
  function definition(id) {
    const item = definitions.get(id);
    if (!item) throw new Error("Unknown diagnostic plugin: " + id);
    return item;
  }
  function context(id) { return {id, config:configs.get(id) || {}, emit:(code, severity, detail, extra) => emit(id,code,severity,detail,extra)}; }
  const pendingStarts = new Map();
  async function enableInternal(id) {
    if (pendingStarts.has(id)) return pendingStarts.get(id);
    const operation = startPlugin(id);
    pendingStarts.set(id, operation);
    try { return await operation; }
    finally { pendingStarts.delete(id); }
  }
  async function startPlugin(id) {
    const spec = definition(id);
    if (running.has(id)) return {id,status:"enabled"};
    try {
      const state = spec.start ? await spec.start(context(id)) : null;
      running.set(id, state || {stop:() => {}});
      return {id,status:"enabled"};
    } catch (error) {
      emit(id,"plugin-start-failed","warning",normalizeError(error));
      return {id,status:"unavailable",reason:normalizeError(error)};
    }
  }
  function ensureStarted() {
    // Boot begins as this script loads, before Applications runtime.
    // All asynchronous public operations join the same boot promise.
    return startup.ready || Promise.resolve();
  }
  async function enable(id) {
    await ensureStarted();
    return enableInternal(id);
  }
  async function disable(id) {
    await ensureStarted();
    definition(id);
    if (!running.has(id)) return {id,status:"disabled"};
    const state = running.get(id);
    running.delete(id);
    try { if (state && typeof state.stop === "function") await state.stop(); }
    catch (error) { emit(id,"plugin-stop-failed","warning",normalizeError(error)); }
    return {id,status:"disabled"};
  }
  async function run(id) {
    await ensureStarted();
    const spec = definition(id);
    if (!spec.run) return {id,status:"not-an-audit"};
    try { return {id,status:"complete",result:await spec.run(context(id))}; }
    catch (error) {
      emit(id,"plugin-run-failed","warning",normalizeError(error));
      return {id,status:"unavailable",reason:normalizeError(error)};
    }
  }
  function configure(id, config) {
    definition(id);
    if (running.has(id)) throw new Error("Disable plugin before reconfiguring: " + id);
    if (!config || typeof config !== "object" || Array.isArray(config)) throw new TypeError("Config must be an object");
    configs.set(id, {...config});
    return {id,status:"configured"};
  }
  function list() {
    return [...definitions.values()].map(({id,kind,description,availability}) => ({
      id,kind,description,enabled:running.has(id),
      availability:typeof availability === "function" ? availability(context(id)) : "requires-configuration"
    }));
  }
  function collectFindings() {
    // Include errors generated after the early collector was initially drained.
    return findings.concat(window.__MCELDiagnosticEarly?.events() || []);
  }
  window.MCELDiagnosticPlugins = {register,enable,disable,run,configure,list,findings:collectFindings,
    status:() => ({armedAt:startup.armedAt, states:{...startup.states},
      earlyEvents:window.__MCELDiagnosticEarly?.events().length || 0}),
    whenReady:ensureStarted,
    clear:() => {findings.length=0;window.__MCELDiagnosticEarly?.drain();}};

  register({id:"axe-core",kind:"audit",description:"Accessibility audit of current document",availability:() => window.axe?.run ? "ready" : "requires-local-axe-core-script",
    async run(ctx) {
      if (!window.axe?.run) throw new Error("axe-core not loaded; bundle its local browser script before enabling");
      const result = await window.axe.run(document, ctx.config.options || {});
      for (const violation of result.violations) ctx.emit("axe:"+violation.id,"error",violation.help,{targetCount:violation.nodes.length,impact:violation.impact});
      return {violations:result.violations.length,incomplete:result.incomplete.length,passes:result.passes.length};
    }});

  register({id:"rrweb",kind:"recorder",description:"Local DOM/session event recording (not uploaded)",availability:() => (window.rrweb?.record || window.rrwebRecord?.record) ? "ready" : "requires-local-rrweb-script",
    start(ctx) {
      const record = window.rrweb?.record || window.rrwebRecord?.record;
      if (typeof record !== "function") throw new Error("rrweb recorder not loaded");
      const events = []; const limit = Math.max(10, Math.min(2000, Number(ctx.config.maxEvents) || 500));
      const stop = record({emit(event) {events.push(event); if (events.length > limit) events.shift();}, maskAllInputs:true, ...(ctx.config.options || {})});
      if (typeof stop !== "function") throw new Error("rrweb failed to start");
      // A separate export call is opt-in; avoid leaking recorded content in generic findings.
      const state = {stop,export:() => events.slice()};
      recordingStates.set("rrweb",state);
      return {stop() {stop(); recordingStates.delete("rrweb");}};
    }});
  const recordingStates = new Map();
  window.MCELDiagnosticPlugins.exportRecording = (id) => {
    if (id !== "rrweb" || !recordingStates.has(id)) throw new Error("rrweb recorder is not active");
    return recordingStates.get(id).export();
  };

  register({id:"sentry",kind:"runtime",description:"Browser exception capture using an already-initialized Sentry SDK",availability:() => window.Sentry?.captureException ? "ready" : "requires-configured-sentry-sdk",
    start(ctx) {
      if (!window.Sentry?.captureException) throw new Error("Sentry SDK not initialized; do not transmit data without explicit configuration");
      const onError = event => ctx.emit("browser-error","error",event.message || "Browser error",{source:event.filename,line:event.lineno});
      const onRejection = event => ctx.emit("unhandled-rejection","error",normalizeError(event.reason));
      window.addEventListener("error",onError); window.addEventListener("unhandledrejection",onRejection);
      return {stop() {window.removeEventListener("error",onError);window.removeEventListener("unhandledrejection",onRejection);}};
    }});

  register({id:"chrome-performance",kind:"runtime",description:"Long animation frames or long-task reporting",availability:() => typeof PerformanceObserver !== "undefined" ? "ready" : "unsupported-browser",
    start(ctx) {
      if (typeof PerformanceObserver === "undefined") throw new Error("PerformanceObserver unavailable");
      const supported = PerformanceObserver.supportedEntryTypes || [];
      const type = supported.includes("long-animation-frame") ? "long-animation-frame" : supported.includes("longtask") ? "longtask" : null;
      if (!type) throw new Error("Long animation frames and long tasks are unavailable in this browser");
      const threshold = Math.max(50, Number(ctx.config.thresholdMs) || 200);
      const observer = new PerformanceObserver(list => {
        for (const entry of list.getEntries()) {
          if (entry.duration >= threshold) ctx.emit(type,"warning",`${entry.duration.toFixed(1)}ms`,{durationMs:entry.duration,startTime:entry.startTime});
        }
      });
      observer.observe({type,buffered:true});
      return {stop() {observer.disconnect();}};
    }});

  register({id:"webhint",kind:"external-audit",description:"Node CLI site audit, imported as evidence",availability:() => "requires-external-node-runner",
    async run(ctx) {throw new Error("webhint is a Node CLI; run it outside the page and import a JSON report");}});
  register({id:"lighthouse",kind:"external-audit",description:"Chrome browser audit, imported as evidence",availability:() => "requires-external-chrome-runner",
    async run(ctx) {throw new Error("Lighthouse requires a Chrome/Node runner; run it outside the page and import a JSON report");}});
  register({id:"clarity",kind:"external-service",description:"Optional Microsoft Clarity hosted session analytics",availability:() => window.clarity ? "ready-needs-consent" : "requires-explicit-hosted-setup",
    start(ctx) {
      if (!ctx.config.consentGranted) throw new Error("Clarity requires explicit consentGranted=true and external service setup");
      if (typeof window.clarity !== "function") throw new Error("Clarity is not installed or configured");
      return {stop() { /* Clarity does not expose a complete reliable SDK shutdown; disable requires reload. */ }};
    }});

  // Import audit results generated outside the browser. No network connections required.
  window.MCELDiagnosticPlugins.importAudit = (id, report) => {
    if (id !== "webhint" && id !== "lighthouse") throw new Error("Only external audit reports can be imported");
    if (!report || typeof report !== "object") throw new TypeError("Expected parsed JSON report");
    const ctx = context(id);
    if (id === "lighthouse") {
      if (!report.categories || !report.audits) throw new Error("Not a Lighthouse JSON report");
      for (const [key, audit] of Object.entries(report.audits)) {
        if (audit.score !== null && audit.score !== undefined && audit.score < 0.9) ctx.emit("lighthouse:"+key,"warning",audit.title || key,{score:audit.score});
      }
    } else {
      const entries = Array.isArray(report) ? report : Array.isArray(report.messages) ? report.messages : null;
      if (!entries) throw new Error("Expected webhint JSON messages array");
      for (const message of entries) ctx.emit("webhint:"+(message.hintId || "hint"),"warning",message.message || "webhint finding",{resource:message.resource});
    }
    return {id,findings:findings.filter(f => f.pluginId===id).length};
  };
  // Start local, in-page runtime engines before any Applications runtime script.
  // Audit runners are deliberately not started here: they need a complete DOM
  // or an external process. Clarity never autostarts (remote telemetry).
  for (const event of window.__MCELDiagnosticEarly?.drain() || []) {
    findings.push(event);
  }
  const earlyPlugins = ["chrome-performance", "rrweb", "sentry"];
  const jobs = earlyPlugins.map(id => {
    const availability = definition(id).availability(context(id));
    if (availability !== "ready") {
      startup.states[id] = {status:"unavailable",availability};
      return Promise.resolve();
    }
    startup.states[id] = {status:"starting"};
    return enableInternal(id).then(result => {startup.states[id] = result;});
  });
  startup.ready = Promise.all(jobs).then(() => ({armedAt:startup.armedAt, states:{...startup.states}}));
  Object.freeze(window.MCELDiagnosticPlugins);
})();
