"use strict";
(function(global){
 const addr=/^0x[0-9a-f]{40}$/;const identityRE=/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;
 const str=v=>v==null?"":String(v);
 const normalize=v=>{const s=str(v).trim().toLowerCase();if(!addr.test(s)||s==="0x"+"0".repeat(40))throw Error("INVALID_WALLET_ADDRESS");return s;};
 const amountWei=value=>{if(typeof value!=="string"||! /^(?:0|[1-9][0-9]{0,58})(?:\.[0-9]{1,18})?$/.test(value))throw Error("INVALID_AMOUNT");const [w,f=""]=value.split(".");const n=BigInt(w)*10n**18n+BigInt(f.padEnd(18,"0"));if(n<=0n||n>=2n**256n)throw Error("INVALID_AMOUNT");return n.toString();};
 const displayWei=v=>{if(v==null)return "Not observed";if(!/^(0|[1-9][0-9]{0,77})$/.test(str(v)))throw Error("INVALID_BALANCE");const p=str(v).padStart(19,"0"),f=p.slice(-18).replace(/0+$/,"");return p.slice(0,-18)+(f?"."+f:"");};
 const canonical=v=>JSON.stringify(Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])));
 const hexMessage=v=>"0x"+Array.from(new TextEncoder().encode(canonical(v)),x=>x.toString(16).padStart(2,"0")).join("");
 const specs={
  "refresh-identities":["wallet-observer","identities","observeIdentities","external-read",[]],"discover-wallets":["wallet-observer","inventory","discoverWallets","external-read",[]],"refresh-portfolio":["wallet-observer","portfolio","refreshPortfolio","external-read",[]],"connect-provider":["wallet-observer","connect","connectProvider","external-read",[]],"add-wallet":["wallet-observer","watch","addWallet","external-read",["wallet"]],"add-org":["wallet-observer","org","addOrg","external-read",["identity","target","address","port","pin"]],"inspect-wallet":["wallet-observer","inspect","inspectWallet","external-read",["selected"]],"authorize-read":["wallet-observer","authorize","authorizeWalletRead","external-read",["selected"]],"next-history":["wallet-observer","history","nextHistoryPage","external-read",["selected"]],"send-credits":["wallet-transfer","send","sendFunds","consequential",["selected","destination","recipient","amount","asset"]],"retry-transfer":["wallet-transfer","retry","retryTransfer","consequential",[]]};
 function admitted(r){const s=specs[r?.intentId?.slice(7)];return !!s&&r.appId==="wallet"&&r.capabilityId==="capability:"+s[0]&&r.operation===s[1]&&r.runtimeMethod===s[2]&&r.risk===s[3]&&r.effectId==="effect:"+r.intentId.slice(7)+".request"&&r.arguments&&Object.keys(r.arguments).sort().join(",")===s[4].slice().sort().join(",")&&Object.values(r.arguments).every(v=>typeof v==="string");}
 const emptyReceipt=(status,message)=>({status,message,transferId:"",receiptId:"",source:"",recipient:"",amount:""});
 const contextKey=c=>c.target==="local"?"local":`network:${c.address}:${c.port}`;
 function create({fetcher=global.fetch.bind(global),provider=()=>global.ethereum,confirm=message=>global.confirm(message),storage=global.localStorage}={}){
  const STORAGE="mcel.wallet.portfolio.v2";let saved={wallets:[],orgs:[],pending:null};
  try{const value=JSON.parse(storage?.getItem(STORAGE)||"null");if(value&&Array.isArray(value.wallets)&&Array.isArray(value.orgs))saved=value;}catch(_){}
  const persist=()=>{storage?.setItem(STORAGE,JSON.stringify(saved));};
  let known=new Map(),networkInfo=[],identities=[],accounts=new Set(),proofs=new Map(),busy=false,orgDiscovered=false;
  const entries=new Map();
  async function post(path,header,body){const response=await fetcher(path,{method:"POST",credentials:"same-origin",headers:{Accept:"application/json","Content-Type":"application/json","X-MCEL-Wallet-Operation":header},body:JSON.stringify(body)});const out=await response.json();if(!response.ok||out.ok!==true)throw Error(str(out.error?.code)||"WALLET_REQUEST_FAILED");return out.result;}
  const api=body=>post("/api/applications/wallet/portfolio","portfolio",body);
  const orgApi=(context,operation,args={})=>post("/api/applications/wallet/org","org-wallet",{...context,operation,arguments:args});
  async function identityList(){const r=await fetcher("/api/applications/slh/identities",{credentials:"same-origin",headers:{Accept:"application/json"}});const x=await r.json();if(!r.ok||x.schema!=="mcel.slh.identities.v4b"||x.state!=="AVAILABLE"||!Array.isArray(x.identities))throw Error("IDENTITY_DIRECTORY_UNAVAILABLE");identities=x.identities.filter(i=>identityRE.test(i.deviceId)&&typeof i.label==="string").map(i=>({deviceId:i.deviceId,label:i.label}));return identities;}
  async function browserAccounts(request=false){const eth=provider();if(!eth?.request){accounts=new Set();return;}const a=await eth.request({method:request?"eth_requestAccounts":"eth_accounts"});accounts=new Set((Array.isArray(a)?a:[]).map(str).map(a=>a.toLowerCase()).filter(a=>addr.test(a)));if(request){saved.wallets=[...new Set([...saved.wallets,...accounts])];persist();}}
  async function sign(wallet,intent){const eth=provider();if(!eth?.request)throw Error("BROWSER_WALLET_PROVIDER_REQUIRED");const list=await eth.request({method:"eth_requestAccounts"});if(!Array.isArray(list)||!list.some(a=>str(a).toLowerCase()===wallet))throw Error("AUTHORIZE_SOURCE_ACCOUNT_IN_BROWSER_WALLET");const signature=await eth.request({method:"personal_sign",params:[hexMessage(intent),wallet]});if(!/^0x[0-9a-fA-F]{130}$/.test(signature))throw Error("INVALID_WALLET_SIGNATURE");return {intent,signature};}
  function project(e){const x=e.data||{},v=x.portfolio||{},b=v.balance||x.balance||{};return {id:e.id,owner:e.owner,network:e.network,wallet:e.wallet||"",hub:str(x.hub||e.hub),cluster:str(x.cluster_id),chain:str(x.chain_id),available:displayWei(b.available_credit_wei),held:displayWei(b.held_credit_wei),spent:displayWei(b.spent_credit_wei),earned:displayWei(b.earned_credit_wei??b.hub_earned_credit_wei),bridged:displayWei(b.bridge_completed_credit_wei),native:x.nativeWei!=null?displayWei(x.nativeWei)+" "+str(x.currency):str(x.nativeState||"Not observed"),status:str(x.creditState||"NOT_OBSERVED")+(x.nativeState&&x.nativeState!=="CURRENT"?" / "+x.nativeState:""),signer:e.managed?"ORG managed signer":accounts.has(e.wallet)?"Browser account":"Read only / signer not connected",observedAt:v.observed_at?new Date(v.observed_at*1000).toISOString():""};}
  function rows(){return [...entries.values()].sort((a,b)=>a.owner.localeCompare(b.owner)||a.network.localeCompare(b.network)||str(a.wallet).localeCompare(str(b.wallet))).map(project);}
  async function readUser(e,before=""){const proof=proofs.get(e.id);if(proof&&proof.intent.expires_at<=Math.floor(Date.now()/1000))proofs.delete(e.id);try{e.data=await api({operation:"observe",network:e.network,wallet:e.wallet,proof:proofs.get(e.id)||null,before});if(e.data.wallet_address!==e.wallet||e.data.network!==e.network)throw Error("WALLET_OBSERVATION_MISMATCH");}catch(err){e.data={creditState:"UNAVAILABLE: "+str(err.message),hub:e.hub};}return e;}
  async function readOrg(e,before=""){
    try{let x=await orgApi(e.context,"portfolio",{limit:50,before});if(x.pairingRequired){e.data={creditState:"PAIRING_REQUIRED: verify TLS pin "+str(x.tlsSha256)};return e;}
      const w=x.wallet,b=w?.binding,v=x.portfolio;if(w?.schema!=="main-computer.slh-org-wallet.v1"||!b||!v||v.wallet_address!==b.wallet_address||v.cluster_id!==b.cluster_id||v.chain_id!==b.chain_id)throw Error("ORG_OBSERVATION_MISMATCH");
      e.wallet=normalize(b.wallet_address);e.hub=b.hub_url;e.managed=x.managedSigner===true;
      const p=networkInfo.find(n=>n.hub.replace(/\/$/,"")===b.hub_url.replace(/\/$/,"")&&n.chain_id===b.chain_id);e.network=p?.network||"ORG custom Hub";
      e.data={portfolio:v,hub:b.hub_url,cluster_id:b.cluster_id,chain_id:b.chain_id,creditState:w.state,nativeState:"RPC_NOT_CONFIGURED"};
      if(p){const native=await api({operation:"observe",network:p.network,wallet:e.wallet,proof:null,before:""}).catch(()=>null);if(native){for(const k of ["nativeWei","nativeState","checkpoint","currency","escrow","escrowState"])if(k in native)e.data[k]=native[k];}}
    }catch(err){e.managed=false;e.data={creditState:"ORG_UNAVAILABLE: "+str(err.message),hub:e.hub};}
    return e;
  }
  async function readLocal(e,before=""){
    const binding=e.binding;
    try{
      const x=await api({operation:"local-observe",wallet:e.wallet,before});
      if(!x.binding||["wallet_address","hub_url","cluster_id","chain_id","revision"].some(k=>x.binding[k]!==binding[k]))throw Error("LOCAL_ORG_BINDING_CHANGED");
      e.data={hub:binding.hub_url,cluster_id:binding.cluster_id,chain_id:binding.chain_id,creditState:x.state,nativeState:"RPC_NOT_CONFIGURED"};
      if(x.state==="CURRENT"){
        if(x.portfolio?.wallet_address!==e.wallet||x.portfolio?.cluster_id!==binding.cluster_id||x.portfolio?.chain_id!==binding.chain_id)throw Error("LOCAL_ORG_OBSERVATION_MISMATCH");
        e.data.portfolio=x.portfolio;
      }
      if(e.network!=="ORG custom Hub"){
        const native=await api({operation:"observe",network:e.network,wallet:e.wallet,proof:null,before:""}).catch(()=>null);
        if(native?.wallet_address===e.wallet&&native.network===e.network)for(const k of ["nativeWei","nativeState","checkpoint","currency","escrow","escrowState"])if(k in native)e.data[k]=native[k];
      }
    }catch(err){e.data={hub:e.hub,cluster_id:binding.cluster_id,chain_id:binding.chain_id,creditState:"LOCAL_ORG_UNAVAILABLE: "+str(err.message)};}
    return e;
  }
  async function read(e,before=""){return e.local?readLocal(e,before):e.context?readOrg(e,before):readUser(e,before);}
  function installLocal(status){
    const b=status?.binding;
    if(!b){entries.delete("org:local");entries.set("org:local-status",{id:"org:local-status",owner:"ORG-SLH (This Computer)",network:"Unbound",wallet:"",statusOnly:true,data:{creditState:status?.state||"LOCAL_ORG_DISCOVERY_UNAVAILABLE"}});return;}
    entries.delete("org:local-status");
    const wallet=normalize(b.wallet_address),p=networkInfo.find(n=>n.hub.replace(/\/$/,"")===b.hub_url.replace(/\/$/,"")&&n.chain_id===b.chain_id);
    const old=entries.get("org:local"),same=old?.wallet===wallet&&old?.binding?.revision===b.revision&&old?.hub===b.hub_url&&old?.binding?.cluster_id===b.cluster_id&&old?.binding?.chain_id===b.chain_id;
    const e={id:"org:local",owner:"ORG-SLH (This Computer)",local:true,binding:b,wallet,hub:b.hub_url,network:p?.network||"ORG custom Hub",data:{hub:b.hub_url,cluster_id:b.cluster_id,chain_id:b.chain_id,creditState:"BOUND_NOT_OBSERVED"}};
    if(same&&old.context){e.context=old.context;e.managed=old.managed;}
    entries.set(e.id,e);
  }
  async function parallel(items,fn,n=6){let index=0;await Promise.all(Array.from({length:Math.min(n,items.length)},async()=>{while(index<items.length){const item=items[index++];await fn(item);}}));}
  function addContext(c){const key=contextKey(c),id="org:"+key;const e=entries.get(id)||{id,owner:"ORG-SLH "+key,network:"Unbound",wallet:"",context:c};e.context=c;entries.set(id,e);return e;}
  async function discoverOrgs(targets){
    if(orgDiscovered||!targets.length)return;
    try{if(!identities.length)await identityList();}catch(_){return;}
    orgDiscovered=true;
    const candidates=targets.map(t=>({target:"network",address:t.address,port:t.port,pin:""}));
    await parallel(candidates.filter(c=>!saved.orgs.some(x=>contextKey(x)===contextKey(c))),async c=>{
      // Try the user's existing LMS identities; never trust a new TLS fingerprint automatically.
      for(const identity of identities){const context={...c,identity:identity.deviceId};try{const v=await orgApi(context,"inspect");if(v.pairingRequired)break;if(v.schema!=="main-computer.slh-org-wallet.v1")continue;saved.orgs.push(context);addContext(context);persist();return;}catch(_){}}
      const e=addContext({...c,identity:""});e.data={creditState:"ADMIN_IDENTITY_REQUIRED"};
    },3);
  }
  async function discover(){
    const inventory=await api({operation:"inventory"});networkInfo=inventory.networks;installLocal(inventory.localOrg);try{await browserAccounts();}catch(_){}
    const wallets=new Set([...saved.wallets,...accounts,...networkInfo.flatMap(n=>n.wallets||[])].map(str).map(v=>v.toLowerCase()).filter(v=>addr.test(v)));
    // Remember public addresses, not provider credentials or private keys.
    saved.wallets=[...wallets];persist();
    for(const n of networkInfo)for(const wallet of wallets){const id=`user:${n.network}:${wallet}`;if(!entries.has(id))entries.set(id,{id,owner:"User",network:n.network,wallet,hub:n.hub});}
    for(const c of saved.orgs){if(c&&identityRE.test(c.identity)&&c.target==="network")addContext(c);}
    return inventory;
  }
  async function refresh(){
    const inventory=await discover();
    await discoverOrgs(inventory.orgTargets||[]);
    await parallel([...entries.values()].filter(e=>!e.statusOnly&&(e.local||!e.context||e.context.identity)),e=>read(e));return rows();
  }
  function entry(id){const e=entries.get(id);if(!e||!e.wallet)throw Error("SELECT_OBSERVED_WALLET");return e;}
  function detail(e){const p=project(e);delete p.id;delete p.owner;const view=e.data?.portfolio;return {...p,history:view?.transactions?.map(t=>`${t.created_at} | ${t.transaction_type} | ${displayWei(t.credit_wei)} | ${t.transaction_id}${t.deposit_id?" | deposit "+t.deposit_id:""}`).join("\n")||"No verified transaction history",nextPage:view?.next_cursor?"More transactions available":"No further page",bridgeDetails:view?.bridge_deposits?.map(d=>`${d.created_at} | ${d.status} | ${displayWei(d.deposit_amount_units)} | ${d.deposit_id}${d.completion_tx_hash?" | completed "+d.completion_tx_hash:""}`).join("\n")||"No verified deposit records",escrow:e.data?.escrow?Object.entries(e.data.escrow).map(([k,v])=>k+": "+displayWei(v)).join("\n")+"\nOn-chain collateral, not additional spendable Hub credits":str(e.data?.escrowState||"Not observed")};}
  async function authorizeRead(e){if(e.local||e.context)return detail(await read(e));if(!e.data?.cluster_id)await read(e);const x=e.data;if(!x?.cluster_id||!x.chain_id)throw Error("HUB_SCOPE_UNVERIFIED");if(!confirm(`Authorize read-only Hub inspection for ${e.wallet} on ${e.network} (${x.cluster_id}) for 24 hours? This cannot spend credits.`))return detail(e);const intent={schema:"main-computer.wallet-read.v1",cluster_id:x.cluster_id,chain_id:x.chain_id,wallet_address:e.wallet,expires_at:Math.floor(Date.now()/1000)+86300};proofs.set(e.id,await sign(e.wallet,intent));return detail(await read(e));}
  async function settlePending(){const p=saved.pending;if(!p)return emptyReceipt("NONE","No pending transfer");const base={transferId:p.intent?.transfer_id||p.hash||"",receiptId:"",source:p.source,recipient:p.recipient,amount:displayWei(p.amount)};
    try{
      if(p.kind==="native"){
        if(!p.hash)throw Error("WALLET_SUBMISSION_OUTCOME_UNKNOWN: inspect your browser wallet before clearing");
        const e=entry(p.id);await read(e);const eth=provider();await verifyNativeNetwork(e,eth);
        const r=await eth.request({method:"eth_getTransactionReceipt",params:[p.hash]});if(!r)return {...base,status:"PENDING",message:"Awaiting chain receipt",receiptId:p.hash};
        const tx=await eth.request({method:"eth_getTransactionByHash",params:[p.hash]});if(!tx||str(tx.from).toLowerCase()!==p.source||str(tx.to).toLowerCase()!==p.recipient||BigInt(tx.value)!==BigInt(p.amount))throw Error("CHAIN_RECEIPT_MISMATCH");
        saved.pending=null;persist();return {...base,status:BigInt(r.status)===1n?"CONFIRMED":"FAILED",message:"Chain receipt observed",receiptId:p.hash};
      }
      const request=async status=>p.context?orgApi(p.context,p.managed?(status?"managed-transfer-status":"managed-transfer"):(status?"transfer-status":"transfer"),p.managed?{intent:p.intent}:{intent:p.intent,signature:p.signature}):api({operation:status?"transfer-status":"send",network:p.network,wallet:p.source,intent:p.intent,signature:p.signature});
      let r;try{r=await request(false);}catch(error){r=await request(true);if(r?.status==="expired_not_found"&&Object.keys(p.intent).every(k=>p.intent[k]===r[k])){saved.pending=null;persist();return {...base,status:"EXPIRED_NOT_COMMITTED",message:"Hub reports no receipt for expired signed intent; no transfer confirmed."};}if(r?.status!=="confirmed")throw error;}
      if(r?.ok!==true||r.status!=="confirmed"||!r.receipt_id||Object.keys(p.intent).some(k=>p.intent[k]!==r[k]))throw Error("TRANSFER_RECEIPT_UNVERIFIED");saved.pending=null;persist();return {...base,status:"CONFIRMED",message:"Hub confirmed atomic transfer",receiptId:r.receipt_id};
    }catch(err){return {...base,status:"UNVERIFIED",message:str(err.message)+". Resolve the same transfer before submitting another."};}
  }
  async function verifyNativeNetwork(e,eth){if(!eth?.request||!e.data?.checkpoint||e.data.nativeState!=="CURRENT")throw Error("CHAIN_OBSERVATION_REQUIRED");const chain=await eth.request({method:"eth_chainId"});if(BigInt(chain)!==BigInt(e.data.chain_id))throw Error("SWITCH_BROWSER_WALLET_TO_SELECTED_NETWORK");const block=await eth.request({method:"eth_getBlockByNumber",params:[e.data.checkpoint.number,false]});if(!block||str(block.hash).toLowerCase()!==str(e.data.checkpoint.hash).toLowerCase())throw Error("BROWSER_RPC_NETWORK_MISMATCH");}
  async function send(e,a){if(saved.pending)throw Error("RESOLVE_PENDING_TRANSFER_FIRST");const wei=amountWei(a.amount.trim());await read(e);const x=e.data,source=e.wallet;
    const dest=a.destination&&!a.recipient.trim()?entry(a.destination):null;
    if(dest){await read(dest);if(dest.network!==e.network||!dest.data?.chain_id||dest.data.chain_id!==x.chain_id||(a.asset==="compute-credit"&&(!dest.data.cluster_id||dest.data.cluster_id!==x.cluster_id)))throw Error("CROSS_NETWORK_OR_CLUSTER_TRANSFER_NOT_SUPPORTED");}
    const recipient=normalize(dest?dest.wallet:a.recipient);if(recipient===source)throw Error("SELF_TRANSFER");if(!source)throw Error("SOURCE_NOT_OBSERVED");
    if(a.asset==="native"){
      if(x.nativeState!=="CURRENT"||BigInt(wei)>BigInt(x.nativeWei))throw Error("INSUFFICIENT_OR_UNKNOWN_NATIVE_BALANCE");const eth=provider();await verifyNativeNetwork(e,eth);const acc=await eth.request({method:"eth_requestAccounts"});if(!acc.some(v=>str(v).toLowerCase()===source))throw Error("SOURCE_WALLET_SIGNER_REQUIRED");
      if(!confirm(`Send ${displayWei(wei)} ${x.currency} (plus network fee) on ${e.network}?\nFrom: ${source}\nTo: ${recipient}`))return emptyReceipt("CANCELLED","No funds submitted");
      await verifyNativeNetwork(e,eth);saved.pending={kind:"native",id:e.id,source,recipient,amount:wei,hash:""};persist();try{const hash=await eth.request({method:"eth_sendTransaction",params:[{from:source,to:recipient,value:"0x"+BigInt(wei).toString(16)}]});if(!/^0x[0-9a-fA-F]{64}$/.test(hash))throw Error("INVALID_CHAIN_TRANSACTION_HASH");saved.pending.hash=hash;persist();}catch(err){if(err?.code===4001){saved.pending=null;persist();return emptyReceipt("CANCELLED","Wallet rejected transaction");}return emptyReceipt("UNVERIFIED","Native transaction outcome unknown: "+str(err.message));}return settlePending();
    }
    if(a.asset!=="compute-credit"||x.creditState!=="CURRENT"||!x.portfolio||BigInt(wei)>BigInt(x.portfolio.balance.available_credit_wei))throw Error("INSUFFICIENT_OR_UNKNOWN_AVAILABLE_CREDITS");
    if(!confirm(`Transfer ${displayWei(wei)} Compute Credits on ${e.network} / ${x.cluster_id}?\nFrom: ${source}\nTo: ${recipient}\nOnly this Hub ledger is affected; not a cross-network bridge.`))return emptyReceipt("CANCELLED","No funds submitted");
    const intent={schema:"main-computer.org-credit-transfer.v1",transfer_id:global.crypto.randomUUID(),cluster_id:x.cluster_id,chain_id:x.chain_id,wallet_address:source,recipient_wallet_address:recipient,credit_wei:wei,expires_at:Math.floor(Date.now()/1000)+300};if(e.local&&!e.context&&e.network==="ORG custom Hub")throw Error("AUTHORIZE_LOCAL_ORG_TRANSFER_FIRST");const managed=!!(e.context&&e.managed);const proof=managed?{intent}:await sign(source,intent);
    saved.pending={kind:"credit",id:e.id,context:e.context||null,network:e.network,managed,source,recipient,amount:wei,...proof};persist();return settlePending();
  }
  return Object.freeze({authorize:async r=>admitted(r)&&!busy,invoke:async r=>{if(!admitted(r)||busy)throw Error("WALLET_OPERATION_NOT_ADMITTED");busy=true;try{const a=r.arguments;switch(r.intentId){
   case "intent:refresh-identities":return identityList();
   case "intent:discover-wallets":await discover();return rows();
   case "intent:refresh-portfolio":return refresh();
   case "intent:connect-provider":await browserAccounts(true);return refresh();
   case "intent:add-wallet":saved.wallets=[...new Set([...saved.wallets,normalize(a.wallet)])];persist();return refresh();
   case "intent:add-org":{if(!identityRE.test(a.identity)||!identities.some(i=>i.deviceId===a.identity))throw Error("SELECT_ADMIN_IDENTITY");const c={identity:a.identity,target:a.target,address:a.target==="local"?"":a.address.trim(),port:a.target==="local"?"":a.port.trim(),pin:a.target==="local"?"":a.pin.trim().toLowerCase()};const local=c.target==="local"?entries.get("org:local"):null;if(c.target==="local"&&!local)throw Error("LOCAL_ORG_WALLET_NOT_CONFIGURED");
      const e=local?{id:local.id,owner:local.owner,wallet:local.wallet,hub:local.hub,network:local.network,context:c}:addContext(c);await readOrg(e);
      if(local){if(!e.data?.portfolio||e.wallet!==local.wallet||e.data.cluster_id!==local.binding.cluster_id||e.data.chain_id!==local.binding.chain_id||e.hub!==local.hub)throw Error("LOCAL_ORG_AUTHORIZATION_UNVERIFIED");local.context=c;local.managed=e.managed;await readLocal(local);}
      if(e.data?.portfolio){saved.orgs=saved.orgs.filter(x=>contextKey(x)!==contextKey(c));saved.orgs.push({...c,pin:""});persist();}return rows();}
   case "intent:inspect-wallet":return detail(await read(entry(a.selected)));
   case "intent:authorize-read":return authorizeRead(entry(a.selected));
   case "intent:next-history":{const e=entry(a.selected),cursor=e.data?.portfolio?.next_cursor;return cursor?detail(await read(e,cursor)):detail(e);}
   case "intent:send-credits":return send(entry(a.selected),a);
   case "intent:retry-transfer":return settlePending();
   default:throw Error("WALLET_OPERATION_NOT_ADMITTED");
  }}finally{busy=false;}}});
 }
 global.McelSemanticWalletPorts=Object.freeze({create,canonical,amountWei,displayWei,admitted});
})(globalThis);
