"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../main_computer/web/applications/scripts/mcel-diagnostics-counter-widget.js"), "utf8");
let mutationCallback;
const callbacks = [];
class Monitor {
  constructor(cb) { mutationCallback = cb; }
  observe() {}
}
const context = {
  window: {setTimeout(fn) {callbacks.push(fn); return 1;}, clearTimeout() {}},
  document: {readyState: "loading", documentElement: {},
    querySelector() {return null;}, addEventListener(event, cb) {this.boot = cb;}},
  MutationObserver: Monitor,
  Date,
  performance: {timeOrigin: Date.now() - 1000},
  console,
  setTimeout(fn) {callbacks.push(fn); return 1;}
};
vm.runInNewContext(source, context, {filename: "mcel-diagnostics-counter-widget.js"});
const api = context.window.MCELDiagnosticsCounterWidget;
const p = api._private;
context.document.boot();
assert.equal(typeof mutationCallback, "function");

const componentNode = () => ({
  nodeType: 1, tagName: "ARTICLE", id: "activity",
  closest() {return null;}, getAttribute() {return null;},
  querySelectorAll() {return Array(8).fill({});}
});
function cycles(n) {
  for (let i=0;i<n;i++) mutationCallback([{type:"childList", addedNodes:[componentNode()], removedNodes:[componentNode()]}]);
}
const t0 = new Date().toISOString();
const hist = p.createIssueHistory(new Date(Date.now()-1000).toISOString());
const report = (findings, verdict="pass") => ({
  appId:"code-editor", contractId:"test", verdict,
  timestamp:new Date().toISOString(), findings, summary:{}, measurements:{}
});
const update = (r, at=r.timestamp) => {p.updateIssueHistory(hist, r, at); return p.compactPayload(r, p.summarizeReport(r,hist), hist);};

cycles(6);
let early = p.runtimeInstabilityFindings();
assert.equal(early.length, 1);
assert.equal(early[0].severity, "good");
let payload = update(report(early));
assert.equal(payload.verdict, "pass");
assert.equal(payload.counts.errors, 0);
assert.equal(payload.counts.warnings, 0);
assert.equal(payload.current.greenCaveats.length, 1);
assert.equal(payload.history.counts.greenCaveatsSeen, 1);
const greenTime = payload.history.greenCaveats[0].firstSeen;
const greenIdentity = payload.history.greenCaveats[0].issueId;

// Existing structural threshold creates a warning stage, not a reclassification.
cycles(6);
let both = p.runtimeInstabilityFindings();
assert.deepEqual([...both].map(f=>f.severity).sort(),["good","warning"]);
payload = update(report(both));
assert.equal(payload.counts.warnings,1);
assert.equal(payload.counts.errors,0);
assert.equal(payload.history.greenCaveats[0].firstSeen, greenTime);
assert.equal(payload.history.warnings[0].issueId, greenIdentity);
assert.equal(payload.history.counts.warningsSeen,1);
const frozenWarningTime = payload.history.warnings[0].firstSeen;
const frozenWarningDetail = payload.history.warnings[0].finding;
const sig1 = p.diagnosticEventSignature(payload);

// Polling 5 minutes later must not promote the old warning, mutate its
// original evidence or generate a new diagnostic event for the same snapshot.
const later = "2026-10-20T22:40:00.000Z";
const same = {...report(both), timestamp:later};
payload = update(same,later);
assert.equal(payload.verdict,"pass");
assert.equal(payload.counts.errors,0);
assert.equal(payload.counts.warnings,1);
assert.equal(payload.history.warnings[0].firstSeen,frozenWarningTime);
assert.equal(payload.history.warnings[0].finding,frozenWarningDetail);
assert.equal(payload.history.warnings[0].seenCount,1);
assert.equal(p.diagnosticEventSignature(payload),sig1);
assert.equal(p.shouldEmitDiagnosticEvent({__mcelDiagnosticsLastEventSignature:sig1,__mcelDiagnosticsLastEventAt:0},payload,Date.now()+3600000),false);

