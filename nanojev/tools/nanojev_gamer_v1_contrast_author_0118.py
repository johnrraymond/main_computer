"""Controlled conditional binding tasks, not full graph annotations.
All four competing pairs are public. No positive-pair identity in public records.
Every template family belongs to exactly one split across all relations.
"""
import hashlib

RELATIONS=('refers_to','owns','applies_to','measures')
# Five independent instruction constructions. Two fit, one validation, two fresh.
# Direct/reverse differ only in the assignment cue, after all four anchors.
# Each paraphrase has its own wording; canonical anchor IDs remain fixed.
CUES=(
    ((' Match the two groups in the same sequence.',' Match the two groups in the opposite sequence.'),(' Pair the entries in their stated order.',' Pair the entries in inverted order.')),
    ((' The pairing is respectively first with first and second with second.',' The pairing is respectively first with second and second with first.'),(' The first entries go together, as do the second entries.',' The first entry of either group goes with the second of the other.')),
    ((' Use the parallel alignment of the lists.',' Use the crossed alignment of the lists.'),(' Align the lists without exchanging their entries.',' Exchange the alignment of the two lists.')),
    ((' Traverse the second group from its first entry toward its last while traversing the first group normally.',' Traverse the second group from its last entry toward its first while traversing the first group normally.'),(' Read the second group from its beginning to its end to obtain the matches.',' Read the second group from its end to its beginning to obtain the matches.')),
    ((' The first item in the left list corresponds to the first on the right. The remaining items correspond.',' The first item in the left list corresponds to the last on the right. The remaining items correspond.'),(' Begin matching at the leading entry of both lists, then match what remains.',' Begin matching at the leading entry of the left list and the trailing entry of the right list, then match what remains.')),
)
# Different lexical anchors for each construction. Not a substitution-only split.
WORDS=(('Ash','Elm','Red','Tan','planner','trader','cost','gain','17','29'),('Oak','Fir','Sky','Sea','broker','driver','risk','yield','34','62'),('Ivy','Yew','Sun','Fog','editor','sailor','loss','return','41','83'),('Pine','Fern','Gold','Teal','buyer','seller','expense','reward','26','73'),('Lime','Pear','Rose','Iris','porter','pilot','price','profit','58','91'))

def rows():
    result=[]
    for t,cues in enumerate(CUES):
        split='fit' if t<2 else 'validation' if t==2 else 'fresh'
        a,b,x,y,u,v,e,c,q,r=WORDS[t]
        for rel in RELATIONS:
            if rel=='refers_to': left=(('action',x),('action',y));right=(('action',a),('action',b));labels=('reference labels for the options','original option names')
            elif rel=='owns':left=(('actor',u),('actor',v));right=(('action',a),('action',b));labels=('owners of the available actions','available actions')
            elif rel=='applies_to':left=(('quantity',q),('quantity',r));right=(('action',a),('action',b));labels=('cost amounts for the options','options')
            else:left=(('quantity',q),('quantity',r));right=(('outcome',e),('outcome',c));labels=('recorded numeric values','outcome measures')
            for para in (False,True):
                for flip in (False,True):
                    mode=('reverse' if flip else 'direct')+('_paraphrase' if para else '')
                    anchors=[];prompt=''
                    def add(s):
                        nonlocal prompt
                        prompt+=s
                    def group(items,ids):
                        for j,((kind,value),key) in enumerate(zip(items,ids)):
                            if j:add(' and ' if para else ', ')
                            st=len(prompt);add(value);anchors.append({'id':key,'kind':kind,'span':[st,len(prompt)]})
                    # Keep anchor positions/text identical across each meaning reversal.
                    if t==0:
                        add('The '+labels[0]+' are ');group(left,('l0','l1'));add('. The '+labels[1]+' are ');group(right,('r0','r1'));add('.')
                    elif t==1:
                        add('Left list ('+labels[0]+'): ');group(left,('l0','l1'));add('. Right list ('+labels[1]+'): ');group(right,('r0','r1'));add('.')
                    elif t==2:
                        add('Record the '+labels[0]+' as [');group(left,('l0','l1'));add(']; record the '+labels[1]+' as [');group(right,('r0','r1'));add('].')
                    elif t==3:
                        add('There are two '+labels[0]+': ');group(left,('l0','l1'));add('. Separately, identify the '+labels[1]+': ');group(right,('r0','r1'));add('.')
                    else:
                        add('For this assignment the left list contains '+labels[0]+' (');group(left,('l0','l1'));add('), whereas the right list contains '+labels[1]+' (');group(right,('r0','r1'));add(').')
                    if t in (1,4):
                        # Two genuinely different named-assignment constructions.
                        # All public endpoint anchors are still in the prefix; repeated
                        # names in the evidence suffix are not extra scored anchors.
                        a0,a1=left[0][1],left[1][1];b0,b1=right[int(flip)][1],right[1-int(flip)][1]
                        if t==1:
                            verbs={'refers_to':(' denotes ',' is denoted by '),'owns':(' has available ',' is available to '),'applies_to':(' is the cost of ',' has cost '),'measures':(' measures ',' is measured by ')}
                            v0,v1=verbs[rel]
                            add((' '+b0+v1+a0+'; '+b1+v1+a1+'.') if para else (' '+a0+v0+b0+'; '+a1+v0+b1+'.'))
                        else:
                            lead={'refers_to':'Reference mapping','owns':'Action ownership','applies_to':'Cost assignment','measures':'Outcome measurement'}[rel]
                            # Reverse declaration order in the paraphrase, as well
                            # as direction of the written equation/arrow.
                            add((' '+lead+': '+b1+' <- '+a1+', '+b0+' <- '+a0+'.') if para else (' '+lead+': '+a0+' -> '+b0+', '+a1+' -> '+b1+'.'))
                    else:add(cues[int(para)][int(flip)])
                    base=f'0118-{rel}-structure{t}'
                    public={'case_id':base+'-'+mode,'base_id':base,'structure':t,'split':split,'mode':mode,'relation':rel,'prompt':prompt,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),'anchors':anchors}
                    # Public candidate order fixed, independent of label. Two positives.
                    answer={'case_id':public['case_id'],'prompt_sha256':public['prompt_sha256'],'positive':[int(i==j if not flip else i!=j) for i in range(2) for j in range(2)]}
                    result.append((public,answer))
    return result
