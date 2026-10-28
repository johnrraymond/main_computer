/* Application-level semantics only; shared MCEL projector and layout engine
 * remain free of process-field names or Task Manager CSS selectors.
 */
(function(global) {
  "use strict";
  const processView=Object.freeze({
    schema:"mcel.semantic-collection-spec.v2",identity:"pid",
    sourceFields:["pid","name","is_current_process","is_main_computer","memory_rss","memory_availability",
      "status","status_availability","cmdline","created_at"],
    reconciliation:{generationFrom:"created_at"},fields:[
      {key:"pid",from:"pid",kind:"identifier",tier:"primary",label:"PID"},
      {key:"name",from:"name",kind:"text",tier:"primary",label:"Process"},
      {key:"role",kind:"enum",tier:"secondary",priority:1,label:"Role",derive:{kind:"first-true",
        cases:[{field:"is_current_process",value:"Current"},{field:"is_main_computer",value:"Main Computer"}],default:"Other"}},
      {key:"memory",from:"memory_rss",kind:"quantity",tier:"secondary",priority:2,label:"Memory",
        validity:{availabilityFrom:"memory_availability"},presentation:{units:"bytes"}},
      {key:"status",from:"status",kind:"enum",tier:"secondary",priority:3,label:"Status",
        validity:{availabilityFrom:"status_availability"}},
      {key:"command",from:"cmdline",kind:"long-text",tier:"secondary",priority:4,label:"Command",
        presentation:{maxChars:46,compactDisclosure:true}},
      {key:"created",from:"created_at",kind:"timestamp",tier:"detail",label:"Started"}
    ]
  });
  global.McelTaskManagerProcessContract=Object.freeze({processes:processView,all_processes:processView});
})(typeof window!=="undefined"?window:globalThis);
