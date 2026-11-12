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
    const networkRows=dsl.field.list(dsl.field.record({
      id:text,endpoint:text,tlsSha256:text,organizationState:text,
      authenticationState:text,observedAt:text,explanation:text
    }),{key:"id"});
    const networkObservation=dsl.state.derived("network-observations",networkRows);
    const networkIdentity=dsl.capability.external("network-slh-identity",{
      risk:"external-read",
      description:"Read-only inspection of an independently pinned remote Organization Service TLS identity"
    });
    networkIdentity.operation("inspect","inspectPinnedNetworkSlh",{
      inputs:{address:text,port:dsl.field.integer(),tlsSha256:text},result:networkRows
    });
    const inspectNetwork=dsl.intent.capabilityRequest("inspect-network-slh",networkIdentity,{
      operation:"inspect",inputs:{address:text,port:dsl.field.integer(),tlsSha256:text},
      resultTo:networkObservation
    });
    const isTarget=choice=>({state:target,equals:choice});
    const networkContext=dsl.surface.region("observation-query",{
      role:"navigation",priority:"secondary",prefer:{side:"start"},children:[
        dsl.surface.form("network-slh-target",{
          fields:{address:text,port:dsl.field.integer(),tlsSha256:text},
          defaults:{port:8899},intent:inspectNetwork,when:isTarget("network")
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
        dsl.surface.action("disable-org-slh",{
          intent:disableOrganization,
          // A persisted enabled service may be starting or recovering from a
          // crash. Disable must remain available to stop its auto-restart.
          when:isTarget("local"),
          enabledWhen:{any:["running","starting","failed","stopped_unexpectedly"]
            .map(status=>({state:lifecycle,path:"state",equals:status}))}
        })
      ]
    });
    const networkResults=dsl.surface.region("network-results",{
      role:"results",priority:"secondary",children:[
        dsl.surface.collection("verified-network-target",{
          key:"id",fields:["endpoint","organizationState","authenticationState","tlsSha256"],
          state:networkObservation,when:isTarget("network")
        })
      ]
    });
    const surface=dsl.surface.define("slh",{
      purpose:"manage",
      root:dsl.surface.region("semantic-source",{
        children:[networkContext,identityNavigation,configurationTask,serviceLifecycle,networkResults]
      }),
      relations:[dsl.surface.relate(identityNavigation,"selects",configurationTask),
        dsl.surface.relate(serviceLifecycle,"acts-on",configurationTask)]
    });
    return {
      states:[identities,identitySummary,adminDevice,target,lifecycle,setupResult,networkObservation],
      capabilities:[observer,setup,networkIdentity],
      intents:[refreshIdentities,refreshIdentityStatus,refreshLifecycle,chooseIdentity,
        selectTarget,inspectNetwork,configureAndEnable,disableOrganization],
      surfaces:[surface]
    };
  }
);
