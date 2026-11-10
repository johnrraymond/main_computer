"use strict";
// NEW SOURCE-BOUND CANDIDATE reconstructed from the public semantic contract.
// NOT the missing original Phase 3 authored file. Remains outside mcel_apps/.
// Source of truth for the opt-in candidate; generated browser IR is derivative.
const mcel = require("@mcel/app");
const TAB_IDS = ["server-processes", "all-processes", "connections", "hardware"];
const SEMANTIC = {
  "controls": [
    {
      "controls": "#task-panel-processes",
      "id": "select-server-processes",
      "intent": "selectTab",
      "role": "select-tab",
      "selector": "#task-tab-processes",
      "selects": "server-processes"
    },
    {
      "controls": "#task-panel-all-processes",
      "id": "select-all-processes",
      "intent": "selectTab",
      "role": "select-tab",
      "selector": "#task-tab-all-processes",
      "selects": "all-processes"
    },
    {
      "controls": "#task-panel-connections",
      "id": "select-connections",
      "intent": "selectTab",
      "role": "select-tab",
      "selector": "#task-tab-connections",
      "selects": "connections"
    },
    {
      "controls": "#task-panel-hardware",
      "id": "select-hardware",
      "intent": "selectTab",
      "role": "select-tab",
      "selector": "#task-tab-hardware",
      "selects": "hardware"
    }
    ,{
      "id": "refresh-process-data-control",
      "role": "capability-action",
      "intent": "refreshProcessData",
      "label": "Refresh process data",
      "risk": "external-read"
    }
  ],
  // Generic application composition: regions, slots, data, fields and actions.
  // There are no Task Manager selectors or HTML layout instructions in MCEL core.
  "applicationShell": {
    "schema": "mcel.semantic-application-shell.v1",
    "id": "task-manager", "label": "Task Manager", "layout": "sidebar-workspace",
    "regions": [
      {"id":"region:overview", "zone":"sidebar", "kind":"summary", "title":"Task Overview",
       "items":[
         {"id":"running","label":"Server running","path":"server.running"},
         {"id":"pid","label":"Server PID","path":"server.pid"},
         {"id":"processes","label":"Main Computer processes","path":"overview.main_computer_process_count"},
         {"id":"connections","label":"Connections","path":"overview.connection_count"},
         {"id":"schedule-count","label":"Schedules","path":"overview.schedule_count"}]},
      {"id":"region:filters", "zone":"sidebar", "kind":"form", "title":"Process Monitoring",
       "fields":[
         {"id":"query","type":"search","label":"Process filter","placeholder":"name, port, PID"},
         {"id":"limit","type":"number","label":"Row limit","min":5,"max":100,"initial":24},
         {"id":"include-connections","type":"checkbox","label":"Include connections","initial":true},
         {"id":"auto-refresh","type":"checkbox","label":"Automatic refresh","initial":true}],
       "actions":[{"id":"refresh", "label":"Refresh process data","intent":"intent:task-manager-notebook.refresh-process-data", "control":"refresh-process-data-control", "authority":"external-read"}]},
      {"id":"region:server-operations", "zone":"sidebar", "kind":"form", "title":"Server Operations",
       "fields":[], "actions":[
         {"id":"server-start","label":"Start server","intent":null,"authority":"not-admitted"},
         {"id":"server-restart","label":"Restart server","intent":null,"authority":"not-admitted"},
         {"id":"server-shutdown","label":"Shutdown server","intent":null,"authority":"not-admitted"}]},
      {"id":"region:schedules", "zone":"sidebar", "kind":"collection", "title":"Schedules (read only)",
       "path":"schedules", "columns":["action","run_at","note"]},
      {"id":"region:notebook", "zone":"main", "kind":"slot", "title":"Process and Hardware Notebook", "slot":"notebook"},
      {"id":"region:hardware-summary", "zone":"main", "kind":"summary", "title":"Hardware Overview",
       "items":[{"id":"cpu","label":"CPU utilization","path":"hardware.cpu.overall_percent"},
                {"id":"gpu","label":"GPU utilization","path":"hardware.gpu.overall_percent"}]},
      {"id":"region:ai-brief", "zone":"main", "kind":"notice", "title":"AI Operations Brief (read only)",
       "items":[{"id":"ai-report","label":"Last report","path":"aiBrief"}]}
    ]
  },
  "forbiddenDefaultRegions": [],
  "id": "task-manager.notebook.semantic-surface.v1",
  "presentationAuthority": "existing-host-html",
  "regions": [
    {
      "id": "notebook",
      "primary": true,
      "role": "tabbed-workspace",
      "selector": ".task-notebook"
    },
    {
      "id": "tab-list",
      "role": "single-selection-tablist",
      "selector": "#task-notebook-tabs"
    },
    {
      "id": "panel-group",
      "role": "associated-panel-set",
      "selector": ".task-tab-panels"
    },
    {
      "controlledBy": "#task-tab-processes",
      "dataSource": "processes",
      "id": "panel-server-processes",
      "readOnly": true,
      "role": "notebook-data-panel",
      "selector": "#task-panel-processes"
    },
    {
      "controlledBy": "#task-tab-all-processes",
      "dataSource": "all_processes",
      "id": "panel-all-processes",
      "readOnly": true,
      "role": "notebook-data-panel",
      "selector": "#task-panel-all-processes"
    },
    {
      "controlledBy": "#task-tab-connections",
      "dataSource": "connections",
      "id": "panel-connections",
      "readOnly": true,
      "role": "notebook-data-panel",
      "selector": "#task-panel-connections"
    },
    {
      "controlledBy": "#task-tab-hardware",
      "dataSource": "hardware",
      "id": "panel-hardware",
      "readOnly": true,
      "role": "notebook-data-panel",
      "selector": "#task-panel-hardware"
    }
  ],
  "surfaceId": "task-manager.notebook",
  "tabGroup": {
    "a11y": {
      "keyboard": "standard-tabs",
      "panel": "tabpanel",
      "tab": "tab",
      "tabList": "tablist"
    },
    "defaultTabId": "server-processes",
    "id": "task-manager.data-notebook",
    "inactivePanelPolicy": "hidden-but-model-preserved",
    "role": "single-selection-view-state",
    "routeSync": "view-state-only",
    "state": "activeTabId",
    "tabs": [
      {
        "id": "server-processes",
        "label": "Server Processes",
        panel: "#task-panel-processes",
        "tab": "#task-tab-processes"
      },
      {
        "id": "all-processes",
        "label": "All Processes",
        panel: "#task-panel-all-processes",
        "tab": "#task-tab-all-processes"
      },
      {
        "id": "connections",
        "label": "Connections",
        panel: "#task-panel-connections",
        "tab": "#task-tab-connections"
      },
      {
        "id": "hardware",
        "label": "CPU + GPU",
        panel: "#task-panel-hardware",
        "tab": "#task-tab-hardware"
      }
    ]
  }
};
const GRAMMAR = {
  "constraints": [
    {
      "id": "notebook-min-size",
      "minHeight": 0,
      "minWidth": 0,
      "selector": ".task-notebook"
    }
  ],
  "id": "task-manager.notebook.layout.v1",
  "regions": [
    {
      "children": [
        "tab-list",
        "panel-group"
      ],
      "id": "notebook",
      "layout": "stack",
      "selector": ".task-notebook"
    },
    {
      "id": "tab-list",
      "layout": "wrap",
      "overflow": "available-width",
      "selector": "#task-notebook-tabs"
    },
    {
      "id": "panel-group",
      "layout": "fill",
      "overflow": "contained-scroll",
      "selector": ".task-tab-panels"
    }
  ],
  "rootSelector": ".task-notebook"
};
function declareNotebook(app) {
  app.presentation.hostBound("workspace", {
    route: "/applications/task-manager", root: "#task-manager-app",
    presentationAuthority: "existing-host-html"
  });
  app.presentation.semanticSurface(SEMANTIC);
  app.layout.grammar(GRAMMAR);
  app.layout.zones(["notebook", "tab-list", "panel-group"]);
  app.state.rendererLocal("active-tab-id", app.field.text().oneOf(TAB_IDS), {initial: "server-processes"});
  app.state.derived("data-status", app.field.text(), {initial: "not-requested"});
  app.state.derived("notebook-data", app.field.record(), {initial: {
    processes: [], all_processes: [], connections: [], hardware: {}
  }});
  app.invariant.semantic("single-selected-tab", {
    label: "One tab owns one visible panel",
    description: "One selected tab with exactly one visible panel; data in inactive panels persists."
  });
  app.invariant.semantic("tab-selection-has-no-operational-effects", {
    label: "Selection is only view state",
    description: "Tab selection cannot execute a process, server, scheduler, or AI operation."
  });
  app.intent.interaction("select-tab", {
    label: "Select one data view without operational effects",
    binding: "select-tab", runtimeMethod: "selectTab", risk: "local-state",
    lane: "local-view-state",
    viewState: {state: "active-tab-id", input: "selectedTabId", schema: app.field.text().oneOf(TAB_IDS)},
    reads: ["active-tab-id", "data-status", "notebook-data"],
    invariants: ["single-selected-tab", "tab-selection-has-no-operational-effects"],
    outcomes: ["completed", "failed", "refused"]
  });
  // Host adapter supplies the *existing* non-destructive process refresh.
  // The generated UI owns the interaction state/receipt, not the host button.
  const refresh = app.capability.external("process-snapshot-refresh", {
    sourceName: "processSnapshotRefresh", risk: "external-read",
    description: "Request an existing Task Manager process snapshot without machine mutation"
  }).operation("requestSnapshot", "refreshTaskManager");
  app.intent.capabilityRequest("refresh-process-data", refresh, {
    label: "Refresh process data", binding: "refresh-process-data",
    runtimeMethod: "requestSnapshot", lane: "external-read", risk: "external-read",
    reads: ["notebook-data", "data-status"],
    outcomes: ["completed", "refused", "failed", "cancelled"]
  });
  app.scenario.example("refresh-process-data-has-receipt", {
    intent: "refresh-process-data", expect: {receipt: true, mutations: 0}
  });
  app.scenario.example("reject-unknown-tab", {
    intent: "select-tab", given: {selectedTabId:"not-a-tab"},
    expect: {refused:true, activeTabIdUnchanged:true, operationalEffects:0}
  });
  app.scenario.example("select-connections-is-local", {
    intent: "select-tab", given: {activeTabId:"server-processes",selectedTabId:"connections"},
    expect: {activeTabId:"connections",visiblePanel:"connections",operationalEffects:0}
  });
  app.proof.semanticRuntimeProven({requiredAuthorities:["visible-surface","operation-receipt"]});
}
module.exports = mcel.defineApp({
  id:"task-manager-notebook", title:"Task Manager Data Notebook",
  semanticVersion:"1", targetTruthStatus:"semantic-runtime-proven"
}, ({application}) => application.hostBound(declareNotebook));
