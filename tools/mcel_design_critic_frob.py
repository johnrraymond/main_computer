"""MCEL Design Critic Frob: three visual composition alternatives, dual reviewer packet.

Commands (all Python):
  python tools/mcel_design_critic_frob.py --mode build
  python tools/mcel_design_critic_frob.py --mode local --packet runtime/reports/mcel-design-critic/mcel-design-review.zip
  python tools/mcel_design_critic_frob.py --mode local --packet ... --dry-run
  python tools/mcel_design_critic_frob.py --mode import --packet ... --review reviewer-response.json
  python tools/mcel_design_critic_frob.py --mode compare --packet ... --review reviewer-response.json --local-review local-review.json

No live applications, effects, external connections, or automatic policy changes.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
from urllib import request, error, parse
import zipfile

# Preserve direct 'python tools/...py' launcher compatibility.
_PROJECT_ROOT=Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:sys.path.insert(0,str(_PROJECT_ROOT))
from tools import mcel_semantic_generate as generator
from tools.mcel_browser_launch import launch_chromium
from tools.mcel_frob_visual_evidence import capture, write_gallery, verify_capture

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT=ROOT/'runtime/reports/mcel-design-critic'
DEFAULT_MODEL='gemma4:26b-a4b-it-q4_K_M'
PACKET_SCHEMA='mcel.design-review-packet.v1'
REVIEW_SCHEMA='mcel.design-critique.v1'
CRITERIA=('task_clarity','visual_hierarchy','information_density','readability','responsive_quality','visual_coherence')
POLICY_ACTIONS=('prefer_composition','prefer_pattern','change_prominence','change_disclosure','improve_label','adjust_spacing')
# Distinct generic spatial organizations; never inspect app ID or row type.
STRATEGIES={
 'A':{'id':'stacked-workspace','title':'Stacked workspace',
      'why':'Canonical single-column stack: compact status above the primary workspace'},
 'B':{'id':'attention-rail','title':'Attention rail',
      'why':'Canonical wide layout: compact status and tools in a left rail, workspace in the main column'},
 'C':{'id':'workspace-with-tools-rail','title':'Workspace with tools rail',
      'why':'Canonical wide layout: status banner, workspace and optional tools alongside; compact two-column cards on mobile'},
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def jd(path:Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')


def review_questions():
    return [
      {'id':'Q1','text':'Which candidate best supports the declared active task on desktop and mobile? Explain differences and uncertainty.'},
      {'id':'Q2','text':'What concrete visual hierarchy, grouping, density, typography, or information-access defects are visible? Cite screenshots.'},
      {'id':'Q3','text':'Did any candidate make failure/unknown state harder to notice? Distinguish verified failure from unknown outcome.'},
      {'id':'Q4','text':'Which strengths can transfer to an unfamiliar application? Propose general semantic presentation-policy changes, not app-specific CSS.'},
      {'id':'Q5','text':'What tests would falsify each proposed policy? Which issues need human inspection or cannot be answered from PNGs?'},
    ]


def prompt_text(packet):
    digest=review_hash(packet)
    return f'Packet SHA-256 (copy this exact value into packet_sha256): {digest}\n\n'+'''# MCEL comparative design-critique request\n\nYou are a skeptical HCI reviewer, not the application generator.\nExamine the attached actual screenshots **as images** and compare the THREE distinct compositions A/B/C, using exactly the same semantic application, synthetic data, and states. This is a review, not a request to certify aesthetics automatically.\n\nUse the screenshot IDs in `packet.json`. Evidence that is not visually observable must be marked `not_observable`, even if a test report claims PASS. Automated browser proof establishes behavior/geometry, not beauty. Do not presume the existing candidate is best. Do not invent real data, user preferences, or a universal style ranking.\n\nQuestions:\n'''+''.join(f"{q['id']}. {q['text']}\n" for q in packet['questions'])+'''\nReturn a **single JSON object** following `review-schema.json`, including rankings/scores for each candidate, evidence-linked findings, testable **general MCEL policies** for investigation, limitations, and human confirmation required. Do NOT include executable code, per-application CSS, hidden state claims, approval for production, or automatic mutations.\n\nIf any image cannot be viewed, report its ID under `images_not_reviewed` and lower confidence; do not claim to have inspected it. `human_approval` must remain `PENDING`.\n'''


def review_schema():
    return {'schema':REVIEW_SCHEMA,
            'fields':{'schema':REVIEW_SCHEMA,'reviewer':'string','packet_sha256':'sha256 of packet.json',
                      'preferred_candidate':'A|B|C|null',
                      'candidate_scores':{'A':{k:'integer 1..5 or null' for k in CRITERIA},
                                          'B':{k:'integer 1..5 or null' for k in CRITERIA},
                                          'C':{k:'integer 1..5 or null' for k in CRITERIA}},
                      'findings':[{'severity':'minor|major|critical', 'candidate':'A|B|C',
                                   'screenshot_ids':['image id'], 'observation':'string',
                                   'why':'string'}],
                      'policy_hypotheses':[{'id':'string','action':'one of '+','.join(POLICY_ACTIONS),
                                             'scope':'general','rationale':'string',
                                             'verification':['independent checks'],'human_confirmation_required':True}],
                      'images_not_reviewed':['image id'], 'limitations':['string'],
                      'human_approval':'PENDING'}}


def candidate_snapshot(page, *, expected_count:int, scenario:dict):
    return page.evaluate('''({expectedCount})=>{
      const root=document.getElementById('generated-root');const status=root.querySelector('[data-role="status-and-attention"]');
      const workspace=document.getElementById('mcel-workspace-composite');
      const tools=root.querySelector('[data-role="supporting-tools"]');
      const layout=window.__MCEL_LAYOUT_PROOF;
      const rect=e=>{const b=e.getBoundingClientRect();return {x:Math.round(b.left),y:Math.round(b.top),width:Math.round(b.width),height:Math.round(b.height)};};
      const count=root.querySelectorAll('[data-mcel-element-id]').length + Number(root.hasAttribute('data-mcel-element-id'));
      return {ready:window.__MCEL_GENERATED_READY===true,layout_status:layout?.status||null,
        registry_count:count,registry_intact:count===expectedCount,
        search_with_collection:!!workspace&&workspace.contains(root.querySelector('#global-search')),
        status_rect:rect(status),workspace_rect:rect(workspace),tools_rect:rect(tools),
        document_width:document.documentElement.scrollWidth,viewport_width:innerWidth,
        overflow:document.documentElement.scrollWidth>innerWidth+1,
        operation:window.__MCEL_STATE_PROOF?.operation?.state||null,
        visible_failure:!!root.querySelector('[data-role="persistent-nonblocking-feedback"]:not([hidden])'),
        active_task:root.dataset.activeTask||null, screenshot_fixture:root.dataset.visualFixtureState||'default'};
    }''',{'expectedCount':expected_count})


def build(design:Path,output:Path,*,widths=(390,1440),states=('default','operation_verified_failure'),chromium_executable=None):
    """Generate browser-captured candidates and portable/locally reviewable ZIP.

    All three use the same compiled MCEL component tree and component handlers.
    Visual candidate CSS is a versioned experimental policy; not source app CSS.
    """
    from playwright.sync_api import sync_playwright
    design=Path(design).resolve();output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    widths=tuple(dict.fromkeys(widths));states=tuple(dict.fromkeys(states))
    if set(states)-{'default','operation_verified_failure','operation_pending','unavailable','unknown','idle'}:
        raise ValueError('Unsupported critique state')
    if any(w<320 or w>2560 for w in widths) or not widths or not states:
        raise ValueError('Critique requires valid viewports and states')
    # Three calls to the SAME semantic compiler/runtime with different generic
    # canonical composition policies; verify the underlying MCEL element trees match.
    compiled={};sources={};manifests={};generateds={}
    for cid in STRATEGIES:
        generated_out=output/f'generated-{cid}'
        if generator.run(design,generated_out,verify=False,presentation_mode='planned',design_critic_strategy=cid):
            raise ValueError(f'MCEL source generation or DSL compilation refused candidate {cid}')
        generateds[cid]=json.loads((generated_out/'report.json').read_text(encoding='utf-8'))
        sources[cid]=(generated_out/'preview.html').read_text(encoding='utf-8')
        manifests[cid]=json.loads((generated_out/'manifest.json').read_text(encoding='utf-8'))
        compiled[cid]=sha(json.dumps(manifests[cid]['componentTree'],sort_keys=True).encode())
    if len(set(compiled.values()))!=1:raise ValueError('Design candidates differ in semantic MCEL component tree')
    frozen_manifest=json.dumps(manifests['A'],sort_keys=True).encode()
    registered_count=sum(generateds['A']['component_counts'].values())
    captures=[];evaluations=[]
    with sync_playwright() as pw:
        browser,browser_launch=launch_chromium(pw,executable=chromium_executable,headless=True,
                                                args=['--no-sandbox','--disable-dev-shm-usage'])
        try:
            browser_version=browser.version
            for width in widths:
                for state in states:
                    for candidate,policy in STRATEGIES.items():
                        page=browser.new_page(viewport={'width':width,'height':900},device_scale_factor=1)
                        errors=[]
                        page.on('pageerror',lambda e,errors=errors:errors.append(str(e)))
                        try:
                            page.set_content(sources[candidate],wait_until='load',timeout=30000)
                            page.wait_for_function('window.__MCEL_GENERATED_READY===true',timeout=15000)
                            if state!='default':
                                result=page.evaluate('(s)=>window.__MCEL_SET_VISUAL_FIXTURE(s)',state)
                                if result['state']!=state:raise ValueError('State fixture mutation rejected')
                            page.wait_for_timeout(180)  # allow resize handler to stabilize
                            obs=candidate_snapshot(page,expected_count=registered_count,scenario={})
                            if not obs['ready'] or obs['layout_status']!='pass' or not obs['registry_intact'] or obs['overflow'] or errors:
                                raise ValueError(f'Browser evidence failed for {candidate}/{width}/{state}: {obs} {errors}')
                            if not obs['search_with_collection']:
                                raise ValueError('Search detached from semantic collection')
                            if state=='operation_verified_failure':
                                if obs['operation']!='failed' or not obs['visible_failure']:
                                    raise ValueError('Verified failure hidden or misrepresented')
                            entry=capture(page,output,viewport=width,view='primary',state=state,
                                label=f'{candidate} · {policy["title"]} · {width}px · {state}',
                                filename=f'{candidate}-{width}-{state}.png',kind='design-critic')
                            # Verify that distinct spatial plans still preserve active interactions.
                            if state=='default':
                                input_box=page.locator('#global-search')
                                input_box.fill('unmatched-term-frob-09332')
                                if page.locator('.record:visible').count()!=0:
                                    raise ValueError('Search filtering failed in composed candidate')
                                input_box.fill('')
                                if page.locator('.record:visible').count()==0:
                                    raise ValueError('All records disappeared after clearing search')
                                page.locator('.record:visible').first.click()
                                if not page.locator('#selected-entity').inner_text().strip():
                                    raise ValueError('Inspector selection failed in composed candidate')
                            entry['candidate']=candidate
                            captures.append(entry)
                            evaluations.append({'screenshot_id':entry['id'],'candidate':candidate,'viewport':width,
                                                'state':state,'checks':{'layout':True,'semantic_components':True,'no_overflow':True,
                                                'search_colocated':True,'failure_truth':True},'observed':obs})
                        finally:page.close()
        finally:browser.close()
    # Real geometry diversity is mandatory on wide screens; mere renaming fails.
    wide=[e for e in evaluations if e['viewport']>=1000 and e['state']==states[0]]
    if len(wide)==3:
        coords={(r['observed']['status_rect']['x'],r['observed']['status_rect']['y'],
                 r['observed']['workspace_rect']['x'],r['observed']['workspace_rect']['y'],r['observed']['workspace_rect']['width']) for r in wide}
        if len(coords)<3:raise ValueError('Candidates are not spatially distinct in browser geometry')
    gallery=write_gallery(output,captures,title='MCEL Design Critic — 3 competing compositions',
                         provenance={'source_design_sha256':sha(design.read_bytes()),
                                     'compiled_fixture_sha256':sha(frozen_manifest),'component_tree_sha256':compiled['A'],'browser_version':browser_version,'browser_launch':browser_launch,
                                     'visual_quality':'UNREVIEWED','synthetic':True})
    packet={'schema':PACKET_SCHEMA,'run_id':sha(frozen_manifest)[:16],
            'design_sha256':sha(design.read_bytes()),'generated_manifest_sha256':sha(frozen_manifest),
            'generator_report_status':all(r['dsl_compiler']['valid'] for r in generateds.values()),
            'component_count':registered_count,'component_tree_sha256':compiled['A'],'canonical_runtime_sha256':sha(generator.RUNTIME_JS.read_bytes()),'questions':review_questions(),
            'candidates':{k:{**policy,'source':'MCEL canonical layout variant + generic presentation recipe'} for k,policy in STRATEGIES.items()},
            'screenshots':[{'id':c['id'],'candidate':c['candidate'],'viewport':c['viewport'],'state':c['state'],
                            'path':c['screenshot'],'sha256':c['screenshot_sha256'],'width':c['pixel_width'],'height':c['pixel_height']} for c in captures],
            'browser_proof':evaluations,'human_approval':'PENDING',
            'limitations':['Synthetic fixture, no production behavior evidence','No human visual approval',
                           'Experimental canonical composition candidates are not approved production policies',
                           'Candidate aesthetics must not be inferred from geometry checks']}
    jd(output/'packet.json',packet)
    jd(output/'review-schema.json',review_schema())
    (output/'review-prompt.md').write_text(prompt_text(packet),encoding='utf-8')
    sample={'schema':REVIEW_SCHEMA,'reviewer':'chatgpt','packet_sha256':review_hash(packet),
            'preferred_candidate':None,
            'candidate_scores':{k:{criterion:None for criterion in CRITERIA} for k in STRATEGIES},
            'findings':[], 'policy_hypotheses':[], 'images_not_reviewed':[],
            'limitations':['Template only; not a completed visual critique'], 'human_approval':'PENDING'}
    jd(output/'review-template.json',sample)
    jd(output/'browser-report.json',{'status':'BOUNDED_PASS','cases':len(evaluations),
                                    'distinct_wide_compositions':len(coords) if len(wide)==3 else 'not_checked',
                                    'gallery':'gallery.html','component_count':registered_count})
    # Portable packet for ChatGPT upload AND offline Gemma; no source secrets or binary files besides screenshots.
    archive=output/'mcel-design-review.zip'
    names=['packet.json','review-schema.json','review-prompt.md','review-template.json','browser-report.json','gallery.html','visual-manifest.json']+[c['screenshot'] for c in captures]
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for name in names:z.write(output/name,name)
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:raise ValueError('Invalid review packet ZIP')
    return {'status':'PASS','screenshots':len(captures),'packet':str(archive),
            'gallery':str(output/'gallery.html'),'browser_version':browser_version,'browser_launch':browser_launch,
            'component_count':registered_count}


def open_packet(path:Path):
    """Open packet ZIP or directory; validate images and hashes before review."""
    path=Path(path)
    if path.is_dir():
        def load(n):return (path/n).read_bytes()
        names=set(x.name for x in path.iterdir() if x.is_file())
    elif path.is_file() and zipfile.is_zipfile(path):
        z=zipfile.ZipFile(path)
        names=set(z.namelist())
        if len(names)!=len(z.namelist()):raise ValueError('Duplicate ZIP members')
        if any('/' in n or '\\' in n or n.startswith('.') for n in names):raise ValueError('Unsafe ZIP file path')
        def load(n):return z.read(n)
    else:raise ValueError('Packet must be a directory or review ZIP')
    packet=json.loads(load('packet.json'))
    if packet.get('schema')!=PACKET_SCHEMA or len(packet.get('screenshots',[]))<6:
        raise ValueError('Invalid MCEL review packet')
    if len({x['id'] for x in packet['screenshots']}) != len(packet['screenshots']):
        raise ValueError('Duplicate screenshot IDs')
    for entry in packet['screenshots']:
        name=entry['path']
        if name not in names or Path(name).name!=name or len(load(name))>4_000_000 or sha(load(name))!=entry['sha256']:
            raise ValueError('Missing, altered or unsafe screenshot: '+str(name))
    return packet,load


def parse_review(obj,packet,*,reviewer='untrusted'):
    """Validate reviewed evidence; do not turn model output into executable policy."""
    if obj.get('schema')!=REVIEW_SCHEMA:
        raise ValueError('Review schema mismatch')
    if obj.get('packet_sha256')!=sha(json.dumps(packet,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()):
        raise ValueError('Review refers to a different packet')
    if obj.get('preferred_candidate') not in (None,'A','B','C'):
        raise ValueError('Invalid preferred candidate')
    if obj.get('human_approval')!='PENDING':
        raise ValueError('Model review cannot grant human approval')
    scores=obj.get('candidate_scores',{})
    if set(scores)!=set(STRATEGIES):raise ValueError('Must assess all candidates')
    for cid,candidate_scores in scores.items():
        if set(candidate_scores)!=set(CRITERIA):raise ValueError('Missing reviewer criterion')
        if any(v is not None and (type(v) is not int or not 1<=v<=5) for v in candidate_scores.values()):
            raise ValueError('Invalid score range')
    image_ids={s['id'] for s in packet['screenshots']}
    unseen=set(obj.get('images_not_reviewed',[]))
    if not unseen<=image_ids:raise ValueError('Unknown unreviewed screenshot ID')
    if not isinstance(obj.get('limitations'),list):raise ValueError('Review limitations required')
    if not isinstance(obj.get('findings'),list) or not isinstance(obj.get('policy_hypotheses'),list):
        raise ValueError('Review must include findings and hypotheses lists')
    for finding in obj.get('findings',[]):
        if finding.get('candidate') not in STRATEGIES or finding.get('severity') not in ('minor','major','critical'):
            raise ValueError('Invalid finding')
        if not finding.get('screenshot_ids') or not set(finding['screenshot_ids'])<=image_ids:
            raise ValueError('Finding must cite real screenshot IDs')
    for hypothesis in obj.get('policy_hypotheses',[]):
        if hypothesis.get('scope')!='general' or hypothesis.get('action') not in POLICY_ACTIONS:
            raise ValueError('Only general, controlled presentation-policy hypotheses allowed')
        if hypothesis.get('human_confirmation_required') is not True or not hypothesis.get('verification'):
            raise ValueError('Hypothesis requires tests and human confirmation')
    return {'schema':REVIEW_SCHEMA,'reviewer':str(obj.get('reviewer') or reviewer),
            'packet_sha256':obj['packet_sha256'],'preferred_candidate':obj['preferred_candidate'],
            'candidate_scores':scores,'findings':obj.get('findings',[]),
            'policy_hypotheses':obj.get('policy_hypotheses',[]),'images_not_reviewed':list(unseen),
            'limitations':obj['limitations'],'human_approval':'PENDING','status':'CRITIQUE_ONLY'}


def review_hash(packet):
    return sha(json.dumps(packet,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())


def safe_local_ollama(url):
    p=parse.urlparse(url)
    if p.scheme!='http' or p.hostname not in ('localhost','127.0.0.1','::1') or p.username or p.password or p.query or p.fragment:
        raise ValueError('Only a local HTTP Ollama endpoint is allowed for image review')
    return url.rstrip('/')


def http_json(url,payload,timeout):
    data=json.dumps(payload).encode('utf-8')
    req=request.Request(url,data=data,headers={'Content-Type':'application/json'},method='POST')
    try:
        with request.urlopen(req,timeout=timeout) as res:
            if res.status!=200:raise RuntimeError('Ollama HTTP status '+str(res.status))
            raw=res.read(4_000_001)
            if len(raw)>4_000_000:raise RuntimeError('Ollama output exceeds size bound')
            return json.loads(raw)
    except error.HTTPError as exc:
        detail=exc.read(1000).decode('utf-8','replace')
        raise RuntimeError(f'Ollama request refused (HTTP {exc.code}); vision/model support may be missing: {detail}') from exc


def images_for_local(load,images,*,max_w=1152,max_h=1550):
    try:
        from PIL import Image,ImageOps
    except ImportError:
        return [base64.b64encode(load(spec['path'])).decode('ascii') for spec in images]
    encoded=[]
    for spec in images:
        with Image.open(io.BytesIO(load(spec['path']))) as img:
            rgb=ImageOps.exif_transpose(img).convert('RGB')
            rgb.thumbnail((max_w,max_h))
            stream=io.BytesIO();rgb.save(stream,format='JPEG',quality=82,optimize=True)
            encoded.append(base64.b64encode(stream.getvalue()).decode('ascii'))
    return encoded


def local_review(path:Path,output:Path,*,url='http://127.0.0.1:11434',model=DEFAULT_MODEL,dry_run=False,timeout=420):
    packet,load=open_packet(path);url=safe_local_ollama(url)
    groups={}
    for item in packet['screenshots']:
        groups.setdefault((item['viewport'],item['state']),[]).append(item)
    if not all({x['candidate'] for x in items}==set(STRATEGIES) for items in groups.values()):
        raise ValueError('Incomparable critique groups')
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    if dry_run:
        report={'status':'READY_NO_INFERENCE','model':model,'endpoint':url,'packet_sha256':review_hash(packet),
                'groups':[{'viewport':k[0],'state':k[1],'screenshot_ids':[x['id'] for x in v]} for k,v in sorted(groups.items())],
                'questions':packet['questions'],'image_method':'Ollama /api/chat messages[].images (base64); image bytes not saved'}
        jd(output/'local-preflight.json',report)
        return report
    # Do not silently send text-only if model has no image support.
    info=http_json(url+'/api/show',{'model':model},min(30,timeout))
    caps=info.get('capabilities')
    if isinstance(caps,list) and 'vision' not in caps:
        raise RuntimeError(f'Local model does not advertise vision capability: {model}. Cannot judge screenshots.')
    reviews=[]
    for (width,state),images in sorted(groups.items()):
        images=sorted(images,key=lambda item:item['candidate'])
        prompt=("Review three alternative MCEL layouts A/B/C. Compare only this matched viewport/state, not other conditions. "
                f"Viewport={width}px, fixture={state}. Images attached in exact order: "+
                ', '.join(f"{x['candidate']}={x['id']}" for x in images)+". "
                "Answer Q1-Q5 from the provided question set; ground findings in screenshot IDs. "
                "Only propose general, testable MCEL policies. An unavailable metric is not zero; failure must remain visible. "
                "No human approval. Return JSON with keys preferred_candidate (A|B|C|null), "
                "candidate_scores (A/B/C each criterion 1..5 or null), findings (candidate, severity, screenshot_ids, observation, why), "
                "policy_hypotheses (id, action, scope='general', rationale, verification array, human_confirmation_required=true), "
                "images_not_reviewed (array), limitations (array). Criteria: "+','.join(CRITERIA)+'. Questions: '+
                json.dumps(packet['questions'],ensure_ascii=False))
        payload={'model':model,'messages':[{'role':'system','content':'You are an evidence-driven HCI critic, never a production approval authority. Only JSON output.'},
                                            {'role':'user','content':prompt,'images':images_for_local(load,images)}],
                 'stream':False,'format':'json','think':False,'options':{'temperature':0.1,'num_predict':2400}}
        raw=http_json(url+'/api/chat',payload,timeout)
        text=str(raw.get('message',{}).get('content','')).strip()
        if not text:raise RuntimeError('Local model provided no visible review text')
        if text.startswith('```'):
            text=re.sub(r'^```(?:json)?\s*|\s*```$','',text).strip()
        try:review=json.loads(text)
        except ValueError as exc:
            raise RuntimeError('Local model response was not parseable JSON; see its stdout/error. No review accepted.') from exc
        # The model must not claim human approval, even if it produces plausible JSON.
        if review.get('human_approval') not in (None,'PENDING'):
            raise RuntimeError('Local model claimed human approval; review refused')
        # Normalize into the same strict review contract, refusing invented IDs.
        review['schema']=REVIEW_SCHEMA;review['reviewer']='ollama:'+model
        review['packet_sha256']=review_hash(packet);review['human_approval']='PENDING'
        validated=parse_review(review,packet,reviewer='ollama:'+model)
        reviews.append({'viewport':width,'state':state,'review':validated,
                        'screenshot_ids':[x['id'] for x in images]})
        jd(output/f'local-{width}-{state}.json',validated)
        print('LOCAL_REVIEW_PASS',width,state,validated['preferred_candidate'],flush=True)
    # Synthesize deterministic preference votes, not ungrounded text-model prose.
    votes=Counter(x['review']['preferred_candidate'] for x in reviews if x['review']['preferred_candidate'])
    aggregate={'status':'CRITIQUE_ONLY','model':model,'packet_sha256':review_hash(packet),
               'groups':reviews,'votes':dict(votes),'recommended_for_human_comparison':votes.most_common(1)[0][0] if votes else None,
               'human_approval':'PENDING','limitations':['Scores are subjective local-model opinions, not evidence of aesthetic correctness',
                                                      'No policies are auto-applied','One reviewer family may have systematic biases']}
    jd(output/'local-review.json',aggregate)
    return {'status':'CRITIQUE_ONLY','review':str(output/'local-review.json'),'votes':dict(votes),'groups':len(reviews)}


def compare_reviews(packet_path,chatgpt_path,local_path,out):
    packet,_=open_packet(packet_path)
    human=json.loads(Path(chatgpt_path).read_text(encoding='utf-8'))
    chatgpt=parse_review(human,packet)
    local=json.loads(Path(local_path).read_text(encoding='utf-8'))
    if local.get('packet_sha256')!=review_hash(packet):raise ValueError('Local critique is from a different packet')
    votes=Counter(x.get('review',{}).get('preferred_candidate') for x in local.get('groups',[]) if x.get('review',{}).get('preferred_candidate'))
    out.parent.mkdir(parents=True,exist_ok=True)
    result={'schema':'mcel.design-critic-comparison.v1','status':'HUMAN_REVIEW_REQUIRED',
            'packet_sha256':review_hash(packet), 'chatgpt_choice':chatgpt['preferred_candidate'],
            'local_group_votes':dict(votes), 'candidate_agreement':chatgpt['preferred_candidate'] in votes if chatgpt['preferred_candidate'] else None,
            'disagreements':'Compare screenshot-specific reasoning and visual evidence before selecting a policy',
            'human_approval':'PENDING', 'automatic_policy_mutation':False}
    jd(out,result)
    return result


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--mode',choices=['build','local','import','compare'],default='build')
    ap.add_argument('--design',type=Path,default=generator.DEFAULT_DESIGN)
    ap.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    ap.add_argument('--packet',type=Path)
    ap.add_argument('--review',type=Path)
    ap.add_argument('--local-review',type=Path)
    ap.add_argument('--model',default=DEFAULT_MODEL)
    ap.add_argument('--ollama-url',default='http://127.0.0.1:11434')
    ap.add_argument('--timeout',type=int,default=420)
    ap.add_argument('--dry-run',action='store_true')
    ap.add_argument('--chromium-executable',help='Explicit browser executable path; otherwise use Playwright/Edge/Chrome discovery')
    args=ap.parse_args(argv)
    try:
        if args.mode=='build':
            result=build(args.design,args.output,chromium_executable=args.chromium_executable)
        elif args.mode=='local':
            result=local_review(args.packet or args.output/'mcel-design-review.zip',args.output/'local',url=args.ollama_url,
                                model=args.model,dry_run=args.dry_run,timeout=args.timeout)
        elif args.mode=='import':
            if not args.review:raise ValueError('--review is required')
            packet,_=open_packet(args.packet or args.output/'mcel-design-review.zip')
            review=parse_review(json.loads(args.review.read_text(encoding='utf-8')),packet)
            jd(args.output/'chatgpt-review-validated.json',review)
            result={'status':'CRITIQUE_ONLY','review':str(args.output/'chatgpt-review-validated.json')}
        else:
            if not args.review or not args.local_review:raise ValueError('Both --review and --local-review are required')
            result=compare_reviews(args.packet or args.output/'mcel-design-review.zip',args.review,args.local_review,
                                   args.output/'critic-comparison.json')
    except (OSError,KeyError,ValueError,RuntimeError,TimeoutError,zipfile.BadZipFile) as exc:
        print('MCEL_DESIGN_CRITIC_REFUSED:',str(exc),file=sys.stderr)
        return 2
    print('MCEL_DESIGN_CRITIC_'+result['status'])
    print(json.dumps(result,indent=2,ensure_ascii=False))
    return 0


if __name__=='__main__':sys.exit(main())
