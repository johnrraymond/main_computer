"use strict";
// The applications environment is an MCEL-authored semantic parent, not a
// capability-bearing application. Child apps retain their own authorities.
// Paired HTML alone authors the region hierarchy; this declares presentation.
const mcel = require("@mcel/app");

const SHELL_LAYOUT = {
  schema: "mcel.layout-tree.v1",
  root: {
    id: "applications-root", selector: ".mcel-applications-shell", role: "group",
    maxColumns: 1, gap: 0, verifyControls: false,
    children: [
      { id: "masthead", selector: ".mcel-shell-masthead", role: "region", verifyControls: false },
      { id: "shell", selector: ".mcel-shell-content", role: "group", maxColumns: 2,
        gap: 0, verifyControls: false,
        variants: [
          {tracks: ["56px", "minmax(0, 1fr)"], areas: ["launcher workspace"]},
        ],
        children: [
          {id: "launcher", selector: ".mcel-shell-launcher", role: "region", verifyControls: false},
          {id: "workspace", selector: ".mcel-shell-workspace", role: "group", maxColumns: 1,
            gap: 0, verifyControls: false,
            children: [
              {id: "application-mount", selector: ".mcel-shell-application-mount", role: "region", verifyControls: false}
            ]}
        ]},
      {id: "status", selector: ".mcel-shell-status", role: "region", verifyControls: false}
    ]
  }
};

module.exports = mcel.defineApp({id: "semantic-applications-shell", title: "Applications Shell"}, dsl => {
  const surface = dsl.surface.bindHtml("application", {bindings: {}});
  const layout = dsl.layout.define("applications-shell", {surface, tree: SHELL_LAYOUT});
  return {states: [], capabilities: [], intents: [], surfaces: [surface], layouts: [layout]};
});
