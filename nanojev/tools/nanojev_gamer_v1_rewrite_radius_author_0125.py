"""New confirmation population; imported only during preparation/selftest."""
import hashlib, random
from fractions import Fraction
SEED=1252026

def rows():
    rng=random.Random(SEED); result=[]
    def add(prompt,answer,wrong,family,kind):
        answer,wrong=str(answer),str(wrong)
        assert answer!=wrong
        h=hashlib.sha256(prompt.encode()).hexdigest(); j=int(h[:8],16)%2
        result.append(({'id':h[:24],'prompt':prompt,'choices':[answer,wrong] if not j else [wrong,answer],'family':family,'kind':kind,'stage':2 if kind=='math' else 0}, {'id':h[:24],'correct':j,'answer':answer}))
    # Disjoint number range and new wording; 10 signed and 10 cancellation cases.
    for i in range(10):
        n=rng.randrange(431,940); b=rng.randrange(23,83); a=n if i%2 else -n; b=b if i%3 else -b
        if i%2: add(f'Find the integer difference: ({a}) minus ({b}).',a-b,a+b,'signed_arithmetic','math')
        else: add(f'Find the integer sum: ({a}) plus ({b}).',a+b,a-b,'signed_arithmetic','math')
        x=rng.randrange(3001,8800); d=(-1 if i%2 else 1)*(i//2)
        add(f'Evaluate exactly: ({x}) + ({-x+d}).',d,d+1,'zero_line','math')
    for i in range(4):
        a=rng.randrange(971,1500); b=2*i+3
        add(f'Find the reduced fraction equal to {a} divided by {b}.',Fraction(a,b),Fraction(a+1,b),'preserve_fraction','preserve_math')
        add(f'Which is greater, {a} or {b}?',a,b,'preserve_comparison','preserve_math')
    names=('Marta','Derek','Sofia','Owen'); things=('lantern','suitcase','notebook','basket'); places=('attic','garage','kitchen','hall')
    for i,(n,t,p) in enumerate(zip(names,things,places)):
        add(f'{n} carried the {t} into the {p}. Where is the {t} now?',p,places[(i+1)%4],'coreference','preserve_language')
        add(f'Choose the grammatically correct sentence about {n}.',f'{n} has a {t}.',f'{n} have a {t}.','syntax','preserve_language')
    return result
