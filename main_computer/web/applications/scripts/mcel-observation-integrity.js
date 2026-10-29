/* MCEL 14.7.5: source observations are not presentation membership.
 * A partial observation cannot retire omitted records. Only a complete snapshot
 * or an explicit authoritative removal can establish source absence.
 * This module has no Task Manager knowledge and never mutates caller data.
 */
(function(global){
  'use strict';
  const SCHEMA='mcel.source-observation.v1';
  function evaluate(previous,incoming,observation,cursor=null){
    if(!(previous instanceof Map)||!(incoming instanceof Map))return {ok:false,reason:'invalid-source-map'};
    const legacy=observation===undefined;
    if(legacy && cursor!==null)return {ok:false,reason:'unversioned-after-versioned'};
    const o=legacy?{complete:true,removals:[]}:observation;
    if(!legacy && (!o||o.schema!==SCHEMA||typeof o.complete!=='boolean'||
       !Number.isSafeInteger(o.sequence)||o.sequence<0||
       (o.removals!==undefined && (!Array.isArray(o.removals)||
         o.removals.some(id=>typeof id!=='string'||!id.length)||
         new Set(o.removals).size!==o.removals.length))))
      return {ok:false,reason:'invalid-observation-contract'};
    if(!legacy && cursor!==null && o.sequence<=cursor.sequence)
      return {ok:false,reason:o.sequence===cursor.sequence?'duplicate-sequence':'stale-sequence'};
    const removals=new Set(o.removals||[]);
    for(const id of removals)if(incoming.has(id))return {ok:false,reason:'removal-conflicts-with-observed-record'};
    const merged=o.complete?new Map():new Map(previous);
    for(const id of removals)merged.delete(id);
    for(const [id,raw] of incoming)merged.set(id,raw);
    if(merged.size>500)return {ok:false,reason:'merged-source-exceeds-limit'};
    const absent=[...previous.keys()].filter(id=>!merged.has(id));
    return {ok:true,records:merged,absent,complete:o.complete,
      sequence:legacy?null:o.sequence,
      cursor:legacy?null:{sequence:o.sequence}};
  }
  global.McelObservationIntegrity=Object.freeze({SCHEMA,evaluate});
})(typeof window!=='undefined'?window:globalThis);
