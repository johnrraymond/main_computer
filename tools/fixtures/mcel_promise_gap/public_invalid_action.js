"use strict";
const mcel = require("@mcel/app");
module.exports = mcel.defineApp(
  {id: "promise-invalid", title: "Promise Invalid"},
  (dsl) => {
    // Invalid composition must refuse at authoring/compile time.
    dsl.surface.action("orphan", {intent: null});
    return {};
  }
);
