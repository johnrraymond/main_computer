"use strict";
/* Exact typed SEH deployment capability allowlist. No controllers or
 * authority are selected by the browser beyond the owning SLH ticket.
 * Secrets are never returned in semantic read state.
 */
(function(global){
  const specs=Object.freeze({
    "read-seh-deployment":["inspect","observeSehDeployment","external-read",[]],
    "seh-deployment-register":["register","registerCoolifyController","consequential",["name","url","apiToken"]],
    "seh-deployment-select":["select","selectCoolifyController","consequential",["name"]],
    "seh-deployment-preview-deploy":["preview-deploy","previewSehDeploy","consequential",[]],
    "seh-deployment-deploy":["deploy","deploySeh","consequential",[]],
    "seh-deployment-preview-undeploy":["preview-undeploy","previewSehUndeploy","consequential",[]],
    "seh-deployment-undeploy":["undeploy","undeploySeh","consequential",[]]
  });
  function operation(req){
    if(req?.appId!=="slh"||!req.intentId?.startsWith("intent:")||
       req.arguments===null||typeof req.arguments!=="object"||Array.isArray(req.arguments))return null;
    const key=req.intentId.slice(7),spec=specs[key];if(!spec)return null;
    const read=spec[2]==="external-read";
    if(req.capabilityId!==(read?"capability:seh-deployment-observer":"capability:seh-deployment-control")||
       req.effectId!=="effect:"+key+".request"||req.operation!==spec[0]||
       req.runtimeMethod!==spec[1]||req.risk!==spec[2]||
       Object.keys(req.arguments).sort().join(",")!==spec[3].slice().sort().join(","))return null;
    if(spec[3].includes("name")&&!/^coolify-[A-Za-z0-9_-]{1,116}$/.test(req.arguments.name||""))return null;
    if(spec[0]==="register" && (typeof req.arguments.url!=="string"||req.arguments.url.length>250||
       typeof req.arguments.apiToken!=="string"||req.arguments.apiToken.length<6||
       req.arguments.apiToken.length>4096))return null;
    return spec[0];
  }
  global.McelSemanticSehDeploymentPort=Object.freeze({operation});
})(typeof globalThis!=="undefined"?globalThis:this);
