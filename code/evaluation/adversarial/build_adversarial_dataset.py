"""Synthetic adversarial dataset with hand-derived expected outcomes (all people fictional).

    python code/evaluation/adversarial/build_adversarial_dataset.py

Every case combines the fixes for temporal eligibility, conditional policy, conservative identity matching and
record-level associations; the expected result of each is derived by hand from the project rules, not from the pipeline.
Evidence: evidence_a1 = SQL snapshot captured BEFORE every audit_at; evidence_a2 = JSON ledger captured AFTER every
audit_at whose entries are timestamped before them (permitted for discovery, forbidden for state and citations).

 case  scenario                                                                      expected
 a1    later ledger says the search entry is gone; also discovers an audit record     INCOMPLETE from the citable snapshot; audit step uncited
 a2    all compliant; later ledger discovers audit record + legal hold                COMPLETE, only the two supported verifications
 a3,a4 same pool, same class, two account events, no independent evidence             no account for either (ambiguous)
 a5    one unclaimed account event within 30 min of reported_at (wide window)         account step present
 a6    two unclaimed account events within 30 min                                     no account step
 a7-9  three cases, two account events in the pool                                    no account for any (missing true event)
 a10,11 two cases whose two events sit in one slot band                               no account for either (decoy / misfit)
 a12   temporal + policy + identity: late-only support message, ambiguous account     search finding, support check uncited, no account
 a13   partner of a12 in the ambiguous pool                                           search finding only
 a14,15 all compliant, ambiguous accounts, late-only audit record (a14)               a14: INCOMPLETE (open retention check), a15: COMPLETE
"""
import csv
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
DS = HERE / "dataset"
SNAP = "2026-10-10T10:00:00Z"
LATE_CAPTURE = "2026-10-20T10:00:00Z"
LATE_OBS = "2026-10-11T09:00:00Z"


def sha(e):
    return hashlib.sha256(e.lower().encode()).hexdigest()


