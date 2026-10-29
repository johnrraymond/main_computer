/* MCEL Patch 14.0 — pure, opt-in decision continuity laboratory.
 * No DOM, timers, locale, storage, model calls, or runtime activation.
 * A layout decision is discrete; browser measurements are never part of its identity.
 */
(function (root) {
  "use strict";
  const SCHEMA = "mcel.layout-decision.v1";
  const STATE_SCHEMA = "mcel.committed-layout-state.v1";
  const POLICY_VERSION = "mcel.layout-continuity.v1";
  function error(msg) { throw new Error("MCEL layout continuity: " + msg); }
  function compareCodeUnits(a, b) { a = String(a); b = String(b); return a < b ? -1 : (a > b ? 1 : 0); }
  function canonical(value) {
    if (value === null || typeof value === "string" || typeof value === "boolean") return JSON.stringify(value);
    if (typeof value === "number" && Number.isFinite(value)) return JSON.stringify(Object.is(value, -0) ? 0 : value);
    if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
    if (value && typeof value === "object" && Object.getPrototypeOf(value) === Object.prototype) {
      const keys = Object.keys(value).sort(compareCodeUnits);
      return "{" + keys.map(k => JSON.stringify(k) + ":" + canonical(value[k])).join(",") + "}";
    }
    error("decision contains unsupported or non-finite input");
  }
  function stableName({concept, purpose, family, region, occurrence = "default"}) {
    for (const [key, val] of Object.entries({concept, purpose, family, region, occurrence}))
      if (typeof val !== "string" || !val.trim() || val.length > 256) error("invalid semantic layout name component: " + key);
    return "mcel.layout/" + [concept, purpose, family, region, occurrence].map(encodeURIComponent).join("/");
  }
  function makeDecision({name, family, mode, fields = [], regions = [], ordering = "source", configuration = null}) {
    if (typeof name !== "string" || !name || typeof family !== "string" || !family || typeof mode !== "string" || !mode)
      error("a named family and mode are required");
    if (!Array.isArray(fields) || !Array.isArray(regions) || !["source", "semantic", "explicit", "live"].includes(ordering))
      error("invalid decision structure or row-order policy");
    const decision = {schema: SCHEMA, name, family, mode, fields, regions, ordering, configuration};
    // Verify it can be committed and that the caller cannot smuggle pixel metrics into identity.
    const serialized = decisionKey(decision);
    return JSON.parse(serialized);
  }
  function decisionKey(decision) {
    if (decision?.schema !== SCHEMA) error("invalid committed decision schema");
    const forbidden = new Set(["metrics", "measurements", "allocated_px", "content_minimum_px",
      "host_width_px", "available_px", "intrinsic_px", "deficit_px", "height", "width",
      "renderedGeometry", "sourceBindings"]);
    function check(value) {
      if (!value || typeof value !== "object") return;
      for (const [key, part] of Object.entries(value)) {
        if (forbidden.has(key)) error("measured geometry cannot be a committed decision: " + key);
        check(part);
      }
    }
    check(decision);
    return canonical(decision);
  }
  function ensureCandidate(item) {
    if (!item || typeof item !== "object" || !item.decision) error("candidate must carry a decision");
    const key = decisionKey(item.decision);
    if (!Number.isFinite(item.quality)) error("candidate quality must be a finite scalar");
    if (item.failures !== undefined && !Array.isArray(item.failures)) error("candidate failures must be a list");
    return {key, decision: JSON.parse(key), quality: Math.round(item.quality * 1000),
      failures: item.failures || [], feasible: item.feasible !== false};
  }
  function distance(current, other) {
    if (!current) return [0, 0, 0, 0, 0];
    const a = current.decision, b = other.decision;
    const diff = (x, y) => canonical(x) === canonical(y) ? 0 : 1;
    // Compare the number of altered named regions, not only a binary
    // "any region changed" flag. This enables local repair preference.
    const regionChanges = () => {
      if (!Array.isArray(a.regions) || !Array.isArray(b.regions)) return diff(a.regions,b.regions);
      const byId = regions => new Map(regions.map((r,i)=>[String(r.id ?? i),canonical(r)]));
      const left=byId(a.regions),right=byId(b.regions);
      return [...new Set([...left.keys(),...right.keys()])].filter(id=>left.get(id)!==right.get(id)).length;
    };
    return [diff(a.family, b.family), regionChanges(),
      diff(a.fields, b.fields), diff(a.ordering, b.ordering), diff(a.mode, b.mode)];
  }
  function compareTuple(a, b) { for (let i = 0; i < Math.max(a.length, b.length); i++) {
    if ((a[i] || 0) !== (b[i] || 0)) return (a[i] || 0) - (b[i] || 0);
  } return 0; }
  function choose({candidates, incumbent = null, policy = {}}) {
    if (!Array.isArray(candidates) || !candidates.length || candidates.length > 1024) error("bounded candidate list required");
    if (policy.version !== undefined && policy.version !== POLICY_VERSION) error("unknown policy version");
    const epsilon = policy.epsilon === undefined ? 0 : policy.epsilon;
    if (!Number.isInteger(epsilon) || epsilon < 0 || epsilon > 10000000) error("epsilon must be nonnegative integer milli-units");
    const seen = new Set(), viable = [];
    for (const item of candidates) {
      const normalized = ensureCandidate(item);
      if (seen.has(normalized.key)) error("duplicate candidate decision");
      seen.add(normalized.key);
      if (normalized.feasible && !normalized.failures.length) viable.push(normalized);
    }
    if (!viable.length) return {status: "unsatisfied", decision: null, state: incumbent,
      record: {policy: POLICY_VERSION, reason: "no-valid-candidate", considered: candidates.length}};
    let prior = null;
    if (incumbent) {
      if (incumbent.schema !== STATE_SCHEMA || incumbent.policy !== POLICY_VERSION) error("invalid incumbent state");
      const priorKey = decisionKey(incumbent.decision);
      prior = viable.find(candidate => candidate.key === priorKey) || null;
    }
    if (policy.searchTruncated === true && incumbent && !prior) {
      // The incumbent must be explicitly remeasured. Otherwise a bounded
      // search could replace a still-feasible layout just because it was cut.
      return {status:"incomplete",decision:incumbent.decision,state:incumbent,
        record:{policy:POLICY_VERSION,reason:"incumbent-not-evaluated",
          considered:candidates.length}};
    }
    viable.sort((a, b) => a.quality - b.quality || compareCodeUnits(a.key, b.key));
    const best = viable[0];
    let selected, reason;
    if (prior && prior.quality <= best.quality + epsilon) {
      selected = prior; reason = "incumbent-within-quality-threshold";
    } else {
      const near = viable.filter(candidate => candidate.quality <= best.quality + epsilon);
      // A now-infeasible incumbent is still useful for minimizing the scope
      // of a necessary repair; it may not be retained as the winner.
      const reference = prior || (incumbent ? {decision: incumbent.decision} : null);
      near.sort((a,b) => compareTuple(distance(reference, a), distance(reference, b)) ||
        a.quality-b.quality || compareCodeUnits(a.key,b.key));
      selected = near[0]; reason = prior ? "material-quality-improvement" : "canonical-first-commit";
    }
    const state = {schema: STATE_SCHEMA, policy: POLICY_VERSION, decision: selected.decision};
    return {status: "selected", decision: selected.decision, state, record: {
      policy: POLICY_VERSION, reason, incumbentPresent: !!prior,
      candidateCount: candidates.length, eligibleCount: viable.length,
      chosenKey: selected.key, qualityMilli: selected.quality,
      change: prior && prior.key === selected.key ? "retained" : "selected"
    }};
  }
  function replay(events, initialState = null, policy = {}) {
    if (!Array.isArray(events) || events.length > 10000) error("bounded canonical observation log required");
    let state = initialState;
    const history = [];
    for (const entry of events) {
      if (!entry || !Array.isArray(entry.candidates)) error("observation must include candidates");
      const result = choose({candidates: entry.candidates, incumbent: state, policy});
      state = result.state;
      history.push({observation: entry.observation || null, status: result.status,
        decisionKey: result.decision ? decisionKey(result.decision) : null, record: result.record});
    }
    return {state, history, serialized: canonical({state, history})};
  }
  // 14.1: pure collection commit policy. Pixel tracks are committed state,
  // never layout identity. All numbers are finite, rounded to 1/100 pixel.
  const COLLECTION_STATE_SCHEMA = "mcel.collection-committed-state.v1";
  const COLLECTION_POLICY = "mcel.collection-continuity.v1";
  const roundTrack = x => Math.round(x * 100) / 100;
  function planCollection({evaluation, columns, link, previous = null, rowIds = [],
                           policy = {}}) {
    if (evaluation?.status !== "candidate-verified" || !evaluation.plan ||
        !Array.isArray(columns) || !columns.length || columns.length > 16 ||
        !link || !Number.isFinite(evaluation.host_width_px) ||
        !Array.isArray(rowIds) || rowIds.length > 500 ||
        rowIds.some(id => typeof id !== "string") || new Set(rowIds).size !== rowIds.length)
      error("valid measured collection evaluation and unique row identities required");
    const keys = columns.map(x => x.key);
    if (new Set(keys).size !== keys.length || keys.some(k => typeof k !== "string")) error("duplicate field key");
    const signature = canonical(columns.map(c => [c.key, c.semanticPropertyId || c.conceptRef || null,
      c.tier || null, !!c.required]));
    if (previous && (previous.schema !== COLLECTION_STATE_SCHEMA ||
        previous.policy !== COLLECTION_POLICY)) error("incompatible committed collection state");
    const prior = previous?.signature === signature ? previous : null;
    const width = evaluation.host_width_px;
    const bp = link.compact_breakpoint_px;
    const margin = policy.breakpointMarginPx === undefined ? 24 : policy.breakpointMarginPx;
    if (!Number.isFinite(margin) || margin < 0 || margin > 128) error("invalid hysteresis margin");
    const available = evaluation.plan.available_px;
    const gap = 7;
    const minCell = link.min_visible_cell_px;
    if (!Number.isFinite(minCell) || minCell < 24 || !Number.isFinite(available) || available < 0)
      error("invalid width constraints");
    const rawMin = evaluation.plan.content_minimum_px;
    const primary = columns.filter(c => c.tier === "primary" ||
      (!columns.some(x => x.tier !== undefined) && c.required)).map(c => c.key);
    const essential = [...new Set([...link.required_keys, ...primary])];
    // Content can be disclosed and exactly recovered; a single very long cell
    // must never create an unbounded growth ratchet.
    const cap = Math.max(minCell, Math.floor(available * 0.50));
    const base = Object.fromEntries(keys.map(k => [k,
      roundTrack(Math.min(cap, Math.max(minCell, rawMin[k] || minCell)))]));
    const widthOf = selected => selected.reduce((n,k) => n + base[k],0) + gap * Math.max(0,selected.length-1);
    const primaryFits = widthOf(essential) <= available;
    const eligibleTable = primaryFits && available >= minCell;
    let mode = evaluation.plan.mode;
    if (prior) {
      if (prior.decision.mode === "table" && eligibleTable && width >= bp-margin)
        mode = "table";
      else if (prior.decision.mode === "stacked" && width <= bp+margin)
        mode = "stacked";
    }
    if (mode === "table" && !eligibleTable) mode = "stacked";
    const tiered = columns.some(c => c.tier !== undefined);
    let selected = tiered ? (mode === "stacked" ?
        columns.filter(c => c.tier === "primary" ||
          (c.tier === "secondary" && c.presentation?.compactDisclosure !== true)).map(c=>c.key) :
        (evaluation.plan.visible_keys || keys).slice()) : keys.slice();
    if (mode === "table" && prior && prior.decision.mode === "table") {
      const old = prior.decision.fields.filter(f=>f.visible).map(f=>f.sourceKey);
      if (old.length && old.every(k=>keys.includes(k)) && essential.every(k=>old.includes(k)) &&
          widthOf(old) <= available) selected = old;
    }
    // A new field or a genuine width loss can make an existing candidate
    // infeasible. Remove only optional fields and never lose primary data.
    if (mode === "table") {
      while (widthOf(selected) > available && selected.some(k=>!essential.includes(k))) {
        const at = selected.findLastIndex(k=>!essential.includes(k));
        selected.splice(at,1);
      }
      if (widthOf(selected) > available) { mode = "stacked";
        selected = tiered ? columns.filter(c=>c.tier === "primary" ||
          (c.tier === "secondary" && c.presentation?.compactDisclosure !== true)).map(c=>c.key) : keys.slice(); }
    }
    // The 14.1 plan contains source field keys to support the existing
    // browser contract. This is an *instance-local* name, not a portable
    // semantic layout profile; portability is a separate later gate.
    const name = "mcel.layout.local/semantic-collection";
    const toDecision = (m, visibleKeys) => makeDecision({name,family:"semantic-collection",mode:m,
      fields:columns.map(c=>({property:c.semanticPropertyId||c.conceptRef||"source-local",
        occurrence:c.occurrence||"default",sourceKey:c.key,visible:visibleKeys.includes(c.key),
        disclosed:!visibleKeys.includes(c.key)})),ordering:"explicit"});
    const fresh = toDecision(mode,selected);
    const incumbent = prior?.decision || null;
    const candidates = [{decision:fresh,quality:0}];
    // Only retain an incumbent that passes the current width and membership
    // constraints; selection cannot override an invalid or unrepresentable plan.
    if (incumbent && decisionKey(incumbent) !== decisionKey(fresh)) {
      const oldVisible = incumbent.fields.filter(f=>f.visible).map(f=>f.sourceKey);
      const oldValid = incumbent.mode === mode && oldVisible.every(k=>keys.includes(k)) &&
        essential.every(k=>oldVisible.includes(k)) &&
        (mode === "stacked" || widthOf(oldVisible) <= available);
      candidates.push({decision:incumbent,quality:0,failures:oldValid?[]:["infeasible-incumbent"]});
    }
    const selection = choose({candidates,incumbent:prior ?
      {schema:STATE_SCHEMA,policy:POLICY_VERSION,decision:prior.decision}:null,
      policy:{version:POLICY_VERSION,epsilon:0}});
    if (selection.status !== "selected") error("no feasible collection continuity decision");
    const decision = selection.decision;
    selected = decision.fields.filter(f=>f.visible).map(f=>f.sourceKey);
    const allocated = {};
    if (decision.mode === "table") {
      const prevTracks = prior?.decision.mode === "table" ? prior.tracks : {};
      let remaining = available - gap * Math.max(0, selected.length-1);
      // Preserve stable widths under unchanged viewport, including shrinkage
      // of the live contents. Shrink only at a real feasibility boundary.
      for (const k of selected) {
        const previousWidth = prevTracks?.[k];
        // With a committed track, changing the largest live text cell is
        // not permission to resize the column. Exact text remains available
        // through the collection inspector/disclosure machinery.
        allocated[k] = roundTrack(Number.isFinite(previousWidth)?
          Math.max(minCell,previousWidth):base[k]);
      }
      const total = () => selected.reduce((sum,k)=>sum+allocated[k],0);
      if (total() > remaining) {
        const excess = total() - remaining;
        let slack = selected.reduce((sum,k)=>sum+Math.max(0,allocated[k]-minCell),0);
        if (slack < excess - .01) error("table tracks cannot fit safe minimum");
        let toRemove = excess;
        for (const k of selected) {
          const shrinkable = Math.max(0,allocated[k]-minCell);
          const deduction = slack>0 ? Math.min(shrinkable,toRemove*shrinkable/slack) : 0;
          allocated[k] = roundTrack(allocated[k]-deduction);
          toRemove -= deduction; slack -= shrinkable;
        }
        if (toRemove > .01) allocated[selected[selected.length-1]] = roundTrack(allocated[selected[selected.length-1]]-toRemove);
      }
      // Unused space is not automatically redistributed after data churn.
      // New mounts may take the incumbent baseline widths, bounded by budget.
      if (!prior) {
        const baseline = evaluation.plan.allocated_px || {};
        for(const k of selected) if (Number.isFinite(baseline[k]))
          allocated[k] = roundTrack(Math.max(base[k],baseline[k]));
        if (total() > remaining) {
          const factor = Math.max(0, (remaining-selected.length*minCell)/
            Math.max(.001,total()-selected.length*minCell));
          for(const k of selected) allocated[k] = roundTrack(minCell+(allocated[k]-minCell)*factor);
        }
      }
      // 0.01-pixel rounding stays inside the track budget.
      const over = total()-remaining;
      if (over > 0 && selected.length) allocated[selected[selected.length-1]] =
        roundTrack(allocated[selected[selected.length-1]]-over);
    }
    const existing = prior?.rowOrder?.filter(id=>rowIds.includes(id)) || [];
    const order = [...existing,...rowIds.filter(id=>!existing.includes(id))];
    const state = {schema:COLLECTION_STATE_SCHEMA,policy:COLLECTION_POLICY,signature,
      decision,tracks:allocated,rowOrder:order,width,commit:
        prior?.commit === undefined ? 1 : prior.commit+1};
    const finalVisible=new Set(selected);
    const plan = {...evaluation.plan,mode:decision.mode,
      visible_keys:selected,disclosed_keys:keys.filter(k=>!finalVisible.has(k)),allocated_px:allocated,
      unresolved_deficit_px:0};
    const record = {schema:"mcel.collection-continuity-decision.v1",policy:COLLECTION_POLICY,
      action:prior && decisionKey(prior.decision)===decisionKey(decision)?"retain":"commit",
      reason:selection.record.reason,mode:decision.mode,
      geometryChange:prior?canonical(prior.tracks)!==canonical(allocated):true,
      visible:selected,rows:order.length,commit:state.commit};
    return {status:"selected",plan,state,record};
  }


  // Patch 14.2: pure canonical-tree continuity. Named regions are the existing
  // layout-tree node IDs. Measurements remain external; no geometry enters a
  // committed decision. The caller MUST independently measure all candidates,
  // including an incumbent omitted by its bounded search.
  const TREE_STATE_SCHEMA = "mcel.tree-committed-layout-state.v1";
  const TREE_POLICY = "mcel.tree-continuity.v1";
  function treeCandidateDecision(tree, candidate) {
    const regions=[], choices=candidate?.choices;
    if (!tree?.root || !Array.isArray(choices)) error("canonical tree and candidate choices required");
    let i=0;
    function walk(node) {
      const choice=choices[i++];
      if (!choice || typeof node.id!=="string") error("incomplete canonical tree candidate");
      regions.push({id:node.id,role:node.role,columns:choice.columns,
        template:choice.template,gap:choice.gap,areas:choice.areas||null});
      for(const child of node.children||[])walk(child);
    }
    walk(tree.root);
    if(i!==choices.length)error("canonical tree choice count mismatch");
    return makeDecision({name:"mcel.layout.tree/"+encodeURIComponent(tree.root.id),
      family:"canonical-tree",mode:"declared",fields:[],regions,ordering:"explicit"});
  }
  function planTree({tree,evaluation,previous=null,policy={}}) {
    if (evaluation?.schema!=="mcel.layout-tree-evaluation.v1" || !Array.isArray(evaluation.candidates) ||
        !tree?.root || evaluation.candidates.length>513) error("verified canonical-tree evaluation required");
    const signature=canonical(tree);
    if(previous && (previous.schema!==TREE_STATE_SCHEMA || previous.policy!==TREE_POLICY ||
        !previous.decision || !Array.isArray(previous.choices)))error("invalid committed tree state");
    const prior=previous?.signature===signature?previous:null;
    // Restored state is an input, not authority. Check that its saved choices
    // actually describe the saved decision before considering retention.
    if(prior && (decisionKey(prior.decision)!==
        decisionKey(treeCandidateDecision(tree,{choices:prior.choices})) ||
        !Number.isSafeInteger(prior.commit) || prior.commit<1))
      error("committed tree state and choices disagree");
    const prepared=evaluation.candidates.map((candidate)=>{
      const decision=treeCandidateDecision(tree,candidate);
      const height=candidate.evidence?.quality?.height;
      if(!Number.isFinite(height) || height<0)error("finite measured tree height required");
      return {candidate,decision,quality:height,failures:candidate.evidence?.failures||[],
        key:decisionKey(decision)};
    });
    // The incumbent stays subject to bare hard feasibility. A *new* layout
    // needs additional control-size headroom before entering, reducing rapid
    // enter/exit churn at narrow viewport boundaries. Missing measurements
    // are not treated as certified headroom; the gate is not claimed there.
    const entryMargin=policy.entryMarginPx===undefined?8:policy.entryMarginPx;
    if(!Number.isFinite(entryMargin) || entryMargin<0 || entryMargin>64)
      error("invalid canonical-tree entry margin");
    const previousKey=prior?decisionKey(prior.decision):null;
    const incumbentValid=!!prepared.find(x=>x.key===previousKey && !x.failures.length);
    if(incumbentValid)for(const item of prepared) {
      const slack=item.candidate.evidence?.quality?.minControlSlackPx;
      if(item.key!==previousKey && Number.isFinite(slack) && slack<entryMargin)
        item.failures=[...item.failures,"entry-control-margin-not-met"];
    }
    const baseline=prepared.find(item=>item.candidate===evaluation.selected);
    // Initial commits intentionally use the current production ranking. The
    // continuity policy must not silently redesign a first-time mount.
    let result;
    if(!prior && baseline && baseline.failures.length===0) {
      result={status:"selected",decision:baseline.decision,
        record:{reason:"existing-canonical-baseline",change:"committed"}};
    } else {
      const epsilon=policy.epsilon===undefined?8000:policy.epsilon;
      result=choose({candidates:prepared.map(item=>({decision:item.decision,
          quality:item.quality,failures:item.failures})),
        incumbent:prior?{schema:STATE_SCHEMA,policy:POLICY_VERSION,decision:prior.decision}:null,
        policy:{version:POLICY_VERSION,epsilon,searchTruncated:evaluation.searchTruncated}});
    }
    if(result.status!=="selected")return {status:result.status,state:previous,
      decision:null,candidate:null,record:{schema:"mcel.tree-continuity-decision.v1",
        action:"refuse",reason:result.record?.reason||"no-valid-tree-candidate",policy:TREE_POLICY}};
    const selected=prepared.find(item=>item.key===decisionKey(result.decision));
    if(!selected || selected.failures.length)error("selected tree candidate has not passed hard constraints");
    const changed=[];
    const previousRegions=new Map((prior?.decision.regions||[]).map(r=>[r.id,canonical(r)]));
    for(const region of result.decision.regions)
      if(prior && previousRegions.get(region.id)!==canonical(region))changed.push(region.id);
    const kept=!!prior && decisionKey(prior.decision)===selected.key;
    const state={schema:TREE_STATE_SCHEMA,policy:TREE_POLICY,signature,
      decision:selected.decision,choices:JSON.parse(canonical(selected.candidate.choices)),
      commit:(prior?.commit||0)+1};
    return {status:"selected",decision:selected.decision,candidate:selected.candidate,state,
      record:{schema:"mcel.tree-continuity-decision.v1",policy:TREE_POLICY,
        action:kept?"retain":"commit",reason:result.record.reason,
        changedRegions:changed,regionChanges:changed.length,commit:state.commit,
        considered:prepared.length,searchTruncated:!!evaluation.searchTruncated}};
  }

  const api = Object.freeze({SCHEMA, STATE_SCHEMA, POLICY_VERSION, canonical, compareCodeUnits,
    stableName, makeDecision, decisionKey, choose, replay, COLLECTION_STATE_SCHEMA, COLLECTION_POLICY, planCollection,
    TREE_STATE_SCHEMA,TREE_POLICY,treeCandidateDecision,planTree});
  root.McelLayoutContinuity = api;
  if (typeof module === "object" && module?.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
