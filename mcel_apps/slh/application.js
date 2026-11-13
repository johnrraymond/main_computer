"use strict";

// MCEL SLH: explicit configure-and-enable / disable lifecycle, persistent
// signing identity and separately pinned remote inspection. Opening this
// application is always observation-only; lifecycle changes require an action.
const mcel = require("@mcel/app");

module.exports = mcel.defineApp(
  {id:"slh",title:"Secure Local Hub",semanticVersion:"1"},
  dsl => {
    const text=dsl.field.text();
    const identityRow=dsl.field.record({
      deviceId:text,label:text,fingerprint:text,defaultStatus:text
    });
    const identityRows=dsl.field.list(identityRow,{key:"deviceId"});
    const identitySummaryShape=dsl.field.record({
      state:text,defaultDeviceId:text,explanation:text
    });
    const identities=dsl.state.derived("local-identities",identityRows);
    const identitySummary=dsl.state.derived("local-identity-status",identitySummaryShape);
    // Local keys are independent of the selected Organization Service target.
    // The device ID is not a bearer or administrator authorization.
    const adminDevice=dsl.state.rendererLocal("selected-local-identity",text,{initial:""});
    const target=dsl.state.rendererLocal("selected-target",
      text.oneOf(["local","network","remote-seh"]),{initial:"local"});
    const selectTarget=dsl.intent.selectView("select-target",target);
    const chooseIdentity=dsl.intent.selectFromCollection("choose-identity",identities,adminDevice);
    const observer=dsl.capability.external("slh-observer",{
      risk:"external-read",
      description:"Read the existing LMS-SLH identity directory without starting an Organization Service"
    });
    observer.operation("identities","observeSlhIdentities",{result:identityRows});
    observer.operation("identity-status","observeSlhIdentityStatus",{result:identitySummaryShape});
    const lifecycleShape=dsl.field.record({state:text,bindAddress:text,port:dsl.field.integer()});
    const lifecycle=dsl.state.derived("organization-lifecycle",lifecycleShape);
    observer.operation("lifecycle","observeSlhLifecycle",{result:lifecycleShape});
    const refreshLifecycle=dsl.intent.capabilityRequest("refresh-lifecycle",observer,
      {operation:"lifecycle",resultTo:lifecycle});
    const refreshIdentities=dsl.intent.capabilityRequest("refresh-identities",observer,
      {operation:"identities",resultTo:identities});
    const refreshIdentityStatus=dsl.intent.capabilityRequest("refresh-identity-status",observer,
      {operation:"identity-status",resultTo:identitySummary});
    const setupShape=dsl.field.record({outcome:text,message:text,deviceId:text});
    const setupResult=dsl.state.derived("organization-setup-result",setupShape);
    const setup=dsl.capability.external("slh-first-run",{
      risk:"consequential",description:"Explicitly configure/start or disable the local Organization Service"
    });
    setup.operation("configure-and-enable","configureAndEnableSlhOrganization",{
      inputs:{adminDeviceId:text,bindAddress:text,port:dsl.field.integer()},
      result:setupShape,risk:"consequential"
    });
    setup.operation("toggle-enablement","toggleSlhOrganizationEnablement",{
      inputs:{adminDeviceId:text},result:setupShape,risk:"consequential"
    });
    setup.operation("toggle","toggleSlhOrganization",{
      inputs:{adminDeviceId:text},result:setupShape,risk:"consequential"
    });
    const configureAndEnable=dsl.intent.capabilityRequest("configure-and-enable-organization",setup,{
      operation:"configure-and-enable",inputs:{bindAddress:text,port:dsl.field.integer()},
      arguments:{adminDeviceId:adminDevice},resultTo:setupResult,
      confirmationRequired:false
    });
    const toggleEnablement=dsl.intent.capabilityRequest("toggle-organization-enablement",setup,{
      operation:"toggle-enablement",arguments:{adminDeviceId:adminDevice},
      resultTo:setupResult,confirmationRequired:false
    });
    const toggleOrganization=dsl.intent.capabilityRequest("toggle-organization",setup,{
      operation:"toggle",arguments:{adminDeviceId:adminDevice},
      resultTo:setupResult,confirmationRequired:false
    });
    const networkSessionShape=dsl.field.record({
      endpoint:text,tlsSha256:text,organizationState:text,authenticationState:text,
      role:text,deviceId:text,services:text,observedAt:text,explanation:text
    });
    const networkSession=dsl.state.derived("network-session",networkSessionShape);
    const networkConnection=dsl.capability.external("network-slh-connection",{
      risk:"consequential",
      description:"Explicit pinned TLS connection and P-256 device authentication to a remote Organization Service"
    });
    networkConnection.operation("connect","connectNetworkSlh",{
      inputs:{address:text,port:dsl.field.integer(),adminDeviceId:text},
      result:networkSessionShape,risk:"consequential"
    });
    networkConnection.operation("disconnect","disconnectNetworkSlh",{
      result:networkSessionShape,risk:"consequential"
    });
    const connectNetwork=dsl.intent.capabilityRequest("connect-network-slh",networkConnection,{
      operation:"connect",inputs:{address:text,port:dsl.field.integer()},
      arguments:{adminDeviceId:adminDevice},resultTo:networkSession,confirmationRequired:false
    });
    const disconnectNetwork=dsl.intent.capabilityRequest("disconnect-network-slh",networkConnection,{
      operation:"disconnect",resultTo:networkSession,confirmationRequired:false
    });
    const remoteSehSession=dsl.state.derived("remote-seh-session",networkSessionShape);
    const remoteSehConnection=dsl.capability.external("remote-seh-connection",{
      risk:"consequential",description:"Pinned TLS identity and administrator login through an opaque SEH relay"
    });
    remoteSehConnection.operation("connect","connectRemoteSeh",{
      inputs:{sehUrl:text,adminDeviceId:text},result:networkSessionShape,risk:"consequential"
    });
    remoteSehConnection.operation("disconnect","disconnectRemoteSeh",{
      result:networkSessionShape,risk:"consequential"
    });
    const connectRemoteSeh=dsl.intent.capabilityRequest("connect-remote-seh",remoteSehConnection,{
      operation:"connect",inputs:{sehUrl:text},
      arguments:{adminDeviceId:adminDevice},resultTo:remoteSehSession,confirmationRequired:false
    });
    const disconnectRemoteSeh=dsl.intent.capabilityRequest("disconnect-remote-seh",remoteSehConnection,{
      operation:"disconnect",resultTo:remoteSehSession,confirmationRequired:false
    });
    const sehStatusSchema=dsl.field.record({phase:text,connectionEnabled:dsl.field.boolean(),
      connected:dsl.field.boolean(),gatewayReady:dsl.field.boolean(),configurationOwner:text,
      relayRole:text,lastErrorClass:text});
    const sehStatus=dsl.state.derived("seh-connection-status",sehStatusSchema);
    const sehLifecycleRead=dsl.capability.external("seh-connection-observer",{
      risk:"external-read",description:"Selected SLH outbound SEH connection status; never SEH deployment"
    });
    sehLifecycleRead.operation("status","observeSehConnection",{result:sehStatusSchema,risk:"external-read"});
    const readSehStatus=dsl.intent.capabilityRequest("read-seh-connection",sehLifecycleRead,{
      operation:"status",resultTo:sehStatus
    });
    const sehLifecycleWrite=dsl.capability.external("seh-connection-control",{
      risk:"consequential",description:"Manage only the selected SLH outbound SEH connection"
    });
    const sehLifecycleActions=Object.fromEntries(["start","stop","restart"].map(name=>{
      sehLifecycleWrite.operation(name,"controlSehConnection",{result:sehStatusSchema,risk:"consequential"});
      return [name,dsl.intent.capabilityRequest("seh-connection-"+name,sehLifecycleWrite,{
        operation:name,resultTo:sehStatus,confirmationRequired:false
      })];
    }));
    // The existing SLH-owned Coolify harness is the only SEH deployment
    // authority. Browser states contain public metadata only; API tokens are
    // transient form inputs and are never saved to an MCEL state variable.
    const deploymentShape=dsl.field.record({
      generation:dsl.field.integer(),selectedController:text,applicationUuid:text,
      deploymentStatus:text,
      controllers:dsl.field.list(dsl.field.record({identity:text}),{key:"identity"})
    });
    const sehDeployment=dsl.state.derived("seh-deployment-status",deploymentShape);
    const deploymentResultShape=dsl.field.record({outcome:text,message:text,applicationUuid:text});
    const sehDeploymentReceipt=dsl.state.derived("seh-deployment-receipt",deploymentResultShape);
    const deploymentReader=dsl.capability.external("seh-deployment-observer",{
      risk:"external-read",description:"Selected SLH's canonical Coolify controller registry and deployment status"
    });
    deploymentReader.operation("inspect","observeSehDeployment",{result:deploymentShape,risk:"external-read"});
    const inspectDeployment=dsl.intent.capabilityRequest("read-seh-deployment",deploymentReader,{
      operation:"inspect",resultTo:sehDeployment
    });
    const deploymentWriter=dsl.capability.external("seh-deployment-control",{
      risk:"consequential",description:"Operate the selected SLH's guarded SEH harness; never via an SEH relay"
    });
    const deploymentDefinitions=[
      ["register","registerCoolifyController",{name:text,url:text,apiToken:text}],
      ["select","selectCoolifyController",{name:text}],
      ["preview-deploy","previewSehDeploy",{}],["deploy","deploySeh",{}],
      ["preview-undeploy","previewSehUndeploy",{}],["undeploy","undeploySeh",{}]
    ];
    const deploymentIntents=Object.fromEntries(deploymentDefinitions.map(([name,method,inputs])=>{
      deploymentWriter.operation(name,method,{inputs,result:deploymentResultShape,risk:"consequential"});
      return [name,dsl.intent.capabilityRequest("seh-deployment-"+name,deploymentWriter,{
        operation:name,inputs,resultTo:sehDeploymentReceipt,confirmationRequired:false
      })];
    }));
    // Organization records are target-scoped. All authority remains in ORG-SLH.
    const pendingSchema=dsl.field.list(dsl.field.record({id:text,identity:text,role:text,requestedAt:text}),{key:"id"});
    const deviceSchema=dsl.field.list(dsl.field.record({id:text,identity:text,role:text,createdAt:text}),{key:"id"});
    const trashSchema=dsl.field.list(dsl.field.record({id:text,identity:text,role:text,trashedAt:text}),{key:"id"});
    const pending=dsl.state.derived("pending-enrollments",pendingSchema);
    const enrolled=dsl.state.derived("enrolled-devices",deviceSchema);
    const trash=dsl.state.derived("trashed-devices",trashSchema);
    const retentionSchema=dsl.field.record({enabled:dsl.field.boolean()});
    const retention=dsl.state.derived("trash-retention-policy",retentionSchema);
    const notebookResultShape=dsl.field.record({outcome:text,message:text});
    const notebookResult=dsl.state.derived("identity-administration-result",notebookResultShape);
    const administration=dsl.capability.external("organization-identity-administration",{
      risk:"consequential",description:"Authenticated and selected-organization-scoped enrollment, device and Trash administration"
    });
    const administrationRead=dsl.capability.external("organization-identity-observer",{
      risk:"external-read",description:"Authenticated observations scoped to the selected ORG-SLH"
    });
    const observed=[
      ["pending","observeOrganizationPending",pendingSchema,pending],
      ["enrolled","observeOrganizationEnrolled",deviceSchema,enrolled],
      ["trash","observeOrganizationTrash",trashSchema,trash],
      ["retention","observeOrganizationRetention",retentionSchema,retention]
    ];
    const notebookReads=observed.map(([name,method,schema,state])=>{
      administrationRead.operation("read-"+name,method,{result:schema,risk:"external-read"});
      return dsl.intent.capabilityRequest("read-organization-"+name,administrationRead,
        {operation:"read-"+name,resultTo:state});
    });
    const actionDefs=[
      ["accept","acceptOrganizationEnrollment"],["reject","rejectOrganizationEnrollment"],
      ["delete","deleteOrganizationDevice"],["reactivate","reactivateOrganizationDevice"],
      ["remove","removeOrganizationDevice"]
    ];
    const notebookActions=actionDefs.map(([name,method])=>{
      administration.operation(name,method,{inputs:{recordId:text},result:notebookResultShape,risk:"consequential"});
      return dsl.intent.capabilityRequest("organization-"+name,administration,
        {operation:name,inputs:{recordId:text},resultTo:notebookResult,confirmationRequired:false});
    });
    administration.operation("set-retention","setOrganizationTrashRetention",{
      inputs:{enabled:dsl.field.boolean()},result:retentionSchema,risk:"consequential"});
    const setRetention=dsl.intent.capabilityRequest("set-organization-trash-retention",administration,
      {operation:"set-retention",inputs:{enabled:dsl.field.boolean()},resultTo:retention,confirmationRequired:false});
    administration.operation("empty-trash","emptyOrganizationTrash",{
      result:notebookResultShape,risk:"consequential"});
    const emptyTrash=dsl.intent.capabilityRequest("empty-organization-trash",administration,
      {operation:"empty-trash",resultTo:notebookResult,confirmationRequired:false});
    setup.operation("login","loginSlhAdministrator",{
      inputs:{adminDeviceId:text},result:setupShape,risk:"consequential"
    });
    setup.operation("logout","logoutSlhAdministrator",{result:setupShape,risk:"consequential"});
    const loginAdministrator=dsl.intent.capabilityRequest("login-administrator",setup,{
      operation:"login",arguments:{adminDeviceId:adminDevice},resultTo:setupResult,confirmationRequired:false
    });
    const logoutAdministrator=dsl.intent.capabilityRequest("logout-administrator",setup,{
      operation:"logout",resultTo:setupResult,confirmationRequired:false
    });
    const identityNotebook=dsl.surface.region("organization-identities",{
      role:"main-workarea",priority:"primary",prefer:{side:"bottom"},children:[
        dsl.surface.notebook("organization-identity-notebook",{
          title:"Organization Identities",
          tabs:[
            {id:"pending",label:"Pending",key:"id",state:pending,
             fields:["identity","role","requestedAt"],refresh:notebookReads[0],
             actions:{Accept:{intent:notebookActions[0]},Reject:{intent:notebookActions[1]}}},
            {id:"enrolled",label:"Enrolled",key:"id",state:enrolled,
             fields:["identity","role","createdAt"],refresh:notebookReads[1],
             actions:{Delete:{intent:notebookActions[2],confirm:"Revoke this enrolled identity and move it to Trash?"}}},
            {id:"trash",label:"Trash",key:"id",state:trash,
             fields:["identity","role","trashedAt"],refresh:notebookReads[2],
             actions:{Reactivate:{intent:notebookActions[3]},
               Remove:{intent:notebookActions[4],confirm:"Permanently remove this identity's recoverable organization record?"}}}
          ],retention:{state:retention,refresh:notebookReads[3],update:setRetention},empty:emptyTrash
        })
      ]
    });
    const isTarget=choice=>({state:target,equals:choice});
    const networkConfiguration=dsl.surface.region("network-configuration",{
      role:"task",priority:"primary",children:[
        dsl.surface.form("connect-network-slh",{
          fields:{address:text,port:dsl.field.integer()},
          defaults:{port:8899},resetControl:false,submitLabel:"Connect to Network SLH",
          intent:connectNetwork,when:isTarget("network")
        }),
        dsl.surface.text("network-selected-identity",{
          value:adminDevice,label:"Selected local identity",when:isTarget("network")
        })
      ]
    });
    const remoteSehConfiguration=dsl.surface.region("remote-seh-configuration",{
      role:"task",priority:"primary",children:[
        dsl.surface.form("connect-remote-seh",{
          fields:{sehUrl:text},submitLabel:"Connect through SEH",
          intent:connectRemoteSeh,when:isTarget("remote-seh")
        }),
        dsl.surface.text("remote-seh-selected-identity",{
          value:adminDevice,label:"Selected local identity",when:isTarget("remote-seh")
        })
      ]
    });
    const remoteSehConnectionActions=dsl.surface.region("remote-seh-connection-actions",{
      role:"commands",priority:"supporting",children:[
        dsl.surface.action("disconnect-remote-seh",{
          intent:disconnectRemoteSeh,when:isTarget("remote-seh"),
          enabledWhen:{state:remoteSehSession,path:"authenticationState",equals:"AUTHENTICATED"}
        })
      ]
    });
    const sehLifecycleTabs=[
      {id:"configuration",label:"Configuration",state:sehDeployment,refresh:inspectDeployment,
       fields:["selectedController","deploymentStatus","generation"],
       forms:[
         {label:"Register Coolify controller",intent:deploymentIntents.register,
          submitLabel:"Add Coolify",secretFields:["apiToken"]},
         {label:"Select deployment target",intent:deploymentIntents.select,
          submitLabel:"Select Coolify",choicesFrom:{name:{state:sehDeployment,
            path:"controllers",valueKey:"identity",labelKey:"identity"}}}
       ],receipt:sehDeploymentReceipt,
       note:"Adding a controller never deploys an SEH. Selecting a controller cannot move an existing deployment."},
      {id:"deployment",label:"Deployment",state:sehDeployment,refresh:inspectDeployment,
       fields:["selectedController","applicationUuid","deploymentStatus","generation"],
       actions:{
         "Preflight Deploy":{intent:deploymentIntents["preview-deploy"],
           enabledWhen:{state:sehDeployment,path:"deploymentStatus",equals:"UNDEPLOYED"}},
         Deploy:{intent:deploymentIntents.deploy,confirm:"Deploy a new SEH application on the selected Coolify controller? Requires a verified prior canonical undeploy and published Git source.",
           enabledWhen:{state:sehDeployment,path:"deploymentStatus",equals:"UNDEPLOYED"}},
         "Preflight Undeploy":{intent:deploymentIntents["preview-undeploy"],
           enabledWhen:{state:sehDeployment,path:"deploymentStatus",equals:"APPLICATION_REGISTERED_NOT_RUNTIME_VERIFIED"}},
         Undeploy:{intent:deploymentIntents.undeploy,confirm:"Remove the exact registered SEH application from Coolify? First stop the outbound relay and settle all outstanding work.",
           enabledWhen:{state:sehDeployment,path:"deploymentStatus",equals:"APPLICATION_REGISTERED_NOT_RUNTIME_VERIFIED"}}
       },receipt:sehDeploymentReceipt,
       note:"Deployment requests are governed by the SEH harness. Queued builds are not verified running connections."},
      {id:"connection",label:"Connection",state:sehStatus,refresh:readSehStatus,
       fields:["phase","connected","connectionEnabled"],actions:{
        Start:{intent:sehLifecycleActions.start,confirm:"Start this SLH's outbound SEH connection?"},
        Stop:{intent:sehLifecycleActions.stop,confirm:"Stop this SLH's outbound SEH connection?"},
        Restart:{intent:sehLifecycleActions.restart,confirm:"Restart this SLH's outbound SEH connection?"}
       }},
      {id:"status",label:"Status",state:sehStatus,refresh:readSehStatus,
       fields:["phase","gatewayReady","connected"]},
      {id:"diagnostics",label:"Diagnostics",state:sehStatus,refresh:readSehStatus,
       fields:["lastErrorClass","phase","relayRole"]}
    ];
    const sehLifecycleNotebook=dsl.surface.region("seh-lifecycle",{
      role:"main-workarea",priority:"primary",prefer:{side:"bottom"},children:[
        dsl.surface.operationNotebook("local-seh-lifecycle",{
          title:"SEH Lifecycle",tabs:sehLifecycleTabs,when:isTarget("local")
        }),
        dsl.surface.operationNotebook("network-seh-lifecycle",{
          title:"SEH Lifecycle",tabs:sehLifecycleTabs,when:isTarget("network")
        })
      ]
    });
    const identityNavigation=dsl.surface.region("identities",{
      role:"navigation",priority:"secondary",prefer:{side:"start"},children:[
        dsl.surface.collection("available-local-identities",{
          key:"deviceId",fields:["label","deviceId","defaultStatus"],
          state:identities,select:chooseIdentity
        })
      ]
    });
    const configurationTask=dsl.surface.region("configuration",{
      role:"task",priority:"primary",children:[
        dsl.surface.form("configure-org-slh",{
          fields:{bindAddress:text,port:dsl.field.integer()},
          defaults:{bindAddress:"0.0.0.0",port:8899},resetControl:false,
          submitLabel:"Configure ORG-SLH",intent:configureAndEnable,
          acceptResult:{
            bindAddress:{state:lifecycle,field:"bindAddress",pick:"bindAddress",whenIntent:refreshLifecycle},
            port:{state:lifecycle,field:"port",pick:"port",whenIntent:refreshLifecycle}
          },when:isTarget("local")
        }),
        dsl.surface.action("organization-enablement",{
          intent:toggleEnablement,when:isTarget("local"),
          labelByState:{state:lifecycle,path:"state",labels:{running:"Disable",stopped:"Disable",disabled:"Enable"},fallback:"Disable"},
          enabledWhen:{any:["running","stopped","disabled"].map(state=>({state:lifecycle,path:"state",equals:state}))}
        }),
        dsl.surface.action("start-stop-org-slh",{
          intent:toggleOrganization,when:isTarget("local"),
          labelByState:{state:lifecycle,path:"state",labels:{running:"Stop",stopped:"Start"},fallback:"Start"},
          enabledWhen:{any:["running","stopped"].map(state=>({state:lifecycle,path:"state",equals:state}))}
        }),
        dsl.surface.text("first-administrator-identity",{
          value:adminDevice,label:"Proposed first administrator",when:isTarget("local")
        })
      ]
    });
    const networkConnectionActions=dsl.surface.region("network-connection-actions",{
      role:"commands",priority:"supporting",children:[
        dsl.surface.action("disconnect-network-slh",{
          intent:disconnectNetwork,when:isTarget("network"),
          enabledWhen:{state:networkSession,path:"authenticationState",equals:"AUTHENTICATED"}
        })
      ]
    });
    const serviceDetails=dsl.surface.region("service-details",{
      role:"inspector",priority:"supporting",prefer:{side:"end"},children:[
        dsl.surface.text("observed-service",{
          label:"Current service",value:lifecycle,when:isTarget("local")
        }),
        dsl.surface.text("last-successful-operation",{
          label:"Last successful operation",value:setupResult,when:isTarget("local")
        }),
        dsl.surface.text("network-connection-details",{
          label:"Network connection",value:networkSession,when:isTarget("network")
        }),
        dsl.surface.text("remote-seh-connection-details",{
          label:"SEH relay connection",value:remoteSehSession,when:isTarget("remote-seh")
        }),
        dsl.surface.text("seh-connection-details",{
          label:"SLH outbound SEH connection",value:sehStatus,when:isTarget("local")
        }),
        dsl.surface.text("network-seh-connection-details",{
          label:"Selected SLH outbound SEH connection",value:sehStatus,when:isTarget("network")
        })
      ]
    });
    const operationActivity=dsl.surface.region("operation-activity",{
      role:"status",priority:"supporting",children:[
        dsl.surface.receipt("activity")
      ]
    });
    const surface=dsl.surface.define("slh",{
      purpose:"manage",
      root:dsl.surface.region("semantic-source",{
        children:[identityNavigation,configurationTask,networkConfiguration,remoteSehConfiguration,networkConnectionActions,remoteSehConnectionActions,sehLifecycleNotebook,identityNotebook,serviceDetails,operationActivity]
      }),
      relations:[dsl.surface.relate(identityNavigation,"selects",configurationTask),
        dsl.surface.relate(identityNavigation,"selects",networkConfiguration),
        dsl.surface.relate(networkConnectionActions,"acts-on",networkConfiguration),
        dsl.surface.relate(serviceDetails,"informs",networkConfiguration),
        dsl.surface.relate(remoteSehConnectionActions,"acts-on",remoteSehConfiguration),
        dsl.surface.relate(identityNavigation,"selects",remoteSehConfiguration),
        dsl.surface.relate(serviceDetails,"informs",remoteSehConfiguration),
        dsl.surface.relate(serviceDetails,"informs",sehLifecycleNotebook),
        dsl.surface.relate(serviceDetails,"informs",configurationTask),
        dsl.surface.relate(operationActivity,"reports-for",configurationTask),
        dsl.surface.relate(serviceDetails,"informs",identityNotebook)]
    });
    return {
      states:[identities,identitySummary,adminDevice,target,lifecycle,setupResult,networkSession,remoteSehSession,sehStatus,sehDeployment,sehDeploymentReceipt,pending,enrolled,trash,retention,notebookResult],
      capabilities:[observer,setup,networkConnection,remoteSehConnection,sehLifecycleRead,sehLifecycleWrite,deploymentReader,deploymentWriter,administration,administrationRead],
      intents:[refreshIdentities,refreshIdentityStatus,refreshLifecycle,chooseIdentity,
        selectTarget,connectNetwork,disconnectNetwork,connectRemoteSeh,disconnectRemoteSeh,readSehStatus,...Object.values(sehLifecycleActions),inspectDeployment,...Object.values(deploymentIntents),configureAndEnable,toggleEnablement,toggleOrganization,loginAdministrator,logoutAdministrator,
        ...notebookReads,...notebookActions,setRetention,emptyTrash],
      surfaces:[surface]
    };
  }
);
