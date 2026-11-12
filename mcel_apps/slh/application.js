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
      text.oneOf(["local","network"]),{initial:"local"});
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
    setup.operation("disable","disableSlhOrganization",{
      inputs:{adminDeviceId:text},result:setupShape,risk:"consequential"
    });
    const configureAndEnable=dsl.intent.capabilityRequest("configure-and-enable-organization",setup,{
      operation:"configure-and-enable",inputs:{bindAddress:text,port:dsl.field.integer()},
      arguments:{adminDeviceId:adminDevice},resultTo:setupResult,
      confirmationRequired:false
    });
    const disableOrganization=dsl.intent.capabilityRequest("disable-organization",setup,{
      operation:"disable",arguments:{adminDeviceId:adminDevice},
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
          defaults:{port:8899},submitLabel:"Connect to Network SLH",
          intent:connectNetwork,when:isTarget("network")
        }),
        dsl.surface.text("network-selected-identity",{
          value:adminDevice,label:"Selected local identity",when:isTarget("network")
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
          defaults:{bindAddress:"0.0.0.0",port:8899},
          submitLabel:"Configure ORG-SLH",intent:configureAndEnable,
          acceptResult:{
            bindAddress:{state:lifecycle,field:"bindAddress",pick:"bindAddress",whenIntent:refreshLifecycle},
            port:{state:lifecycle,field:"port",pick:"port",whenIntent:refreshLifecycle}
          },when:isTarget("local")
        }),
        dsl.surface.text("first-administrator-identity",{
          value:adminDevice,label:"Proposed first administrator",when:isTarget("local")
        })
      ]
    });
    const serviceLifecycle=dsl.surface.region("service-lifecycle",{
      role:"commands",priority:"supporting",children:[
        dsl.surface.action("login-administrator",{
          intent:loginAdministrator,when:isTarget("local")
        }),
        dsl.surface.action("logout-administrator",{
          intent:logoutAdministrator,when:isTarget("local")
        }),
        dsl.surface.action("disable-org-slh",{
          intent:disableOrganization,when:isTarget("local"),
          enabledWhen:{any:["running","starting","failed","stopped_unexpectedly"]
            .map(status=>({state:lifecycle,path:"state",equals:status}))}
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
        children:[identityNavigation,configurationTask,networkConfiguration,serviceLifecycle,networkConnectionActions,identityNotebook,serviceDetails,operationActivity]
      }),
      relations:[dsl.surface.relate(identityNavigation,"selects",configurationTask),
        dsl.surface.relate(serviceLifecycle,"acts-on",configurationTask),
        dsl.surface.relate(identityNavigation,"selects",networkConfiguration),
        dsl.surface.relate(networkConnectionActions,"acts-on",networkConfiguration),
        dsl.surface.relate(serviceDetails,"informs",networkConfiguration),
        dsl.surface.relate(serviceDetails,"informs",configurationTask),
        dsl.surface.relate(operationActivity,"reports-for",configurationTask),
        dsl.surface.relate(serviceDetails,"informs",identityNotebook)]
    });
    return {
      states:[identities,identitySummary,adminDevice,target,lifecycle,setupResult,networkSession,pending,enrolled,trash,retention,notebookResult],
      capabilities:[observer,setup,networkConnection,administration,administrationRead],
      intents:[refreshIdentities,refreshIdentityStatus,refreshLifecycle,chooseIdentity,
        selectTarget,connectNetwork,disconnectNetwork,configureAndEnable,disableOrganization,loginAdministrator,logoutAdministrator,
        ...notebookReads,...notebookActions,setRetention,emptyTrash],
      surfaces:[surface]
    };
  }
);
