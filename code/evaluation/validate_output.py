"""Structural validation of output.csv against the submission contract.

    python code/evaluation/validate_output.py [output.csv]
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PHASES = ["FINDING", "REMEDIATION", "VERIFICATION"]
OPS = {
    "FINDING": ["REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK"],
    "REMEDIATION": ["DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"],
    "VERIFICATION": ["VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED",
                     "VERIFY_RECORD_ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED"],
}
STATE = {
    "REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT", "REPORT_ACTIVE_DATA_FLOW": "ACTIVE",
    "REPORT_RESTORE_RISK": "ACTIVE", "DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED",
    "DELETE_RECORD": "ABSENT", "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT", "VERIFY_RECORD_REDACTED": "REDACTED",
    "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED": "DISABLED", "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}
FIELDS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}


def validate(path):
    errors = []
    with open(ROOT / "dataset" / "deletion_requests.csv", encoding="utf-8", newline="") as f:
        expected = [r["case_id"] for r in csv.DictReader(f)]
    caps = {}
    with open(ROOT / "dataset" / "evidence_files.csv", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            caps[r["file_id"]] = r["captured_at"]
    audit = {}
    with open(ROOT / "dataset" / "deletion_requests.csv", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            audit[r["case_id"]] = r["audit_at"]
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != ["case_id", "deletion_status", "steps_json"]:
            errors.append(f"bad header {reader.fieldnames}")
        rows = list(reader)
    ids = [r["case_id"] for r in rows]
    if sorted(ids, key=lambda c: int(c.split("_")[1])) != ids:
        errors.append("rows are not sorted by case_id")
    if set(ids) != set(expected) or len(ids) != len(set(ids)):
        errors.append(f"case_id mismatch: {len(ids)} rows vs {len(expected)} requests")
    for r in rows:
        cid = r["case_id"]
        if r["deletion_status"] not in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"):
            errors.append(f"{cid}: bad status")
        try:
            steps = json.loads(r["steps_json"])
        except ValueError:
            errors.append(f"{cid}: steps_json is not JSON")
            continue
        if r["deletion_status"] == "NOT_APPLICABLE" and steps != []:
            errors.append(f"{cid}: NOT_APPLICABLE must have []")
        last_phase, last_key = -1, None
        for i, s in enumerate(steps, 1):
            if set(s) != FIELDS:
                errors.append(f"{cid}: step {i} has fields {sorted(s)}")
                continue
            if s["step_no"] != i:
                errors.append(f"{cid}: step_no {s['step_no']} at position {i}")
            ph = PHASES.index(s["phase"])
            if ph < last_phase:
                errors.append(f"{cid}: phase order broken at step {i}")
            key = (OPS[s["phase"]].index(s["operation"]), s["system_id"], s["resource_id"])
            if ph == last_phase and key < last_key:
                errors.append(f"{cid}: within-phase order broken at step {i}")
            last_phase, last_key = ph, key
            if STATE[s["operation"]] != s["state"]:
                errors.append(f"{cid}: state/operation mismatch at step {i}")
            cited = s["evidence_id"]
            if s["phase"] == "REMEDIATION" and cited:
                errors.append(f"{cid}: remediation must not cite evidence")
            if r["deletion_status"] == "INCOMPLETE" and s["phase"] == "VERIFICATION" and cited:
                errors.append(f"{cid}: future verification must not cite evidence")
            if s["phase"] == "FINDING" and not cited:
                errors.append(f"{cid}: finding at step {i} cites no evidence")
            if r["deletion_status"] == "COMPLETE" and not cited:
                errors.append(f"{cid}: COMPLETE verification at step {i} cites no evidence")
            if cited and cited not in caps:
                errors.append(f"{cid}: cites {cited}, which is not in the manifest")
            if cited and caps.get(cited, "9") > audit[cid]:
                errors.append(f"{cid}: cites {cited} captured after audit_at")
        if r["deletion_status"] == "COMPLETE" and any(s["phase"] != "VERIFICATION" for s in steps):
            errors.append(f"{cid}: COMPLETE must contain verification steps only")
    return rows, errors


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else ROOT / "output.csv"
    rows, errors = validate(path)
    print(f"{len(rows)} rows checked; {len(errors)} problems")
    for e in errors[:50]:
        print("  ", e)
    sys.exit(1 if errors else 0)
