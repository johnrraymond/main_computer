"""Application-neutral validation for semantic presentation argument resolution.

Only validated canonical IR/plan identities and capability links are accepted.
This module cannot execute or authorize an effect. A host-supplied document
job policy is validated as a host extension and excluded from the authored IR
semantic fingerprint, as in the existing semantic host.
"""
from __future__ import annotations

from collections.abc import Mapping
import copy

from main_computer.mcel_application_ir import semantic_fingerprint
from .workspace_plan import verify_workspace_plan


def _ref(value):
    return value.get("ref") if isinstance(value, Mapping) else None


def verify_argument_sources(ir: Mapping, plan: Mapping) -> None:
    """Refuse stale IR/plan or dangling, conflicting capability authorities."""
    if not isinstance(ir, Mapping) or ir.get("schema") != "mcel.application-ir.v1":
        raise ValueError("Semantic argument resolver requires canonical IR")
    source_hash = (ir.get("fingerprints") or {}).get("semantic")
    if not isinstance(source_hash, str) or not source_hash.startswith("sha256:"):
        raise ValueError("Missing canonical IR semantic fingerprint")
    if not isinstance(plan, Mapping) or plan.get("sourceIrHash") != source_hash:
        raise ValueError("Stale workspace plan source fingerprint")
    # Semantic host adds the document external-job policy after compilation.
    # It is *host* authority, not authored application semantics. Check its
    # relationships before excluding it from the authored fingerprint proof.
    authored = copy.deepcopy(ir)
    intents = {i["id"]: i for i in ir.get("intents", ())}
    for node in authored.get("surfaces", [{}])[0].get("nodes", ()):
        job = node.get("externalJob")
        if job is None:
            continue
        if (node.get("nodeKind") != "document-editor" or not isinstance(job, Mapping)
                or any(_ref(job.get(k)) not in intents for k in ("launchIntent", "statusIntent"))
                or intents[_ref(job["launchIntent"])].get("risk") != "consequential"
                or intents[_ref(job["statusIntent"])].get("risk") != "external-read"):
            raise ValueError("Unverified host document-job extension")
        del node["externalJob"]
    if semantic_fingerprint(authored) != source_hash:
        raise ValueError("Compiled IR semantic fingerprint does not match declarations")
    verify_workspace_plan(ir, plan)
    caps = {c["id"]: c for c in ir.get("capabilities", ())}
    effects = {e["id"]: e for e in ir.get("effects", ())}
    if (len(intents) != len(ir.get("intents", ())) or
        len(caps) != len(ir.get("capabilities", ())) or
        len(effects) != len(ir.get("effects", ()))):
        raise ValueError("Duplicate operational identity")
    for intent in intents.values():
        if intent.get("operationKind") != "capability":
            if intent.get("effectRefs"):
                raise ValueError("Local interaction cannot own effects")
            continue
        refs = intent.get("effectRefs") or []
        if len(refs) != 1:
            raise ValueError("Capability intent requires exactly one effect")
        effect = effects.get(_ref(refs[0]))
        if (not effect or effect.get("effectKind") != "capability-request" or
            _ref(effect.get("owner")) != intent["id"] or
            effect.get("risk") != intent.get("risk")):
            raise ValueError("Effect/intent authority mismatch")
        cap_id = _ref(effect.get("target"))
        cap = caps.get(cap_id)
        if (not cap or _ref(effect.get("authority")) != cap_id or
            cap.get("risk") != intent.get("risk")):
            raise ValueError("Effect/capability authority mismatch")
        if not any(op.get("name") == intent.get("operation") and
                   op.get("runtimeMethod") == intent.get("runtimeMethod") and
                   op.get("risk") == intent.get("risk")
                   for op in cap.get("operations", ())):
            raise ValueError("Capability operation/handler mismatch")
