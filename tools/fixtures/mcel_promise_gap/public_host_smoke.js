"use strict";
const mcel = require("@mcel/app");
module.exports = mcel.defineApp(
  {id: "promise-probe", title: "Promise Probe", semanticVersion: "1"},
  ({application}) => application.hostBound((app) => {
    app.presentation.hostBound("workspace", {
      route: "/applications/promise-probe", root: "#promise-probe",
      presentationAuthority: "existing-host-html"
    });
    app.state.rendererLocal("filter", app.field.string(), {initial: ""});
    app.intent.interaction("changeFilter", {
      runtimeMethod: "changeFilter", binding: "changeFilter", label: "Filter",
      lane: "local-ui", reads: ["filter"]
    });
    app.scenario.example("change-filter", {intent: "changeFilter", expect: {ok: true}});
    app.layout.zones(["main"]);
    app.proof.semanticRuntimeProven();
  })
);
