"""Small, source-owned mathematical excitation pilot; no model dependencies."""
from fractions import Fraction
import hashlib,random
from tools import nanojev_game_math_registry as M
SEED=1242026

def card(split,stage,i,body,answer,wrong,family):
    answer,wrong=str(answer),str(wrong)
    assert answer!=wrong
    h=hashlib.sha256(body.encode()).hexdigest(); flip=int(h[:8],16)%2
    choices=[answer,wrong] if not flip else [wrong,answer]
    return ({'id':h[:24],'prompt':body,'choices':choices,'family':family,'stage':stage,'split':split}, {'id':h[:24],'correct':flip,'answer':answer})

def math_rows():
    from tools import nanojev_qwen_zero_line_backbone as Z
    from tools import nanojev_qwen_hdb_frontier as H
    from tools import nanojev_qwen_lm_radius_ladder as L
    zero=Z.make_cases(seed=SEED,train_count=6,dev_count=6,test_count=6)
    rows=[]
    for si,split in enumerate(('fit','dev','blind')):
        rng=random.Random(SEED+si); off=101+si*173
        def add(stage,body,answer,wrong,family):rows.append(card(split,stage,len(rows),body,answer,wrong,family))
        for i,r in enumerate(zero[('train','dev','test')[si]]):
            a=r['answer'];add(2,'Calculate exactly: '+r['expression']+'.',a,a+(-1 if i%2 else 1),'zero_line')
        for i in range(4):
            a=(-1 if i%2 else 1)*(off+i*7); b=(-1 if i%3 else 1)*(13+i*3)
            op='ADD' if i%2 else 'SUBTRACT'; ans=M.execute(op,a,b);add(2,f'Compute {a} '+('plus' if op=='ADD' else 'minus')+f' ({b}).',ans,ans+1,'signed_arithmetic')
        for i in range(3):
            a=off+i*11;b=7+i;c=3+i;ans=M.execute('SUBTRACT',M.execute('MULTIPLY',a,b),c);add(3,f'Compute ({a} × {b}) − {c}.',ans,M.execute('MULTIPLY',a,b-c),'composition')
            x=off+i*2;y=i+3;ans=M.execute('DIVIDE',x,y);add(3,f'Give the exact reduced rational value of {x} divided by {y}.',ans,ans+1,'fraction')
            ans=M.execute('EXPECTATION_DISCRETE',[x,y],[Fraction(1,4),Fraction(3,4)]);add(3,f'An outcome is {x} with probability 1/4 and {y} with probability 3/4. What is its expectation?',ans,M.execute('AVERAGE',x,y),'probability')
        for i in range(3):
            a=off+i*5;b=i+4;c=M.execute('ADD',M.execute('MULTIPLY',a,b),7);add(4,f'Solve {b}x + 7 = {c} for x.',a,a+1,'algebra')
            ans=M.execute('MULTIPLY',a,b);add(4,f'A rectangle has sides {a} and {b}. Find its area.',ans,M.execute('DOUBLE_SUM',a,b),'geometry')
            ans=M.execute('DISCOUNTED_SUM',[a,b,4],Fraction(1,2));add(4,f'Rewards are {a}, {b}, 4 at times 0, 1, 2; discount factor 1/2. Compute discounted sum.',ans,a+b+4,'discounting')
        # Reuse the original HDB prompt renderer and exact oracle, including a zero crossing.
        variant=((11,1,1),(13,1,3),(17,3,1))[si]
        for tier in range(9):
            for k in (-2,0,2):
                r=H._row(tier,k,variant,label='0124-'+split);ans=r['expected'];wrong={'HAWK':'DOVE','DOVE':'HAWK','TIE':'HAWK'}[ans];add(4,r['source'].removesuffix('\nReturn only HAWK, DOVE, or TIE.'),ans,wrong,f'hdb_{tier:02d}')
        for rung in range(9):
            leaves=[(-1 if i%2 else 1)*(off+i*11+rung*3) for i in range(rung+2)];packet=L._case(rung,leaves,'0124-'+split,terminal_mode='numeric');ans=packet['residual'];add(4,'Reduce to the final signed integer:\n'+packet['source'],ans,ans+1,f'T{rung:02d}')
        # Separate mathematical preservation neighborhood, not future acquisition cases.
        for i in range(2):
            a=off+91+i*9;b=5+i;ans=M.execute('ADD',a,-a+b);add(0,f'Compute {a} + ({-a+b}).',ans,ans-1,'preserve_math_cancellation')
            ans=M.execute('DIVIDE',a,b);add(0,f'What exact fraction is {a}/{b}?',ans,ans+2,'preserve_math_fraction')
            ans=M.execute('GREATER_THAN',a,b);add(0,f'Is {a} greater than {b}? Return 1 for yes and 0 for no.',ans,1-ans,'preserve_math_comparison')
    return rows

def language_rows():
    # Explicit facts, inference, sequence, classification, syntax and instructions;
    # preservation tests include original wrong outputs as distribution targets.
    rows=[]
    for si,s in enumerate(('fit','dev','blind')):
        n=('Nora','Felix','Iris')[si];a=('oak','birch','cedar')[si];b=('red','green','blue')[si]
        facts=[(f'{n} put the key inside a box, then closed the box. Where is the key?','inside the box','outside the box','coreference'),(f'The {a} is a tree. Is it a plant or a mineral?','plant','mineral','classification'),(f'Choose the grammatical sentence about {n}.',f'{n} walks home.',f'{n} walk home.','syntax'),(f'If all {b} discs are round and this disc is {b}, what follows?','it is round','it is square','logic'),(f'Follow the instruction: choose the word {a}, not {b}.',a,b,'instruction'),(f'In the sequence {n}, {a}, {b}, what comes immediately after {n}?',a,b,'sequence'),(f'The capital of '+('France','Japan','Italy')[si]+' is which city?',('Paris','Tokyo','Rome')[si],('Lyon','Osaka','Milan')[si],'factual')]
        for i,(p,y,w,f) in enumerate(facts):rows.append(card(s,0,i,p,y,w,f))
    return rows
