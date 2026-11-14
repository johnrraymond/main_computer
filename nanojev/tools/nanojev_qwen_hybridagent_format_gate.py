#!/usr/bin/env python3
"""Single-command orchestrator: paper's format-activation smoke then paired diagnostic.

Stages run synchronously, in order. Outputs use independent, create-only paths.
Never alters the numerical backbone, HybridLink activation or joint checkpoints.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

from tools import nanojev_qwen_hybridagent_format_activation as fmt
from tools import nanojev_qwen_hybridagent_paired_diagnostic as paired


def summarize(phase,diagnostic):
    ini=phase['initial_dev'];cur=phase['current_dev']
    metrics={
      'format_dev_loss_decreased':cur['loss'] < ini['loss'],
      'entry_marker_probability_before':ini['transitions']['entry_probability'],
      'entry_marker_probability_after':cur['transitions']['entry_probability'],
      'exit_marker_probability_before':ini['transitions']['exit_probability'],
      'exit_marker_probability_after':cur['transitions']['exit_probability'],
    }
    score_map={(x['configuration'],x['depth'],x['control']):x for x in diagnostic['scores']}
    comparisons=[]
    for depth in diagnostic['depths']:
        a=score_map[('numerical_pre_format',depth,'intact')]
        b=score_map[('format_activated',depth,'intact')]
        comparisons.append({'depth':depth,'pre_format_correct':a['answer_label_correct'],
                            'post_format_correct':b['answer_label_correct'],
                            'net_correct_gain':b['answer_label_correct']-a['answer_label_correct'],
                            'pre_format_parseable':a['parseable_answer_label'],
                            'post_format_parseable':b['parseable_answer_label']})
    return {'status':'FORMAT_AND_PAIRED_COMPLETED_UNCERTIFIED','format':metrics,
      'paired_comparison':comparisons,'joint_included':diagnostic['configurations']==3,
      'interpretation':'Mechanics and observed behavior only; not certification of adaptive mode selection, causal reasoning or efficiency.'}


def run(*,device='cuda',steps=24,eval_every=6,seed=71,format_output=fmt.OUTPUT,
        pair_output=paired.OUTPUT,cases=6,max_new_tokens=48,skip_joint=False,
        resume_format=False,allow_download=False):
    format_output=Path(format_output);pair_output=Path(pair_output)
    if (format_output/'result.json').is_file() and not resume_format:
        prior=json.loads((format_output/'result.json').read_text(encoding='utf8'))
        cfg=json.loads((format_output/'run_config.json').read_text(encoding='utf8'))
        assert prior['status']=='FORMAT_ACTIVATION_SMOKE_COMPLETED_UNCERTIFIED','FORMAT_GATE_INCOMPLETE_PRIOR_RUN'
        assert cfg['steps']==steps and cfg['eval_every']==eval_every and cfg['seed']==seed,'FORMAT_GATE_PRIOR_CONFIG_DRIFT'
        report=prior
    else:
        report=fmt.train(device=device,output=format_output,steps=steps,eval_every=eval_every,
          seed=seed,resume=resume_format,allow_download=allow_download)
    assert report['status']=='FORMAT_ACTIVATION_SMOKE_COMPLETED_UNCERTIFIED','FORMAT_GATE_STAGE_INCOMPLETE'
    diagnostic=paired.run(device=device,format_dir=format_output,output=pair_output,
         cases=cases,max_new_tokens=max_new_tokens,include_joint=not skip_joint,
         seed=seed,allow_download=allow_download)
    result=summarize(report,diagnostic)
    print('HYBRID_FORMAT_GATE_RESULT='+json.dumps(result,sort_keys=True),flush=True)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--steps',type=int,default=24)
    p.add_argument('--eval-every',type=int,default=6)
    p.add_argument('--seed',type=int,default=71)
    p.add_argument('--format-output',type=Path,default=fmt.OUTPUT)
    p.add_argument('--pair-output',type=Path,default=paired.OUTPUT)
    p.add_argument('--cases',type=int,default=6)
    p.add_argument('--max-new-tokens',type=int,default=48)
    p.add_argument('--skip-joint',action='store_true')
    p.add_argument('--resume-format',action='store_true')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    try:
        run(device=args.device,steps=args.steps,eval_every=args.eval_every,
            seed=args.seed,format_output=args.format_output,pair_output=args.pair_output,
            cases=args.cases,max_new_tokens=args.max_new_tokens,
            skip_joint=args.skip_joint,resume_format=args.resume_format,
            allow_download=args.allow_model_download)
        return 0
    except (RuntimeError,AssertionError,FileExistsError,OSError,KeyError,ValueError) as exc:
        print('HYBRID_FORMAT_GATE_REFUSED='+type(exc).__name__+':'+str(exc),file=sys.stderr,flush=True)
        return 2

if __name__=='__main__':sys.exit(main())
