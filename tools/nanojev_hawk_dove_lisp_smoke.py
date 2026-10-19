"""Build a fresh, source-generated temporary Hawk–Dove teacher and prove it."""
import argparse
from decimal import Decimal as D
import json
from pathlib import Path
import tempfile
from tools import nanojev_hawk_dove_lisp as hd
from tools import nanojev_hawk_dove_lisp_train as train


def run(bindings,intervals=400,input_mode='probabilities'):
    payoffs,meta=hd.load_bindings(bindings)
    with tempfile.TemporaryDirectory() as temp:
        teacher=Path(temp)/'teacher.sqlite3'
        outcome=hd.build_db(teacher,bindings,intervals,input_mode)
        preflight=train.teacher_info(teacher)
        # Check every synthesized row against a freshly rebuilt oracle.
        checked=0
        for split in ('train','dev','test'):
            for row in hd.read_rows(teacher,split):
                _,tier,col,_=row['case_id'].split('/')
                # Numeric coefficients are reconstructed from the row's case ID.
                i=int(row['instance']);a,b=hd.input_pair(i,intervals,input_mode)
                gold=hd.validate_prompt(row['prompt'],tier,a,b,payoffs,int(col))
                if gold!=(D(row['x']),D(row['y'])):raise AssertionError('ROW_TARGET_DRIFT')
                checked+=1
        # Algebraic equivalence across independent, UNLEARNED alternative game
        # bindings checks rewrite correctness beyond one demonstration fixture.
        other={tag:D(k) for k,tag in enumerate(hd.TAGS,1)}
        equivalences=0
        for p in (payoffs,other):
            for i in range(101):
                a,b=hd.input_pair(i,100,input_mode)
                goal=hd.oracle(a,b,p)[:2]
                for tier in hd.TIERS[2:]:
                    for variation in (range(16) if tier=='T03' else (0,)):
                        prompt=hd.lisp(a,b,tier,variation=variation,mode=input_mode)
                        if hd.evaluate_presentation(prompt,p)!=goal:raise AssertionError('REWRITE_DRIFT')
                        equivalences+=1
        return {'status':'PASS','schema':hd.SCHEMA,'rows_recomputed':checked,
                'alternative_binding_equivalence_checks':equivalences,
                'teacher_sha256':outcome['teacher_sha256'],'input_mode':input_mode,
                'splits':preflight['groups'],'binding_version':meta['binding_version'],
                'demo_only':meta['demo_only'],'model_trained':False}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bindings',type=Path,default=Path('examples/hawk_dove_DEMO_ONLY.json'))
    p.add_argument('--intervals',type=int,default=400)
    p.add_argument('--report',type=Path)
    p.add_argument('--input-mode',choices=('probabilities','weights'),default='probabilities')
    args=p.parse_args(argv)
    result=run(args.bindings,args.intervals,args.input_mode)
    if args.report:train.jwrite(args.report,result)
    print(json.dumps(result,sort_keys=True,indent=2))

if __name__=='__main__':main()
