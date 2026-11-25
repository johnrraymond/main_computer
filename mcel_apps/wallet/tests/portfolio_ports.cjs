const assert=require('node:assert/strict');
const {webcrypto}=require('node:crypto');globalThis.crypto=webcrypto;
require('../../../main_computer/web/applications/scripts/mcel-semantic-wallet-ports.js');
const user='0x'+'11'.repeat(20),org='0x'+'22'.repeat(20);const networkNames=['dev','test','testnet','mainnet'];
const profiles=networkNames.map((network,i)=>({network,chain_id:String(i===1||i===2?42424241:42424240+i),hub:`https://${network}.example`,currency:'MCXLAG',wallets:[user]}));
let balance=5n*10n**18n,transfers=0,simulateLost=false;const memory=new Map(),storage={getItem:k=>memory.get(k),setItem:(k,v)=>memory.set(k,v)};
function view(wallet,cluster,chain,b=balance){return {ok:true,wallet_address:wallet,cluster_id:cluster,chain_id:chain,account_id:'account',observed_at:Math.floor(Date.now()/1000),balance:{available_credit_wei:String(b),held_credit_wei:'0',spent_credit_wei:'0',earned_credit_wei:'0',bridge_completed_credit_wei:'1000000000000000000'},transactions:[],bridge_deposits:[],next_cursor:''};}
let pendingReceipt=null;let identityUnavailable=true,orgCalls=0,hubOffline=false;
const binding={wallet_address:org,hub_url:profiles[0].hub,cluster_id:"dev",chain_id:profiles[0].chain_id,revision:1};
const fetcher=async(path,opt={})=>{
 const body=opt.body?JSON.parse(opt.body):{};const result=()=>{
 if(path.endsWith('/identities')&&identityUnavailable)throw Error('LMS offline');
 if(path.endsWith('/identities'))return {schema:'mcel.slh.identities.v4b',state:'AVAILABLE',identities:[{deviceId:'admin',label:'Admin'}]};
 if(path.endsWith('/org')){orgCalls++;
  const b={wallet_address:org,hub_url:profiles[0].hub,cluster_id:'dev',chain_id:profiles[0].chain_id};const w={schema:'main-computer.slh-org-wallet.v1',state:'CURRENT',binding:b};
  if(body.operation==='inspect')return w;
  if(body.operation==='portfolio')return {wallet:w,portfolio:view(org,'dev',b.chain_id),managedSigner:true};
  if(body.operation==='managed-transfer'){transfers++;const intent=body.arguments.intent;pendingReceipt={ok:true,status:'confirmed',receipt_id:'receipt-org',...intent};if(simulateLost)throw Error('simulated disconnect');return pendingReceipt;}
  if(body.operation==='managed-transfer-status')return pendingReceipt;
 }
 if(body.operation==='inventory')return {networks:profiles,orgTargets:[],localOrg:{state:'BOUND',binding}};
 if(body.operation==='local-observe')return hubOffline?{state:'HUB_READ_UNAVAILABLE_OR_NOT_AUTHORIZED',binding}:{state:'CURRENT',binding,portfolio:view(org,'dev',binding.chain_id)};
 const p=profiles.find(n=>n.network===body.network);
 if(body.operation==='observe')return {network:p.network,wallet_address:body.wallet,hub:p.hub,cluster_id:p.network,chain_id:p.chain_id,creditState:'CURRENT',nativeState:'CURRENT',nativeWei:'3000000000000000000',currency:'MCXLAG',checkpoint:{number:'0x1',hash:'0xabc'},portfolio:view(body.wallet,p.network,p.chain_id)};
 if(body.operation==='send'){assert.equal(body.intent.wallet_address,user);transfers++;return {ok:true,status:'confirmed',receipt_id:'receipt-user',...body.intent};}
 throw Error('unexpected '+JSON.stringify(body));};
 const x=result();return {ok:true,json:async()=>path.endsWith('/identities')?x:{ok:true,result:x}};
};
const eth={request:async({method})=>{if(method==='eth_accounts'||method==='eth_requestAccounts')return [user];if(method==='personal_sign')return '0x'+'aa'.repeat(65);throw Error('unexpected provider method '+method);}};
function request(intent,cap,op,runtime,risk,args={}){return {intentId:'intent:'+intent,appId:'wallet',capabilityId:'capability:'+cap,operation:op,runtimeMethod:runtime,risk,effectId:'effect:'+intent+'.request',arguments:args};}
const read=(i,o,m,a={})=>request(i,'wallet-observer',o,m,'external-read',a);
const send=(selected,destination='',recipient=org)=>request('send-credits','wallet-transfer','send','sendFunds','consequential',{selected,destination,recipient,amount:'1.000000000000000001',asset:'compute-credit'});
(async()=>{const port=McelSemanticWalletPorts.create({fetcher,provider:()=>eth,confirm:()=>true,storage});
 const discovered=await port.invoke(read('discover-wallets','inventory','discoverWallets'));assert.equal(discovered.find(r=>r.owner.startsWith('ORG-SLH')).wallet,org);assert.equal(orgCalls,0);
 const refresh=read('refresh-portfolio','portfolio','refreshPortfolio');assert(await port.authorize(refresh));let rows=await port.invoke(refresh);assert.equal(rows.length,5);assert.equal(rows.filter(r=>r.owner==='User').length,4);const source=rows.find(r=>r.owner==='User'&&r.network==='dev');const target=rows.find(r=>r.owner.startsWith('ORG-SLH'));assert.equal(target.bridged,'1');assert.equal(target.signer,'Read only / signer not connected');assert.equal(orgCalls,0);
 hubOffline=true;const offline=await port.invoke(refresh);assert.equal(offline.find(r=>r.id===target.id).wallet,org);assert.equal(offline.find(r=>r.id===target.id).available,'Not observed');assert.equal(orgCalls,0);hubOffline=false;
 identityUnavailable=false;await port.invoke(read('refresh-identities','identities','observeIdentities'));rows=await port.invoke(read('add-org','org','addOrg',{identity:'admin',target:'local',address:'',port:'',pin:''}));assert.equal(rows.find(r=>r.id===target.id).signer,'ORG managed signer');
 let r=await port.invoke(send(source.id,target.id,''));assert.equal(r.status,'CONFIRMED');assert.equal(transfers,1);
 simulateLost=true;r=await port.invoke(send(target.id,'',user));assert.equal(r.status,'CONFIRMED');assert.equal(transfers,2);assert.equal(JSON.parse(memory.get('mcel.wallet.portfolio.v2')).pending,null);
 const foreign=rows.find(r=>r.owner==='User'&&r.network==='testnet');await assert.rejects(()=>port.invoke(send(source.id,foreign.id,'')),/CROSS_NETWORK/);assert.equal(transfers,2);
 const detail=await port.invoke(read('inspect-wallet','inspect','inspectWallet',{selected:source.id}));assert.equal(detail.available,'5');assert.equal(detail.bridged,'1');console.log('WALLET_PORTS PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});
