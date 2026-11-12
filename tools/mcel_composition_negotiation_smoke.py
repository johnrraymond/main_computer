"""PRE-PRODUCTION MCEL composition-negotiation smoke.

Pure prototype: reads real compiled Application IR; neither production renderer,
compiler, semantics, CSS nor authority is modified.  The candidate solver tests
whether semantic types and VERIFIED result bindings suffice to produce an
adaptive composition.  Scores are experimental, not accepted product policy.

Run: python -m tools.mcel_composition_negotiation_smoke [--browser] [--report FILE]
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Demand:
    region: str
    kind: str
    min_width: int
    comfortable_width: int
    min_height: int
    growth: str
    field_count: int = 0


@dataclass(frozen=True)
class Candidate:
    arrangement: str
    navigation: str
    nav_width: int
    input_width: int
    result_width: int
    score: float
    usable: bool
    reason: str


class SmokeRefusal(ValueError):
    """No verified producer/result relation, or no acceptable composition."""


def _ref(value):
    return value.get("ref") if isinstance(value, Mapping) else None


def _read_states(node):
    states = set()
    if node.get("nodeKind") == "plot":
        states.add(_ref(node.get("state")))
    value = node.get("value") or {}
    states.add(_ref(value.get("state")))
    return states - {None}


def derive_unit(ir: Mapping, mode: str) -> tuple[Demand, Demand, dict]:
    """Only schema, type, visibleWhen, and actual state-flow decide pairing.

    No Calculator IDs, English headings, DOM location or node sourceName are
    consulted.  Ambiguous and forged producer/result relationships fail closed.
    """
    if ir.get("schema") != "mcel.application-ir.v1" or len(ir.get("surfaces", [])) != 1:
        raise SmokeRefusal("not one canonical Application IR surface")
    surface = ir["surfaces"][0]
    interface = surface.get("interfaceIntent") or {}
    if interface.get("schema") != "mcel.interface-intent.v1" or not interface.get("hierarchy"):
        raise SmokeRefusal("missing paired HTML semantic hierarchy")
    nodes = {n["id"]: n for n in surface.get("nodes", [])}
    intents = {i["id"]: i for i in ir.get("intents", [])}
    visible = [r for r in interface.get("regions", []) if
               (r.get("visibleWhen") or {}).get("equals") == mode]
    inputs = [r for r in visible if r.get("purpose") == "input"]
    results = [r for r in visible if r.get("purpose") == "results"]
    matches = []
    for source in inputs:
        writers = set()
        source_nodes = [nodes[m["ref"]] for m in source["members"]]
        for node in source_nodes:
            intent = intents.get(_ref(node.get("intent")), {})
            writers.update(_ref(binding.get("target")) for binding in intent.get("resultBindings", []))
            writers.update(_ref(binding) for binding in intent.get("writes", []))
        writers.discard(None)
        for target in results:
            target_nodes = [nodes[m["ref"]] for m in target["members"]]
            readers = set().union(*(_read_states(n) for n in target_nodes))
            evidence = writers & readers
            if evidence:
                matches.append((source, target, source_nodes, target_nodes, sorted(evidence)))
    if len(matches) != 1:
        raise SmokeRefusal("requires exactly one verified mode-local producer/result pair")
    source, target, src_nodes, dst_nodes, evidence = matches[0]
    forms = [n for n in src_nodes if n.get("nodeKind") == "form"]
    if not forms:
        raise SmokeRefusal("producer has no recognized interactive form")
    fields = sum(len(n.get("fields") or {}) for n in forms)
    has_editor = any(n.get("editor") for n in forms)
    # Intrinsic contracts of widget TYPES, not a graphing-app table. The values
    # are experimental smoke hypotheses to validate against measured geometry.
    control_min = 210 + (25 if fields >= 4 else 0) + (20 if has_editor else 0)
    control_comfort = control_min + 55 + min(40, fields * 5)
    control_height = 125 + fields * 43 + (96 if has_editor else 0)
    control = Demand(source["id"], "interactive-form", control_min,
                     control_comfort, control_height, "saturating", fields)
    kinds = {n.get("nodeKind") for n in dst_nodes}
    if "plot" in kinds:
        result = Demand(target["id"], "visual-series", 260, 710, 245, "visual")
    elif kinds <= {"state-value", "operation-evidence"}:
        result = Demand(target["id"], "scalar", 80, 175, 55, "saturating")
    elif "collection" in kinds:
        result = Demand(target["id"], "collection", 270, 620, 230, "visual")
    else:
        raise SmokeRefusal("result has no tested intrinsic presentation contract")
    return control, result, {"stateEvidence": evidence,
                             "sourceNodeKinds": sorted({n["nodeKind"] for n in src_nodes}),
                             "resultNodeKinds": sorted(kinds), "mode": mode}


def _comfort(width: int, item: Demand) -> float:
    if width < item.min_width:
        return -100.0
    # Interactive controls have sharply diminishing returns after comfortably
    # readable dimensions.  Visual-series content continues to gain resolution.
    if item.growth == "saturating":
        return 35 * min(1.0, width / item.comfortable_width) + min(2, max(0, width-item.comfortable_width) / 150)
    return 24 + 34 * (1 - math.exp(-(width-item.min_width) / 360))


def negotiate(ir: Mapping, mode: str, width: int, height: int = 740) -> dict:
    """Enumerate feasible arrangements and use one explicit objective to rank.

    1/2/3 is a *candidate*, never a binding assigned by purpose.  If a 3-way
    candidate costs more than it gains, a compact or stacked candidate wins.
    """
    if width < 240 or height < 240:
        raise SmokeRefusal("available space is below the smoke's measured minimum")
    source, target, relationship = derive_unit(ir, mode)
    gap = 12
    nav_min, nav_comfort = 200, 215
    candidates = []

    # Stacking is always possible; navigation is a short band rather than a
    # mandatory left column. Its cost is additional scroll/deferred visibility.
    full = width
    stacked_height = source.min_height + target.min_height + 95
    fold = max(0, stacked_height - (height - 105))
    stack_score = (_comfort(full, source) + _comfort(full, target)
                   - fold / 10.0 - (7 if target.growth == "visual" else 0))
    candidates.append(Candidate("stacked", "band", 0, full, full,
                                round(stack_score, 4), True,
                                "stacking preserves minimum widths at the cost of scrolling"))

    # A scalar answer has a cheap, high-cohesion inline option. This must beat
    # the temptation to manufacture an entire right-hand details region.
    if target.kind == "scalar":
        unit_width = min(width, max(source.comfortable_width, 300))
        score = _comfort(unit_width, source) + _comfort(unit_width, target) + 18
        candidates.append(Candidate("compact-unit", "band", 0, unit_width,
                                    unit_width, round(score, 4), True,
                                    "scalar result stays attached to its compact producer"))

    # Search possible 3-region configurations rather than pinning column 3.
    # Navigation and controls surrender width only down to usable bounds.
    for nav_width in (nav_min, nav_comfort, 235):
        remainder = width - nav_width - 2*gap
        for control_width in range(source.min_width, max(source.min_width, remainder-target.min_width)+1, 10):
            result_width = remainder - control_width
            if result_width < target.min_width:
                continue
            # Relationship remains visible without vertical separation. Extra
            # width is worth more to a visual result than a saturated form.
            score = (_comfort(control_width, source) + _comfort(result_width, target)
                     + 18 - 0.12*max(0, nav_width-nav_comfort)
                     - (12 if target.kind == "scalar" else 0))
            candidates.append(Candidate("three-region", "rail", nav_width,
                                        control_width, result_width,
                                        round(score, 4), True,
                                        "simultaneous form and result with negotiated rail"))

    # 2-region option (navigation band).  It may win over three columns when
    # navigation would otherwise starve either work unit.
    remainder = width - gap
    for control_width in range(source.min_width, max(source.min_width, remainder-target.min_width)+1, 10):
        result_width = remainder - control_width
        if result_width < target.min_width:
            continue
        score = (_comfort(control_width, source) + _comfort(result_width, target)
                 + 11 - (12 if target.kind == "scalar" else 0))
        candidates.append(Candidate("two-region", "band", 0, control_width,
                                    result_width, round(score, 4), True,
                                    "navigation compresses to band to protect both work units"))

    if not candidates:
        raise SmokeRefusal("no feasible candidates")
    # Exact deterministic tie break, independent of region IDs or prose.
    candidates.sort(key=lambda c: (-c.score, c.arrangement, c.input_width, c.nav_width))
    winner = candidates[0]
    alternatives = []
    for kind in ("three-region", "two-region", "stacked", "compact-unit"):
        best = next((c for c in candidates if c.arrangement == kind), None)
        if best is not None:
            alternatives.append(asdict(best))
    return {"schema": "mcel.negotiation-smoke.v0", "productionPromotion": False,
            "experimental": True, "sourceRegion": source.region, "resultRegion": target.region,
            "sourceDemand": asdict(source), "resultDemand": asdict(target),
            "relationship": relationship, "width": width, "height": height,
            "winner": asdict(winner), "alternatives": alternatives,
            "candidateCount": len(candidates)}


def run_static_smoke(ir: Mapping) -> list[dict]:
    results = []
    for mode, width, height in (("graphing", 390, 740), ("graphing", 800, 740),
                                ("graphing", 1100, 740), ("graphing", 1260, 740),
                                ("basic", 390, 740), ("basic", 1260, 740),
                                ("symbolic", 1100, 740)):
        outcome = negotiate(ir, mode, width, height)
        results.append(outcome)
    return results


def run_browser_smoke(ir: Mapping, *, screenshots: Path | None = None) -> list[dict]:
    """Trial negotiated geometry on live semantic DOM, without changing source.

    The graph's original bound node moves, *not* a cloned mock. This probes
    listener/state preservation and browser geometry. In production, this
    layout would need an owned, shared composition path, not DOM reparenting.
    """
    from playwright.sync_api import sync_playwright
    from main_computer.mcel_calculator_expanded_host import render_expanded_page
    from tools.mcel_phase2b_browser import launch_semantic_browser
    trial = []
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            for viewport in (390, 800, 1260):
                page = browser.new_page(viewport={"width": viewport, "height": 740})
                try:
                    # Only the browser fixture's opaque-origin storage boundary
                    # is stubbed. The real DSL runtime, intents and graph run.
                    page.evaluate("""() => {
                      const data=new Map();
                      Object.defineProperty(window,'localStorage',{configurable:true,
                        value:{getItem:k=>data.get(String(k))??null,
                        setItem:(k,v)=>data.set(String(k),String(v)),
                        removeItem:k=>data.delete(String(k)),clear:()=>data.clear(),
                        key:i=>[...data.keys()][i]??null,get length(){return data.size;}}});
                    }""")
                    page.set_content(render_expanded_page())
                    if page.locator('#mcel-expanded-diagnostic').inner_text() != 'Semantic Calculator ready':
                        raise AssertionError('canonical semantic runtime failed to mount')
                    page.locator('[data-mcel-mode-tabs] button[data-value="graphing"]').click()
                    f = page.locator('form[data-intent="intent:draw-graph"]')
                    f.locator('input[name="expression"]').fill('sin(x)')
                    f.locator('button[type="submit"]').click()
                    page.locator('[data-mcel-plot] svg path').wait_for(timeout=10000)
                    before = page.evaluate("""() => ({
                      samples:window.__mcelExpanded.app.readState('state:graph-points').length,
                      path:document.querySelector('[data-mcel-plot] svg path').getAttribute('d')
                    })""")
                    assert before['samples'] > 100
                    baseline_geometry = page.evaluate("""() => {
                      const form=document.querySelector('form[data-intent="intent:draw-graph"]');
                      const plot=document.querySelector('[data-mcel-plot]');
                      const a=form.closest('[data-mcel-region]').getBoundingClientRect();
                      const b=plot.closest('[data-mcel-region]').getBoundingClientRect();
                      return {inputWidth:a.width, resultWidth:b.width,
                        resultY:b.y, inputY:a.y,
                        resultZone:plot.closest('[data-mcel-zone]').dataset.mcelZone};
                    }""")
                    assert baseline_geometry['resultZone'] == 'workspace'
                    hidden_leak = page.evaluate("""() => [...document.querySelectorAll('[data-mcel-region][hidden]')]
                      .filter(e=>getComputedStyle(e).display!=='none')
                      .map(e=>e.dataset.mcelRegion)""")
                    available = round(page.locator('#mcel-expanded-root').bounding_box()['width'])
                    result = negotiate(ir, 'graphing', available, 740)
                    choice = result['winner']
                    form_field_kinds = {}
                    surface_nodes = {n['id']: n for n in ir['surfaces'][0]['nodes']}
                    for region in ir['surfaces'][0]['interfaceIntent']['regions']:
                        if region['id'] == result['sourceRegion']:
                            for member in region['members']:
                                form_field_kinds.update({k: v.get('kind') for k, v in
                                    (surface_nodes[member['ref']].get('fields') or {}).items()})
                    page.evaluate("""choice => {
                      const root=document.querySelector('#mcel-expanded-root > [data-mcel-natural-layout]');
                      const workspace=root.querySelector('[data-mcel-zone="workspace"]');
                      const details=root.querySelector('[data-mcel-zone="tools"]');
                      const input=[...root.querySelectorAll('[data-mcel-region]')].find(e=>e.dataset.mcelRegion===choice.source);
                      const output=[...root.querySelectorAll('[data-mcel-region]')].find(e=>e.dataset.mcelRegion===choice.target);
                      if(!input||!output||!workspace||!details) throw Error('semantic surface not found');
                      // Experimental child negotiation: typed fields have
                      // different width needs.  A long text input spans both
                      // columns; numeric fields share short columns.  No
                      // field names, labels or Calculator IDs are consulted.
                      const form=input.querySelector('form');
                      if(form) {
                        form.style.setProperty('display','grid','important');
                        form.style.setProperty('grid-template-columns','repeat(2,minmax(0,1fr))','important');
                        form.style.setProperty('gap','8px','important');
                        form.style.setProperty('align-items','start','important');
                        [...form.children].forEach(child=>{
                          child.style.setProperty('grid-row','auto','important');
                          if(child.matches('label[data-field]')) {
                            const kind=choice.fieldKinds[child.dataset.field];
                            child.style.setProperty('grid-column',kind==='string'?'1 / -1':'auto','important');
                          } else child.style.setProperty('grid-column','1 / -1','important');
                        });
                      }
                      // The existing runtime sets hidden correctly, but a
                      // legacy !important compact-result CSS rule defeats
                      // native hidden.  Keep this correction TEST-ONLY and
                      // report it as a separate production blocker.
                      const applyHidden=e=>{
                        if(e.hidden) e.style.setProperty('display','none','important');
                        else e.style.removeProperty('display');
                      };
                      root.querySelectorAll('[data-mcel-region]').forEach(applyHidden);
                      new MutationObserver(ms=>ms.forEach(m=>applyHidden(m.target))).observe(root,
                        {subtree:true,attributes:true,attributeFilter:['hidden']});
                      root.setAttribute('data-mcel-smoke-only','');
                      if(choice.plan.arrangement==='three-region') {
                        root.style.setProperty('grid-template-columns',`${choice.plan.nav_width}px ${choice.plan.input_width}px minmax(0,1fr)`,'important');
                        root.style.setProperty('grid-template-rows','minmax(0,1fr)','important');
                        details.prepend(output);
                      } else if(choice.plan.arrangement==='two-region') {
                        // The navigator is a band. The two working areas stay
                        // side by side; the status joins the result surface.
                        root.style.setProperty('grid-template-columns',`${choice.plan.input_width}px minmax(0,1fr)`,'important');
                        root.style.setProperty('grid-template-rows','auto minmax(0,1fr)','important');
                        const navigation=root.querySelector('[data-mcel-zone="navigator"]');
                        navigation.style.setProperty('grid-column','1 / -1','important');
                        navigation.style.setProperty('grid-row','1','important');
                        navigation.style.setProperty('height','auto','important');
                        navigation.style.setProperty('max-height','130px','important');
                        const tabs=navigation.querySelector('[data-mcel-mode-tabs]');
                        if(tabs) tabs.style.setProperty('grid-template-columns','repeat(4,minmax(0,1fr))','important');
                        navigation.scrollTop=0;
                        workspace.style.setProperty('grid-column','1','important');
                        details.style.setProperty('grid-column','2','important');
                        workspace.style.setProperty('grid-row','2','important');
                        details.style.setProperty('grid-row','2','important');
                        details.prepend(output);
                      } else {
                        workspace.append(output);
                        // The stacked alternative exchanges some workspace
                        // height for a 2-row navigation band with all four
                        // modes accessible. It is not a fixed application
                        // breakpoint; the chosen candidate invokes it.
                        const navigation=root.querySelector('[data-mcel-zone="navigator"]');
                        root.style.setProperty('grid-template-rows','160px minmax(0,1fr) 84px','important');
                        const tabs=navigation.querySelector('[data-mcel-mode-tabs]');
                        if(tabs) tabs.style.setProperty('grid-template-columns','repeat(2,minmax(0,1fr))','important');
                        const title=navigation.querySelector('[data-mcel-purpose="mode-control"] > h2');
                        if(title) title.style.setProperty('display','none','important');
                        navigation.scrollTop=0;
                      }
                    }""", {"source": result['sourceRegion'], "target": result['resultRegion'], "plan": choice,
                           "fieldKinds": form_field_kinds})
                    page.wait_for_timeout(80)
                    geometry = page.evaluate("""refs => {
                      const root=document.querySelector('#mcel-expanded-root');
                      const input=[...root.querySelectorAll('[data-mcel-region]')].find(e=>e.dataset.mcelRegion===refs.source);
                      const output=[...root.querySelectorAll('[data-mcel-region]')].find(e=>e.dataset.mcelRegion===refs.target);
                      const a=input.getBoundingClientRect(),b=output.getBoundingClientRect();
                      const sc=output.parentElement.dataset.mcelZone;
                      return {input:{x:a.x,y:a.y,width:a.width,height:a.height,right:a.right},
                        result:{x:b.x,y:b.y,width:b.width,height:b.height,right:b.right},
                        resultZone:sc, overflowX:document.documentElement.scrollWidth>innerWidth};
                    }""", {"source": result['sourceRegion'], "target": result['resultRegion']})
                    overlap = page.evaluate("""() => {
                      const f=document.querySelector('form[data-intent="intent:draw-graph"]');
                      const elements=[...f.querySelectorAll('label[data-field],button[type="submit"],button[type="reset"]')];
                      const boxes=elements.map(e=>({id:e.dataset.field||e.textContent.trim(),r:e.getBoundingClientRect()}));
                      const collisions=[];
                      for(let i=0;i<boxes.length;i++) for(let j=i+1;j<boxes.length;j++) {
                        const a=boxes[i].r,b=boxes[j].r;
                        if(Math.min(a.right,b.right)-Math.max(a.left,b.left)>2 &&
                           Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>2)
                          collisions.push([boxes[i].id,boxes[j].id]);
                      }
                      const nav=[...document.querySelectorAll('[data-mcel-mode-tabs] button')]
                        .filter(e=>e.scrollWidth>e.clientWidth+2).map(e=>e.textContent.trim());
                      const hidden=[...document.querySelectorAll('[data-mcel-region][hidden]')]
                        .filter(e=>getComputedStyle(e).display!=='none').map(e=>e.dataset.mcelRegion);
                      const navzone=document.querySelector('[data-mcel-zone="navigator"]');
                      const title=navzone.querySelector('[data-mcel-application-title]');
                      const n=navzone.getBoundingClientRect(), t=title.getBoundingClientRect();
                      const titleVisible=t.top>=n.top-2 && t.bottom<=n.bottom+2;
                      return {collisions,clippedNavigation:nav,visibleHiddenRegions:hidden,titleVisible};
                    }""")
                    assert not overlap['collisions'], overlap
                    assert not overlap['clippedNavigation'], overlap
                    assert not overlap['visibleHiddenRegions'], overlap
                    assert overlap['titleVisible'], overlap
                    if choice['arrangement'] in {'two-region', 'three-region'}:
                        assert geometry['resultZone'] == 'tools'
                        assert geometry['result']['x'] >= geometry['input']['right'] - 2, geometry
                        assert geometry['result']['width'] >= result['resultDemand']['min_width'], geometry
                        assert geometry['input']['y'] < 0.4 * 740, geometry
                        assert geometry['result']['y'] < 0.4 * 740, geometry
                    else:
                        assert geometry['resultZone'] == 'workspace', geometry
                    assert not geometry['overflowX'], geometry
                    # Event handlers, capability boundary and observed state
                    # must survive moving the REAL output element.
                    f.locator('input[name="expression"]').fill('cos(x)')
                    f.locator('button[type="submit"]').click()
                    page.wait_for_function("() => window.__mcelExpanded.app.readState('state:graph-expression') === 'cos(x)'")
                    after = page.evaluate("""() => ({
                      samples:window.__mcelExpanded.app.readState('state:graph-points').length,
                      path:document.querySelector('[data-mcel-plot] svg path').getAttribute('d')
                    })""")
                    assert after['samples'] > 100 and before['path'] != after['path']
                    if choice['arrangement'] in {'two-region', 'three-region'}:
                        assert baseline_geometry['resultY'] > baseline_geometry['inputY']
                        assert geometry['result']['y'] <= geometry['input']['y'] + 2, (viewport, geometry, choice)
                        assert geometry['result']['width'] >= baseline_geometry['resultWidth'] * 0.90, (viewport, geometry, baseline_geometry, choice)
                        assert geometry['result']['width'] > geometry['input']['width']
                    # Mode navigation is still wired and does not lose its
                    # original state linkage when the result is relocated.
                    page.locator('[data-mcel-mode-tabs] button[data-value="basic"]').click()
                    page.wait_for_function("() => window.__mcelExpanded.app.readState('state:mode') === 'basic'")
                    page.locator('[data-mcel-mode-tabs] button[data-value="graphing"]').click()
                    page.wait_for_function("() => window.__mcelExpanded.app.readState('state:mode') === 'graphing'")
                    assert page.locator('[data-mcel-plot] svg path').count() > 0
                    assert page.locator('[data-mcel-region$="status"]').count() == 1
                    if screenshots is not None:
                        screenshots.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(screenshots / f'negotiated-{viewport}.png'))
                    trial.append({"viewport": viewport, "negotiation": result,
                                  "baselineGeometry": baseline_geometry,
                                  "baselineHiddenLeak": hidden_leak,
                                  "childGeometryOracle": overlap,
                                  "geometry": geometry, "stateRetained": True,
                                  "modeNavigationRetained": True,
                                  "functionalityRetained": True})
                finally:
                    page.close()
        finally:
            browser.close()
    return trial


def main() -> None:
    from main_computer.mcel_calculator_expanded_host import compile_expanded
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--browser', action='store_true', help='trial real Calculator DOM and audit the complete canonical MCEL app battery')
    ap.add_argument('--calculator-only', action='store_true', help='legacy isolated experimental smoke only; not a production gate')
    ap.add_argument('--report', type=Path, help='save inspectable JSON evidence')
    ap.add_argument('--screenshots', type=Path, help='record the temporary browser composition at each viewport')
    args = ap.parse_args()
    ir = compile_expanded()
    report = {"schema": "mcel.negotiation-smoke-report.v1", "productionPromotion": False,
              "static": run_static_smoke(ir), "browser": run_browser_smoke(ir, screenshots=args.screenshots) if args.browser else None}
    if args.browser and not args.calculator_only:
        from tools.mcel_composition_battery_smoke import run_battery
        report['fullBattery'] = run_battery(browser=True, screenshots=args.screenshots)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    for item in report['static']:
        plan = item['winner']
        print(f"{item['relationship']['mode']:>8} {item['width']:4d}px: "
              f"{plan['arrangement']:<14} nav={plan['nav_width']:3} "
              f"controls={plan['input_width']:4} result={plan['result_width']:4} "
              f"score={plan['score']:.2f} candidates={item['candidateCount']}")
    if report['browser'] is not None:
        for item in report['browser']:
            print(f"browser {item['viewport']:4d}px: {item['negotiation']['winner']['arrangement']}, "
                  f"{item['geometry']['resultZone']} placement, graph updated after reflow")
    battery=report.get('fullBattery')
    if battery is not None:
        cv=battery['coverage']
        print(f"BATTERY {cv['inventory']} canonical destinations + {cv['additionalMCELTargets']} separate SLH targets; "
              f"{cv['shellRoutesChecked']} shell checks, {cv['browserObservations']} semantic DOM checks, "
              f"{cv['legacyMarkupObservations']} inert legacy checks; guard failures={cv['guardFailures']}")
        print(f"GENERAL NEGOTIATOR eligible={cv['negotiationEligible']}/7 semantic targets; "
              f"live trials={cv['liveTrials']} fully trialed targets={cv['fullyTrialedTargets']} "
              f"partial targets={cv['partiallyTrialedTargets']} "
              f"unsupported topologies={cv['unsupportedTopologies']} "
              f"rejected trials={cv['rejectedTrials']}")
        print('EXPERIMENTAL GATE:',battery['experimentalGate'])
        print('PRODUCTION GATE:',battery['productionGate'])
        if battery['productionGate']!='CANDIDATE':
            raise SystemExit(2)
    print('SMOKE PASS (experimental only; production renderer unchanged)')


if __name__ == '__main__':
    main()