def write_csv(name, header, rows):
    with open(DS / name, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def at(t, minutes):
    return (datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ") + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def email(n):
    return f"adv{n}@example.org"


CASES = [  # (n, requested, status, reported, audit)
    (1, "2026-10-05T09:00:00Z", "NOT_STARTED", "2026-10-08T12:00:00Z", "2026-10-12T12:00:00Z"),
    (2, "2026-10-05T09:00:00Z", "COMPLETE", "2026-10-08T13:00:00Z", "2026-10-12T12:00:00Z"),
    (3, "2026-10-05T09:00:00Z", "NOT_STARTED", "2026-10-09T12:00:00Z", "2026-10-12T12:00:00Z"),
    (4, "2026-10-06T09:00:00Z", "NOT_STARTED", "2026-10-09T12:00:00Z", "2026-10-12T12:00:00Z"),
    (5, "2026-10-05T09:00:00Z", "COMPLETE", "2026-10-10T08:00:00Z", "2026-10-12T12:00:00Z"),
    (6, "2026-10-05T09:00:00Z", "COMPLETE", "2026-10-10T20:00:00Z", "2026-10-12T12:00:00Z"),
    (7, "2026-10-05T09:00:00Z", "NOT_STARTED", "2026-10-11T12:00:00Z", "2026-10-12T12:00:00Z"),
    (8, "2026-10-06T09:00:00Z", "NOT_STARTED", "2026-10-11T12:00:00Z", "2026-10-12T12:00:00Z"),
    (9, "2026-10-07T09:00:00Z", "NOT_STARTED", "2026-10-11T12:00:00Z", "2026-10-12T12:00:00Z"),
    (10, "2026-10-05T09:00:00Z", "NOT_STARTED", "2026-10-13T12:00:00Z", "2026-10-14T12:00:00Z"),
    (11, "2026-10-06T09:00:00Z", "NOT_STARTED", "2026-10-13T12:00:00Z", "2026-10-14T12:00:00Z"),
    (12, "2026-10-05T09:00:00Z", "NOT_STARTED", "2026-10-15T12:00:00Z", "2026-10-16T12:00:00Z"),
    (13, "2026-10-06T09:00:00Z", "NOT_STARTED", "2026-10-15T12:00:00Z", "2026-10-16T12:00:00Z"),
    (14, "2026-10-05T09:00:00Z", "COMPLETE", "2026-10-17T12:00:00Z", "2026-10-18T12:00:00Z"),
    (15, "2026-10-06T09:00:00Z", "COMPLETE", "2026-10-17T12:00:00Z", "2026-10-18T12:00:00Z"),
]
# account-delete events: (resource, reported_at of its pool, minutes before/after reported_at, result)
EVENTS = [
    ("account_3000", "2026-10-09T12:00:00Z", -70, "FAILED"), ("account_4000", "2026-10-09T12:00:00Z", -50, "FAILED"),
    ("account_5000", "2026-10-10T08:00:00Z", -5, "SUCCESS"),
    ("account_6000", "2026-10-10T20:00:00Z", -10, "SUCCESS"), ("account_6001", "2026-10-10T20:00:00Z", 10, "SUCCESS"),
    ("account_7000", "2026-10-11T12:00:00Z", -70, "FAILED"), ("account_8000", "2026-10-11T12:00:00Z", -50, "FAILED"),
    ("account_10000", "2026-10-13T12:00:00Z", -52, "FAILED"), ("account_11000", "2026-10-13T12:00:00Z", -50, "FAILED"),
    ("account_12000", "2026-10-15T12:00:00Z", -70, "FAILED"), ("account_13000", "2026-10-15T12:00:00Z", -50, "FAILED"),
    ("account_14000", "2026-10-17T12:00:00Z", -70, "SUCCESS"), ("account_15000", "2026-10-17T12:00:00Z", -50, "SUCCESS"),
]
TEXT = {"en": "Please delete my account and personal data."}


def main():
    (DS / "evidence" / "databases").mkdir(parents=True, exist_ok=True)
    (DS / "evidence" / "records").mkdir(parents=True, exist_ok=True)
    write_csv("user_identities.csv", ["user_id", "name", "email", "phone", "email_hash", "updated_at"],
              [(f"user_adv{n}", f"Adv Person{n}", email(n), f"+1 555 000{n:04d}", sha(email(n)), "2026-09-01T08:00:00Z") for n in range(1, 16)])
    write_csv("user_audit_log.csv", ["audit_id", "user_id", "event_time", "field_name", "old_value", "new_value"], [])
    rows = []
    for i, (rid, rep, mins, res) in enumerate(EVENTS):
        rows.append((f"activity_{i}", at(rep, mins), "DELETE_RECORD", "deletion_worker", "account_service", rid, res,
                     f"Deletion worker submitted primary profile {rid} to account_service."))
    write_csv("system_activity.csv", ["activity_id", "event_time", "event_type", "source_system", "target_system",
                                      "resource_id", "reported_result", "details"], rows)
    rules = Path(__file__).resolve().parents[3] / "dataset" / "deletion_rules.csv"
    (DS / "deletion_rules.csv").write_text(rules.read_text(encoding="utf-8"), encoding="utf-8")
    hdr = ["case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at", "request_text"]
    write_csv("deletion_requests.csv", hdr, [(f"case_a{n}", email(n), rq, st, rp, au, TEXT["en"]) for n, rq, st, rp, au in CASES])
    write_csv("sample_deletion_requests.csv", hdr, [])
    write_csv("evidence_files.csv", ["file_id", "file_path", "origin", "captured_at"], [
        ("evidence_a1", "evidence/databases/customer_data_inventory_20261010.sql", "customer_data_inventory_capture", SNAP),
        ("evidence_a2", "evidence/records/security_audit_ledger_20261020.json", "security_audit_service", LATE_CAPTURE)])

    sql = f"""PRAGMA foreign_keys=ON;
BEGIN TRANSACTION;
CREATE TABLE snapshot_scope (origin_system TEXT PRIMARY KEY, captured_at TEXT NOT NULL);
CREATE TABLE customer_profiles (resource_id TEXT PRIMARY KEY, customer_reference TEXT, full_name TEXT, email TEXT, phone TEXT, email_hash TEXT, account_status TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE customer_search_documents (resource_id TEXT PRIMARY KEY, customer_reference TEXT, display_name TEXT, email TEXT, phone TEXT, email_hash TEXT, index_name TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE support_records (resource_id TEXT PRIMARY KEY, customer_reference TEXT, requester_name TEXT, requester_email TEXT, requester_phone TEXT, message_excerpt TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE connector_subscriptions (resource_id TEXT PRIMARY KEY, customer_reference TEXT, customer_email TEXT, connector_name TEXT NOT NULL, enabled INTEGER NOT NULL CHECK (enabled IN (0,1)), last_delivery_status TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE security_audit_records (resource_id TEXT PRIMARY KEY, subject_reference TEXT, event_code TEXT NOT NULL, retention_until TEXT NOT NULL, updated_at TEXT NOT NULL);
"""
    for s in ["account_service", "search_service", "support_platform", "integration_service", "security_service"]:
        sql += f"INSERT INTO snapshot_scope VALUES('{s}','{SNAP}');\n"
    search = {1: 1001, 3: 3001, 4: 4001, 5: 5001, 6: 6001, 7: 7001, 8: 8001, 9: 9001, 10: 10001, 11: 11001, 12: 12001, 13: 13001}
    for n, rid in search.items():
        sql += f"INSERT INTO customer_search_documents VALUES('search_{rid}',NULL,NULL,'{email(n)}',NULL,NULL,'customer_directory_v3','{SNAP}');\n"
    for n, sup, flow in ((2, 2001, 2002), (14, 14001, 14002), (15, 15001, 15002)):
        sql += f"INSERT INTO support_records VALUES('support_{sup}','user_adv{n}',NULL,NULL,NULL,'[REDACTED]','{SNAP}');\n"
        sql += f"INSERT INTO connector_subscriptions VALUES('data_flow_{flow}','user_adv{n}',NULL,'customer_events_connector',0,'stopped','{SNAP}');\n"
    sql += "COMMIT;\n"
    (DS / "evidence" / "databases" / "customer_data_inventory_20261010.sql").write_text(sql, encoding="utf-8")

    def rec(system, rid, rtype, result, who):
        return {"recorded_at": LATE_OBS, "system": system, "resource_id": rid, "record_type": rtype, "result": result,
                "subject_reference": who}
    ledger = [rec("search_service", "search_1001", "SEARCH_INDEX_ENTRY", "NOT_FOUND", email(1)),
              rec("security_service", "audit_record_1002", "SECURITY_AUDIT_LOG", "FOUND", "user_adv1"),
              rec("security_service", "audit_record_2003", "SECURITY_AUDIT_LOG", "FOUND", "user_adv2"),
              rec("legal_service", "legal_hold_2004", "LEGAL_HOLD_RECORD", "FOUND", "user_adv2"),
              rec("support_platform", "support_12002", "SUPPORT_MESSAGE", "IDENTIFIABLE", email(12)),
              rec("security_service", "audit_record_14003", "SECURITY_AUDIT_LOG", "FOUND", "user_adv14")]
    (DS / "evidence" / "records" / "security_audit_ledger_20261020.json").write_text(
        json.dumps({"exported_at": LATE_CAPTURE, "origin": "security_audit_service", "record_count": len(ledger),
                    "records": ledger}, indent=2), encoding="utf-8")

    def step(n, ph, sysid, rid, op, state, ev=""):
        return {"step_no": n, "phase": ph, "system_id": sysid, "resource_id": rid, "operation": op, "state": state, "evidence_id": ev}
    F, R, V, E = "FINDING", "REMEDIATION", "VERIFICATION", "evidence_a1"

    def search_case(n, extra_ver=(), account=None):
        rid = f"search_{search[n]}"
        steps = [step(1, F, "search_service", rid, "REPORT_REMAINING_RECORD", "PRESENT", E),
                 step(2, R, "search_service", rid, "DELETE_RECORD", "ABSENT")]
        ver = []
        if account:
            ver.append(("account_service", account, "VERIFY_RECORD_ABSENT", "ABSENT"))
        ver.append(("search_service", rid, "VERIFY_RECORD_ABSENT", "ABSENT"))
        ver += list(extra_ver)
        for k, (s_, r_, op, st) in enumerate(ver):
            steps.append(step(3 + k, V, s_, r_, op, st))
        return "INCOMPLETE", steps

    exp = {}
    exp["case_a1"] = search_case(1, extra_ver=[("security_service", "audit_record_1002", "VERIFY_RECORD_PRESENT", "PRESENT")])
    # audit_record_2003 / legal_hold_2004 are known only from the later ledger, so their retention conditions are UNKNOWN. An open
    # mandatory check blocks COMPLETE: INCOMPLETE, no finding (nothing proves a violation), every check as future work.
    exp["case_a2"] = ("INCOMPLETE", [step(1, V, "legal_service", "legal_hold_2004", "VERIFY_RECORD_PRESENT", "PRESENT", ""),
                                     step(2, V, "security_service", "audit_record_2003", "VERIFY_RECORD_PRESENT", "PRESENT", ""),
                                     step(3, V, "support_platform", "support_2001", "VERIFY_RECORD_REDACTED", "REDACTED", ""),
                                     step(4, V, "integration_service", "data_flow_2002", "VERIFY_DATA_FLOW_DISABLED", "DISABLED", "")])
    for n in (3, 4, 7, 8, 9, 10, 11, 13):
        exp[f"case_a{n}"] = search_case(n)
    exp["case_a5"] = search_case(5, account="account_5000")
    exp["case_a6"] = search_case(6)
    exp["case_a12"] = search_case(12, extra_ver=[("support_platform", "support_12002", "VERIFY_RECORD_REDACTED", "REDACTED")])
    exp["case_a14"] = ("INCOMPLETE", [step(1, V, "security_service", "audit_record_14003", "VERIFY_RECORD_PRESENT", "PRESENT", ""),
                                      step(2, V, "support_platform", "support_14001", "VERIFY_RECORD_REDACTED", "REDACTED", ""),
                                      step(3, V, "integration_service", "data_flow_14002", "VERIFY_DATA_FLOW_DISABLED", "DISABLED", "")])
    exp["case_a15"] = ("COMPLETE", [step(1, V, "support_platform", "support_15001", "VERIFY_RECORD_REDACTED", "REDACTED", E),
                                    step(2, V, "integration_service", "data_flow_15002", "VERIFY_DATA_FLOW_DISABLED", "DISABLED", E)])
    with open(HERE / "expected_output.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["case_id", "deletion_status", "steps_json"])
        for n in range(1, 16):
            status, steps = exp[f"case_a{n}"]
            w.writerow([f"case_a{n}", status, json.dumps(steps, ensure_ascii=False, separators=(",", ":"))])
    print("adversarial dataset written to", DS)


if __name__ == "__main__":
    main()
