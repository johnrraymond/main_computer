"use strict";

// Native MCEL Worker migration candidate. The source declares the complete
// authority of this slice; marketplace wallet / payment authorities are NOT
// silently borrowed from the legacy DOM application.
const mcel = require("@mcel/app");

module.exports = mcel.defineApp(
  {id:"worker",title:"Worker",semanticVersion:"1"},
  dsl => {
    const text=dsl.field.text();
    const network=dsl.field.text().oneOf(["none","mainnet","testnet","test","dev","slh"]);
    const requestedRing=dsl.field.integer();
    const availabilityMode=dsl.field.text().oneOf(["totally_idle","ai_idle"]);
    const destinationShape=dsl.field.record({
      selectedNetwork:text,connectionStatus:text,hub:text,chainId:text,
      requestedRing,assignedRing:text,workerId:text,explanation:text,
      slhEnrollment:text
    });
    const runtimeShape=dsl.field.record({
      state:text,phase:text,reason:text,activeJobs:dsl.field.integer(),
      allowedToAccept:dsl.field.boolean()
    });
    const policyShape=dsl.field.record({
      sellerEnabled:dsl.field.boolean(),availabilityMode,
      remoteEnabled:dsl.field.boolean(),remoteMode:dsl.field.text().oneOf(["never","ask-when-busy","auto-when-busy"])
    });
    const networkOption=dsl.field.record({network:text,label:text});
    const networkOptions=dsl.field.list(networkOption,{key:"network"});
    const destinations=dsl.state.derived("destinations",networkOptions);
    // Navigation is a view selector. A Hub is a mutually exclusive choice
    // within the Hub Choice workspace, never a destination in the navigation rail.
    const view=dsl.state.rendererLocal("worker-view",
      text.oneOf(["hub-choice","worker","requester"]),{initial:"hub-choice"});
    const switchView=dsl.intent.selectView("switch-view",view);
    const inView=choice=>({state:view,equals:choice});
    const selected=dsl.state.rendererLocal("selected-network",text,{initial:""});
    const inSlhHub={all:[inView("hub-choice"),{state:selected,equals:"slh"}]};
    const inWalletHub={all:[inView("hub-choice"),{any:["mainnet","testnet","test","dev"]
      .map(choice=>({state:selected,equals:choice}))}]};
    const chooseNetwork=dsl.intent.selectFromCollection("choose-network",destinations,selected);
    const destination=dsl.state.derived("destination",destinationShape);
    const savedHubChoice=dsl.state.derived("saved-hub-choice",text);
    const runtime=dsl.state.derived("runtime",runtimeShape);
    const policy=dsl.state.derived("policy",policyShape);
    const slhTargetShape=dsl.field.record({hubUrl:text,authenticationState:text});
    const slhTarget=dsl.state.derived("slh-target",slhTargetShape);
    const operationReceipt=dsl.state.derived("last-operation",destinationShape);

    const observer=dsl.capability.external("worker-observer",{
      risk:"external-read",
      description:"Read-only local Worker supervisor and destination snapshot; no Hub probe or worker enrollment"
    });
    observer.operation("destinations","observeWorkerDestinations",{result:networkOptions});
    observer.operation("destination","observeWorkerDestination",{result:destinationShape});
    observer.operation("runtime","observeWorkerRuntime",{result:runtimeShape});
    observer.operation("policy","observeWorkerPolicy",{result:policyShape});
    observer.operation("slh-target","observeWorkerSlhTarget",{result:slhTargetShape});
    const refreshDestinations=dsl.intent.capabilityRequest("refresh-destinations",observer,
      {operation:"destinations",resultTo:destinations});
    const refreshDestination=dsl.intent.capabilityRequest("refresh-destination",observer,
      {operation:"destination",resultTo:destination});
    const refreshRuntime=dsl.intent.capabilityRequest("refresh-runtime",observer,
      {operation:"runtime",resultTo:runtime});
    const refreshPolicy=dsl.intent.capabilityRequest("refresh-policy",observer,
      {operation:"policy",resultTo:policy});
    const refreshSlhTarget=dsl.intent.capabilityRequest("refresh-slh-target",observer,
      {operation:"slh-target",resultTo:slhTarget});

    const chooser=dsl.capability.external("worker-hub-choice-control",{
      risk:"consequential",
      description:"Persist only the shared MCEL Hub Choice, never connect, authenticate or enroll"
    });
    chooser.operation("save","saveWorkerHubChoice",{
      inputs:{network:text},result:text,risk:"consequential"
    });
    const controller=dsl.capability.external("worker-destination-control",{
      risk:"consequential",
      description:"Explicitly select one Worker destination or disconnect; SLH selection never enrolls a device"
    });
    controller.operation("select","selectWorkerDestination",{
      inputs:{network:text,requestedRing},result:destinationShape,risk:"consequential"
    });
    controller.operation("disconnect","disconnectWorkerDestination",{
      result:destinationShape,risk:"consequential"
    });
    // Hub Choice persists independently of the actual Worker network session.
    const commitHubChoice=dsl.intent.capabilityRequest("commit-hub-choice",chooser,{
      operation:"save",arguments:{network:selected},resultTo:savedHubChoice,
      confirmationRequired:false
    });
    const selectDestination=dsl.intent.capabilityRequest("select-destination",controller,{
      operation:"select",inputs:{requestedRing},arguments:{network:selected},resultTo:destination,
      confirmationRequired:true
    });
    const disconnect=dsl.intent.capabilityRequest("disconnect",controller,{
      operation:"disconnect",resultTo:operationReceipt,confirmationRequired:true
    });
    const policyWriter=dsl.capability.external("worker-policy-control",{
      risk:"consequential",description:"Save four explicit Worker policy fields; no wallet or registration authority"
    });
    policyWriter.operation("save","saveWorkerPolicy",{
      inputs:{sellerEnabled:dsl.field.boolean(),availabilityMode,
        remoteEnabled:dsl.field.boolean(),remoteMode:dsl.field.text().oneOf([
          "never","ask-when-busy","auto-when-busy"])},
      result:policyShape,risk:"consequential"
    });
    const slhWriter=dsl.capability.external("worker-slh-target-control",{
      risk:"consequential",
      description:"Explicitly configure only the Worker managed SLH endpoint; never connect, authenticate or enroll"
    });
    slhWriter.operation("save","saveWorkerSlhTarget",{
      inputs:{hubUrl:text},result:slhTargetShape,risk:"consequential"
    });
    const saveSlhTarget=dsl.intent.capabilityRequest("save-slh-target",slhWriter,{
      operation:"save",inputs:{hubUrl:text},resultTo:slhTarget,confirmationRequired:true
    });
    const savePolicy=dsl.intent.capabilityRequest("save-policy",policyWriter,{
      operation:"save",inputs:{sellerEnabled:dsl.field.boolean(),availabilityMode,
        remoteEnabled:dsl.field.boolean(),remoteMode:dsl.field.text().oneOf([
          "never","ask-when-busy","auto-when-busy"])},
      resultTo:policy,confirmationRequired:true
    });

    const navigation=dsl.surface.region("app-navigation",{
      role:"navigation",priority:"secondary",prefer:{side:"start"},children:[
        dsl.surface.action("navigation-hub-worker-requester",{
          intent:switchView,
          navigationSubtitle:{
            state:selected,forValue:"hub-choice",fallback:"None",
            labels:{none:"None",mainnet:"Mainnet",testnet:"Testnet",
              test:"Local Test",dev:"Local Dev",slh:"SLH/SEH"}
          }
        })
      ]
    });
    const hubChoices=dsl.surface.region("hub-choices",{
      role:"main-workarea",priority:"primary",children:[
        dsl.surface.collection("hub-radio-group",{
          key:"network",fields:["label","network"],state:destinations,
          select:chooseNetwork,selectionPresentation:"radio",label:"Hub Choice",clearable:false,
          when:inView("hub-choice")
        })
      ]
    });
    // Ring configuration is a Worker operation, not part of choosing a Hub.
    // Keep the existing requested-ring control without an Apply Hub button.
    const ringTask=dsl.surface.region("worker-ring-configuration",{
      role:"task",priority:"primary",children:[
        dsl.surface.form("configure-worker-ring",{
          fields:{requestedRing},defaults:{requestedRing:3},
          resetControl:false,submitLabel:"Update worker ring",intent:selectDestination,
          acceptResult:{requestedRing:{state:destination,field:"requestedRing",pick:"requestedRing",whenIntent:refreshDestination}},
          when:inView("worker")
        }),
        dsl.surface.text("worker-hub-choice",{value:selected,label:"Hub choice",when:inView("worker")})
      ]
    });
    const slhTargetTask=dsl.surface.region("slh-target-selection",{
      role:"main-workarea",priority:"primary",children:[
        dsl.surface.form("configure-slh-target",{
          label:"SLH/SEH Hub endpoint (HTTPS)",
          fields:{hubUrl:text},defaults:{hubUrl:""},resetControl:false,
          submitLabel:"Save SLH/SEH target",intent:saveSlhTarget,
          acceptResult:{hubUrl:{state:slhTarget,field:"hubUrl",pick:"hubUrl",whenIntent:refreshSlhTarget}},
          when:inSlhHub
        }),
        dsl.surface.text("slh-target-status",{
          label:"Configured SLH target (connection and authentication not verified)",
          value:slhTarget,when:inSlhHub
        })
      ]
    });
    // The first migration stage reuses the accepted legacy wallet/MSK handlers.
    // Wallet/MSK belongs to Hub Choice for marketplace destinations. The
    // compatibility island remains gated by the separately verified Worker
    // session; selecting a Hub alone never authorizes wallet operations.
    const walletSlot=dsl.surface.region("worker-wallet-compat",{
      role:"main-workarea",priority:"supporting",children:[
        dsl.surface.text("wallet-compat-anchor",{
          label:"Legacy wallet and multi-session key controls",
          value:runtime,when:inWalletHub
        })
      ]
    });
    const policyTask=dsl.surface.region("worker-policy",{
      role:"main-workarea",priority:"primary",children:[
        dsl.surface.form("policy-settings",{
          fields:{sellerEnabled:dsl.field.boolean(),availabilityMode,
            remoteEnabled:dsl.field.boolean(),remoteMode:dsl.field.text().oneOf([
              "never","ask-when-busy","auto-when-busy"])},
          defaults:{sellerEnabled:false,availabilityMode:"totally_idle",remoteEnabled:false,remoteMode:"ask-when-busy"},
          resetControl:false,submitLabel:"Apply policy",intent:savePolicy,
          acceptResult:{
            sellerEnabled:{state:policy,field:"sellerEnabled",pick:"sellerEnabled",whenIntent:refreshPolicy},
            availabilityMode:{state:policy,field:"availabilityMode",pick:"availabilityMode",whenIntent:refreshPolicy},
            remoteEnabled:{state:policy,field:"remoteEnabled",pick:"remoteEnabled",whenIntent:refreshPolicy},
            remoteMode:{state:policy,field:"remoteMode",pick:"remoteMode",whenIntent:refreshPolicy}
          },when:inView("worker")
        }),
        dsl.surface.text("observed-policy",{value:policy,label:"Observed Worker policy",when:inView("worker")})
      ]
    });
    const requesterContext=dsl.surface.region("requester-context",{
      role:"main-workarea",priority:"primary",children:[
        dsl.surface.text("requester-hub",{
          value:destination,label:"Requester Hub context (request operations not yet migrated)",
          when:inView("requester")
        })
      ]
    });
    const observations=dsl.surface.region("runtime-observation",{
      role:"inspector",priority:"supporting",prefer:{side:"end"},children:[
        dsl.surface.text("runtime-status",{value:runtime,label:"Worker supervisor",when:inView("worker")}),
        dsl.surface.text("last-operation",{
          value:operationReceipt,label:"Hub operation result",when:inView("hub-choice")
        })
      ]
    });
    const refreshRegion=dsl.surface.region("refresh",{
      role:"auxiliary-workarea",priority:"supporting",children:[
        dsl.surface.action("refresh-destinations",{intent:refreshDestinations,when:inView("hub-choice")}),
        dsl.surface.action("refresh-destination",{intent:refreshDestination,when:inView("hub-choice")}),
        dsl.surface.action("refresh-slh-target",{intent:refreshSlhTarget,when:inSlhHub}),
        dsl.surface.action("refresh-runtime",{intent:refreshRuntime,when:inView("worker")}),
        dsl.surface.action("refresh-policy",{intent:refreshPolicy,when:inView("worker")})
      ]
    });
    const activity=dsl.surface.region("activity",{
      role:"status",priority:"supporting",children:[
        dsl.surface.receipt("worker-activity",{entries:[
          dsl.activity.info("Hub choice changed",{intent:commitHubChoice,dispositions:["completed"]}),
          dsl.activity.warn("Hub choice refused",{intent:commitHubChoice,dispositions:["refused","failed","unknown"]}),
          dsl.activity.info("Worker ring updated",{intent:selectDestination,dispositions:["completed"]}),
          dsl.activity.warn("Destination change refused",{intent:selectDestination,dispositions:["refused","failed","unknown"]}),
          dsl.activity.info("Worker disconnected",{intent:disconnect,dispositions:["completed"]}),
          dsl.activity.info("SLH target configured (not authenticated)",{intent:saveSlhTarget,dispositions:["completed"]}),
          dsl.activity.warn("SLH target configuration refused",{intent:saveSlhTarget,dispositions:["refused","failed","unknown"]}),
          dsl.activity.info("Policy saved",{intent:savePolicy,dispositions:["completed"]}),
          dsl.activity.warn("Policy save refused",{intent:savePolicy,dispositions:["refused","failed","unknown"]})
        ]})
      ]
    });
    const surface=dsl.surface.define("worker",{
      purpose:"manage",
      root:dsl.surface.region("source",{children:[navigation,hubChoices,ringTask,walletSlot,slhTargetTask,policyTask,requesterContext,observations,refreshRegion,activity]}),
      relations:[dsl.surface.relate(navigation,"selects",hubChoices),
        dsl.surface.relate(observations,"informs",hubChoices),
        dsl.surface.relate(observations,"informs",ringTask),
        dsl.surface.relate(walletSlot,"informs",hubChoices),
        dsl.surface.relate(observations,"informs",policyTask),
        dsl.surface.relate(activity,"reports-for",hubChoices)]
    });
    return {
      states:[view,destinations,selected,destination,savedHubChoice,runtime,policy,slhTarget,operationReceipt],
      capabilities:[observer,chooser,controller,policyWriter,slhWriter],
      intents:[switchView,chooseNetwork,refreshDestinations,refreshDestination,refreshRuntime,refreshPolicy,
        refreshSlhTarget,commitHubChoice,selectDestination,disconnect,savePolicy,saveSlhTarget],
      surfaces:[surface]
    };
  }
);
