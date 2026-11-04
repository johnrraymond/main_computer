"use strict";
/* Generic, isolated pixel viewport for an explicitly requested semantic script.
 * User code executes inside a sandboxed opaque-origin frame; the parent only
 * receives bounded PNG pixels and textual diagnostics. It never receives
 * document, source, capability, filesystem or application service authority.
 */
(function(global){
  let serial=0;
  const fail=reason=>{throw Error("MCEL_VIEWPORT_REFUSED:"+reason);};
  const validPng=value=>typeof value==="string" &&
    /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(value) && value.length<=2400000;
  function run(source,{timeoutMs=5000}={}){
    if(typeof source!=="string" || !source.trim() || source.length>10000)fail("script-size");
    if(!global.document?.body || typeof global.addEventListener!=="function")fail("browser-unavailable");
    const token="viewport-"+(++serial);
    const frame=global.document.createElement("iframe");
    frame.setAttribute("sandbox","allow-scripts");
    frame.setAttribute("aria-hidden","true");
    frame.hidden=true;
    frame.style.display="none";
    const scriptLiteral=JSON.stringify(source).replace(/</g,"\\u003c").replace(/\u2028/g,"\\u2028").replace(/\u2029/g,"\\u2029");
    const tokenLiteral=JSON.stringify(token);
    // `srcdoc` inherits the embedding origin only if allow-same-origin is set.
    // Never grant it. CSP forbids network, forms, workers, embeds and imports.
    frame.srcdoc=`<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; connect-src 'none'; img-src data:; script-src 'unsafe-inline' 'unsafe-eval'; style-src 'none'; worker-src 'none'; frame-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'">
<canvas id="v" width="320" height="200"></canvas><script>(async()=>{
 "use strict";
 const token=${tokenLiteral}, source=${scriptLiteral};
 const c=document.getElementById('v'),ctx=c.getContext('2d',{willReadFrequently:true});
 const logs=[];const size=n=>Number.isInteger(n)&&n>=1&&n<=1024;
 const byte=x=>{const n=Number(x);return Number.isFinite(n)?Math.max(0,Math.min(255,Math.round(n))):0;};
 const color=v=>{if(!Array.isArray(v)||v.length<3||v.length>4)throw Error('invalid-rgba');return 'rgba('+byte(v[0])+','+byte(v[1])+','+byte(v[2])+','+(v.length>3?byte(v[3]):255)/255+')';};
 const pixel=(x,y)=>Number.isInteger(x)&&Number.isInteger(y)&&x>=0&&y>=0&&x<c.width&&y<c.height;
 const vram={
   reset(o={}){const w=o.width??c.width,h=o.height??c.height;if(!size(w)||!size(h)||w*h>1048576)throw Error('invalid-resolution');c.width=w;c.height=h;ctx.fillStyle=color(o.fill??[0,0,0,255]);ctx.fillRect(0,0,w,h);return this.getSize();},
   fillRect(x,y,w,h,rgba){if(![x,y,w,h].every(Number.isFinite)||w<0||h<0)throw Error('invalid-rect');ctx.fillStyle=color(rgba);ctx.fillRect(x,y,w,h);},
   setPixel(x,y,rgba){if(!pixel(x,y))throw Error('invalid-pixel');ctx.fillStyle=color(rgba);ctx.fillRect(x,y,1,1);},
   setPixels(values){if(!Array.isArray(values)||values.length>50000)throw Error('invalid-pixels');for(const p of values){if(!Array.isArray(p)||p.length!==3)throw Error('invalid-pixel');this.setPixel(p[0],p[1],p[2]);}},
   getPixel(x,y){if(!pixel(x,y))throw Error('invalid-pixel');return Array.from(ctx.getImageData(x,y,1,1).data);},
   getSize(){return {width:c.width,height:c.height};},
   getImageData(){return ctx.getImageData(0,0,c.width,c.height);}
 };
 vram.reset({width:320,height:200,fill:[0,0,0,255]});
 const consoleFacade={};for(const level of ['log','warn','error'])consoleFacade[level]=(...args)=>{
   if(logs.length<80)logs.push('['+level+'] '+args.map(x=>typeof x==='string'?x:JSON.stringify(x)).join(' ').slice(0,512));
 };
 let status='completed';try{
   const fn=new (Object.getPrototypeOf(async function(){}).constructor)('viewport','console','"use strict"; const vram=viewport.vram;\\n'+source);
   await fn(Object.freeze({vram}),consoleFacade);
 }catch(e){status='script-error';consoleFacade.error(String(e?.message||e));}
 const result={kind:'mcel.sandboxed-viewport-result.v1',token,status,width:c.width,height:c.height,
   logs,png:c.toDataURL('image/png')};
 parent.postMessage(result,'*');
})().catch(e=>parent.postMessage({kind:'mcel.sandboxed-viewport-result.v1',token:${tokenLiteral},status:'script-error',width:0,height:0,logs:[String(e)],png:''},'*'));<\/script>`;
    return new Promise((resolve,reject)=>{
      let finished=false;
      const finish=(error,data)=>{
        if(finished)return;finished=true;
        global.removeEventListener("message",onMessage);
        global.clearTimeout(timer);frame.remove();
        error?reject(error):resolve(data);
      };
      const onMessage=event=>{
        if(event.source!==frame.contentWindow || event.origin!=="null")return;
        const data=event.data;
        if(!data || data.kind!=="mcel.sandboxed-viewport-result.v1" || data.token!==token)return;
        if(!["completed","script-error"].includes(data.status) ||
           !Number.isInteger(data.width) || !Number.isInteger(data.height) ||
           data.width<0 || data.height<0 || data.width>1024 || data.height>1024 ||
           !Array.isArray(data.logs) || data.logs.length>80 ||
           data.logs.some(line=>typeof line!=="string" || line.length>600) ||
           (data.width && !validPng(data.png)))return finish(Error("MCEL_VIEWPORT_REFUSED:response-invalid"));
        const {kind,status,width,height,logs,png}=data;
        finish(null,JSON.stringify({kind,status,width,height,logs,png}));
      };
      const timer=global.setTimeout(()=>finish(Error("MCEL_VIEWPORT_REFUSED:timeout")),timeoutMs);
      global.addEventListener("message",onMessage);
      global.document.body.append(frame);
    });
  }
  global.McelSandboxedViewport=Object.freeze({run});
})(typeof globalThis!=="undefined"?globalThis:this);
