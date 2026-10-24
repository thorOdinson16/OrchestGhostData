"""Builds the small synthetic dataset behind our additional evaluation cases (all people are fictional).

    python code/evaluation/custom/build_custom_dataset.py

Writes code/evaluation/custom/dataset/ and expected_output.csv. The cases deliberately exercise behaviours the public
samples only touch lightly: historical-email resolution, hash-only analytics rows, near-identical decoy emails
(exact matching), a non-English non-deletion request, an all-compliant COMPLETE case, and a "later evidence" trap.
"""
import csv
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DS = HERE / "dataset"


def sha(e):
    return hashlib.sha256(e.lower().encode()).hexdigest()


def write_csv(name, header, rows):
    with open(DS / name, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def main():
    (DS / "evidence" / "databases").mkdir(parents=True, exist_ok=True)
    (DS / "evidence" / "records").mkdir(parents=True, exist_ok=True)

    users = [
        ("user_c1", "Greta Lang", "greta.lang@example.org", "+49 151 1000001"),
        ("user_c2", "Hiro Tanaka", "hiro.tanaka@example.org", "+81 90 1000002"),
        ("user_c3", "Lena Moreau", "lena.moreau@example.org", "+33 6 1000003"),
        ("user_c4", "Omar Fadel", "omar.fadel@example.org", "+20 10 1000004"),
        ("user_c5", "Greta Langer", "greta.langer@example.org", "+49 151 1000005"),
        ("user_c6", "Omar Fadell", "omar.fadell@example.org", "+20 10 1000006"),
    ]
    write_csv("user_identities.csv", ["user_id", "name", "email", "phone", "email_hash", "updated_at"],
              [(u, n, e, p, sha(e), "2026-10-01T08:00:00Z") for u, n, e, p in users])
    old = "g.lang@example.org"
    write_csv("user_audit_log.csv", ["audit_id", "user_id", "event_time", "field_name", "old_value", "new_value"],
              [("audit_c1", "user_c1", "2026-10-01T08:00:00Z", "email", old, "greta.lang@example.org"),
               ("audit_c2", "user_c1", "2026-10-01T08:00:00Z", "email_hash", sha(old), sha("greta.lang@example.org"))])
    # the deletion worker's account delete for Greta (reported NOT_STARTED -> FAILED, ~60 min before reported_at)
    write_csv("system_activity.csv",
              ["activity_id", "event_time", "event_type", "source_system", "target_system", "resource_id", "reported_result", "details"],
              [("activity_c1", "2026-10-12T11:03:10Z", "DELETE_RECORD", "deletion_worker", "account_service", "account_7100", "FAILED",
                "Deletion worker submitted primary profile account_7100 to account_service.")])
    rules = Path(__file__).resolve().parents[3] / "dataset" / "deletion_rules.csv"
    (DS / "deletion_rules.csv").write_text(rules.read_text(encoding="utf-8"), encoding="utf-8")

    hdr = ["case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at", "request_text"]
    cases = [
        ("case_c1", old, "2026-10-10T09:00:00Z", "NOT_STARTED", "2026-10-12T12:00:00Z", "2026-10-20T12:00:00Z",
         "Bitte löschen Sie mein Konto dauerhaft und prüfen Sie dabei auch meine frühere E-Mail-Adresse."),
        ("case_c2", "hiro.tanaka@example.org", "2026-10-11T09:00:00Z", "COMPLETE", "2026-10-12T09:00:00Z", "2026-10-21T09:00:00Z",
         "Hola, quiero actualizar mi número de teléfono. Por favor no borren mi cuenta."),
        ("case_c3", "lena.moreau@example.org", "2026-10-15T09:00:00Z", "COMPLETE", "2026-10-17T09:00:00Z", "2026-10-22T09:00:00Z",
         "アカウントと関連する個人データの完全な削除をお願いします。"),
        ("case_c4", "omar.fadel@example.org", "2026-10-18T09:00:00Z", "COMPLETE", "2026-10-20T09:00:00Z", "2026-10-25T12:00:00Z",
         "Je demande la suppression de mon compte et de mes données personnelles."),
        ("case_c5", "greta.langer@example.org", "2026-10-10T10:00:00Z", "COMPLETE", "2026-10-12T14:00:00Z", "2026-10-20T12:00:00Z",
         "Please delete my account and any personal data linked to it."),
    ]
    write_csv("deletion_requests.csv", hdr, cases)
    write_csv("sample_deletion_requests.csv", hdr, [])
    write_csv("evidence_files.csv", ["file_id", "file_path", "origin", "captured_at"], [
        ("evidence_c1", "evidence/databases/customer_data_inventory_20261020.sql", "customer_data_inventory_capture", "2026-10-20T11:00:00Z"),
        ("evidence_c2", "evidence/records/customer_account_handover_20261022.txt", "customer_account_service", "2026-10-22T09:00:00Z"),
        ("evidence_c3", "evidence/records/security_audit_ledger_20261024.json", "security_audit_service", "2026-10-24T10:00:00Z"),
        ("evidence_c4", "evidence/records/security_audit_ledger_20261027.json", "security_audit_service", "2026-10-27T10:00:00Z"),
    ])

    # --- evidence_c1: inventory-style SQL snapshot (explicit per-system tables), scope time 2026-10-20T10:55:00Z
    snap = "2026-10-20T10:55:00Z"
    sql = f"""PRAGMA foreign_keys=ON;
BEGIN TRANSACTION;
CREATE TABLE snapshot_scope (origin_system TEXT PRIMARY KEY, captured_at TEXT NOT NULL);
CREATE TABLE customer_profiles (resource_id TEXT PRIMARY KEY, customer_reference TEXT, full_name TEXT, email TEXT, phone TEXT, email_hash TEXT, account_status TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE customer_search_documents (resource_id TEXT PRIMARY KEY, customer_reference TEXT, display_name TEXT, email TEXT, phone TEXT, email_hash TEXT, index_name TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE customer_analytics_profiles (resource_id TEXT PRIMARY KEY, customer_reference TEXT, email TEXT, phone TEXT, email_hash TEXT, audience_cohort TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE support_records (resource_id TEXT PRIMARY KEY, customer_reference TEXT, requester_name TEXT, requester_email TEXT, requester_phone TEXT, message_excerpt TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE connector_subscriptions (resource_id TEXT PRIMARY KEY, customer_reference TEXT, customer_email TEXT, connector_name TEXT NOT NULL, enabled INTEGER NOT NULL CHECK (enabled IN (0,1)), last_delivery_status TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE backup_restore_entries (resource_id TEXT PRIMARY KEY, customer_reference TEXT, customer_email TEXT, archive_key TEXT NOT NULL, restore_allowed INTEGER NOT NULL CHECK (restore_allowed IN (0,1)), updated_at TEXT NOT NULL);
CREATE TABLE security_audit_records (resource_id TEXT PRIMARY KEY, subject_reference TEXT, event_code TEXT NOT NULL, retention_until TEXT NOT NULL, updated_at TEXT NOT NULL);
"""
    for s in ["account_service", "search_service", "analytics_platform", "support_platform", "integration_service",
              "backup_service", "security_service"]:
        sql += f"INSERT INTO snapshot_scope VALUES('{s}','{snap}');\n"
    sql += f"""INSERT INTO customer_search_documents VALUES('search_7101',NULL,NULL,'{old}',NULL,NULL,'customer_directory_v3','{snap}');
INSERT INTO customer_search_documents VALUES('search_7102',NULL,NULL,'greta.langer@example.org',NULL,NULL,'customer_directory_v3','{snap}');
INSERT INTO customer_analytics_profiles VALUES('analytics_7103',NULL,NULL,NULL,'{sha(old)}','home_lifestyle','{snap}');
INSERT INTO support_records VALUES('support_7104','user_c1',NULL,NULL,NULL,'[REDACTED]','{snap}');
INSERT INTO connector_subscriptions VALUES('data_flow_7105',NULL,'greta.lang@example.org','customer_events_connector',1,'delivered','{snap}');
INSERT INTO backup_restore_entries VALUES('backup_7106','user_c1',NULL,'archive/customer-data/backup_7106.tar.zst',1,'{snap}');
INSERT INTO security_audit_records VALUES('audit_record_7107','user_c1','PRIVACY_REQUEST_ACCEPTED','2032-06-30T23:59:59Z','{snap}');
COMMIT;
"""
    (DS / "evidence" / "databases" / "customer_data_inventory_20261020.sql").write_text(sql, encoding="utf-8")

    # --- evidence_c2: handover text, all compliant for Lena (user_c3)
    txt = """CUSTOMER PLATFORM - SHIFT HANDOVER
Snapshot: 2026-10-22T09:00:00Z

Account and downstream checks completed during the shift:

- 2026-10-22T09:00:00Z | account_service | account_7200 | USER_PROFILE | NOT_FOUND | lena.moreau@example.org
- 2026-10-22T09:00:00Z | support_platform | support_7201 | SUPPORT_MESSAGE | REDACTED | user_c3
- 2026-10-22T09:00:00Z | integration_service | data_flow_7202 | DATA_FLOW | DISABLED | user_c3
- 2026-10-22T09:00:00Z | backup_service | backup_7203 | BACKUP_ARCHIVE | BLOCKED | user_c3
- 2026-10-22T09:00:00Z | security_service | audit_record_7204 | SECURITY_AUDIT_LOG | FOUND | user_c3
- 2026-10-22T09:00:00Z | search_service | search_7205 | SEARCH_INDEX_ENTRY | FOUND | lena.moreau2@example.org
"""
    (DS / "evidence" / "records" / "customer_account_handover_20261022.txt").write_text(txt, encoding="utf-8")

    # --- evidence_c3 (before audit) shows Omar's records still present; evidence_c4 (captured AFTER audit_at) shows them gone
    def ledger(at, rows):
        return json.dumps({"exported_at": at, "origin": "security_audit_service", "record_count": len(rows),
                           "records": [{"recorded_at": at, "system": s, "resource_id": r, "record_type": t, "result": res,
                                        "subject_reference": who} for s, r, t, res, who in rows]}, indent=2)
    (DS / "evidence" / "records" / "security_audit_ledger_20261024.json").write_text(ledger("2026-10-24T10:00:00Z", [
        ("account_service", "account_7300", "USER_PROFILE", "FOUND", "omar.fadel@example.org"),
        ("search_service", "search_7301", "SEARCH_INDEX_ENTRY", "FOUND", "omar.fadel@example.org"),
        ("search_service", "search_7302", "SEARCH_INDEX_ENTRY", "FOUND", "omar.fadell@example.org"),
    ]), encoding="utf-8")
    (DS / "evidence" / "records" / "security_audit_ledger_20261027.json").write_text(ledger("2026-10-27T10:00:00Z", [
        ("account_service", "account_7300", "USER_PROFILE", "NOT_FOUND", "omar.fadel@example.org"),
        ("search_service", "search_7301", "SEARCH_INDEX_ENTRY", "NOT_FOUND", "omar.fadel@example.org"),
    ]), encoding="utf-8")

    # --- expected answers (derived by hand from the policy, not from the pipeline output)
    def step(n, ph, sysid, rid, op, state, ev=""):
        return {"step_no": n, "phase": ph, "system_id": sysid, "resource_id": rid, "operation": op, "state": state, "evidence_id": ev}
    F, R, V = "FINDING", "REMEDIATION", "VERIFICATION"
    exp = {}
    exp["case_c1"] = ("INCOMPLETE", [
        step(1, F, "search_service", "search_7101", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_c1"),
        step(2, F, "analytics_platform", "analytics_7103", "REPORT_EXPOSED_PII", "PRESENT", "evidence_c1"),
        step(3, F, "integration_service", "data_flow_7105", "REPORT_ACTIVE_DATA_FLOW", "ACTIVE", "evidence_c1"),
        step(4, F, "backup_service", "backup_7106", "REPORT_RESTORE_RISK", "ACTIVE", "evidence_c1"),
        step(5, R, "integration_service", "data_flow_7105", "DISABLE_DATA_FLOW", "DISABLED"),
        step(6, R, "backup_service", "backup_7106", "BLOCK_RESTORE", "BLOCKED"),
        step(7, R, "search_service", "search_7101", "DELETE_RECORD", "ABSENT"),
        step(8, R, "analytics_platform", "analytics_7103", "ANONYMIZE_RECORD", "ANONYMIZED"),
        step(9, V, "account_service", "account_7100", "VERIFY_RECORD_ABSENT", "ABSENT"),
        step(10, V, "search_service", "search_7101", "VERIFY_RECORD_ABSENT", "ABSENT"),
        step(11, V, "security_service", "audit_record_7107", "VERIFY_RECORD_PRESENT", "PRESENT"),
        step(12, V, "support_platform", "support_7104", "VERIFY_RECORD_REDACTED", "REDACTED"),
        step(13, V, "analytics_platform", "analytics_7103", "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED"),
        step(14, V, "integration_service", "data_flow_7105", "VERIFY_DATA_FLOW_DISABLED", "DISABLED"),
        step(15, V, "backup_service", "backup_7106", "VERIFY_RESTORE_BLOCKED", "BLOCKED"),
    ])
    exp["case_c2"] = ("NOT_APPLICABLE", [])
    exp["case_c3"] = ("COMPLETE", [
        step(1, V, "account_service", "account_7200", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_c2"),
        step(2, V, "security_service", "audit_record_7204", "VERIFY_RECORD_PRESENT", "PRESENT", "evidence_c2"),
        step(3, V, "support_platform", "support_7201", "VERIFY_RECORD_REDACTED", "REDACTED", "evidence_c2"),
        step(4, V, "integration_service", "data_flow_7202", "VERIFY_DATA_FLOW_DISABLED", "DISABLED", "evidence_c2"),
        step(5, V, "backup_service", "backup_7203", "VERIFY_RESTORE_BLOCKED", "BLOCKED", "evidence_c2"),
    ])
    exp["case_c4"] = ("INCOMPLETE", [
        step(1, F, "account_service", "account_7300", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_c3"),
        step(2, F, "search_service", "search_7301", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_c3"),
        step(3, R, "account_service", "account_7300", "DELETE_RECORD", "ABSENT"),
        step(4, R, "search_service", "search_7301", "DELETE_RECORD", "ABSENT"),
        step(5, V, "account_service", "account_7300", "VERIFY_RECORD_ABSENT", "ABSENT"),
        step(6, V, "search_service", "search_7301", "VERIFY_RECORD_ABSENT", "ABSENT"),
    ])
    exp["case_c5"] = ("INCOMPLETE", [
        step(1, F, "search_service", "search_7102", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_c1"),
        step(2, R, "search_service", "search_7102", "DELETE_RECORD", "ABSENT"),
        step(3, V, "search_service", "search_7102", "VERIFY_RECORD_ABSENT", "ABSENT"),
    ])
    with open(HERE / "expected_output.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["case_id", "deletion_status", "steps_json"])
        for cid, (status, steps) in exp.items():
            w.writerow([cid, status, json.dumps(steps, ensure_ascii=False, separators=(",", ":"))])
    print("custom dataset written to", DS)


if __name__ == "__main__":
    main()
