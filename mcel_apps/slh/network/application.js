"use strict";
// Network SLH is deliberately a separate semantic authority from localhost SLH.
// No service bootstrap, local admin identity, bearer, or Worker mutation is bound here.
const mcel = require("@mcel/app");
module.exports = mcel.defineApp(
  {id:"slh-network",title:"Network Secure Local Hub",semanticVersion:"1"},
  dsl => {
    const text=dsl.field.text(), integer=dsl.field.integer();
    const observationShape=dsl.field.list(dsl.field.record({
      id:text,endpoint:text,tlsSha256:text,organizationState:text,
      authenticationState:text,observedAt:text,explanation:text
    }),{key:"id"});
    const observation=dsl.state.derived("network-observations",observationShape);
    const inspection=dsl.capability.external("network-slh-identity",{
      risk:"external-read",description:"Verify a private-network SLH Organization Service using an explicitly pinned TLS identity. Read only."
    });
    inspection.operation("inspect","inspectPinnedNetworkSlh",{
      inputs:{address:text,port:integer,tlsSha256:text},result:observationShape
    });
    const inspect=dsl.intent.capabilityRequest("inspect-network-slh",inspection,{
      operation:"inspect",inputs:{address:text,port:integer,tlsSha256:text},resultTo:observation
    });
    const surface=dsl.surface.define("network-slh",{purpose:"monitor",root:
      dsl.surface.region("semantic-source",{children:[
        dsl.surface.form("network-slh-target",{
          fields:{address:text,port:integer,tlsSha256:text},defaults:{port:8899},intent:inspect
        }),
        dsl.surface.collection("verified-network-target",{
          key:"id",fields:["endpoint","organizationState","authenticationState","tlsSha256"],state:observation
        }),
        dsl.surface.receipt("network-target-evidence")
      ]})
    });
    return {states:[observation],capabilities:[inspection],intents:[inspect],surfaces:[surface]};
  }
);
