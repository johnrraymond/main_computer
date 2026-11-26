"""Author one elementary ownership task; no model or training imports."""
import hashlib,random
NAMES='Ada Bert Cora Dale Ezra Faye Gale Hugo Iris Joss Kira Leon Mira Noel Opal Paul Quinn Ruth Seth Tess Uma Vera Wade Xena Yara Zane Alice Bruno Clara Diego Elsie Felix Greta Henry Ines Jasper Lorna Marco Nadia Oscar Petra Rowan Signe Talia Ulric Viola Wyatt Zelda Anya Boris Celia Dario Elin Farid Giselle Harlan'.split()
SEED=1232026

def rows(split):
    # Same lexical sets across held-out wording variants, never across fit and holdout.
    ids=range(1) if split=='one' else range(8) if split=='eight' else range(8,28)
    data=[]
    for i in ids:
        u,v=NAMES[2*i:2*i+2];a,b=f'task {101+2*i}',f'task {102+2*i}'
        prefix=f'Actors: {u}, {v}; available actions: {a}, {b}'
        anchors=[]
        for id,word,kind in [('l0',u,'actor'),('l1',v,'actor'),('r0',a,'action'),('r1',b,'action')]:
            pos=prefix.index(word);anchors.append(dict(id=id,kind=kind,span=[pos,pos+len(word)]))
        for reverse in (False,True):
            x,y=(b,a) if reverse else (a,b)
            if split in ('one','eight','names'):cue=f'{u} owns {x}; {v} owns {y}.'
            elif split=='paraphrase':cue=f'{y} is assigned exclusively to {v}, while {x} is assigned exclusively to {u}.'
            elif i%2==0:cue=f'Each actor owns exactly one of these actions, and each action has one owner. {u} does not own {y}.'
            else:cue=f'The owner column reads {v}, then {u}. The action column reads {y}, then {x}. Entries on the same row belong together.'
            prompt=prefix+'. '+cue;h=hashlib.sha256(prompt.encode()).hexdigest();case='0123-'+h[:24]
            r=dict(case_id=case,prompt=prompt,prompt_sha256=h,relation='owns',anchors=anchors)
            g=dict(case_id=case,prompt_sha256=h,base_id=f'base-{i:02}',mode='reverse' if reverse else 'direct',positive=[0,1,1,0] if reverse else [1,0,0,1],structure='negated' if split=='structure' and i%2==0 else 'table' if split=='structure' else split)
            data.append((r,g))
    random.Random(SEED+sum(map(ord,split))).shuffle(data)
    return data
