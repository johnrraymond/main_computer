"use strict";
// Chat Console is a composable semantic host. The established shared notebook
// runtime supplies execution, thread lifecycle and dynamically created cells.
// No new executable operations or permissions are granted by this surface.
const mcel = require("@mcel/app");
module.exports = mcel.defineApp({id:"semantic-chat-console",title:"Chat Console"}, dsl => {
  const surface = dsl.surface.bindHtml("application", {bindings:{}});
  return {states:[],capabilities:[],intents:[],surfaces:[surface]};
});
