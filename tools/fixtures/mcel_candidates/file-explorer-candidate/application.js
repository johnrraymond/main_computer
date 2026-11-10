"use strict";
/* Patch 05 independent candidate: the existing read-only File Explorer domain
 * expressed once with the public MCEL DSL. This author-owned source is not a
 * copy of the legacy HTML, nor a recovered historical notebook.
 */
const mcel = require("@mcel/app");
const APP = "file-explorer-candidate";
const READS = [
  ["inspect-roots", "inspectRoots", "Load trusted roots"],
  ["choose-root", "selectRoot", "Select root"],
  ["refresh-directory", "listDirectory", "Refresh directory"],
  ["navigate-up", "navigateUp", "Go to parent"],
  ["search-directory", "searchCurrentFolder", "Search folder"],
  ["open-entry", "openEntry", "Open directory or preview entry"]
];
function declare(app) {
  app.presentation.hostBound("workspace",{
    route:"/applications/file-explorer",root:"#file-explorer-app",
    presentationAuthority:"mcel-generated-semantic-shell",
    runtimeFacade:"McelFileExplorerCandidate"
  });
  const controls = READS.map(([id,sourceName,label])=>({id,intent:sourceName,role:"capability-action",risk:"external-read",label}));
  app.presentation.semanticSurface({
    controls,
    applicationShell:{schema:"mcel.semantic-application-shell.v1",layout:"sidebar-workspace",
      id:"file-workspace",label:"File Explorer — MCEL Candidate",regions:[
      {id:"region:roots",zone:"sidebar",kind:"collection",title:"Trusted roots",path:"roots",
       columns:["id","label"],rowKey:"id",selectControl:"choose-root"},
      {id:"region:navigation",zone:"sidebar",kind:"form",title:"Read-only navigation",
       fields:[{id:"query",type:"search",label:"Search current folder",placeholder:"Search"}],
       actions:[
        {id:"inspect-roots",label:"Load roots",authority:"external-read",intent:`intent:${APP}.inspect-roots`,control:"inspect-roots"},
        {id:"refresh-directory",label:"Refresh directory",authority:"external-read",intent:`intent:${APP}.refresh-directory`,control:"refresh-directory"},
        {id:"navigate-up",label:"Up",authority:"external-read",intent:`intent:${APP}.navigate-up`,control:"navigate-up"},
        {id:"search-directory",label:"Search",authority:"external-read",intent:`intent:${APP}.search-directory`,control:"search-directory"},
        {id:"delete",label:"Delete (not admitted)",authority:"not-admitted",intent:null}
       ]},
      {id:"region:location",zone:"sidebar",kind:"summary",title:"Location",
       items:[{id:"root",label:"Root",path:"location.root"},
              {id:"directory",label:"Directory",path:"location.directory"},
              {id:"count",label:"Visible entries",path:"location.count"}]},
      {id:"region:entries",zone:"main",kind:"collection",title:"Directory entries",path:"entries",
       columns:["name","kind","bytes","relative_path"],rowKey:"relative_path",selectControl:"open-entry"},
      {id:"region:details",zone:"main",kind:"summary",title:"Selected entry / preview",
       items:[{id:"entry-name",label:"Name",path:"preview.name"},
              {id:"entry-kind",label:"Kind",path:"preview.kind"},
              {id:"entry-status",label:"Read status",path:"preview.status"},
              {id:"entry-content",label:"Bounded preview",path:"preview.content"}]}
    ]}
  });
  for(const [id,sourceName,label] of READS){
    const cap=app.capability.external(id,{sourceName,description:`Authorized bounded read: ${label}`,risk:"external-read"})
      .operation("request", "executeRead");
    app.intent.capabilityRequest(id,cap,{sourceName,runtimeMethod:"request",binding:id,label,
      lane:"external-read",risk:"external-read"});
  }
  app.proof.semanticRuntimeProven({targetTruthStatus:"candidate"});
}
module.exports=mcel.defineApp({id:APP,title:"MCEL File Explorer Candidate",semanticVersion:"1",
  targetTruthStatus:"candidate"},({application})=>application.hostBound(declare));
