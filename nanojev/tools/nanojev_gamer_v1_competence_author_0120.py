"""Small fixed, paired diagnostic populations. Imported only during preparation."""
import hashlib

SPECS = {
    'refers_to': ('action', 'action', 'aliases', 'original options', 'denotes'),
    'owns': ('actor', 'action', 'actors', 'available actions', 'has available'),
    'applies_to': ('quantity', 'action', 'cost amounts', 'options', 'is the cost of'),
    'measures': ('quantity', 'outcome', 'numeric values', 'outcomes', 'is the value of'),
}


def rows():
    # Two distinct constructions per cohort, balanced direct/crossed. No fitting.
    for split in ('calibration', 'fresh'):
        for rel, (lk, rk, ll, rl, verb) in SPECS.items():
            for structure in range(2):
                if split == 'calibration':
                    right = ('Pine', 'Elm') if rk == 'action' else ('revenue', 'expense')
                    left = {'refers_to': ('Topaz', 'Agate'), 'owns': ('miller', 'baker'), 'applies_to': ('14', '83'), 'measures': ('26', '71')}[rel]
                else:
                    right = ('Cobalt', 'Ochre') if rk == 'action' else ('income', 'loss')
                    left = {'refers_to': ('Poppy', 'Iris'), 'owns': ('diver', 'weaver'), 'applies_to': ('37', '92'), 'measures': ('18', '64')}[rel]
                for flip in (False, True):
                    prompt = ''; anchors = []
                    def add(text):
                        nonlocal prompt
                        prompt += text
                    def entry(side, i, text, kind):
                        start = len(prompt); add(text)
                        anchors.append({'id': side+str(i), 'kind':kind, 'span':[start,len(prompt)]})
                    add(ll.capitalize()+': '); entry('l',0,left[0],lk); add(', '); entry('l',1,left[1],lk)
                    add('; '+rl+': '); entry('r',0,right[0],rk); add(', '); entry('r',1,right[1],rk); add('. ')
                    x,y=right[int(flip)],right[1-int(flip)]
                    if split == 'calibration':
                        if structure == 0: add(f'{left[0]} {verb} {x}. {left[1]} {verb} {y}.')
                        else: add(f'{left[1]} {verb} {y}, and {left[0]} {verb} {x}.')
                    elif structure == 0:
                        add(f'The two pairings are unambiguous. The partner of {left[0]} is not {y}; the remaining two entries are paired with each other.')
                    else:
                        add(f'The partner of the second entry in the first list is the {"first" if flip else "second"} entry in the second list. Each entry has exactly one partner.')
                    base=f'0120-{split}-{rel}-{structure}'; mode='reverse' if flip else 'direct'; key=base+'-'+mode
                    h=hashlib.sha256(prompt.encode()).hexdigest()
                    yield {'case_id':key,'base_id':base,'structure':structure,'split':split,'mode':mode,'relation':rel,'prompt':prompt,'prompt_sha256':h,'anchors':anchors}, {'case_id':key,'prompt_sha256':h,'positive':[int((i!=j) if flip else (i==j)) for i in range(2) for j in range(2)]}
