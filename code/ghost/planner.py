"""Turn policy treatments into the ordered, serialised steps_json."""
import json

from .policy import STATE_OF_OP

FINDING_ORDER = ["REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK"]
REMEDIATION_ORDER = ["DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"]
VERIFICATION_ORDER = ["VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED",
                      "VERIFY_RECORD_ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED"]


def _sorted(steps, order):
    return sorted(steps, key=lambda s: (order.index(s["operation"]), s["system_id"], s["resource_id"]))


def build_steps(treatments, complete):
    """treatments: list of policy.Treatment. COMPLETE -> verification only; INCOMPLETE -> all three phases."""
    def step(phase, system, rid, op, evidence):
        return {"phase": phase, "system_id": system, "resource_id": rid, "operation": op,
                "state": STATE_OF_OP[op], "evidence_id": evidence}

    findings, remediation, verification = [], [], []
    for t in treatments:
        if not t.verify_op:
            continue   # the applicable rule needs no state check for this resource
        if complete and (not t.evidence_id or t.unresolved):
            continue   # a COMPLETE verdict only asserts what citable evidence proves
        if t.violated and not complete:
            findings.append(step("FINDING", t.system, t.resource_id, t.finding_op, t.evidence_id))
            remediation.append(step("REMEDIATION", t.system, t.resource_id, t.remediation_op, ""))
        verification.append(step("VERIFICATION", t.system, t.resource_id, t.verify_op, t.evidence_id if complete else ""))
    if complete:
        steps = _sorted(verification, VERIFICATION_ORDER)
    else:
        steps = (_sorted(findings, FINDING_ORDER) + _sorted(remediation, REMEDIATION_ORDER)
                 + _sorted(verification, VERIFICATION_ORDER))
    for i, s in enumerate(steps, 1):
        s["step_no"] = i
    return steps


def serialise(steps):
    return json.dumps(steps, ensure_ascii=False, separators=(",", ":"))
