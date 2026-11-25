"""0117 held-out independently authored binding fragments. Imported in PREPARE only.
Not intended as a complete game-solving ontology. Implicit ownership is exploratory.
IDs identify mention positions, not normalized entity names. Paraphrase IDs match.
"""
from tools import nanojev_gamer_v1_g2_multilayer_reconstruction_0116 as L

def cases():
    rows=[]
    def make(base,mode,segments,edges,implicit=False):
        prompt='';nodes=[]
        for s in segments:
            if isinstance(s,str):prompt+=s;continue
            i,k,v=s;nodes.append({'id':i,'kind':k,'span':[len(prompt),len(prompt)+len(v)]});prompt+=v
        graph={'nodes':nodes,'edges':[{'src':a,'dst':b,'type':t} for a,b,t in edges]}
        row={'case_id':'0117-'+base+'-'+mode,'base_id':'0117-'+base,'family':'binding_fragment','cohort':('heldout_implicit_quantity' if implicit=='quantity' else 'heldout_implicit_ownership') if implicit else 'heldout_explicit','mode':mode,'prompt':prompt,'prompt_sha256':L.P.digest_text(prompt),'graph':graph}
        assert len(L.gold(row))==len(edges);rows.append(row)
    for para in (False,True):
        for flip in (False,True):
            mode=('reverse' if flip else 'direct')+('_paraphrase' if para else '')
            def act(i,s):return (i,'action',s)
            def actor(i,s):return (i,'actor',s)
            def qty(i,s):return (i,'quantity',s)
            def out(i,s):return (i,'outcome',s)
            def named(a,b):return (b,a) if flip else (a,b)
            def refs():return [('x','b' if flip else 'a','refers_to'),('y','a' if flip else 'b','refers_to')]
            own=[('u','a','owns'),('u','b','owns')]
            # Held-out syntax: two declarations followed by a priority notation.
            a,b=named('Jade','Opal')
            seg=[actor('u','broker'),' has two options: ',act('a','Jade'),', ',act('b','Opal'),'. Priority order [',act('x',a),' > ',act('y',b),'].'] if not para else ['The choices of the ',actor('u','broker'),' are ',act('a','Jade'),' and ',act('b','Opal'),'. Rank ',act('x',a),' ahead of ',act('y',b),'.']
            make('priority_notation',mode,seg,own+refs())
            # Reference target changes without any numeric or position cue.
            a,b=named('Moss','Reed')
            seg=[actor('u','curator'),' may select ',act('a','Moss'),' or ',act('b','Reed'),'. The first entry in the review is ',act('x',a),'; the second is ',act('y',b),'.'] if not para else [actor('u','curator'),' has the options ',act('a','Moss'),' and ',act('b','Reed'),'. Review ',act('x',a),' first and ',act('y',b),' second.']
            make('review_slots',mode,seg,own+refs())
            # Value-before-reference layout; distinct punctuation and clauses.
            a,b=named('Lime','Pear')
            seg=[actor('u','buyer'),' can choose ',act('a','Lime'),' or ',act('b','Pear'),'. ',out('e','cost'),': ',qty('q','24'),' for ',act('x',a),'; ',out('c','cost'),': ',qty('r','81'),' for ',act('y',b),'.'] if not para else [actor('u','buyer'),' has alternatives ',act('a','Lime'),', ',act('b','Pear'),'. ',qty('q','24'),' is the ',out('e','cost'),' of ',act('x',a),'. ',qty('r','81'),' is the ',out('c','cost'),' of ',act('y',b),'.']
            bindings=[('q','x','applies_to'),('r','y','applies_to'),('q','e','measures'),('r','c','measures')]
            make('cost_notation',mode,seg,own+refs()+bindings)
            # Outcome-first action associations with action-first paraphrase.
            a,b=named('Dawn','Dusk')
            seg=[actor('u','seller'),' offers ',act('a','Dawn'),' and ',act('b','Dusk'),'. For ',act('x',a),', ',out('e','benefit'),' is ',qty('q','32'),'. For ',act('y',b),', ',out('c','benefit'),' is ',qty('r','76'),'.'] if not para else [actor('u','seller'),' has two options, ',act('a','Dawn'),' and ',act('b','Dusk'),'. ',out('e','benefit'),' of ',act('x',a),' equals ',qty('q','32'),'; ',out('c','benefit'),' of ',act('y',b),' equals ',qty('r','76'),'.']
            make('benefit_clauses',mode,seg,own+refs()+bindings)
            # Ownership rather than realized choice. Historical ontology may not
            # include implicit role resolution; these two structures are exploratory.
            u,v=('latter','former') if flip else ('former','latter')
            seg=[actor('u','porter'),' and ',actor('v','driver'),' each have one option. ',act('a','Pine'),' belongs to the '+u+'; ',act('b','Fern'),' belongs to the '+v+'.'] if not para else ['There are two actors, ',actor('u','porter'),' and ',actor('v','driver'),'. The option of the '+u+' is ',act('a','Pine'),'; the option of the '+v+' is ',act('b','Fern'),'.']
            own2=[('v' if flip else 'u','a','owns'),('u' if flip else 'v','b','owns')]
            make('role_possessive',mode,seg,own2,True)
            seg=[act('a','Flax'),' and ',act('b','Sage'),' are the options. The ',actor('u','editor'),' has the '+('latter' if flip else 'former')+' option; the ',actor('v','author'),' has the '+('former' if flip else 'latter')+' option.'] if not para else ['Of ',act('a','Flax'),' and ',act('b','Sage'),', the '+('latter' if flip else 'former')+' is available to the ',actor('u','editor'),', the '+('former' if flip else 'latter')+' to the ',actor('v','author'),'.']
            make('role_availability',mode,seg,own2,True)
            # Genuine applies_to reversal: numbers and declared actions stay in
            # the same positions; the linking instruction alone changes. This
            # is a new implicit binding convention and is scored separately.
            order='reverse' if flip else 'listed'
            seg=[actor('u','analyst'),' has options ',act('a','Rose'),' and ',act('b','Iris'),'. The ',out('e','cost'),' values are ',qty('q','15'),' and ',qty('r','42'),' in '+order+' order of the options.'] if not para else [actor('u','analyst'),' may choose ',act('a','Rose'),', ',act('b','Iris'),'. Associate the ',out('e','cost'),' values ',qty('q','15'),' and ',qty('r','42'),' with the options in '+order+' order.']
            make('ordered_quantity_binding',mode,seg,own+[('q','e','measures'),('r','e','measures'),('q','b' if flip else 'a','applies_to'),('r','a' if flip else 'b','applies_to')],'quantity')
    return rows
