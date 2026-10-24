"""Citation checks for the steps a pipeline run produced.

Two different things are checked and reported separately:

* FORMAT   - the cited id is a file in the manifest and was captured at or before the case's audit_at;
* SUPPORT  - the cited file really supports THIS claim about THIS resource:
    - it observes the resource inside the case window, or it is a snapshot of the resource's system that speaks for the
      subject (it holds the subject's records, or declares system-wide scope, or the subject has no other records at all);
    - the observation says what the step claims (still present / exposes identifiers / active / absent / redacted / disabled);
    - an absence claim rests either on an observation of absence or on a snapshot that omits the record - never on a file
      that shows the record present, and never on a snapshot that does not speak for the subject.

Passing SUPPORT means the cited evidence is consistent with the claim as the pipeline reads it; it is not an independent proof
that the claim is true.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghost.state import fact_flags, merge_flags  # noqa: E402

CLAIM = {  # operation -> what the cited observation has to show
    "REPORT_REMAINING_RECORD": "present", "REPORT_EXPOSED_PII": "exposes", "REPORT_ACTIVE_DATA_FLOW": "active",
    "REPORT_RESTORE_RISK": "active", "VERIFY_RECORD_PRESENT": "present", "VERIFY_RECORD_ABSENT": "absent",
    "VERIFY_RECORD_REDACTED": "clean", "VERIFY_RECORD_ANONYMIZED": "clean", "VERIFY_DATA_FLOW_DISABLED": "inactive",
    "VERIFY_RESTORE_BLOCKED": "inactive",
}


def format_problems(view, step, audit_at):
    fid = step["evidence_id"]
    if fid not in view.cap:
        return ["cited file is not in the manifest"]
    return ["cited file captured after audit_at"] if view.cap[fid] > audit_at else []


def support_problems(view, step, requested_at, audit_at, subject_files):
    """Reasons the cited file does not support the step ([] if it does). Only meaningful when format_problems is empty."""
    fid, rid, system, op = step["evidence_id"], step["resource_id"], step["system_id"], step["operation"]
    obs = [f for f in view.by_res.get(rid, []) if f.file_id == fid and f.obs_at and requested_at <= f.obs_at <= audit_at]
    snap = [(s, at) for s, f, at in view.snapshots if f == fid and s in (system, "*") and requested_at <= at <= audit_at]
    speaks = bool(snap) and any(view._speaks_for_subject(s, fid, subject_files) or not subject_files for s, _ in snap)
    if not obs and not snap:
        return ["cited file neither observes the resource nor snapshots its system"]
    claim = CLAIM[op]
    shown = None
    if obs:
        present, identified, active = merge_flags(obs)     # later observations in the file override earlier ones
        shown = {"present": present is True, "exposes": bool(identified) and present is not False, "active": active is True,
                 "inactive": active is False or present is False, "absent": present is False,
                 "clean": present is False or not identified}
    if claim == "absent":
        if shown is not None and shown["present"] and not shown["absent"]:
            return ["cited file shows the record present, so it cannot prove absence"]
        if shown is not None and shown["absent"]:
            return []
        return [] if speaks else ["absence rests on a snapshot that does not speak for the subject"]
    if shown is None:
        # a snapshot omitting the record supports "absent"-style states only; here the claim needs an observation
        return [] if (claim in ("clean", "inactive") and speaks) else ["cited file holds no observation of the resource to back the claim"]
    return [] if shown[claim] else [f"cited observation does not show the claimed state ({claim})"]


def check_run(pipeline, results, requests):
    """-> (counts, violations): violations are (case_id, step_no, resource_id, evidence_id, kind, reason)."""
    req = {r["case_id"]: r for r in requests}
    counts = {"cited": 0, "format_ok": 0, "support_ok": 0}
    bad = []
    for cid, (status, steps, _) in results.items():
        if cid not in req:
            continue
        r = req[cid]
        pc = pipeline.per_case.get(cid)
        files = {f.file_id for rid in (pc["linked"] if pc else []) for f in pipeline.view.by_res.get(rid, [])
                 if f.obs_at and r["requested_at"] <= f.obs_at <= r["audit_at"]}
        for s in steps:
            if not s["evidence_id"]:
                continue
            counts["cited"] += 1
            fp = format_problems(pipeline.view, s, r["audit_at"])
            if fp:
                bad += [(cid, s["step_no"], s["resource_id"], s["evidence_id"], "format", w) for w in fp]
                continue
            counts["format_ok"] += 1
            sp = support_problems(pipeline.view, s, r["requested_at"], r["audit_at"], files)
            if sp:
                bad += [(cid, s["step_no"], s["resource_id"], s["evidence_id"], "support", w) for w in sp]
            else:
                counts["support_ok"] += 1
    return counts, bad
