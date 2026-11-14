#!/usr/bin/env python3
"""Cut the current Qwen+logP NanoJev head over to three-backbone concatenated input.

Existing tensors and optimizer moments are preserved exactly. New Pythia/TinyStories
input weights are zero, so cutover is function-preserving.
"""
from __future__ import annotations
import argparse, copy, hashlib, json, os, shutil
from pathlib import Path
from typing import Any

DEFAULT_SOURCE_EXPERIMENT=r"C:\Users\subsi\NanoJev\runs\main_computer_consensus_heavy_meta_curriculum_v1"
DEFAULT_OUTPUT_DIR=r"C:\Users\subsi\NanoJev\runs\main_computer_three_backbone_logp_cutover_v1"
AUX_WIDTH=1282
NEW=("aux_scalar.weight","aux_project.weight")

def emit(event,**kw): print(json.dumps({"event":event,**kw},sort_keys=True),flush=True)
def read(p): return json.loads(Path(p).read_text(encoding="utf-8"))
def sha(p):
 h=hashlib.sha256()
 with open(p,"rb") as f:
  for b in iter(lambda:f.read(1<<20),b""): h.update(b)
 return h.hexdigest()
def checkpoint(exp, explicit):
 if explicit:return Path(explicit).expanduser().resolve(strict=True)
 s=read(Path(exp)/"state.json"); p=s.get("latest_checkpoint")
 if not p: raise RuntimeError("source consensus experiment has no latest_checkpoint")
 return Path(p).expanduser().resolve(strict=True)
def main():
 import torch
 from safetensors.torch import load_file,save_file
 ap=argparse.ArgumentParser(); ap.add_argument("--source-experiment-dir",default=DEFAULT_SOURCE_EXPERIMENT); ap.add_argument("--source-checkpoint"); ap.add_argument("--output-dir",default=DEFAULT_OUTPUT_DIR); ap.add_argument("--dry-run",action="store_true"); a=ap.parse_args()
 exp=Path(a.source_experiment_dir).expanduser().resolve(strict=True); cp=checkpoint(exp,a.source_checkpoint); out=Path(a.output_dir).expanduser().resolve()
 for n in ("head.safetensors","optimizer.pt","rng_state.pt"): 
  if not (cp/n).is_file(): raise RuntimeError(f"missing {n}: {cp}")
 state=load_file(str(cp/"head.safetensors"),device="cpu")
 if any(k in state for k in NEW): raise RuntimeError("source head is already three-backbone cut over")
 emit("three_backbone_cutover_resolved",source_checkpoint=str(cp),output_dir=str(out),source_head_sha256=sha(cp/"head.safetensors"),aux_width=AUX_WIDTH,dry_run=a.dry_run)
 if a.dry_run:return
 if out.exists(): raise RuntimeError(f"output exists: {out}")
 tmp=out.parent/("."+out.name+".tmp"); shutil.rmtree(tmp,ignore_errors=True); tmp.mkdir(parents=True)
 new={k:v.clone() for k,v in state.items()}; new[NEW[0]]=torch.zeros((1,AUX_WIDTH),dtype=state["scalar.weight"].dtype); new[NEW[1]]=torch.zeros((128,AUX_WIDTH),dtype=state["set_project.weight"].dtype); save_file(new,str(tmp/"head.safetensors"))
 opt=torch.load(cp/"optimizer.pt",map_location="cpu",weights_only=False); opt=copy.deepcopy(opt); groups=opt["param_groups"]
 if len(groups)!=1: raise RuntimeError("expected one optimizer parameter group")
 ids=list(groups[0]["params"]); numeric=[int(x) for x in ids]+[int(x) for x in opt.get("state",{}).keys()]; nxt=max(numeric)+1 if numeric else 0; groups[0]["params"]=ids+[nxt,nxt+1]; torch.save(opt,tmp/"optimizer.pt"); shutil.copy2(cp/"rng_state.pt",tmp/"rng_state.pt")
 source_manifest=read(exp/"experiment.json"); cp_meta=read(cp/"meta.json")
 source_cycle=int(cp_meta.get("cycle",0)); source_global_step=int(cp_meta.get("global_step",0)); source_database=source_manifest.get("database")
 if not source_database: raise RuntimeError("source consensus manifest does not identify its lexical DB")
 manifest={"schema_version":"main-computer-nanojev-three-backbone-logp-cutover-v1","source_experiment":str(exp),"source_checkpoint":str(cp),"source_cycle":source_cycle,"source_global_step":source_global_step,"source_database":str(Path(str(source_database)).expanduser().resolve(strict=True)),"source_head_sha256":sha(cp/"head.safetensors"),"models":["Qwen/Qwen3-0.6B","EleutherAI/pythia-70m","roneneldan/TinyStories-33M"],"feature_order":["qwen_hidden_1024","qwen_logp_1","pythia_hidden_512","pythia_logp_1","tinystories_hidden_768","tinystories_logp_1"],"candidate_feature_width":2307,"aux_feature_width":AUX_WIDTH,"cutover_function_preserving":True,"source":source_manifest}
 (tmp/"cutover.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n",encoding="utf-8"); os.replace(tmp,out); emit("three_backbone_cutover_done",output_dir=str(out),candidate_feature_width=2307,added_trainable_params=1*AUX_WIDTH+128*AUX_WIDTH)
if __name__=="__main__":main()
