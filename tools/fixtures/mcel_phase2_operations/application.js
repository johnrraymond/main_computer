"use strict";
const mcel = require("@mcel/app");
module.exports = mcel.defineApp({id: "phase2-operations", title: "Phase 2 Operations"}, dsl => {
  const entrySchema = dsl.field.record({id: dsl.field.text(), name: dsl.field.text()});
  const entryListSchema = dsl.field.list(entrySchema, {key: "id"});
  const entries = dsl.state.derived("entries", entryListSchema);
  const selectedEntry = dsl.state.rendererLocal("selected-entry", dsl.field.text(), {initial: ""});
  const preview = dsl.state.derived("preview", dsl.field.text());
  const disk = dsl.capability.external("disk", {risk: "external-read", description: "Governed read operations"});
  disk.operation("list", "listDirectory", {result: entryListSchema, cancellable: true});
  disk.operation("preview", "readPreview", {inputs: {entryId: dsl.field.text()}, result: dsl.field.text()});
  const list = dsl.intent.capabilityRequest("list-directory", disk, {operation: "list", resultTo: entries});
  const select = dsl.intent.selectFromCollection("select-entry", entries, selectedEntry);
  const read = dsl.intent.capabilityRequest("read-preview", disk, {operation: "preview", arguments: {entryId: selectedEntry}, resultTo: preview});
  const refresh = dsl.surface.action("refresh", {intent: list});
  const rootList = dsl.surface.collection("entry-navigation", {key: "id", fields: ["name"], state: entries});
  const files = dsl.surface.collection("entries", {key: "id", fields: ["name"], state: entries, select});
  const choose = dsl.surface.action("choose", {intent: select});
  const view = dsl.surface.action("view", {intent: read});
  const content = dsl.surface.text("preview-text", {value: preview});
  const receipt = dsl.surface.receipt("operation-receipt");
  const surface = dsl.surface.bindHtml("workspace", {bindings: {
    locations: [rootList], workspace: [files, refresh, choose],
    details: [view, content], "operation-status": [receipt]
  }});
  return {states: [entries, selectedEntry, preview], capabilities: [disk],
    intents: [list, select, read], surfaces: [surface]};
});
