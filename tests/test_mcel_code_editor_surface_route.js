"use strict";

// Focused route-lifecycle regression for the unchanged surface diagnosis contract.
// Run: node tests/test_mcel_code_editor_surface_route.js
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "main_computer", "web", "applications", "scripts", "mcel-code-editor-surface-status.js"), "utf8");
const checks = [];
const badge = {
  dataset: {},
  attributes: {},
  innerHTML: "",
  setAttribute(name, value) { this.attributes[name] = String(value); }
};
const body = {dataset: {activeApp: "desktop"}};
const document = {
  readyState: "complete",
  body,
  querySelector(selector) {
    return selector === "#code-editor-mcel-surface-status" ? badge : null;
  }
};
let routeObservers = [];
let frames = [];
let intervals = [];
let diagnosisCount = 0;
let hostWidth = 0;
let forceFailure = false;
const sandbox = {
  console,
  document,
  setInterval(callback, milliseconds) {
    assert.equal(milliseconds, 30000);
    const item = {callback, milliseconds, active: true};
    intervals.push(item);
    return item;
  },
  clearInterval(item) { item.active = false; },
  requestAnimationFrame(callback) { frames.push(callback); },
  MutationObserver: class {
    constructor(callback) { this.callback = callback; this.connected = false; routeObservers.push(this); }
    observe(target, options) {
      assert.equal(target, body);
      assert.deepEqual(Array.from(options.attributeFilter), ["data-active-app"]);
      this.connected = true;
    }
    disconnect() { this.connected = false; }
  },
  McelSelfDiagnosis: {
    diagnose() {
      diagnosisCount++;
      const valid = Boolean(hostWidth) && !forceFailure;
      return {
        verdict: valid ? "pass" : "fail",
        counts: {errors: valid ? 0 : 1},
        appSurfaceConformance: {
          status: valid ? "pass" : "fail",
          valid,
          layers: [{id: "runtime-ownership", status: valid ? "pass" : "fail"}]
        },
        mcelSurfacePathway: {
          status: valid ? "pass" : "fail",
          valid,
          semanticRidgesPresent: true,
          surfaceIrBuildable: valid,
          surfaceIrValid: valid,
          layoutGrammarPresent: true,
          layoutGrammarValid: valid,
          extractable: valid,
          roundTripStatus: valid ? "pass" : "fail",
          counts: {errors: valid ? 0 : 1}
        },
        measurement: {hostWidth}
      };
    }
  }
};
sandbox.window = sandbox;
vm.runInNewContext(source, sandbox, {filename: "mcel-code-editor-surface-status.js"});
const api = sandbox.McelCodeEditorSurfaceStatus;
const state = () => badge.dataset.mcelSurfaceStatusState;
function switchRoute(name) {
  body.dataset.activeApp = name;
  for (const observer of routeObservers) {
    if (observer.connected) observer.callback([{type: "attributes", attributeName: "data-active-app"}]);
  }
}
function paint() {
  const queued = frames;
  frames = [];
  queued.forEach(callback => callback());
}
function test(name, action) { action(); checks.push(name); }

test("inactive desktop mounts as PENDING, without a hidden-surface diagnosis", () => {
  assert.equal(state(), "pending");
  assert.equal(diagnosisCount, 0);
  assert.equal(badge.__mcelSurfaceStatusReport, null);
  assert.match(badge.attributes.title, /not the active application/);
});
test("opening Code Editor schedules diagnosis after route layout completes", () => {
  switchRoute("code-editor");
  assert.equal(state(), "pending");
  assert.equal(diagnosisCount, 0);
  hostWidth = 646;  // route handler makes the formerly hidden editor visible
  paint();
  assert.equal(state(), "pass");
  assert.equal(diagnosisCount, 1);
  assert.equal(badge.__mcelSurfaceStatusReport.measurement.hostWidth, 646);
});
test("periodic fallback still diagnoses the active editor", () => {
  intervals.find(x => x.active).callback();
  assert.equal(diagnosisCount, 2);
  assert.equal(state(), "pass");
});
test("leaving Code Editor resets the badge to PENDING without diagnosing", () => {
  switchRoute("desktop");
  assert.equal(state(), "pending");
  assert.equal(badge.__mcelSurfaceStatusReport, null);
  const prior = diagnosisCount;
  intervals.find(x => x.active).callback();
  assert.equal(diagnosisCount, prior);
  assert.equal(state(), "pending");
});
test("actual post-activation surface violation remains FAIL", () => {
  hostWidth = 0;
  forceFailure = true;
  switchRoute("code-editor");
  paint();
  assert.equal(state(), "fail");
  assert.equal(badge.__mcelSurfaceStatusReport.appSurfaceConformance.status, "fail");
});
test("restoring a usable surface recovers at the next normal diagnosis", () => {
  hostWidth = 646;
  forceFailure = false;
  intervals.find(x => x.active).callback();
  assert.equal(state(), "pass");
});
test("route changes before scheduled frame cannot replay obsolete diagnosis", () => {
  const prior = diagnosisCount;
  switchRoute("desktop");
  switchRoute("code-editor");
  switchRoute("desktop");
  paint();
  assert.equal(diagnosisCount, prior);
  assert.equal(state(), "pending");
});
test("remount replaces route observer and timer without duplicates", () => {
  const prior = diagnosisCount;
  api.mount();
  assert.equal(state(), "pending");
  assert.equal(diagnosisCount, prior);
  assert.equal(routeObservers.filter(x => x.connected).length, 1);
  assert.equal(intervals.filter(x => x.active).length, 1);
  switchRoute("code-editor");
  paint();
  assert.equal(diagnosisCount, prior + 1);
  assert.equal(state(), "pass");
});
console.log(JSON.stringify({ok: true, checks}));
