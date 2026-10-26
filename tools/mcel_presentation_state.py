"""MCEL state-aware presentation policy: pure, domain-independent, effect-free.

State is a *read-only projection* of authoritative runtime facts or deliberately
marked synthetic fixtures. No display policy writes back to those facts.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy

SCHEMA='mcel.presentation-state.v1'
AVAILABILITY={'ready','unavailable','stale','unknown','error'}
OPERATION={'none','submitted','acknowledged','running','succeeded','failed','canceled','unknown'}
ROLE_CHOICES={'primary-content','status-and-attention','global-context-and-search',
              'selection-details','persistent-nonblocking-feedback','supporting-tools'}


def normalize_state(raw):
    if not isinstance(raw,dict): raise ValueError('presentation state must be a mapping')
    if raw.get('schema',SCHEMA)!=SCHEMA:raise ValueError('unsupported presentation state schema')
    observations=raw.get('observations',[])
    if not isinstance(observations,list):raise ValueError('observations must be a list')
    normalized=[];seen=set()
    for obs in observations:
        if not isinstance(obs,dict) or not isinstance(obs.get('id'),str) or not obs['id'] or obs['id'] in seen:
            raise ValueError('observation must have unique, nonempty identity')
        seen.add(obs['id'])
        avail=obs.get('availability','unknown')
        if avail not in AVAILABILITY:raise ValueError('unknown observation availability')
        normalized.append({'id':obs['id'],'source':str(obs.get('source') or 'unattributed'),
                           'availability':avail,'value':deepcopy(obs.get('value'))})
    op=raw.get('operation', {'state':'none'})
    if not isinstance(op,dict) or op.get('state') not in OPERATION:
        raise ValueError('invalid operation state')
    state=op['state']
    verified=op.get('verified',False) is True
    # Never turn a claim of failure/success into an asserted final outcome
    # unless a trusted completion witness is supplied by the source.
    if state in ('succeeded','failed','canceled') and not verified:
        state='unknown'
    selected=raw.get('selection')
    if selected is not None and not isinstance(selected,str):raise ValueError('selection must be an identity or null')
    return {'schema':SCHEMA,'synthetic':raw.get('synthetic',True) is True,
            'observations':normalized,'operation':{'state':state,'verified':verified,
                'source':str(op.get('source') or 'unattributed'),
                'id':str(op.get('id') or '')},
            'selection':selected,
            'workflow':str(raw.get('workflow') or 'browse')}


def plan_state(presentation,raw,required_regions=()):
    """Decide *visibility*, not factual truth or execution state.

    An absent region never deletes its semantic source or registered components.
    """
    facts=normalize_state(raw)
    if not presentation or not presentation.get('semantic_preservation'):
        raise ValueError('cannot present invalid semantic source')
    regions=presentation['regions']
    if set(regions)!=ROLE_CHOICES:raise ValueError('unsupported region contract')
    count=Counter(o['availability'] for o in facts['observations'])
    op=facts['operation']['state']
    has_issue=bool(count['error'] or count['unavailable'] or count['stale'] or count['unknown'])
    active=op in ('submitted','acknowledged','running')
    alarming=op in ('failed','unknown') or count['error']>0
    # No observation is never interpreted as a healthy observation.
    if not facts['observations']:
        status='No verified observations'
    elif count['error']:
        status='Observation errors'
    elif count['unavailable'] or count['stale'] or count['unknown']:
        status='Observations incomplete'
    else:
        status='Observations available'  # not a global 'healthy' claim
    if op=='failed': status='Operation failed'
    elif op=='unknown':status='Operation outcome unknown'
    elif active:status='Operation in progress'
    summary={'message':status,'availability_counts':dict(sorted(count.items())),
             'sources':sorted({o['source'] for o in facts['observations']}),
             'observation_ids':[o['id'] for o in facts['observations']],
             'operation_state':op,'operation_verified':facts['operation']['verified'],
             'no_healthy_inference':True}
    region_decisions={
        'primary-content':('normal','Primary collection remains accessible'),
        'status-and-attention':('prominent' if alarming else 'compact',
             'Active failures or unknown outcomes retain attention' if alarming else 'Consolidate observations into one accessible summary'),
        'global-context-and-search':('compact','Keep search accessible near active collection'),
        'selection-details':('normal' if facts['selection'] else 'absent',
             'Only show contextual inspector after selection'),
        'persistent-nonblocking-feedback':('prominent' if alarming else 'normal' if active else 'absent',
             'Show operational feedback only during activity or unresolved/failed outcome'),
        'supporting-tools':('compact','Optional actions remain accessible through disclosure'),
    }
    required=set(required_regions)
    unknown=required-{r['region'] for r in regions.values()}
    if unknown:raise ValueError('require references missing semantic regions: '+','.join(sorted(unknown)))
    for role,(mode,_) in region_decisions.items():
        if mode=='absent' and regions[role]['region'] in required:
            raise ValueError('MCEL_STATE_REQUIRED_REGION_HIDDEN: '+regions[role]['region'])
    return {'schema':'mcel.state-presentation-plan.v1','facts':facts,'summary':summary,
            'regions':{role:{'id':regions[role]['region'],'mode':mode,'reason':reason,
                       'semantic_retained':True} for role,(mode,reason) in region_decisions.items()},
            'reason':'Pure state-aware visibility/attention policy, does not authorize effects'}


def fixture_state(preset='unavailable',selection=None):
    """Explicit synthetic evidence, never a claim of a live service outcome."""
    known={
      'unavailable':([('cpu','unavailable'),('memory','unavailable'),('worker','unknown')],'none',False),
      'idle':([('cpu','ready'),('memory','ready'),('worker','ready')],'none',False),
      'running':([('cpu','ready'),('memory','ready')],'running',False),
      'failure':([('cpu','ready'),('memory','ready')],'failed',True),
      'unknown':([('cpu','stale'),('memory','unknown')],'unknown',False),
      'empty':([], 'none',False),
    }
    if preset not in known:raise ValueError('Unknown synthetic presentation fixture')
    observations,op,verified=known[preset]
    return {'schema':SCHEMA,'synthetic':True,'observations':[
      {'id':i,'source':'fixture-telemetry','availability':a,'value':None} for i,a in observations],
      'operation':{'id':'fixture-operation','state':op,'verified':verified,'source':'fixture-operations'},
      'selection':selection,'workflow':'browse'}
