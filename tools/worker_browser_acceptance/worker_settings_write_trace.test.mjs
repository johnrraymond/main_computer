import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {createWorkerSettingsWriteTrace} from './worker_settings_write_trace.mjs';
const request = (body, method='POST', url='http://127.0.0.1:8765/api/applications/worker/settings') => ({
  method:()=>method, url:()=>url, postDataJSON:()=>body
});
const response = (req, body, status=200) => ({request:()=>req, status:()=>status, json:async()=>body});
test('records original rate then overwritten rate without raw request bodies', async () => {
  let time=0;
  const tracer=createWorkerSettingsWriteTrace(()=>time);
  const page=new EventEmitter(); const detach=tracer.attach(page);
  const desired=request({settings:{sellerCreditsPerToken:'0.00001',sellerTargetTokens:512,secret:'MUST_NOT_LEAK'},changed_fields:['sellerCreditsPerToken','sellerTargetTokens']});
  page.emit('request',desired);time+=7;
  page.emit('response',response(desired,{ok:true,settings:{sellerCreditsPerToken:'0.00001',sellerTargetTokens:512,privateKey:'MUST_NOT_LEAK'}}));
  const stale=request({settings:{sellerCreditsPerToken:'0.001'},changed_fields:['sellerCreditsPerToken']});
  page.emit('request',stale);time+=3;
  page.emit('response',response(stale,{ok:true,settings:{sellerCreditsPerToken:'0.001',sellerTargetTokens:512}}));
  const rows=await tracer.finish();detach();
  assert.deepEqual(rows.map(r=>[r.requestRate,r.responseRate,r.http]),[
    ['0.00001','0.00001',200],['0.001','0.001',200]]);
  assert.doesNotMatch(JSON.stringify(rows),/MUST_NOT_LEAK|privateKey|secret/);
});
test('does not expose arbitrary fields, endpoints, or malformed values', async () => {
  const tracer=createWorkerSettingsWriteTrace();const page=new EventEmitter();const detach=tracer.attach(page);
  const req=request({settings:{sellerCreditsPerToken:'wallet secret'},changed_fields:['walletMnemonic','sellerCreditsPerToken']});
  page.emit('request',req);page.emit('response',response(req,{ok:false,error:'wallet secret'},400));
  page.emit('request',request({},'POST','https://untrusted.example/elsewhere'));
  const rows=await tracer.finish();detach();
  assert.equal(rows.length,1);
  assert.equal(rows[0].requestRate,'unknown');
  assert.deepEqual(rows[0].fields,['sellerCreditsPerToken']);
  assert.equal(rows[0].responseOk,false);
  assert.doesNotMatch(JSON.stringify(rows),/wallet secret|walletMnemonic|untrusted/);
});