// A real functional failure of the SAME issue creates an independent red
// stage; the earlier green and yellow histories stay intact.
const critical = {severity:"critical",code:"runtime-structural-churn",issueId:greenIdentity,
  observationId:"observed-functional-failure",observedAt:new Date().toISOString(),
  finding:"Required authoring interaction is now blocked."};
payload = update(report([...both,critical],"fail"));
assert.equal(payload.verdict,"fail");
assert.equal(payload.counts.errors,1);
assert.equal(payload.counts.warnings,1);
assert.equal(payload.history.counts.greenCaveatsSeen,1);
assert.equal(payload.history.counts.warningsSeen,1);
assert.equal(payload.history.counts.errorsSeen,1);
assert.deepEqual([...payload.history.issues].map(x=>x.normalizedSeverity).sort(),["error","good","warning"]);
assert.equal(payload.history.greenCaveats[0].firstSeen,greenTime);

// Clear all findings; the present-state verdict recovers, but E/W stays
// latched and each historical stage remains available for copying.
const clearedAt="2026-10-20T22:45:00.000Z";
payload = update({...report([]),timestamp:clearedAt},clearedAt);
assert.equal(payload.verdict,"pass");
assert.equal(payload.counts.errors,1);
assert.equal(payload.counts.warnings,1);
assert.equal(payload.current.counts.errors,0);
assert.equal(payload.current.counts.warnings,0);
assert.equal(payload.history.issues.length,3);
assert.ok(payload.history.issues.every(x=>!x.currentlyActive && x.clearedAt===clearedAt));

// Reoccurrence is a new episode rather than an overwrite of the old stage.
const renewed = {...both.find(x=>x.severity==="warning"),observationId:"new-episode",observedAt:"2026-10-20T22:46:00.000Z"};
payload = update({...report([renewed]),timestamp:"2026-10-20T22:46:00.000Z"});
assert.equal(payload.history.warnings.length,2);
assert.equal(payload.history.warnings[0].firstSeen,frozenWarningTime);
assert.equal(payload.history.warnings[1].observationId,"new-episode");
assert.equal(payload.history.warnings.filter(x=>x.currentlyActive).length,1);
assert.equal(payload.counts.warnings,1, "repeated episodes of one issue do not inflate W");
assert.equal(payload.current.counts.warnings,1);

// The structural observer also recognizes a new episode after a quiet interval,
// even if the 30-second periodic refresh did not run in between.
const firstWarningObservationId = both.find(x => x.severity === "warning").observationId;
for (const cb of callbacks.splice(0)) cb();
const forwardedNow = Date.now() + 25000;
context.Date = class FutureDate extends Date {
  constructor(...args) { super(...(args.length ? args : [forwardedNow])); }
  static now() { return forwardedNow; }
};
cycles(12);
const recurring = p.runtimeInstabilityFindings();
const recurringWarning = recurring.find(x => x.severity === "warning");
assert.ok(recurringWarning);
assert.notEqual(recurringWarning.observationId, firstWarningObservationId);
assert.ok(callbacks.length > 0, "new episode schedules a fresh publication");

// An actual critical diagnosis during startup never becomes a mere warning.
const startupHist = p.createIssueHistory("2026-10-20T22:00:00.000Z");
const startup = {verdict:"fail",timestamp:"2026-10-20T22:00:01.000Z",
  findings:[{severity:"critical",code:"editor-missing",finding:"Monaco unavailable"}]};
p.updateIssueHistory(startupHist,startup,startup.timestamp);
const startupCopy = p.compactPayload(startup,p.summarizeReport(startup,startupHist),startupHist);
assert.equal(startupCopy.counts.errors,1);
assert.equal(startupCopy.counts.warnings,0);
assert.equal(startupCopy.verdict,"fail");
assert.equal(startupCopy.history.errors[0].lifecyclePhase,"startup");
assert.equal(startupCopy.history.errors[0].countedAsStartupWarning,false);

console.log("PASS E/W/G sticky history: caveat, independent warning, functional error, latched E/W, current verdict, no clock promotion, dedup, resolution, recurrence, startup safety");
