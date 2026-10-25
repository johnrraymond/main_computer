/* Bounded, independently executed MCEL model/controller witnesses.
 * Runs real file-basket implementation with disposable in-memory data.
 * No Git, filesystem, network, or live application mutations.
 * A PASS is not a full registry-element certification.
 */
(function (global) {
  "use strict";
  const IDS = [
    "element.resource.file-basket-model",
    "element.resource.file-boundary",
    "element.resource.directory-tree",
    "element.resource.tree-selection-model",
    "element.toolkit.selection-controller",
    "element.toolkit.bulk-selector"
  ];
  function witness(id, observations, scope, details) {
    const failures = Object.entries(observations).filter(([,value]) => value !== true).map(([key])=>key);
    return {id, status:failures.length ? "FAIL":"PASS", scope,
      observations, failedObservations:failures, details,
      fullElementCertification:false};
  }
  function equal(left,right) { return JSON.stringify(left)===JSON.stringify(right); }
  function run() {
    const modelApi=global.McelFileBasketModel, controllerApi=global.McelFileBasketController;
    if (!modelApi || !controllerApi) throw new Error("Original MCEL FileBasketModel and FileBasketController required");
    const review={candidate_groups:{
      selected_by_default:[{path:"src/main.js",status:"modified",reason:"source",risk:"clean"}],
      review_before_selecting:[{path:"src/sub/util.js",status:"untracked",reason:"review",risk:"review"}],
      blocked_possible_secrets:[{path:"src/secrets.env",status:"untracked",reason:"secret",risk:"blocked",blocking_security_findings_count:1}],
      excluded_generated_runtime:[{path:"build/cache.js",status:"untracked",reason:"generated runtime"}]
    }};
    const model=modelApi.buildFileBasketModel(review,{surfaceId:"acid.disposable.file-basket"});
    const ctl=controllerApi.createFileBasketController(model,{selectedPaths:[]});
    const eligible=["src/main.js","src/sub/util.js"];
    const visible=model.rows.map(r=>r.path);
    const blocked=["src/secrets.env","build/cache.js"];
    const modelProof=witness("element.resource.file-basket-model",{
      oneRowPerCandidate:model.rows.length===4,
      eligibleFilesExact:equal(model.selectablePaths,eligible),
      blockedFilesVisible:blocked.every(x=>visible.includes(x)),
      blockedFilesExcluded:blocked.every(x=>!model.selectablePaths.includes(x)),
      independentOutput:equal(modelApi.selectedOutput(model,[...eligible,...blocked,"../other.js"]),eligible),
      immutableSelectionContract:model.selectionContract?.selectedFilesAreSourceOfTruth===true
    },"file-basket-model-data-boundary",{modelSource:"mcel-file-basket-model.js",fixture:"in-memory-review", limitations:["No renderer", "No Git mutation"]});
    const normalize=modelApi.normalizeRepoPath;
    const fileProof=witness("element.resource.file-boundary",{
      normalizedForwardSlash:normalize("src\\\\sub\\\\util.js")==="src/sub/util.js",
      normalizedRepoRoot:normalize("main_computer_test/src/main.js")==="src/main.js",
      traversalRefused:normalize("src/../secrets.env")==="",
      windowsAbsoluteRefused:normalize("C:/secret.txt")==="",
      uncRefused:normalize("//server/share/secret.txt")==="",
      pathNotInContractRefused:ctl.canSelectPath("src/nonexistent.js")===false,
      visibleBlockedStillReadOnly:ctl.canSeePath("src/secrets.env")===true && ctl.canSelectPath("src/secrets.env")===false
    },"file-basket-repo-path-adapter-only",{limitations:["Not a filesystem sandbox", "Absolute POSIX paths outside this adapter not tested"]});
    const before=ctl.selectedPaths();
    const legal=ctl.apply("set-file-selection",{path:"src/sub/util.js",selected:true});
    const afterLegal=ctl.selectedPaths();
    const refused=ctl.apply("set-file-selection",{path:"src/secrets.env",selected:true});
    const unknown=ctl.apply("unsupported-action",{path:"src/main.js"});
    const stateAfterRefusals=ctl.selectedPaths();
    const selectProof=witness("element.toolkit.selection-controller",{
      initiallyEmpty:equal(before,[]),
      legalTransitionAccepted:legal.ok===true && equal(afterLegal,["src/sub/util.js"]),
      blockedTransitionRefused:refused.ok===false && typeof refused.reason==="string" && !!refused.reason,
      unknownCommandRefused:unknown.ok===false,
      refusedCommandsPreserveState:equal(stateAfterRefusals,afterLegal),
      explicitSelectedOutput:equal(ctl.selectedOutput(),afterLegal)
    },"file-basket-selection-controller-subset",{controllerId:ctl.controllerId,limitations:["No range selection", "No generic toolkit renderer"]});
    const all=ctl.apply("select-all-eligible");
    const selectedAll=ctl.selectedPaths();
    const cleared=ctl.apply("clear-selection");
    const bulkProof=witness("element.toolkit.bulk-selector",{
      bulkSelectExact:all.ok===true && equal(selectedAll,eligible),
      blockedExcluded:blocked.every(x=>!selectedAll.includes(x)),
      countsConsistent:all.summary?.selected===2 && all.summary?.selectedBlocked===0,
      clearingWorks:cleared.ok===true && equal(ctl.selectedPaths(),[])
    },"file-basket-controller-bulk-selection-subset",{limitations:["No rendered bulk selector", "No filtered/visible selection modes"]});
    const none=ctl.deriveDirectorySelectionState([],"src");
    const some=ctl.deriveDirectorySelectionState(["src/main.js"],"src");
    const allState=ctl.deriveDirectorySelectionState(eligible,"src");
    const blockedNode={kind:"file",path:"src/secrets.env",selectable:false};
    const treeProof=witness("element.resource.tree-selection-model",{
      noneState:none==="none",mixedState:some==="mixed",allState:allState==="all",
      blockedNodeRefused:ctl.canSelectTreeNode(blockedNode)===false,
      blockedNodeState:ctl.treeNodeSelectionState(blockedNode) === "blocked",
      fileNodeState:ctl.treeNodeSelectionState({kind:"file",path:"src/main.js"},eligible)==="all"
    },"file-basket-tree-selection-tristate-subset",{limitations:["No focus controller", "No preview target", "No keyboard/DOM witness"]});
    const dirs=model.directoryRows || [];
    const directoryProof=witness("element.resource.directory-tree",{
      rootRowsPresent:Array.isArray(model.hierarchy) && model.hierarchy.length>0,
      srcDirectoryExists:dirs.some(x=>x.path==="src"),
      nestedDirectoryExists:dirs.some(x=>x.path==="src/sub"),
      selectableDescendantsExact:equal(ctl.selectableDescendantPaths("src"),eligible),
      blockedRowVisible:visible.includes("src/secrets.env"),
      controllerHasNoMutationMethods:!Object.keys(ctl).some(k=>/^(delete|move|rename|write|exec)$/i.test(k))
    },"file-basket-hierarchy-model-subset",{limitations:["No tree DOM", "No expansion/keyboard", "No filesystem mutation proof"]});
    const rows=[modelProof,fileProof,directoryProof,treeProof,selectProof,bulkProof];
    return {schema:"mcel.file-basket.boundary-witness.v1",roster:IDS,elements:rows,
      status:rows.some(x=>x.status==="FAIL")?"FAIL":"PASS",
      failed:rows.filter(x=>x.status==="FAIL").map(x=>({id:x.id,failedObservations:x.failedObservations})),
      executionContext:"disposable-memory-fixture; no operational effects"};
  }
  global.McelAcidFileBasket={run,ROSTER:IDS.slice()};
})(typeof window!=="undefined"?window:globalThis);
