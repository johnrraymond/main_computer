"use strict";
// Browser-port regression without a running ORG-SLH: verifies that the
// Organization remains the authorization authority and that no ambiguous
// mutation result is ever replayed.
const test=require("node:test");
const assert=require("node:assert/strict");
require("../main_computer/web/applications/scripts/mcel-semantic-slh-ports.js");
const create=globalThis.McelSemanticSlhPorts.create;
const id="a".repeat(32);
const reject={appId:"slh",intentId:"intent:organization-reject",risk:"consequential",
  capabilityId:"capability:organization-identity-administration",
  effectId:"effect:organization-reject.request",operation:"reject",
  runtimeMethod:"rejectOrganizationEnrollment",arguments:{recordId:id}};
const read={appId:"slh",intentId:"intent:read-organization-pending",risk:"external-read",
  capabilityId:"capability:organization-identity-observer",
  effectId:"effect:read-organization-pending.request",operation:"read-pending",
  runtimeMethod:"observeOrganizationPending",arguments:{}};
const reply=(data,status=200)=>({ok:status<400,status,json:async()=>data});
function fixture(serve,expiry){
  const calls=[];
  let logins=0;
  const port=create({appId:"slh",fetcher:async(url,options)=>{
    const body=options.body?JSON.parse(options.body):{};
    calls.push({url,body,token:options.headers?.Authorization||""});
    if(url==="/api/applications/slh/setup/action"){
      if(body.action==="authenticate")return reply({access_token:(++logins).toString().repeat(40),
        device_id:body.arguments.adminDeviceId,expires_at:expiry?.() ?? Math.floor(Date.now()/1000)+900});
      if(body.action==="logout")return reply({outcome:"SIGNED_OUT"});
    }
    return serve(url,body,options);
  }});
  port.setIdentity("owner");
  return {port,calls,logins:()=>logins};
}

test("an expired browser-held bearer is refreshed before Reject; never sent to ORG",async()=>{
  const original=Date.now;
  let now=original();
  Date.now=()=>now;
  try{
    const f=fixture((url,body,opts)=>reply({ok:true,result:body.operation==="read-pending"?[]:
      {outcome:"DENIED",message:"DENIED"}}),()=>Math.floor(now/1000)+60);
    assert.equal((await f.port.invoke(read)).length,0);
    assert.equal(f.logins(),1);
    now+=40000; // token is now within the 30-second renewal margin
    assert.equal((await f.port.invoke(reject)).outcome,"DENIED");
    const actions=f.calls.filter(x=>x.url.endsWith("identity-notebook"));
    assert.equal(f.logins(),2);
    assert.match(actions[0].token,/^Bearer 1+$/);
    assert.match(actions[1].token,/^Bearer 2+$/);
  }finally{Date.now=original;}
});

test("401 SESSION_INVALID from a direct ORG notebook operation renews once, then succeeds",async()=>{
  let actions=0;
  const f=fixture((url,body,opts)=>{
    assert.equal(body.operation,"reject");
    if(++actions===1)return reply({error:{code:"SESSION_INVALID"}},401);
    return reply({ok:true,result:{outcome:"DENIED",message:"DENIED"}});
  });
  assert.equal((await f.port.invoke(reject)).outcome,"DENIED");
  assert.equal(actions,2);
  assert.equal(f.logins(),2);
  assert.equal(f.calls.filter(x=>x.url.endsWith("identity-notebook")).length,2);
});

test("403 and revoked authority are not retried",async()=>{
  let actions=0;
  const f=fixture(()=>{actions++;return reply({error:{code:"ADMIN_REQUIRED"}},403);});
  await assert.rejects(f.port.invoke(reject),/MCEL_SLH_NOTEBOOK:ADMIN_REQUIRED/);
  assert.equal(actions,1);
  assert.equal(f.logins(),1);
});

test("a second 401 SESSION_INVALID is a final refusal, not an infinite retry",async()=>{
  let actions=0;
  const f=fixture(()=>{actions++;return reply({error:{code:"SESSION_INVALID"}},401);});
  await assert.rejects(f.port.invoke(reject),/MCEL_SLH_NOTEBOOK:SESSION_INVALID/);
  assert.equal(actions,2);
  assert.equal(f.logins(),2);
});

test("identity switching during a refused request cannot retry under another signer",async()=>{
  let actions=0;
  let f;
  f=fixture(()=>{
    actions++;
    f.port.setIdentity("other");
    return reply({error:{code:"SESSION_INVALID"}},401);
  });
  await assert.rejects(f.port.invoke(reject),/MCEL_SLH_PORT:IDENTITY_CONTEXT_CHANGED/);
  assert.equal(actions,1);
  assert.equal(f.logins(),1);
});

test("transport ambiguity never triggers a second consequential request",async()=>{
  let actions=0;
  const f=fixture(()=>{actions++;throw Error("connection lost after unknown commit");});
  await assert.rejects(f.port.invoke(reject),/MCEL_SLH_NOTEBOOK:TRANSPORT_UNAVAILABLE/);
  assert.equal(actions,1);
  assert.equal(f.logins(),1);
});

test("explicit SESSION_REVOKED invalidates UI auth without attempting another login",async()=>{
  let actions=0,verified=[];
  const f=fixture(()=>{actions++;return reply({error:{code:"SESSION_REVOKED"}},401);});
  f.port.onAuthenticationChanged(value=>verified.push(value));
  await assert.rejects(f.port.invoke(reject),/MCEL_SLH_NOTEBOOK:SESSION_REVOKED/);
  assert.equal(actions,1);
  assert.equal(f.logins(),1);
  assert.deepEqual(verified,[true,false]);
});

test("multi-step Worker rename must not replay on a late 401",async()=>{
  const prior=globalThis.confirm;
  globalThis.confirm=()=>true;
  try{
    let actions=0;
    const f=fixture(()=>{actions++;return reply({error:{code:"SESSION_INVALID"}},401);});
    const rename={appId:"slh",intentId:"intent:rename-worker",risk:"consequential",
      capabilityId:"capability:slh-worker-admin",effectId:"effect:rename-worker.request",
      operation:"rename",runtimeMethod:"renameSlhWorker",
      arguments:{deviceId:"worker-1",label:"Changed"}};
    await assert.rejects(f.port.invoke(rename),/MCEL_SLH_PORT:worker-mutation-refused/);
    assert.equal(actions,1);
    assert.equal(f.logins(),1);
  }finally{globalThis.confirm=prior;}
});
