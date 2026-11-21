"""Read-only smoke for policy-free deterministic semantic argument matching."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

from main_computer.mcel_application_ir import semantic_fingerprint
from main_computer.mcel_semantic_other_apps_host import compile_semantic_app
from main_computer.mcel_knowledge.code_argument_resolver import (
    match_code_workbench, resolve_code_arguments)
from main_computer.mcel_knowledge.workspace_plan import plan_workspace

ROOT = Path(__file__).resolve().parents[1]


def run(repo: Path = ROOT) -> dict:
    ir = compile_semantic_app("code-editor", repo)
    plan = plan_workspace(ir)
    original = copy.deepcopy((ir, plan))
    args = resolve_code_arguments(ir, plan)
    groups = args["groups"]
    cases = []

    def check(name, fn):
        fn()
        cases.append({"case": name, "outcome": "PASS"})

    def require(value, message):
        if not value:
            raise AssertionError(message)

    def refuse(fn):
        try:
            fn()
        except ValueError:
            return
        raise AssertionError("Expected safe refusal")

    def recompile(other):
        source = copy.deepcopy(other)
        for node in source["surfaces"][0]["nodes"]:
            node.pop("externalJob", None)
        other["fingerprints"]["semantic"] = semantic_fingerprint(source)
        return plan_workspace(other)

    def exact_coverage():
        require({k: len(v) for k, v in groups.items()} ==
                {"execution": 2, "preview": 2, "session": 7}, "Unexpected groups")
        members = next(r["members"] for r in ir["surfaces"][0]["interfaceIntent"]["regions"]
                       if r["id"] == args["region"])
        require(sorted(x for members in groups.values() for x in members) ==
                sorted(x["ref"] for x in members), "Missing/duplicate member")
    check("full argument coverage", exact_coverage)

    def reordered():
        other = copy.deepcopy(ir)
        for key in ("intents", "capabilities", "effects", "states"):
            other[key].reverse()
        other["surfaces"][0]["nodes"].reverse()
        other["surfaces"][0]["interfaceIntent"]["regions"].reverse()
        for region in other["surfaces"][0]["interfaceIntent"]["regions"]:
            region["members"].reverse()
        require(resolve_code_arguments(other, recompile(other))["groups"] == groups,
                "Declaration reordering changed assignments")
    check("declaration order invariance", reordered)

    def renamed():
        other = copy.deepcopy(ir)
        region = next(r for r in other["surfaces"][0]["interfaceIntent"]["regions"]
                      if r["id"] == args["region"])
        rename = {m["ref"]: "surface-node:renamed."+str(i)
                  for i, m in enumerate(region["members"])}
        inverse = {v:k for k,v in rename.items()}
        for member in region["members"]:
            member["ref"] = rename[member["ref"]]
        for node in other["surfaces"][0]["nodes"]:
            node["id"] = rename.get(node["id"], node["id"])
        actual = resolve_code_arguments(other, recompile(other))["groups"]
        require({k: sorted(inverse[x] for x in v) for k,v in actual.items()} == groups,
                "Incidental ID renaming changed role")
    check("incidental node renaming equivariance", renamed)

    def neighbors():
        other = copy.deepcopy(ir)
        nav = next(r for r in other["surfaces"][0]["interfaceIntent"]["regions"]
                   if r["purpose"] == "navigation")
        node = copy.deepcopy(next(n for n in other["surfaces"][0]["nodes"]
                                  if n["id"] == nav["members"][0]["ref"]))
        node["id"] = "surface-node:unrelated.navigation"
        other["surfaces"][0]["nodes"].append(node)
        nav["members"].append({"ref": node["id"]})
        require(resolve_code_arguments(other, recompile(other))["groups"] == groups,
                "Irrelevant neighbor changed roles")
    check("irrelevant neighbor immunity", neighbors)

    def changed_semantics():
        other = copy.deepcopy(ir)
        node = next(n for n in other["surfaces"][0]["nodes"]
                    if n["id"] in groups["execution"] and n["nodeKind"] == "form")
        node["intent"] = {"ref":"intent:read-component-doc"}
        refuse(lambda: resolve_code_arguments(other,recompile(other)))
    check("changed operation refuses", changed_semantics)

    def changed_risk():
        other = copy.deepcopy(ir)
        next(i for i in other["intents"] if i["id"] == "intent:run-aider")["risk"] = "external-read"
        refuse(lambda: resolve_code_arguments(other,recompile(other)))
    check("changed risk refuses", changed_risk)

    def shared_result_ambiguous():
        other = copy.deepcopy(ir)
        next(i for i in other["intents"] if i["id"] == "intent:preview-aider-plan")["resultBindings"][0]["target"] = {"ref": "state:aider-result"}
        refuse(lambda: resolve_code_arguments(other,recompile(other)))
    check("competing primary result owners refuse", shared_result_ambiguous)

    def missing_anchor():
        other = copy.deepcopy(ir)
        n = next(n for n in other["surfaces"][0]["nodes"] if n["nodeKind"] == "document-editor")
        n["nodeKind"] = "state-value"
        refuse(lambda: resolve_code_arguments(other,recompile(other)))
    check("missing editor refuses", missing_anchor)

    def missing_execution():
        other = copy.deepcopy(ir)
        i=next(i for i in other["intents"] if i["id"] == "intent:run-aider")
        i["typedOperation"]["kind"] = "refresh"
        refuse(lambda: resolve_code_arguments(other,recompile(other)))
    check("missing primary execution refuses", missing_execution)

    def stale_plan():
        other=copy.deepcopy(ir)
        other["intents"][0]["description"] = "different intent"
        recompile(other)
        refuse(lambda: resolve_code_arguments(other,plan))
    check("stale plan refuses", stale_plan)

    def foreign_apps():
        for name in ("file-manager", "task-manager"):
            other=compile_semantic_app(name,repo)
            require(match_code_workbench(other,plan_workspace(other)) is None,
                    "Unrelated application matched")
    check("unrelated application not matched", foreign_apps)

    def policy_rejected():
        refuse(lambda: resolve_code_arguments(ir,plan,{"profile":"code"}))
    check("obsolete policy cannot override graph", policy_rejected)

    def no_mutation():
        require((ir,plan)==original,"Mutated source inputs")
    check("source IR and plan unchanged", no_mutation)

    return {"schema":"mcel.semantic-argument-resolver-smoke.v2",
            "status":"PASS_POLICY_FREE_GRAPH_MATCH",
            "scope":"verified semantic graph and registered template signature",
            "app":"code-editor", "cases":cases,
            "argumentCount":sum(map(len,groups.values())),
            "groups":{k:len(v) for k,v in groups.items()},
            "authorizesEffects":False,"writesFiles":False}


def main():
    try:
        result=run()
    except Exception as e:
        print(f"MCEL_SEMANTIC_ARGUMENT_SMOKE FAIL {type(e).__name__}: {e}",file=sys.stderr)
        return 1
    print(f"MCEL_SEMANTIC_ARGUMENT_SMOKE PASS cases={len(result['cases'])} arguments={result['argumentCount']}")
    print(json.dumps(result,indent=2,sort_keys=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
