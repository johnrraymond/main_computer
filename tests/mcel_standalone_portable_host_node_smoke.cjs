// Minimal deterministic DOM substitute for JS host wiring tests only.
// This is *not* a replacement for the required Chromium browser smoke.
const fs = require('fs');
const path = require('path');
const {pathToFileURL} = require('url');
const [rootPath, appId, widthString] = process.argv.slice(2);
const width = Number(widthString);
class Element {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.dataset = {};this.attrs={};
    this.id = '';this.disabled = false;this.textContent = '';this.hidden = false;
    this.style = {setProperty:(key,value)=>{this.style[key]=value}};
    this.classList={items:new Set(),add:(v)=>this.classList.items.add(v)};this.value='';this.listeners={};
  }
  setAttribute(key,value) {this.attrs[key]=String(value); if(key==='id')this.id=value; if(key.startsWith('data-')) this.dataset[key.slice(5).replace(/-([a-z])/g,(_a,c)=>c.toUpperCase())]=value;}
  removeAttribute(key){delete this.attrs[key]}
  addEventListener(name,callback){this.listeners[name]=callback}
  append(...items) {this.children.push(...items)}
  replaceChildren(...items){this.children=[...items]}
  getBoundingClientRect(){return {width:width-32,height:920,left:0,right:width-32}}
  _walk() {const results=[];const todo=[...this.children];while(todo.length){const e=todo.shift();results.push(e);todo.unshift(...e.children)}return results}
  querySelectorAll(selector) {if(selector==='[data-mcel-structural-node]')return this._walk().filter(e=>e.attrs['data-mcel-structural-node']!==undefined);
    if(selector==='[data-mcel-portable-intent]')return this._walk().filter(e=>e.dataset.mcelPortableIntent);
    if(selector==='input')return this._walk().filter(e=>e.tagName==='input');
    throw Error('unsupported mock selector '+selector)}
  querySelector(selector) {
    const direct=selector.match(/^\[data-mcel-direct-child="([^\"]+)"\]$/);
    if(direct)return this._walk().find(e=>e.dataset.mcelDirectChild===direct[1])||null;
    const action=selector.match(/^\[data-mcel-unimplemented-intent="([^\"]+)"\]:disabled$/);
    if(action)return this._walk().find(e=>e.attrs['data-mcel-unimplemented-intent']===action[1]&&e.disabled)||null;
    const portable=selector.match(/^\[data-mcel-portable-intent="([^"]+)"\]$/);
    if(portable)return this._walk().find(e=>e.dataset.mcelPortableIntent===portable[1])||null;
    if(selector==='[data-mcel-standalone-content]')return this._walk().find(e=>e.attrs['data-mcel-standalone-content']!==undefined)||null;
    if(selector==='[data-mcel-standalone-status]')return this._walk().find(e=>e.attrs['data-mcel-standalone-status']!==undefined)||null;
    return null;
  }
}
const host=new Element('main');host.id='mcel-standalone-host';
const status=new Element('p');status.setAttribute('data-mcel-standalone-status','');
const content=new Element('div');content.setAttribute('data-mcel-standalone-content','');
host.append(status,content);
global.document={createElement:(tag)=>new Element(tag),querySelector:(s)=>s==='#mcel-standalone-host'?host:null};
global.window={dispatchEvent:()=>{},innerWidth:width};
global.CustomEvent=class{constructor(name,opts){this.name=name;this.detail=opts.detail}};
global.fetch=async (url)=>({ok:true,json:async()=>JSON.parse(fs.readFileSync(path.resolve(rootPath,appId,url),'utf8'))});
(async()=>{
 await import(pathToFileURL(path.resolve(rootPath,appId,'standalone.js')).href);
 // The module's async fetch imports settle via a series of event-loop turns.
 for(let i=0;i<100&&!global.McelStandalonePackageHost;i++)await new Promise(r=>setTimeout(r,10));
 const h=global.McelStandalonePackageHost;
 if(!h?.ready||!h.structuralVerified||h.fullSemanticRuntimeProven!==false) throw Error('standalone JS refused: '+JSON.stringify(h));
 if(!h.portableMode)throw Error('portable mode not started');
 if(h.disabledIntents.length!==(appId==='calculator'?5:5))throw Error('wrong capability intent count');
 if(appId==='calculator'){
   const cleared=await h.dispatch('clearExpression');
   if(!cleared.ok)throw Error('calculator pure intent not executed');
   const numeric=await h.dispatch('evaluateExpression',{expression:'4+9'});
   if(numeric.receipt.value!==13)throw Error('calculator computation wrong');
 }else{
   let refused=false;try {await h.dispatch('openFile',{path:'example.txt'})}catch(e){refused=String(e.message).includes('MCEL_PORTABLE_REFUSED')}
   if(!refused)throw Error('ungranted source read not refused');
 }
 console.log(JSON.stringify({appId,width,ready:h.ready,nodeCount:h.nodes.length,disabledIntents:h.disabledIntents.length,
   fullSemanticRuntimeProven:h.fullSemanticRuntimeProven, selected:h.selected,portableMode:h.portableMode}));
})().catch(e=>{console.error(e);process.exitCode=1});
