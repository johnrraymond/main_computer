"""Independent instruction families; labels only created by prepare."""
import hashlib
RELS=('refers_to','owns','applies_to','measures')
WORDS=(('Aster','Birch','Jade','Onyx','clerk','guard','cost','yield','13','47'),('Cedar','Dune','Ruby','Opal','judge','smith','loss','gain','21','86'),('Frost','Grove','Mint','Plum','baker','miner','risk','reward','35','79'),('Hawk','Lark','Sage','Rose','porter','pilot','price','profit','42','68'),('Moss','Reed','Blue','Gray','nurse','cook','fee','return','19','54'),('Pike','Trout','Pearl','Coral','coach','mayor','expense','benefit','23','76'),('Quartz','Flint','Amber','Ivory','rider','sailor','tax','income','31','82'),('Brook','Heath','Lilac','Peach','editor','farmer','debt','revenue','16','93'),('Wren','Finch','Indigo','Ochre','mason','tailor','charge','surplus','28','67'))

def rows():
    for t,w in enumerate(WORDS):
        a,b,x,y,u,v,e,c,q,z=w;split='fit' if t<4 else 'validation' if t<6 else 'fresh'
        for rel in RELS:
            if rel=='refers_to':left=(('action',x),('action',y));right=(('action',a),('action',b));labels=('aliases','original options');verb='denotes';inv='is denoted by'
            elif rel=='owns':left=(('actor',u),('actor',v));right=(('action',a),('action',b));labels=('actors','available actions');verb='has available';inv='is available to'
            elif rel=='applies_to':left=(('quantity',q),('quantity',z));right=(('action',a),('action',b));labels=('cost amounts','options');verb='is the cost of';inv='has cost'
            else:left=(('quantity',q),('quantity',z));right=(('outcome',e),('outcome',c));labels=('numeric values','outcomes');verb='is the value of';inv='has value'
            for para in (False,True):
                for flip in (False,True):
                    anchors=[];prompt=''
                    def add(s):
                        nonlocal prompt
                        prompt+=s
                    def group(items,side):
                        for i,(kind,value) in enumerate(items):
                            if i:add(' and ' if para else ', ')
                            st=len(prompt);add(value);anchors.append({'id':side+str(i),'kind':kind,'span':[st,len(prompt)]})
                    add(('Inventory: ' if not para else 'The available entries are as follows. ')+labels[0]+': ');group(left,'l');add('; '+labels[1]+': ');group(right,'r');add('. ')
                    l0,l1=[i[1] for i in left];r0,r1=[right[int(flip)][1],right[1-int(flip)][1]]
                    cues=(
                      (f'{l0} {verb} {r0}. {l1} {verb} {r1}.',f'{r1} {inv} {l1}; {r0} {inv} {l0}.'),
                      (f'Use {r0} with {l0}; use {r1} with {l1}.',f'For {l1}, the matching entry is {r1}; for {l0}, it is {r0}.'),
                      (f'In the first group, the first entry matches the {"second" if flip else "first"} entry of the other group; match the two remaining entries.',f'The unmentioned pair follows by elimination after matching the last entry of the first group with the {"first" if flip else "last"} entry of the second.'),
                      (f'Match {l0} to {r0}, not {r1}; match {l1} to {r1}, not {r0}.',f'Do not associate {l1} with {r0} or {l0} with {r1}. The valid pairs are {l1} with {r1} and {l0} with {r0}.'),
                      (f'The two bindings are ({r1}, {l1}) and ({r0}, {l0}), written right entry first.',f'Read each tuple as left then right: ({l0}, {r0}); ({l1}, {r1}).'),
                      (f'Only {r0} is paired with {l0}. The other two entries make the second pair.',f'The pair containing {r1} contains {l1}; pair the unused entries together.'),
                      (f'Initially the lists were aligned in {"the same" if flip else "opposite"} order. Replace that assignment by exchanging the right-hand partners.',f'Cancel the {"parallel" if flip else "crossed"} arrangement and instead use the {"crossed" if flip else "parallel"} arrangement.'),
                      (f'{r0} is the entry for {l0}, whereas {r1} belongs with {l1}. An unrelated record is archived.',f'An unrelated record is archived. Concerning {r1}, its partner is {l1}; concerning {r0}, its partner is {l0}.'),
                      (f'For the left-hand entries in their listed order, the corresponding right-hand entries are {r0}, then {r1}.',f'The right-hand entries, read in the order associated with the first and then the second left-hand entry, are {r0} and {r1}.'),
                    )
                    add(cues[t][int(para)]);base=f'0119-{rel}-structure{t}';mode=('reverse' if flip else 'direct')+('_paraphrase' if para else '')
                    row={'case_id':base+'-'+mode,'base_id':base,'structure':t,'split':split,'mode':mode,'relation':rel,'prompt':prompt,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),'anchors':anchors}
                    yield row,{'case_id':row['case_id'],'prompt_sha256':row['prompt_sha256'],'positive':[int((i!=j) if flip else (i==j)) for i in range(2) for j in range(2)]}
