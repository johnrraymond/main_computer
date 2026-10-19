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
    this.classList={items:new Set(),add:(v)=>this.classList.items.add(v)};
  }
  setAttribute(key,value) {this.attrs[key]=String(value); if(key==='id')this.id=value; if(key.startsWith('data-')) this.dataset[key.slice(5).replace(/-([a-z])/g,(_a,c)=>c.toUpperCase())]=value;}
  append(...items) {this.children.push(...items)}
  replaceChildren(...items){this.children=[...items]}
  getBoundingClientRect(){return {width:width-32,height:920,left:0,right:width-32}}
  _walk() {const results=[];const todo=[...this.children];while(todo.length){const e=todo.shift();results.push(e);todo.unshift(...e.children)}return results}
  querySelectorAll(selector) {if(selector==='[data-mcel-structural-node]')return this._walk().filter(e=>e.attrs['data-mcel-structural-node']!==undefined);throw Error('unsupported mock selector '+selector)}
  querySelector(selector) {
    const direct=selector.match(/^\[data-mcel-direct-child="([^\"]+)"\]$/);
    if(direct)return this._walk().find(e=>e.dataset.mcelDirectChild===direct[1])||null;
    const action=selector.match(/^\[data-mcel-unimplemented-intent="([^\"]+)"\]:disabled$/);
    if(action)return this._walk().find(e=>e.attrs['data-mcel-unimplemented-intent']===action[1]&&e.disabled)||null;
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
 if(h.disabledIntents.length!==(appId==='calculator'?11:8))throw Error('wrong disabled intent count');
 let refused=false;try {h.dispatch('saveFile')}catch(e){refused=String(e.message).includes('MCEL_STANDALONE_REFUSED')}
 if(!refused)throw Error('portable domain call was not refused');
 console.log(JSON.stringify({appId,width,ready:h.ready,nodeCount:h.nodes.length,disabledIntents:h.disabledIntents.length,
   fullSemanticRuntimeProven:h.fullSemanticRuntimeProven, selected:h.selected}));
})().catch(e=>{console.error(e);process.exitCode=1});
