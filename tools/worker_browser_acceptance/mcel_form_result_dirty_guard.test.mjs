import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import test from 'node:test';
import vm from 'node:vm';

const here=path.dirname(fileURLToPath(import.meta.url));
const runtimePath=path.resolve(here,'../../main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js');
const source=readFileSync(runtimePath,'utf8');
// Execute the helpers from the actual operational runtime, not duplicate test
// implementations, in a minimal DOM-compatible environment.
const start=source.indexOf('  function formResultValue(input) {');
const end=source.indexOf('  function checkSchema(schema, x, path) {',start);
assert.ok(start>0&&end>start,'Form receiver helpers must exist in the operational runtime');
class MiniEvent { constructor(type,init={}){this.type=type;this.bubbles=!!init.bubbles;} }
const ctx=vm.createContext({Event:MiniEvent});
vm.runInContext(source.slice(start,end),ctx);
const create=vm.runInContext('formResultReceiver',ctx);
const hydrate=vm.runInContext('acceptFormResult',ctx);
class Input {
  constructor(value,type='text'){
    this.type=type;this.value=String(value);this.checked=!!value;
    this.listeners=new Map();this.dispatched=[];this.isConnected=true;
  }
  addEventListener(type,fn){const listeners=this.listeners.get(type)||[];listeners.push(fn);this.listeners.set(type,listeners);}
  dispatchEvent(event){this.dispatched.push(event.type);for(const cb of this.listeners.get(event.type)||[])cb(event);return true;}
  edit(value){
    if(this.type==='checkbox')this.checked=!!value;
    else this.value=String(value);
    this.dispatchEvent(new MiniEvent('input',{bubbles:true}));
  }
}

test('a refresh arriving after a live price edit must not replace the local draft',()=>{
  const input=new Input('0.001');
  const receiver=create(input,{whenIntent:'intent:refresh-seller'});
  input.edit('0.00001');
  assert.equal(receiver.dirty,true);
  const beforeEvents=input.dispatched.length;
  assert.equal(hydrate(receiver,'0.001'),false);
  assert.equal(input.value,'0.00001');
  assert.equal(input.dispatched.length,beforeEvents,'rejected read must not emit synthetic input');
  assert.equal(receiver.dirty,true);
});

test('matching saved response rebases draft so subsequent authoritative reads work',()=>{
  const input=new Input('0.001');
  const receiver=create(input,{});
  input.edit('0.00001');
  assert.equal(hydrate(receiver,'0.00001'),true);
  assert.equal(receiver.dirty,false);
  assert.equal(receiver.lastAccepted,'0.00001');
  assert.equal(hydrate(receiver,'0.002'),true);
  assert.equal(input.value,'0.002');
});

test('refresh may update pristine fields while a sibling has an unsaved draft',()=>{
  const price=new Input('0.001'),tokens=new Input('1024');
  const priceReceiver=create(price,{}),tokenReceiver=create(tokens,{});
  price.edit('0.00001');
  assert.equal(hydrate(priceReceiver,'0.001'),false);
  assert.equal(hydrate(tokenReceiver,512),true);
  assert.equal(tokens.value,'512');
  assert.equal(price.value,'0.00001');
});

test('checkbox edit is preserved across refresh and a matching server state rebases it',()=>{
  const checkbox=new Input(false,'checkbox'),receiver=create(checkbox,{});
  checkbox.edit(true);
  assert.equal(hydrate(receiver,false),false);
  assert.equal(checkbox.checked,true);
  assert.equal(hydrate(receiver,true),true);
  assert.equal(receiver.dirty,false);
});

test('field edits survive blur but an edit back to the saved value is not dirty',()=>{
  const input=new Input('0.001'),receiver=create(input,{});
  input.edit('0.00001');
  input.dispatchEvent(new MiniEvent('change'));
  assert.equal(hydrate(receiver,'0.001'),false);
  input.edit('0.001');
  assert.equal(receiver.dirty,false);
  assert.equal(hydrate(receiver,'0.002'),true);
  assert.equal(input.value,'0.002');
});

test('internal hydration input events do not create a new dirty draft',()=>{
  const input=new Input('0.001'),receiver=create(input,{});
  assert.equal(hydrate(receiver,'0.002'),true);
  assert.equal(receiver.dirty,false);
  assert.equal(receiver.lastAccepted,'0.002');
  assert.deepEqual(input.dispatched,['input']);
});

test('operational runtime actually uses the draft-aware helper on result receiver writes',()=>{
  assert.match(source,/const key=ref\(item\.state\),receiver=formResultReceiver\(controls\[item\.field\]/);
  assert.match(source,/if\(!acceptFormResult\(receiver,next\)\)continue;/);
});
