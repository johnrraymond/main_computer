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
    // Same observed LMS-SLH identities used by the SLH application. Device
    // selection is local view state, never a claim of authentication or access.
    const identityRow=dsl.field.record({deviceId:text,label:text,defaultStatus:text,accessStatus:text});
    const identityRows=dsl.field.list(identityRow,{key:"deviceId"});
    const identitySummaryShape=dsl.field.record({state:text,defaultDeviceId:text,explanation:text});
    const identities=dsl.state.derived("local-identities",identityRows);
    const identitySummary=dsl.state.derived("local-identity-status",identitySummaryShape);
    const selectedIdentity=dsl.state.rendererLocal("selected-local-identity",text,{initial:""});
    const chooseIdentity=dsl.intent.selectFromCollection("choose-identity",identities,selectedIdentity);
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

    const observer=dsl.capability.external("worker-observer",{
      risk:"external-read",
      description:"Read-only local Worker supervisor and destination snapshot; no Hub probe or worker enrollment"
    });
    observer.operation("destinations","observeWorkerDestinations",{result:networkOptions});
    observer.operation("destination","observeWorkerDestination",{result:destinationShape});
    observer.operation("runtime","observeWorkerRuntime",{result:runtimeShape});
    observer.operation("policy","observeWorkerPolicy",{result:policyShape});
    observer.operation("slh-target","observeWorkerSlhTarget",{result:slhTargetShape});
    observer.operation("identities","observeWorkerIdentities",{result:identityRows});
    observer.operation("identity-status","observeWorkerIdentityStatus",{result:identitySummaryShape});
    const refreshIdentities=dsl.intent.capabilityRequest("refresh-identities",observer,
      {operation:"identities",resultTo:identities});
    const refreshIdentityStatus=dsl.intent.capabilityRequest("refresh-identity-status",observer,
      {operation:"identity-status",resultTo:identitySummary});
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
      // The port verifies the disconnect postcondition before returning.
      // Publish that result to the same destination state used by the UI.
      operation:"disconnect",resultTo:destination,confirmationRequired:true
    });
    const policyWriter=dsl.capability.external("worker-policy-control",{
      risk:"consequential",description:"Save four explicit Worker policy fields; no wallet or registration authority"
    });
    policyWriter.operation("save","saveWorkerPolicy",{
      inputs:{sellerEnabled:dsl.field.boolean(),availabilityMode},
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
    const enrollmentShape=dsl.field.record({deviceId:text,transport:text,endpoint:text,status:text,requestId:text,role:text});
    const enrollment=dsl.state.derived("worker-enrollment",enrollmentShape);
    const enrollmentInputs={transport:text.oneOf(["local","slh","seh"]),endpoint:text};
    const enrollmentControl=dsl.capability.external("worker-enrollment",{
      risk:"consequential",description:"Request signed Worker enrollment and reobserve authorization; never approve"
    });
    enrollmentControl.operation("request","requestWorkerEnrollment",{
      inputs:{deviceId:text,...enrollmentInputs},result:enrollmentShape,risk:"consequential"
    });
    enrollmentControl.operation("status","observeWorkerEnrollment",{
      inputs:{deviceId:text,...enrollmentInputs},result:enrollmentShape,risk:"consequential"
    });
    const requestEnrollment=dsl.intent.capabilityRequest("request-enrollment",enrollmentControl,{
      operation:"request",inputs:enrollmentInputs,arguments:{deviceId:selectedIdentity},resultTo:enrollment,confirmationRequired:true
    });
    const observeEnrollment=dsl.intent.capabilityRequest("observe-enrollment",enrollmentControl,{
      operation:"status",inputs:enrollmentInputs,arguments:{deviceId:selectedIdentity},resultTo:enrollment,confirmationRequired:false
    });
    const savePolicy=dsl.intent.capabilityRequest("save-policy",policyWriter,{
      operation:"save",inputs:{sellerEnabled:dsl.field.boolean(),availabilityMode},
      resultTo:policy,confirmationRequired:true
    });

    // Legacy marketplace parity is an explicit, typed MCEL authority. The
    // existing P-256/MetaMask/MSK registration implementation remains the signer.
    const decimalText=text;
    const sellerShape=dsl.field.record({
      sellerTargetTokens:dsl.field.integer(),sellerCreditsPerToken:decimalText,
      maxConcurrency:dsl.field.integer(),models:text,capability:text,executionMode:text
    });
    const requesterShape=dsl.field.record({
      remoteEnabled:dsl.field.boolean(),remoteMode:dsl.field.text().oneOf(["never","ask-when-busy","auto-when-busy"]),
      remoteCreditsPerToken:decimalText,remoteMaxOutputTokens:dsl.field.integer(),
      remoteDailyLimit:dsl.field.integer(),remoteAskBeforeSpend:dsl.field.boolean(),
      remoteOnlyWhenBusy:dsl.field.boolean()
    });
    const marketplaceStatusShape=dsl.field.record({
      network:text,connectionStatus:text,registrationStatus:text,workerId:text,
      assignedRing:text,phase:text,activeJobs:dsl.field.integer(),lastHeartbeat:text,
      lastError:text,workNowState:text,workNowExpiresAt:text,remainingSeconds:dsl.field.integer()
    });
    const marketplaceResultShape=dsl.field.record({status:text,detail:text});
    const seller=dsl.state.derived("seller-configuration",sellerShape);
    const requester=dsl.state.derived("requester-configuration",requesterShape);
    const marketplaceStatus=dsl.state.derived("marketplace-status",marketplaceStatusShape);
    const marketplaceResult=dsl.state.derived("marketplace-operation",marketplaceResultShape);
    const marketReader=dsl.capability.external("worker-marketplace-observer",{
      risk:"external-read",description:"Observe sanitized marketplace settings and supervisor status; never read credentials"
    });
    marketReader.operation("seller","observeMarketplaceSeller",{result:sellerShape});
    marketReader.operation("requester","observeMarketplaceRequester",{result:requesterShape});
    marketReader.operation("status","observeMarketplaceStatus",{result:marketplaceStatusShape});
    const refreshSeller=dsl.intent.capabilityRequest("refresh-seller",marketReader,{operation:"seller",resultTo:seller});
    const refreshRequester=dsl.intent.capabilityRequest("refresh-requester",marketReader,{operation:"requester",resultTo:requester});
    const refreshMarketplaceStatus=dsl.intent.capabilityRequest("refresh-marketplace-status",marketReader,{operation:"status",resultTo:marketplaceStatus});
    const marketWriter=dsl.capability.external("worker-marketplace-settings",{
      risk:"consequential",description:"Save bounded seller price and requester budget fields through existing Worker settings authority"
    });
    marketWriter.operation("seller","saveMarketplaceSeller",{
      inputs:{sellerTargetTokens:dsl.field.integer(),sellerCreditsPerToken:decimalText,
        maxConcurrency:dsl.field.integer(),models:text,capability:text,executionMode:text},
      result:sellerShape,risk:"consequential"
    });
    marketWriter.operation("requester","saveMarketplaceRequester",{
      inputs:{remoteEnabled:dsl.field.boolean(),remoteMode:dsl.field.text().oneOf(["never","ask-when-busy","auto-when-busy"]),
        remoteCreditsPerToken:decimalText,remoteMaxOutputTokens:dsl.field.integer(),
        remoteDailyLimit:dsl.field.integer(),remoteAskBeforeSpend:dsl.field.boolean(),
        remoteOnlyWhenBusy:dsl.field.boolean()},result:requesterShape,risk:"consequential"
    });
    const saveSeller=dsl.intent.capabilityRequest("save-seller",marketWriter,{
      operation:"seller",inputs:{sellerTargetTokens:dsl.field.integer(),sellerCreditsPerToken:decimalText,
        maxConcurrency:dsl.field.integer(),models:text,capability:text,executionMode:text},
      resultTo:seller,confirmationRequired:true
    });
    const saveRequester=dsl.intent.capabilityRequest("save-requester",marketWriter,{
      operation:"requester",inputs:{remoteEnabled:dsl.field.boolean(),remoteMode:dsl.field.text().oneOf(["never","ask-when-busy","auto-when-busy"]),
        remoteCreditsPerToken:decimalText,remoteMaxOutputTokens:dsl.field.integer(),
        remoteDailyLimit:dsl.field.integer(),remoteAskBeforeSpend:dsl.field.boolean(),
        remoteOnlyWhenBusy:dsl.field.boolean()},resultTo:requester,confirmationRequired:true
    });
    const marketActions=dsl.capability.external("worker-marketplace-actions",{
      risk:"consequential",description:"Reuse existing marketplace wallet/MSK offer and Work Now authority; never synthesize signatures"
    });
    marketActions.operation("register","registerMarketplaceOffer",{result:marketplaceResultShape,risk:"consequential"});
    marketActions.operation("work-now","startMarketplaceWorkNow",{
      inputs:{minutes:dsl.field.integer()},result:marketplaceResultShape,risk:"consequential"
    });
    marketActions.operation("finish","finishMarketplaceWorkNow",{result:marketplaceResultShape,risk:"consequential"});
    marketActions.operation("pause","pauseMarketplaceSelling",{result:marketplaceResultShape,risk:"consequential"});
    const registerOffer=dsl.intent.capabilityRequest("register-offer",marketActions,{
      operation:"register",resultTo:marketplaceResult,confirmationRequired:true
    });
    const startWorkNow=dsl.intent.capabilityRequest("work-now",marketActions,{
      operation:"work-now",inputs:{minutes:dsl.field.integer()},resultTo:marketplaceResult,confirmationRequired:true
    });
    const finishWorkNow=dsl.intent.capabilityRequest("finish-work-now",marketActions,{
      operation:"finish",resultTo:marketplaceResult,confirmationRequired:true
    });
    const pauseSelling=dsl.intent.capabilityRequest("pause-selling",marketActions,{
      operation:"pause",resultTo:marketplaceResult,confirmationRequired:true
    });
    controller.operation("retry","retryWorkerDestination",{result:destinationShape,risk:"consequential"});
    const retryHub=dsl.intent.capabilityRequest("retry-hub",controller,{
      operation:"retry",resultTo:destination,confirmationRequired:true
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
    // Identity selection is its own navigation region, as in the SLH app.
    // It is not a fourth app destination and does not authenticate a device.
    const identityNavigation=dsl.surface.region("identities",{
      role:"navigation",priority:"secondary",prefer:{side:"start"},children:[
        dsl.surface.collection("worker-local-identities",{
          key:"deviceId",fields:["label"],state:identities,
          select:chooseIdentity,clearable:true,label:"Identities",
          when:{state:identitySummary,path:"state",equals:"AVAILABLE"}
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
    const hubConnection=dsl.surface.region("hub-connection",{
      role:"main-workarea",priority:"supporting",children:[
        dsl.surface.action("retry-hub",{intent:retryHub,when:inWalletHub}),
        dsl.surface.action("disconnect",{intent:disconnect,when:inView("hub-choice")}),
        dsl.surface.text("active-hub-session",{value:destination,label:"Active Worker session (not Hub Choice)",when:inView("hub-choice")})
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
          fields:{hubUrl:text},defaults:{hubUrl:""},
          placeholders:{hubUrl:"https://localhost:8898"},resetControl:false,
          submitLabel:"Save SLH/SEH target",intent:saveSlhTarget,
          acceptResult:{hubUrl:{state:slhTarget,field:"hubUrl",pick:"hubUrl",whenIntent:refreshSlhTarget}},
          when:inSlhHub
        }),
        dsl.surface.text("slh-selected-local-identity",{
          label:"Identity selected for SLH/SEH access (not authenticated)",
          value:selectedIdentity,when:inSlhHub
        }),
        dsl.surface.form("request-worker-enrollment",{
          label:"Request Worker enrollment (SLH or SEH)",fields:enrollmentInputs,
          defaults:{transport:"local",endpoint:""},
          placeholders:{endpoint:"Local: empty; SLH: https://IP:API-port; SEH: wss://host"},
          submitLabel:"Request enrollment",resetControl:false,intent:requestEnrollment,when:inView("hub-choice")
        }),
        dsl.surface.form("observe-worker-enrollment",{
          label:"Verify enrollment / authentication",fields:enrollmentInputs,
          defaults:{transport:"local",endpoint:""},
          placeholders:{endpoint:"Same endpoint as enrollment"},
          submitLabel:"Verify access",resetControl:false,intent:observeEnrollment,when:inView("hub-choice")
        }),
        dsl.surface.text("worker-enrollment-status",{
          label:"Last verified enrollment result (identity and target shown; not a persistent Worker connection)",value:enrollment,when:inView("hub-choice")
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
          fields:{sellerEnabled:dsl.field.boolean(),availabilityMode},
          defaults:{sellerEnabled:false,availabilityMode:"totally_idle"},
          resetControl:false,submitLabel:"Apply policy",intent:savePolicy,
          acceptResult:{
            sellerEnabled:{state:policy,field:"sellerEnabled",pick:"sellerEnabled",whenIntent:refreshPolicy},
            availabilityMode:{state:policy,field:"availabilityMode",pick:"availabilityMode",whenIntent:refreshPolicy}
          },when:inView("worker")
        }),
        dsl.surface.text("observed-policy",{value:policy,label:"Observed Worker policy",when:inView("worker")})
      ]
    });
    const sellerTask=dsl.surface.region("seller-offer",{
      role:"main-workarea",priority:"primary",children:[
        dsl.surface.form("seller-offer-settings",{
          label:"Seller Offer Configuration",fields:{
            sellerTargetTokens:dsl.field.integer(),sellerCreditsPerToken:decimalText,
            maxConcurrency:dsl.field.integer(),models:text,capability:text,executionMode:text
          },defaults:{sellerTargetTokens:1024,sellerCreditsPerToken:"0.001",maxConcurrency:1,
            models:"gemma4:26b-a4b-it-q4_K_M",capability:"chat.completions",executionMode:"worker_pull_v0"},
          resetControl:false,submitLabel:"Save Seller Offer",intent:saveSeller,
          acceptResult:{
            sellerTargetTokens:{state:seller,field:"sellerTargetTokens",pick:"sellerTargetTokens",whenIntent:refreshSeller},
            sellerCreditsPerToken:{state:seller,field:"sellerCreditsPerToken",pick:"sellerCreditsPerToken",whenIntent:refreshSeller},
            maxConcurrency:{state:seller,field:"maxConcurrency",pick:"maxConcurrency",whenIntent:refreshSeller},
            models:{state:seller,field:"models",pick:"models",whenIntent:refreshSeller},
            capability:{state:seller,field:"capability",pick:"capability",whenIntent:refreshSeller},
            executionMode:{state:seller,field:"executionMode",pick:"executionMode",whenIntent:refreshSeller}
          },when:inView("worker")
        }),
        dsl.surface.text("seller-observed",{value:seller,label:"Saved seller offer",when:inView("worker")})
      ]
    });
    const workNowTask=dsl.surface.region("worker-execution",{
      role:"main-workarea",priority:"primary",children:[
        dsl.surface.form("worker-work-now",{
          label:"Work Now",fields:{minutes:dsl.field.integer()},defaults:{minutes:60},
          resetControl:false,submitLabel:"Start Work Now",intent:startWorkNow,when:inView("worker")
        }),
        dsl.surface.text("marketplace-action-result",{value:marketplaceResult,label:"Last marketplace action",when:inView("worker")})
      ]
    });
    const marketplaceCommands=dsl.surface.region("marketplace-commands",{
      role:"commands",priority:"primary",children:[
        dsl.surface.action("register-offer",{label:"Register / Update Offer",intent:registerOffer,when:inView("worker")}),
        dsl.surface.action("finish-after-current-request",{label:"Finish after current request",intent:finishWorkNow,when:inView("worker")}),
        dsl.surface.action("pause-selling",{intent:pauseSelling,when:inView("worker")})
      ]
    });
    const requesterTask=dsl.surface.region("requester-limits",{
      role:"main-workarea",priority:"primary",children:[
        dsl.surface.form("requester-spending-policy",{
          label:"Requester Spending and Consent",fields:{
            remoteEnabled:dsl.field.boolean(),remoteMode:dsl.field.text().oneOf(["never","ask-when-busy","auto-when-busy"]),
            remoteCreditsPerToken:decimalText,remoteMaxOutputTokens:dsl.field.integer(),
            remoteDailyLimit:dsl.field.integer(),remoteAskBeforeSpend:dsl.field.boolean(),
            remoteOnlyWhenBusy:dsl.field.boolean()
          },defaults:{remoteEnabled:false,remoteMode:"ask-when-busy",remoteCreditsPerToken:"0.001",
            remoteMaxOutputTokens:1024,remoteDailyLimit:100000,remoteAskBeforeSpend:true,remoteOnlyWhenBusy:true},
          resetControl:false,submitLabel:"Save Requester Limits",intent:saveRequester,
          acceptResult:{
            remoteEnabled:{state:requester,field:"remoteEnabled",pick:"remoteEnabled",whenIntent:refreshRequester},
            remoteMode:{state:requester,field:"remoteMode",pick:"remoteMode",whenIntent:refreshRequester},
            remoteCreditsPerToken:{state:requester,field:"remoteCreditsPerToken",pick:"remoteCreditsPerToken",whenIntent:refreshRequester},
            remoteMaxOutputTokens:{state:requester,field:"remoteMaxOutputTokens",pick:"remoteMaxOutputTokens",whenIntent:refreshRequester},
            remoteDailyLimit:{state:requester,field:"remoteDailyLimit",pick:"remoteDailyLimit",whenIntent:refreshRequester},
            remoteAskBeforeSpend:{state:requester,field:"remoteAskBeforeSpend",pick:"remoteAskBeforeSpend",whenIntent:refreshRequester},
            remoteOnlyWhenBusy:{state:requester,field:"remoteOnlyWhenBusy",pick:"remoteOnlyWhenBusy",whenIntent:refreshRequester}
          },when:inView("requester")
        }),
        dsl.surface.text("requester-observed",{value:requester,label:"Saved requester limits",when:inView("requester")})
      ]
    });
    const requesterContext=dsl.surface.region("requester-context",{
      role:"main-workarea",priority:"primary",children:[
        dsl.surface.text("requester-hub",{
          value:destination,label:"Requester Hub context (remote spend enforcement remains server-owned)",
          when:inView("requester")
        }),
        dsl.surface.text("requester-selected-identity",{
          value:selectedIdentity,label:"Local identity (not authenticated)",when:inView("requester")
        })
      ]
    });
    const observations=dsl.surface.region("runtime-observation",{
      role:"inspector",priority:"supporting",prefer:{side:"end"},children:[
        dsl.surface.text("runtime-status",{value:runtime,label:"Worker supervisor",when:inView("worker")}),
        dsl.surface.text("marketplace-runtime",{value:marketplaceStatus,label:"Registration, work-now and supervisor evidence",when:inView("worker")}),
        dsl.surface.text("last-operation",{
          value:destination,label:"Verified Hub operation result",when:inView("hub-choice")
        })
      ]
    });
    const refreshRegion=dsl.surface.region("refresh",{
      role:"auxiliary-workarea",priority:"supporting",children:[
        dsl.surface.action("refresh-destinations",{intent:refreshDestinations,when:inView("hub-choice")}),
        dsl.surface.action("refresh-destination",{intent:refreshDestination,when:inView("hub-choice")}),
        dsl.surface.action("refresh-slh-target",{intent:refreshSlhTarget,when:inSlhHub}),
        dsl.surface.action("refresh-runtime",{intent:refreshRuntime,when:inView("worker")}),
        dsl.surface.action("refresh-policy",{intent:refreshPolicy,when:inView("worker")}),
        dsl.surface.action("refresh-seller",{intent:refreshSeller,when:inView("worker")}),
        dsl.surface.action("refresh-marketplace-status",{intent:refreshMarketplaceStatus,when:inView("worker")}),
        dsl.surface.action("refresh-requester",{intent:refreshRequester,when:inView("requester")})
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
          dsl.activity.warn("Policy save refused",{intent:savePolicy,dispositions:["refused","failed","unknown"]}),
          dsl.activity.info("Seller offer saved",{intent:saveSeller,dispositions:["completed"]}),
          dsl.activity.warn("Seller offer save refused",{intent:saveSeller,dispositions:["refused","failed","unknown"]}),
          dsl.activity.info("Offer registration requested",{intent:registerOffer,dispositions:["completed"]}),
          dsl.activity.warn("Offer registration refused",{intent:registerOffer,dispositions:["refused","failed","unknown"]}),
          dsl.activity.info("Work Now started",{intent:startWorkNow,dispositions:["completed"]}),
          dsl.activity.info("Finish after current request",{intent:finishWorkNow,dispositions:["completed"]}),
          dsl.activity.info("Selling paused",{intent:pauseSelling,dispositions:["completed"]}),
          dsl.activity.info("Requester limits saved",{intent:saveRequester,dispositions:["completed"]}),
          dsl.activity.warn("Requester limits refused",{intent:saveRequester,dispositions:["refused","failed","unknown"]})
        ]})
      ]
    });
    const surface=dsl.surface.define("worker",{
      purpose:"manage",
      root:dsl.surface.region("source",{children:[navigation,identityNavigation,hubChoices,hubConnection,ringTask,walletSlot,slhTargetTask,policyTask,sellerTask,workNowTask,marketplaceCommands,requesterTask,requesterContext,observations,refreshRegion,activity]}),
      relations:[dsl.surface.relate(navigation,"selects",hubChoices),
        dsl.surface.relate(identityNavigation,"selects",hubChoices),
        dsl.surface.relate(observations,"informs",hubChoices),
        dsl.surface.relate(observations,"informs",ringTask),
        dsl.surface.relate(walletSlot,"informs",hubChoices),
        dsl.surface.relate(observations,"informs",policyTask),
        dsl.surface.relate(sellerTask,"informs",workNowTask),
        dsl.surface.relate(activity,"reports-for",hubChoices)]
    });
    return {
      states:[view,destinations,selected,identities,identitySummary,selectedIdentity,destination,savedHubChoice,runtime,policy,slhTarget,seller,requester,marketplaceStatus,marketplaceResult,enrollment],
      capabilities:[observer,chooser,controller,policyWriter,slhWriter,marketReader,marketWriter,marketActions,enrollmentControl],
      intents:[switchView,chooseNetwork,chooseIdentity,refreshIdentities,refreshIdentityStatus,
        refreshDestinations,refreshDestination,refreshRuntime,refreshPolicy,
        refreshSlhTarget,commitHubChoice,selectDestination,disconnect,savePolicy,saveSlhTarget,refreshSeller,refreshRequester,refreshMarketplaceStatus,saveSeller,saveRequester,registerOffer,startWorkNow,finishWorkNow,pauseSelling,retryHub,requestEnrollment,observeEnrollment],
      surfaces:[surface]
    };
  }
);
