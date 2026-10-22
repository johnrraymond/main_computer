"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const script = fs.readFileSync(path.join(__dirname,
  "../main_computer/web/applications/scripts/mcel-diagnostics-counter-widget.js"), "utf8");

function harness() {
  let onMutations = null;
  const pending = [];
  const doc = {
    readyState: "loading", documentElement: {},
    querySelector() { return null; },
    addEventListener(event, fn) { if (event === "DOMContentLoaded") this.boot = fn; }
  };
  const context = {
    document: doc, Date, performance: {timeOrigin: Date.now()},
    window: {
      setTimeout(fn) { pending.push(fn); return pending.length; },
      clearTimeout() {}
    },
    console,
    MutationObserver: class {
      constructor(fn) { onMutations = fn; }
      observe() {}
    }
  };
  vm.runInNewContext(script, context, {filename: "mcel-diagnostics-counter-widget.js"});
  doc.boot();
  assert.equal(typeof onMutations, "function");
  const privateApi = context.window.MCELDiagnosticsCounterWidget._private;
  return {
    deliver(records) { onMutations(records); },
    findings() { return privateApi.runtimeInstabilityFindings(); },
    api: privateApi,
    pending
  };
}

const list = {nodeType: 1, tagName: "DIV", id: "machine-activity-events"};
function card(id) {
  return {
    nodeType: 1, tagName: "ARTICLE", className: "machine-activity-event",
    ...(id === undefined ? {} : {__activityEventId: id}),
    matches(selector) { return selector === "article.machine-activity-event"; },
    closest() { return null; },
    querySelectorAll() { return Array(8).fill(null); },
    getAttribute() { return null; }
  };
}
function layoutPreview() {
  return {
    nodeType: 1, tagName: "SECTION", id: "layout",
    matches(selector) { return selector === "[data-mcel-layout-preview]"; },
    closest() { return null; },
    getAttribute(name) {
      return name === "data-mc-component-id" ? "code-editor.studio.shell" : null;
    }
  };
}
function delivery(added, removed, target = list) {
  return {type: "childList", target, addedNodes: added, removedNodes: removed};
}
function severities(h) {
  return h.findings().map(x => x.severity).sort().join(",");
}

// The observed 78/78 live-feed turnover is 78 distinct new IDs, not churn.
{
  const h = harness();
  const old = Array.from({length:78}, (_, i) => card(`old-${i}`));
  const fresh = Array.from({length:78}, (_, i) => card(`new-${i}`));
  h.deliver([delivery(fresh, old)]);
  assert.equal(severities(h), "", "legitimate 78-card feed rollover isn't a warning");
  assert.equal(h.pending.length, 0, "legitimate turnover must not trigger a diagnostic refresh");
}

// Moving DOM nodes, whether once or repeatedly, doesn't recreate cards.
{
  const h = harness();
  const nodes = Array.from({length:12}, (_, i) => card(`event-${i}`));
  h.deliver([delivery(nodes, nodes)]);
  assert.equal(severities(h), "", "a reorder is not a replacement");
  for (const node of nodes) h.deliver([delivery([node], [node])]);
  assert.equal(severities(h), "", "repeated moves never become a warning");
}

// Recreating DOM nodes for the same logical IDs is genuine structural churn.
{
  const h = harness();
  const makePairs = (start, count) => {
    const ids = Array.from({length:count}, (_, i) => `event-${start+i}`);
    h.deliver([delivery(ids.map(card), ids.map(card))]);
  };
  makePairs(0, 6);
  assert.equal(severities(h), "good", "six same-ID recreations get a caveat");
  makePairs(6, 6);
  assert.equal(severities(h), "good,warning", "twelve same-ID recreations get a warning");
  assert.equal(h.findings().find(f => f.severity === "warning").issueId,
    "runtime-structural-churn:article:machine-activity-event");
}

// 78 legitimate replacements mixed with one same-ID recreation: count one.
{
  const h = harness();
  const old = Array.from({length:78}, (_, i) => card(`old-${i}`));
  const fresh = Array.from({length:78}, (_, i) => card(`new-${i}`));
  h.deliver([delivery([...fresh, card("same")], [...old, card("same")])]);
  assert.equal(severities(h), "", "different IDs cannot amplify a same-ID recreation");
  for (let i = 0; i < 5; i++) h.deliver([delivery([card("same")], [card("same")])]);
  assert.equal(severities(h), "good");
}

// Without an event ID, the existing generic conservative rule still applies.
{
  const h = harness();
  for (let i = 0; i < 12; i++) h.deliver([delivery([card()], [card()])]);
  assert.equal(severities(h), "good,warning", "missing IDs retain generic detection");
}

// Other structural detectors are unchanged; neither list-ID filtering nor
// Activity Dock move handling may alter layout-preview or attribute detection.
{
  const h = harness();
  for (let i = 0; i < 12; i++) h.deliver([delivery([layoutPreview()], [layoutPreview()])]);
  assert.equal(severities(h), "good,warning");
  assert.equal(h.findings().find(f => f.severity === "warning").issueId,
    "runtime-structural-churn:layout-preview:code-editor.studio.shell");
}
{
  const h = harness();
  const node = {
    nodeType: 1, tagName: "SECTION", id: "other-component",
    closest() { return null; },
    getAttribute(name) { return name === "data-mode" ? "authoring" : null; }
  };
  for (let i = 0; i < 12; i++) h.deliver([{
    type: "attributes", target: node, attributeName: "data-mode", oldValue: "authoring"
  }]);
  assert.equal(severities(h), "good,warning", "redundant-attribute detector unchanged");
}

console.log("PASS E/W/G Activity Dock identity: 78-ID rollover, moves, six/12 same-ID replacements, mixed batches, missing IDs, layout previews, redundant attributes");
