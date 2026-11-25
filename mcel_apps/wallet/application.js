"use strict";
const mcel=require("@mcel/app");
module.exports=mcel.defineApp({id:"wallet",title:"Wallet",semanticVersion:"2"},dsl=>{
  const text=dsl.field.text();
  const row=dsl.field.record({id:text,owner:text,network:text,wallet:text,hub:text,cluster:text,chain:text,available:text,held:text,spent:text,earned:text,bridged:text,native:text,status:text,signer:text,observedAt:text});
  const rows=dsl.field.list(row,{key:"id"});
  const portfolio=dsl.state.derived("portfolio",rows);
  const selected=dsl.state.rendererLocal("selected-wallet",text,{initial:""});
  const choose=dsl.intent.selectFromCollection("choose-wallet",portfolio,selected);
  const destination=dsl.state.rendererLocal("selected-recipient",text,{initial:""});
  const chooseDestination=dsl.intent.selectFromCollection("choose-recipient",portfolio,destination);
  const info=dsl.field.record({wallet:text,network:text,hub:text,cluster:text,chain:text,available:text,held:text,spent:text,earned:text,bridged:text,native:text,status:text,signer:text,observedAt:text,history:text,nextPage:text,bridgeDetails:text,escrow:text});
  const detail=dsl.state.derived("wallet-detail",info);
  const receiptShape=dsl.field.record({status:text,message:text,transferId:text,receiptId:text,source:text,recipient:text,amount:text});
  const receipt=dsl.state.derived("transfer-receipt",receiptShape);
  const identityRows=dsl.field.list(dsl.field.record({deviceId:text,label:text}),{key:"deviceId"});
  const identities=dsl.state.derived("identities",identityRows);
  const identity=dsl.state.rendererLocal("selected-identity",text,{initial:""});
  const chooseIdentity=dsl.intent.selectFromCollection("choose-identity",identities,identity);
  const reader=dsl.capability.external("wallet-observer",{risk:"external-read",description:"Wallet-scoped authoritative Hub, chain, provider and authenticated ORG-SLH observations"});
  reader.operation("identities","observeIdentities",{result:identityRows});
  reader.operation("inventory","discoverWallets",{result:rows});
  reader.operation("portfolio","refreshPortfolio",{result:rows});
  reader.operation("connect","connectProvider",{result:rows});
  reader.operation("watch","addWallet",{inputs:{wallet:text},result:rows});
  reader.operation("org","addOrg",{inputs:{identity:text,target:text.oneOf(["local","network"]),address:text,port:text,pin:text},result:rows});
  reader.operation("inspect","inspectWallet",{inputs:{selected:text},result:info});
  reader.operation("authorize","authorizeWalletRead",{inputs:{selected:text},result:info});
  reader.operation("history","nextHistoryPage",{inputs:{selected:text},result:info});
  const control=dsl.capability.external("wallet-transfer",{risk:"consequential",description:"Explicit wallet spending authorization; atomic Hub transfer or native chain transaction"});
  control.operation("send","sendFunds",{inputs:{selected:text,destination:text,recipient:text,amount:text,asset:text.oneOf(["compute-credit","native"])},result:receiptShape,risk:"consequential"});
  control.operation("retry","retryTransfer",{result:receiptShape,risk:"consequential"});
  const refreshIdentities=dsl.intent.capabilityRequest("refresh-identities",reader,{operation:"identities",resultTo:identities});
  const discover=dsl.intent.capabilityRequest("discover-wallets",reader,{operation:"inventory",resultTo:portfolio});
  const refresh=dsl.intent.capabilityRequest("refresh-portfolio",reader,{operation:"portfolio",resultTo:portfolio});
  const connect=dsl.intent.capabilityRequest("connect-provider",reader,{operation:"connect",resultTo:portfolio});
  const watch=dsl.intent.capabilityRequest("add-wallet",reader,{operation:"watch",inputs:{wallet:text},resultTo:portfolio});
  const org=dsl.intent.capabilityRequest("add-org",reader,{operation:"org",inputs:{target:text.oneOf(["local","network"]),address:text,port:text,pin:text},arguments:{identity},resultTo:portfolio});
  const inspect=dsl.intent.capabilityRequest("inspect-wallet",reader,{operation:"inspect",arguments:{selected},resultTo:detail});
  const authorize=dsl.intent.capabilityRequest("authorize-read",reader,{operation:"authorize",arguments:{selected},resultTo:detail});
  const next=dsl.intent.capabilityRequest("next-history",reader,{operation:"history",arguments:{selected},resultTo:detail});
  const asset=text.oneOf(["compute-credit","native"]);
  const send=dsl.intent.capabilityRequest("send-credits",control,{operation:"send",inputs:{recipient:text,amount:text,asset},arguments:{selected,destination},resultTo:receipt,confirmationRequired:true});
  const retry=dsl.intent.capabilityRequest("retry-transfer",control,{operation:"retry",resultTo:receipt,confirmationRequired:true});
  const view=dsl.state.rendererLocal("view",text.oneOf(["portfolio","wallet-details","transfer","organization-slh"]),{initial:"portfolio"});
  const switchView=dsl.intent.selectView("switch-view",view);
  const inView=equals=>({state:view,equals});
  const nav=dsl.surface.region("navigation",{role:"navigation",priority:"secondary",prefer:{side:"start"},children:[dsl.surface.action("wallet-navigation",{intent:switchView})]});
  const balances=dsl.surface.region("portfolio",{role:"main-workarea",priority:"primary",children:[
    dsl.surface.action("refresh-portfolio",{intent:refresh,label:"Refresh all wallets",when:inView("portfolio")}),
    dsl.surface.action("connect-wallets",{intent:connect,label:"Connect browser wallets",when:inView("portfolio")}),
    dsl.surface.form("watch-wallet",{fields:{wallet:text},defaults:{wallet:""},resetControl:false,submitLabel:"Monitor another wallet",intent:watch,when:inView("portfolio")}),
    dsl.surface.collection("all-wallets",{key:"id",fields:["owner","network","wallet","available","held","bridged","native","status"],state:portfolio,select:choose,clearable:false,label:"User and ORG-SLH wallets • select source"})]});
  const details=dsl.surface.region("details",{role:"main-workarea",priority:"primary",children:[
    dsl.surface.action("inspect-wallet",{intent:inspect,label:"Refresh selected wallet",when:inView("wallet-details")}),
    dsl.surface.action("authorize-read",{intent:authorize,label:"Authorize Hub inspection (read only)",when:inView("wallet-details")}),
    dsl.surface.text("wallet-detail",{value:detail,label:"Selected wallet • bridged is cumulative, not extra spendable credit",when:inView("wallet-details")}),
    dsl.surface.action("next-history",{intent:next,label:"Older Hub transactions",when:inView("wallet-details")})]});
  const transfer=dsl.surface.region("transfer",{role:"main-workarea",priority:"primary",children:[
    dsl.surface.collection("recipient-wallets",{key:"id",fields:["owner","network","wallet"],state:portfolio,select:chooseDestination,clearable:true,label:"Recipient wallet (or type an address below)",when:inView("transfer")}),
    dsl.surface.form("transfer-funds",{fields:{recipient:text,amount:text,asset},defaults:{recipient:"",amount:"",asset:"compute-credit"},resetControl:false,submitLabel:"Review and transfer from selected wallet",intent:send,when:inView("transfer")}),
    dsl.surface.action("retry-transfer",{intent:retry,label:"Resolve pending transfer",when:inView("transfer")}),
    dsl.surface.text("transfer-receipt",{value:receipt,label:"Transfer result",when:inView("transfer")})]});
  const orgs=dsl.surface.region("org-slh",{role:"main-workarea",priority:"primary",children:[
    dsl.surface.action("discover-local-wallet",{intent:discover,label:"Discover saved wallet addresses",when:inView("organization-slh")}),
    dsl.surface.collection("local-identities",{key:"deviceId",fields:["label"],state:identities,select:chooseIdentity,clearable:true,label:"Identity for remote connection or managed spending",when:inView("organization-slh")}),
    dsl.surface.action("refresh-identities",{intent:refreshIdentities,when:inView("organization-slh")}),
    dsl.surface.form("organization-target",{fields:{target:text.oneOf(["local","network"]),address:text,port:text,pin:text},defaults:{target:"network",address:"",port:"",pin:""},resetControl:false,submitLabel:"Connect remote ORG-SLH or authorize local spending",intent:org,when:inView("organization-slh")})]});
  const activity=dsl.surface.region("activity",{role:"status",priority:"supporting",children:[dsl.surface.receipt("wallet-activity",{entries:[
    dsl.activity.info("Wallet portfolio observed",{intent:refresh,dispositions:["completed"]}),
    dsl.activity.warn("Portfolio refresh failed",{intent:refresh,dispositions:["failed","refused","unknown"]}),
    dsl.activity.info("Transfer operation returned; inspect receipt",{intent:send,dispositions:["completed"]}),
    dsl.activity.warn("Transfer not verified",{intent:send,dispositions:["failed","refused","unknown"]})]})]});
  const surface=dsl.surface.define("wallet",{purpose:"manage",root:dsl.surface.region("source",{children:[nav,balances,details,transfer,orgs,activity]}),relations:[dsl.surface.relate(nav,"selects",balances),dsl.surface.relate(balances,"informs",details),dsl.surface.relate(details,"informs",transfer),dsl.surface.relate(orgs,"informs",balances),dsl.surface.relate(activity,"reports-for",transfer)]});
  return {states:[portfolio,selected,destination,detail,receipt,identities,identity,view],capabilities:[reader,control],intents:[choose,chooseDestination,chooseIdentity,refreshIdentities,discover,refresh,connect,watch,org,inspect,authorize,next,send,retry,switchView],surfaces:[surface]};
});
