from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCM = ROOT / "main_computer/web/applications/scripts/mcel-scm.js"
RUNTIME = ROOT / "main_computer/web/applications/scripts/mcel-application-runtime.js"
CATALOG = ROOT / "runtime/build/mcel/web/applications/scripts/mcel-application-package-catalog.js"
MANIFEST = ROOT / "runtime/build/mcel/web/applications/mcel-packages/contract-workbench/mcel.runtime.json"


def _run_node_json(tmp_path: Path, body: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable")
    script = f'''\
"use strict";
const fs = require("fs");
const path = require("path");
{SCM.read_text(encoding="utf-8")}
{RUNTIME.read_text(encoding="utf-8")}
async function importContract(relativePath) {{
  const source = fs.readFileSync(path.join({json.dumps(str(ROOT))}, relativePath), "utf8");
  return import(`data:text/javascript;base64,${{Buffer.from(source).toString("base64")}}`);
}}
{body}
'''
    target = tmp_path / "collection-runtime-test.js"
    target.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        [node, str(target)], cwd=ROOT, text=True, capture_output=True, check=False, timeout=45
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(completed.stdout)


def test_keyed_collection_reconciliation_and_item_controls(tmp_path: Path) -> None:
    body = r'''
const packageCatalog = require(__CATALOG__);
const runtimeManifest = JSON.parse(fs.readFileSync(__MANIFEST__, "utf8"));
let nextUid = 1;
function selectorSpec(selector) {
  if (!selector.startsWith("[") || !selector.endsWith("]")) return null;
  const inner = selector.slice(1, -1);
  const equals = inner.indexOf("=");
  if (equals < 0) return {name: inner, value: undefined};
  const name = inner.slice(0, equals);
  let value = inner.slice(equals + 1);
  if ((value.startsWith("\"") && value.endsWith("\"")) || (value.startsWith("'") && value.endsWith("'"))) {
    value = value.slice(1, -1);
  }
  return {name, value};
}
class FakeElement {
  constructor(tagName="div", attrs={}, children=[]) {
    this.uid = nextUid++;
    this.tagName = String(tagName).toUpperCase();
    this.attrs = {...attrs};
    this.children = [];
    this.parentNode = null;
    this.parentElement = null;
    this.listeners = new Map();
    this.dataset = {};
    this.textContent = String(attrs.textContent || "");
    this.value = String(attrs.value || "");
    this.disabled = false;
    children.forEach((child) => this.appendChild(child));
  }
  get firstChild() { return this.children[0] || null; }
  get firstElementChild() { return this.children[0] || null; }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  removeAttribute(name) { delete this.attrs[name]; }
  querySelectorAll(selector) {
    const spec = selectorSpec(selector);
    if (!spec) throw new Error(`unsupported selector ${selector}`);
    const out = [];
    const walk = (node) => node.children.forEach((child) => {
      const actual = child.getAttribute(spec.name);
      if (actual !== null && (spec.value === undefined || String(actual) === spec.value)) out.push(child);
      walk(child);
    });
    walk(this);
    return out;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  addEventListener(name, handler) { this.listeners.set(name, handler); }
  removeEventListener(name, handler) { if (this.listeners.get(name) === handler) this.listeners.delete(name); }
  emit(name, event={}) { return this.listeners.get(name)?.({target: this, ...event}); }
  appendChild(child) {
    if (child.parentNode && child.parentNode !== this) child.parentNode.removeChild(child);
    const existing = this.children.indexOf(child);
    if (existing >= 0) this.children.splice(existing, 1);
    this.children.push(child);
    child.parentNode = this;
    child.parentElement = this;
    return child;
  }
  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index < 0) throw new Error("child not found");
    this.children.splice(index, 1);
    child.parentNode = null;
    child.parentElement = null;
    return child;
  }
  replaceChildren(...children) {
    this.children.forEach((child) => { child.parentNode = null; child.parentElement = null; });
    this.children = [];
    children.forEach((child) => this.appendChild(child));
  }
  cloneNode(deep=false) {
    const copy = new FakeElement(this.tagName, {...this.attrs, value: this.value, textContent: this.textContent}, []);
    copy.value = this.value;
    copy.textContent = this.textContent;
    if (deep) this.children.forEach((child) => copy.appendChild(child.cloneNode(true)));
    return copy;
  }
}
class FakeFragment {
  constructor(children=[]) { this.children = children; }
  cloneNode(deep=false) { return new FakeFragment(deep ? this.children.map((child) => child.cloneNode(true)) : []); }
}
class FakeTemplate extends FakeElement {
  constructor(id, children) { super("template", {"data-mcel-template-id": id}, []); this.content = new FakeFragment(children); }
}
function field(name, tag="span", attrs={}) { return new FakeElement(tag, {...attrs, "data-mcel-item-field": name}); }
function action(intent) { return new FakeElement("button", {"data-mcel-item-intent": intent}); }
const rowTemplate = new FakeTemplate("contract-workbench.item", [
  new FakeElement("article", {}, [
    field("name", "strong"), field("category"), field("quantity", "input", {type:"number"}),
    field("quote-status"), field("quote-amount", "output"),
    action("update-quantity"), action("request-quote"), action("cancel-quote"), action("remove-contract")
  ])
]);
const host = new FakeElement("div", {"data-mcel-node-id":"contract-workbench.items", "data-mcel-collection-host":""});
const receipt = new FakeElement("pre", {"data-mcel-node-id":"contract-workbench.latest-receipt"});
const collectionRegion = new FakeElement("section", {"data-mcel-region-id":"collection"}, [host]);
const evidenceRegion = new FakeElement("section", {"data-mcel-region-id":"evidence"}, [receipt]);
const status = new FakeElement("p", {"data-mcel-runtime-status":"pending"});
const root = new FakeElement("main", {"data-mcel-surface-id":"collection.surface", "data-mcel-region-id":"shell"}, [collectionRegion, evidenceRegion, status, rowTemplate]);
const surface = {
  schema:"mcel.semantic-surface-ir.v1", appId:"contract-workbench", surfaceId:"collection.surface",
  regions:[{id:"shell",role:"application"},{id:"collection",role:"list"},{id:"evidence",role:"status"}],
  nodes:[
    {id:"contract-workbench.items",kind:"collection",regionId:"collection",statePath:"visibleContracts",keyPath:"id",templateId:"contract-workbench.item",item:{
      fields:{
        name:{selector:"[data-mcel-item-field='name']",itemPath:"name",property:"textContent"},
        category:{selector:"[data-mcel-item-field='category']",itemPath:"category",property:"textContent"},
        quantity:{selector:"[data-mcel-item-field='quantity']",itemPath:"quantity",property:"value"},
        quoteStatus:{selector:"[data-mcel-item-field='quote-status']",itemPath:"quoteStatus",property:"textContent",provisional:{statePath:"quoteProgress",keyFromItem:true,valuePath:"status",transform:"quote-progress",fallback:"item"}},
        quoteAmount:{selector:"[data-mcel-item-field='quote-amount']",itemPath:"quoteAmount",property:"textContent",transform:"currency-integer"}
      },
      controls:{
        update:{selector:"[data-mcel-item-intent='update-quantity']",intentId:"update-quantity",payload:{contractId:{fromItemKey:true},quantity:{fromItemField:"quantity",property:"value",parse:"integer"}}},
        quote:{selector:"[data-mcel-item-intent='request-quote']",intentId:"request-quote",payload:{contractId:{fromItemKey:true}}},
        cancel:{selector:"[data-mcel-item-intent='cancel-quote']",intentId:"cancel-quote",payload:{contractId:{fromItemKey:true}}},
        remove:{selector:"[data-mcel-item-intent='remove-contract']",intentId:"remove-contract",payload:{contractId:{fromItemKey:true}}}
      }
    }},
    {id:"contract-workbench.latest-receipt",kind:"operation-evidence",regionId:"evidence"}
  ]
};
const layout = {schema:"mcel.layout-grammar.v1",surfaceId:"collection.surface",regions:{shell:{direction:"column"},collection:{direction:"column"},evidence:{direction:"column"}},constraints:[]};
(async()=>{
  const domain=await importContract("runtime/build/mcel/web/applications/mcel-packages/contract-workbench/contracts/domain.js");
  const intents=await importContract("runtime/build/mcel/web/applications/mcel-packages/contract-workbench/contracts/intents.js");
  const adapter=await importContract("runtime/build/mcel/web/applications/mcel-packages/contract-workbench/contracts/adapter.js");
  const progressSnapshots=[];
  const mount=await McelApplicationRuntime.mountApplicationPackage({
    appId:"contract-workbench",root,packageCatalog,manifest:runtimeManifest,
    capabilities:{quotes:{
      async *requestQuote(){
        yield {type:"quote.started",expected:2};
        progressSnapshots.push(host.children.find((row)=>row.getAttribute("data-mcel-collection-key")==="contract-1").querySelector("[data-mcel-item-field='quote-status']").textContent);
        yield {type:"quote.received",report:{amount:100,source:"alpha"}};
        progressSnapshots.push(host.children.find((row)=>row.getAttribute("data-mcel-collection-key")==="contract-1").querySelector("[data-mcel-item-field='quote-status']").textContent);
        yield {type:"quote.received",report:{amount:140,source:"beta"}};
      }
    }},
    manifestUrl:"http://example.test/applications/mcel-packages/contract-workbench/mcel.runtime.json",
    operationIdFactory:({intentId,revision})=>`collection:${intentId}:${revision}:${nextUid}`,
    moduleLoader:async(_url,entry)=>{
      if(entry.export==="ContractWorkbenchDomain")return domain;
      if(entry.export==="ContractWorkbenchIntents")return intents;
      if(entry.export==="ContractWorkbenchAdapter")return adapter;
      if(entry.export==="ContractWorkbenchSurface")return {ContractWorkbenchSurface:surface};
      if(entry.export==="ContractWorkbenchLayout")return {ContractWorkbenchLayout:layout};
      return {ContractWorkbenchObservation:{schema:"mcel.browser-observation.v1",appId:"contract-workbench"}};
    }
  });
  mount.dispatch("add-contract",{name:"Steel",quantity:12,category:"materials"},{operationId:"add-1"});
  mount.dispatch("add-contract",{name:"Transport",quantity:5,category:"transport"},{operationId:"add-2"});
  mount.dispatch("add-contract",{name:"Services",quantity:8,category:"services"},{operationId:"add-3"});
  const snapshot=()=>host.children.map((row)=>({
    key:row.getAttribute("data-mcel-collection-key"), uid:row.uid,
    name:row.querySelector("[data-mcel-item-field='name']").textContent,
    quantity:row.querySelector("[data-mcel-item-field='quantity']").value,
    amount:row.querySelector("[data-mcel-item-field='quote-amount']").textContent
  }));
  const initial=snapshot();
  const steelRow=host.children.find((row)=>row.getAttribute("data-mcel-collection-key")==="contract-1");
  const steelUid=steelRow.uid;
  mount.updateLocalState({sortMode:"quantity"});
  const sorted=snapshot();
  mount.updateLocalState({filterText:"steel"});
  const filtered=snapshot();
  mount.updateLocalState({filterText:""});
  const restored=snapshot();
  const currentSteel=host.children.find((row)=>row.getAttribute("data-mcel-collection-key")==="contract-1");
  const quantity=currentSteel.querySelector("[data-mcel-item-field='quantity']");
  const update=currentSteel.querySelector("[data-mcel-item-intent='update-quantity']");
  quantity.value="20";
  host.emit("click",{target:update});
  const updated={state:mount.readState(),rows:snapshot(),result:mount.readLastResult()};
  quantity.value="bad";
  host.emit("click",{target:update});
  const parseFailure={state:mount.readState(),result:mount.readLastResult()};
  const quote=currentSteel.querySelector("[data-mcel-item-intent='request-quote']");
  await host.emit("click",{target:quote});
  const quoteResult={state:mount.readState(),provisional:mount.readProvisionalState(),rows:snapshot(),result:mount.readLastResult(),progressSnapshots};
  const cancel=currentSteel.querySelector("[data-mcel-item-intent='cancel-quote']");
  host.emit("click",{target:cancel});
  const cancelBlocked=mount.readLastResult();
  const remove=currentSteel.querySelector("[data-mcel-item-intent='remove-contract']");
  host.emit("click",{target:remove});
  const removed={state:mount.readState(),rows:snapshot(),result:mount.readLastResult()};
  host.emit("click",{target:remove});
  const stale=mount.readLastResult();
  mount.unmount();
  process.stdout.write(JSON.stringify({initial,sorted,filtered,restored,steelUid,updated,parseFailure,quoteResult,cancelBlocked,removed,stale,afterUnmount:host.children.length}));
})().catch((error)=>{console.error(error);process.exit(1);});
'''
    body = body.replace('__CATALOG__', json.dumps(str(CATALOG))).replace('__MANIFEST__', json.dumps(str(MANIFEST)))
    data = _run_node_json(tmp_path, body)
    assert [row["name"] for row in data["initial"]] == ["Services", "Steel", "Transport"]
    assert [row["name"] for row in data["sorted"]] == ["Transport", "Services", "Steel"]
    assert data["filtered"] == [{"key": "contract-1", "uid": data["steelUid"], "name": "Steel", "quantity": "12", "amount": "$0"}]
    assert next(row for row in data["restored"] if row["key"] == "contract-1")["uid"] == data["steelUid"]
    assert data["updated"]["result"]["ok"] is True
    assert next(item for item in data["updated"]["state"]["contracts"] if item["id"] == "contract-1")["quantity"] == 20
    assert next(row for row in data["updated"]["rows"] if row["key"] == "contract-1")["uid"] == data["steelUid"]
    assert data["parseFailure"]["result"]["code"] == "APPLICATION_CONTROL_PAYLOAD_PARSE_FAILED"
    assert data["parseFailure"]["state"]["revision"] == 4
    assert data["quoteResult"]["result"]["ok"] is True
    assert data["quoteResult"]["result"]["code"] == "APPLICATION_CAPABILITY_OPERATION_COMMITTED"
    quoted = next(item for item in data["quoteResult"]["state"]["contracts"] if item["id"] == "contract-1")
    assert quoted["quoteStatus"] == "quoted"
    assert quoted["quoteAmount"] == 120
    assert data["quoteResult"]["provisional"] == {"quoteProgress": {}}
    assert data["quoteResult"]["progressSnapshots"] == ["running 0/2", "running 1/2"]
    quoted_row = next(row for row in data["quoteResult"]["rows"] if row["key"] == "contract-1")
    assert quoted_row["amount"] == "$120"
    assert data["cancelBlocked"]["code"] == "APPLICATION_ASYNC_OPERATION_NOT_ACTIVE"
    assert data["removed"]["result"]["ok"] is True
    assert all(item["id"] != "contract-1" for item in data["removed"]["state"]["contracts"])
    assert data["stale"]["code"] == "APPLICATION_COLLECTION_ITEM_KEY_STALE"
    assert data["afterUnmount"] == 0
