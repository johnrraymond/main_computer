"use strict";
// Chat Console's notebook and thread semantics are authored here, using the
// same canonical-tree surface as Calculator and Code Editor. The paired HTML
// source owns the static region hierarchy; the shared notebook runtime owns
// dynamic cells, thread state and all execution capabilities.
//
// P3 is authoring/compilation only. P4 mounts this canonical tree in both
// standalone and embedded contexts; this source does NOT grant effects.
const mcel = require("@mcel/app");

const CHAT_CONSOLE_LAYOUT_TREE = {
  schema: "mcel.layout-tree.v1",
  root: {
    id: "chat-shell", selector: ".chat-console-shell", role: "group",
    maxColumns: 1, gap: 8,
    children: [
      {id: "chat-header", selector: ".chat-console-header", role: "region",
        verifyControls: false},
      {id: "thread-layout", selector: ".chat-console-thread-layout", role: "group",
        maxColumns: 2, gap: 12,
        // The host's measured space and visibility determine which authored
        // alternative is feasible. Hidden embedded thread rails are optional.
        variants: [
          {tracks: ["minmax(0, 0.30fr)", "minmax(0, 0.70fr)"],
            areas: ["thread-rail thread-workspace"]},
          {tracks: ["minmax(0, 1fr)"],
            areas: ["thread-rail", "thread-workspace"]},
          {tracks: ["minmax(0, 1fr)"],
            areas: ["thread-workspace"]}
        ],
        children: [
          {id: "thread-rail", selector: ".chat-thread-rail", role: "region",
            requireVisible: false, verifyControls: false},
          {id: "thread-workspace", selector: ".chat-thread-workspace", role: "group",
            maxColumns: 1, gap: 8,
            children: [
              {id: "active-thread", selector: ".chat-thread-current-bar", role: "region",
                requireVisible: false, verifyControls: false},
              {id: "notebook", selector: ".chat-console-notebook", role: "group",
                maxColumns: 1, gap: 8,
                children: [
                  // P2 repeats one template over runtime cells; it does not
                  // consume a declaration node per cell. Input/output types
                  // share stable cell IDs but may have different inner widgets.
                  {id: "notebook-cells", selector: ".chat-cell", role: "repeat",
                    keyAttribute: "data-cell-id", maxColumns: 2,
                    minTargetWidth: 220, gap: 8, verifyControls: false}
                ]}
            ]}
        ]}
    ]}
};

module.exports = mcel.defineApp({id: "semantic-chat-console", title: "Chat Console"}, dsl => {
  const surface = dsl.surface.bindHtml("application", {bindings: {}});
  const layout = dsl.layout.define("notebook", {surface, tree: CHAT_CONSOLE_LAYOUT_TREE});
  return {states: [], capabilities: [], intents: [], surfaces: [surface], layouts: [layout]};
});
