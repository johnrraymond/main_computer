"""Compare opt-in local MCEL diagnostic plugin traces without inferring parity from API reachability.

    python -m tools.mcel_app_trace_compare production.json semantic.json --output report.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

SCHEMA = "mcel.app-parity-trace.v1"
CATEGORIES = {
    "/api/applications/editor/files": "code-editor.list",
    "/api/applications/editor/read": "code-editor.read",
    "/api/applications/editor/project/file/save": "code-editor.save",
    "/api/applications/editor/project/transaction/prepare": "code-editor.review.prepare",
    "/api/applications/editor/project/transaction/apply": "code-editor.review.apply",
    "/api/applications/aider/prepare": "code-editor.aider.prepare",
    "/api/applications/aider/run": "code-editor.aider.run",
    "/api/applications/file-explorer/roots": "file-manager.roots",
    "/api/applications/file-explorer/list": "file-manager.directory",
    "/api/applications/file-explorer/search": "file-manager.search",
    "/api/applications/file-explorer/read": "file-manager.preview",
    "/api/applications/task/overview": "task-manager.refresh",
    "/api/applications/task/action": "task-manager.control",
    "/api/applications/task/schedule/create": "task-manager.schedule.create",
    "/api/applications/task/schedule/delete": "task-manager.schedule.delete",
    "/api/applications/task/ai": "task-manager.ai",
    "/api/applications/task/schedules": "task-manager.schedules",
    "/api/heartbeat/control": "task-manager.heartbeat",
}

def load(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA or not isinstance(data.get("events"), list):
        raise ValueError(f"Invalid MCEL parity trace: {path}")
    if data.get("identity", {}).get("app") not in {"code-editor", "file-manager", "task-manager"}:
        raise ValueError("Trace has no recognized app identity")
    return data

def summarize(trace: dict) -> dict:
    events = trace["events"]
    requests = {e["requestId"]:e for e in events if e.get("kind") == "backend-request"}
    outcomes = defaultdict(lambda: Counter())
    for e in events:
        if e.get("kind") != "backend-response":
            continue
        req = requests.get(e.get("requestId"))
        if not req:
            continue
        kind = CATEGORIES.get(req.get("endpoint"), "other:" + str(req.get("endpoint", "unknown")))
        outcomes[kind][e.get("outcome", "unknown")]+=1
    for request_id, request in requests.items():
        if not any(e.get("kind") == "backend-response" and e.get("requestId") == request_id for e in events):
            outcomes[CATEGORIES.get(request.get("endpoint"), "unknown")]["no-response"]+=1
    phases = Counter(e.get("phase", "") + ":" + e.get("disposition", "")
                     for e in events if e.get("kind") == "intent")
    states = [e["state"] for e in events if e.get("kind") == "ui-state" and isinstance(e.get("state"), dict)]
    return {"app":trace["identity"]["app"],"variant":trace["identity"].get("variant"),
            "recordedEvents":len(events),"droppedEvents":int(trace.get("dropped", 0)),
            "http":{key:dict(value) for key,value in sorted(outcomes.items())},
            "intentOutcomes":dict(sorted(phases.items())),"stateSnapshots":len(states),
            "finalState":states[-1] if states else trace.get("finalState"),
            "errorEvents":sum(e.get("kind") in {"browser-error","browser-rejection","resource-error"}
                              for e in events)}

def compare(production: dict, semantic: dict) -> dict:
    p, s = summarize(production), summarize(semantic)
    if p["app"] != s["app"]:raise ValueError("Cannot compare different application types")
    if {p["variant"],s["variant"]} != {"production","semantic"}:
        raise ValueError("Traces must identify distinct production and semantic variants")
    if p["variant"] != "production":p,s = s,p
    operations=[]
    for key in sorted(set(p["http"])|set(s["http"])):
        a=p["http"].get(key,{});b=s["http"].get(key,{})
        operations.append({"workflow":key,"production":a,"semantic":b,
                           "coverage":"both-observed" if a and b else
                           "production-only" if a else "semantic-only"})
    return {"schema":"mcel.app-parity-comparison.v1","app":p["app"],
            "status":"OBSERVATIONS_ONLY_NOT_PARITY_CERTIFICATION",
            "production":p,"semantic":s,"workflows":operations,
            "warnings":["HTTP success does not establish rendered UI behavior or feature parity.",
                        "Unobserved workflows are not proven missing; run equivalent scripted tasks.",
                        "These traces contain no raw request/response bodies or input text."]}

def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("production",type=Path)
    parser.add_argument("semantic",type=Path)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    report=compare(load(args.production),load(args.semantic))
    payload=json.dumps(report,indent=2,ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(payload+"\n",encoding="utf-8")
        print(f"MCEL_PARITY_OBSERVATION report: {args.output}")
    else:
        print(payload)
    return 0

if __name__=="__main__":
    raise SystemExit(main())
