"""Reusable evidence index.

Every input format (CSV, SQL, JSON, logs, text, PDF, screenshots) is parsed once into a common
`facts` table inside a SQLite index (code/cache/index.sqlite). All later stages (identity
resolution, data mapping, policy) query this index instead of re-reading raw files.

A fact is one observation of one resource, from one evidence file, at one point in time.
"""
import csv
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path

from .logparse import parse_line

ROOT = Path(__file__).resolve().parents[2]
# GHOST_DATASET lets the evaluation harness point the same code at a small synthetic dataset
DATASET = Path(os.environ.get("GHOST_DATASET") or (ROOT / "dataset"))
CACHE_DIR = ROOT / "code" / "cache"
INDEX_PATH = CACHE_DIR / ("index.sqlite" if DATASET == ROOT / "dataset" else f"index_{DATASET.parent.name}.sqlite")

PREFIX_SYSTEM = {
    "account": ("account_service", "USER_PROFILE"),
    "search": ("search_service", "SEARCH_INDEX_ENTRY"),
    "analytics": ("analytics_platform", "ANALYTICS_PROFILE"),
    "support": ("support_platform", "SUPPORT_MESSAGE"),
    "export": ("export_service", "USER_EXPORT"),
    "data_flow": ("integration_service", "DATA_FLOW"),
    "backup": ("backup_service", "BACKUP_ARCHIVE"),
    "audit_record": ("security_service", "SECURITY_AUDIT_LOG"),
    "marketing": ("marketing_platform", "MARKETING_PROFILE"),
    "tombstone": ("identity_service", "ACCOUNT_TOMBSTONE"),
    "object": ("object_storage", "SUPPORT_ATTACHMENT"),
    "legal_hold": ("legal_service", "LEGAL_HOLD_RECORD"),
    "cache": ("cache_service", "CACHE_ENTRY"),
    "email_delivery": ("email_delivery_service", "EMAIL_DELIVERY_LOG"),
}
RID_RE = re.compile(r"^(account|search|analytics|support|export|data_flow|backup|audit_record|marketing|tombstone|object|legal_hold|cache|email_delivery)_\w+$")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

COLLECTION_RTYPE = {
    "customer_profiles": "USER_PROFILE",
    "customer_search_documents": "SEARCH_INDEX_ENTRY",
    "customer_analytics_reports": "ANALYTICS_PROFILE",
    "customer_analytics_profiles": "ANALYTICS_PROFILE",
    "support_conversations": "SUPPORT_MESSAGE",
    "support_records": "SUPPORT_MESSAGE",
    "customer_exports": "USER_EXPORT",
    "customer_export_jobs": "USER_EXPORT",
    "connector_configurations": "DATA_FLOW",
    "connector_subscriptions": "DATA_FLOW",
    "backup_restore_entries": "BACKUP_ARCHIVE",
    "backup_archives": "BACKUP_ARCHIVE",
    "security_audit_events": "SECURITY_AUDIT_LOG",
    "security_audit_records": "SECURITY_AUDIT_LOG",
    "deleted_customer_references": "ACCOUNT_TOMBSTONE",
    "ticket_attachments": "SUPPORT_ATTACHMENT",
    "backup_catalog": "BACKUP_ARCHIVE",
    "retained_order_records": "LEGAL_HOLD_RECORD",
}

COLLECTION_SYSTEM_BY_TABLE = {
    "customer_profiles": "account_service", "customer_search_documents": "search_service",
    "customer_analytics_profiles": "analytics_platform", "support_records": "support_platform",
    "customer_exports": "export_service", "connector_subscriptions": "integration_service",
    "backup_restore_entries": "backup_service", "security_audit_records": "security_service",
}

NAME_KEYS = {"full_name", "name", "display_name", "requester_name"}
EMAIL_KEYS = {"uploaded_by", "lookup_value", "email", "requester_email", "owner_email", "customer_email", "subject_selector", "query", "matched_value",
              "lookup_key", "correlation_key", "subject_key", "owner_key", "scan_key", "match_key", "subject_ref",
              "subject_reference", "customer_reference", "user_id", "user_ref"}
PHONE_KEYS = {"phone", "requester_phone", "owner_phone"}
USERREF_KEYS = {"user_id", "customer_reference", "subject_reference", "user_ref"}


COVERAGE = []  # (file_id, system, collection, obs_at, n_records) -- what each file covers, even when empty


def cover(file_id, system, collection, obs_at, n, kind="partial"):
    """kind='snapshot': a full table/collection dump, so a record missing from it did not exist at obs_at."""
    COVERAGE.append((file_id, system, collection, obs_at, n, kind))


def sys_of(rid):
    m = re.match(r"^([a-z_]+?)_\d", rid or "")
    return PREFIX_SYSTEM.get(m.group(1), (None, None)) if m else (None, None)


def classify_identity(value):
    """Split an identifier-like string into (email, user_ref, email_hash)."""
    if not value:
        return None, None, None
    v = str(value).strip()
    if not v or v.lower() == "null":
        return None, None, None
    if "@" in v and EMAIL_RE.fullmatch(v):
        return v, None, None
    if re.fullmatch(r"user_\w+", v):
        return None, v, None
    if re.fullmatch(r"[0-9a-f]{64}", v):
        return None, None, v
    return None, None, None


def new_fact(file_id, obs_at, resource_id, loc, **kw):
    system, rtype = sys_of(resource_id)
    f = dict(file_id=file_id, obs_at=obs_at, system=kw.pop("system", None) or system,
             resource_id=resource_id, rtype=kw.pop("rtype", None) or rtype, topology=None, tbl=None,
             exists=None, state=None, email=None, name=None, phone=None, email_hash=None, user_ref=None,
             attrs={}, loc=loc)
    f.update(kw)
    return f


def absorb_fields(fact, rec):
    """Map a raw record's keys onto the identity columns; everything else lands in attrs."""
    for k, v in rec.items():
        if isinstance(v, (dict, list)):
            fact["attrs"][k] = v
            continue
        if v is None or (isinstance(v, str) and v.strip().lower() in ("", "null")):
            continue
        if k in ("message_text", "message_excerpt") and isinstance(v, str):
            found = [e.lower() for e in EMAIL_RE.findall(v)]
            if found:
                fact["attrs"]["text_emails"] = found
            fact["attrs"][k] = v
        elif k in NAME_KEYS:
            fact["name"] = fact["name"] or v
        elif k in PHONE_KEYS:
            fact["phone"] = fact["phone"] or v
        elif k == "email_hash":
            fact["email_hash"] = fact["email_hash"] or v
        elif k in EMAIL_KEYS:
            e, u, h = classify_identity(v)
            if e:
                fact["email"] = fact["email"] or e
            elif u:
                fact["user_ref"] = fact["user_ref"] or u
            elif h:
                fact["email_hash"] = fact["email_hash"] or h
            else:
                fact["attrs"][k] = v
        else:
            fact["attrs"][k] = v
    return fact


# ----------------------------------------------------------------------------- per-format extractors
def parse_handover_txt(file_id, path, captured_at):
    out = []
    snap = captured_at
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("Snapshot:"):
            snap = line.split(":", 1)[1].strip()
        parts = [p.strip() for p in line.lstrip("- ").split(" | ")]
        if len(parts) == 6 and RID_RE.match(parts[2]):
            ts, system, rid, rtype, result, subj = parts
            f = new_fact(file_id, ts, rid, f"line{n}", system=system, rtype=rtype, state=result)
            e, u, h = classify_identity(subj)
            f["email"], f["user_ref"], f["email_hash"] = e, u, h
            f["exists"] = 0 if result in ("NOT_FOUND",) else 1
            out.append(f)
    return out


def parse_record_list_json(file_id, data, loc="rec"):
    out = []
    for i, r in enumerate(data.get("records", [])):
        rid = r.get("resource_id")
        if not rid:
            continue
        f = new_fact(file_id, r.get("recorded_at") or data.get("exported_at"), rid, f"{loc}{i}",
                     system=r.get("system"), rtype=r.get("record_type"), state=r.get("result"))
        e, u, h = classify_identity(r.get("subject_reference"))
        f["email"], f["user_ref"], f["email_hash"] = e, u, h
        f["exists"] = 0 if r.get("result") == "NOT_FOUND" else 1
        out.append(f)
    return out


def parse_bundle_json(file_id, data):
    out = []
    for si, snap in enumerate(data.get("snapshots", [])):
        cover(file_id, snap.get("data_store"), snap.get("collection"), snap.get("captured_at"), len(snap.get("records", [])),
              "snapshot" if snap.get("collection") != "operational_record_history" else "partial")
        for i, r in enumerate(snap.get("records", [])):
            rid = r.get("resource_id")
            if not rid:
                continue
            if snap.get("collection") == "operational_record_history":   # ledger-style rows: result + lookup_value
                f = new_fact(file_id, r.get("recorded_at") or snap.get("captured_at"), rid, f"snap{si}/rec{i}",
                             system=r.get("system"), rtype=r.get("record_type"), state=r.get("result"))
                e, u, h = classify_identity(r.get("lookup_value"))
                f["email"], f["user_ref"], f["email_hash"] = e, u, h
                f["exists"] = 0 if r.get("result") == "NOT_FOUND" else 1
                f["attrs"]["collection"] = "operational_record_history"
                out.append(f)
                continue
            doc = r.get("document") if isinstance(r.get("document"), dict) else {}
            rec = {k: v for k, v in r.items() if k != "document"}
            rec.update(doc)
            f = new_fact(file_id, snap.get("captured_at"), rid, f"snap{si}/rec{i}", system=snap.get("data_store"),
                         rtype=COLLECTION_RTYPE.get(snap.get("collection")), exists=1)
            f["attrs"]["collection"] = snap.get("collection")
            absorb_fields(f, rec)
            out.append(f)
    return out


def parse_collection_json(file_id, data):
    """customer_analytics_report / export_jobs style: top-level records with resource_id."""
    out = []
    cover(file_id, (sys_of((data.get("records") or [{}])[0].get("resource_id", ""))[0]), data.get("collection"),
          data.get("captured_at"), len(data.get("records", [])))
    for i, r in enumerate(data.get("records", [])):
        rid = r.get("resource_id")
        if not rid:
            continue
        f = new_fact(file_id, data.get("captured_at"), rid, f"rec{i}", exists=1)
        f["attrs"]["collection"] = data.get("collection")
        absorb_fields(f, r)
        out.append(f)
    return out


def parse_service_export(file_id, data):
    out = []
    for sysn, groups in data.get("service_exports", {}).items():
        for gname, rows in groups.items():
            cover(file_id, sysn, gname, data.get("exported_at"), len(rows))
            for i, r in enumerate(rows):
                rid = r.get("resource_id")
                if not rid:
                    continue
                f = new_fact(file_id, r.get("observed_at"), rid, f"{sysn}.{gname}[{i}]", system=sysn)
                f["attrs"]["group"] = gname
                rec = dict(r)
                if gname == "profile_lookups":
                    prof = rec.pop("profile", None)
                    f["exists"] = 1 if rec.get("status_code") == 200 else 0
                    f["state"] = str(rec.get("status_code"))
                    if prof:
                        absorb_fields(f, prof)
                elif gname == "index_queries":
                    hits = rec.pop("hits", None)
                    docs = hits.get("documents", []) if isinstance(hits, dict) else []
                    total = hits.get("total", len(docs)) if isinstance(hits, dict) else (hits or 0)
                    f["exists"] = 1 if total > 0 else 0
                    f["state"] = f"hits={total}"
                    for d in docs:
                        if d.get("resource_id") == rid:
                            absorb_fields(f, {k: v for k, v in d.items() if k != "resource_id"})
                else:
                    f["exists"] = 1
                absorb_fields(f, {k: v for k, v in rec.items() if k not in ("status_code", "hits", "observed_at")})
                out.append(f)
    return out


def sql_tables(path):
    con = sqlite3.connect(":memory:")
    con.executescript(Path(path).read_text(encoding="utf-8"))
    con.row_factory = sqlite3.Row
    names = [r[0] for r in con.execute("select name from sqlite_master where type='table' order by name")]
    return con, names


def parse_sql(file_id, path, captured_at):
    out = []
    con, names = sql_tables(path)
    scope = {}
    header = re.search(r"-- Captured at: (\S+)", Path(path).read_text(encoding="utf-8"))
    snap_at = header.group(1) if header else captured_at
    if "snapshot_scope" in names:
        scope = {r["origin_system"]: r["captured_at"] for r in con.execute("select * from snapshot_scope")}
    for t in names:
        if t == "snapshot_scope":
            continue
        tsys = COLLECTION_SYSTEM_BY_TABLE.get(t) or ("search_service" if t == "search_documents_primary" else
                                                     "account_service" if t.startswith("customer_profiles_") else "*")
        cover(file_id, tsys, t, scope.get(tsys) or snap_at, con.execute(f'select count(*) from "{t}"').fetchone()[0], "snapshot")
        topo = "primary" if t.endswith("primary") else (re.sub(r"^customer_(profiles|data)_", "", t) if t.startswith("customer_") and "_" in t else None)
        for i, row in enumerate(con.execute(f'select * from "{t}"')):
            rec = dict(row)
            rid = rec.get("resource_id")
            if not rid:
                continue
            rtype = None
            system = None
            if "record_json" in rec:
                j = json.loads(rec.pop("record_json"))
                system = rec.pop("origin_system", None)
                coll = rec.pop("collection_name", None)
                rtype = COLLECTION_RTYPE.get(coll)
                doc = j.pop("document", None) or {}
                j.update(doc)
                rec = {**rec, **j}
                rec["collection"] = coll
            else:
                rtype = COLLECTION_RTYPE.get(t)
                topo = "primary" if t in ("customer_profiles", "customer_search_documents", "customer_analytics_profiles",
                                          "support_records", "customer_exports", "connector_subscriptions",
                                          "backup_restore_entries", "security_audit_records", "search_documents_primary") or t.endswith("_primary") else topo
                if t.endswith("failover_replica"):
                    topo = "failover_replica"
            # presence is observed when the snapshot was taken; updated_at only says when the row last changed
            f = new_fact(file_id, snap_at, rid, f"{t}#{i}", system=system, rtype=rtype, exists=1)
            sys_scope = scope.get(f["system"] or "") or scope.get(COLLECTION_SYSTEM_BY_TABLE.get(t, ""), "")
            if sys_scope and not header:
                f["obs_at"] = sys_scope
            f["topology"], f["tbl"] = topo, t
            f["attrs"]["scope_at"] = sys_scope or None
            f["attrs"]["row_updated_at"] = rec.get("updated_at") or rec.get("indexed_at")
            rec.pop("updated_at", None)
            absorb_fields(f, rec)
            out.append(f)
    return out


def parse_text_blob(file_id, text, captured_at, kind):
    """Fallback for PDF text: record the raw text; entity extraction is done by the mapper from the blob."""
    return [new_fact(file_id, captured_at, f"__doc__{file_id}", "doc", system="document", rtype=kind,
                     attrs={"text": text})]


def parse_log(file_id, path):
    """Group events by trace so that started/completed pairs share their lookup key."""
    rows = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        r = parse_line(line)
        if r:
            rows.append((n, r[0], r[1]))
    by_trace = {}
    for n, ts, d in rows:
        if d.get("trace"):
            by_trace.setdefault(d["trace"], []).append((n, ts, d))
    out = []
    lookup_keys = ("lookup_key", "match_key", "subject_key", "subject_ref", "owner_key", "scan_key")
    for n, ts, d in rows:
        rid = d.get("resource_id")
        if not rid or not RID_RE.match(rid):
            continue
        f = new_fact(file_id, ts, rid, f"line{n}", system=d.get("system") or d.get("service") or None)
        sysn, rtype = sys_of(rid)
        f["system"], f["rtype"] = d.get("system") or sysn, d.get("record_type") or rtype
        ev = d.get("event", "")
        f["attrs"]["event"] = ev
        # keys carried on sibling lines of the same trace (e.g. *_started lines)
        extra = {}
        for _, _, sib in by_trace.get(d.get("trace"), []):
            for k in lookup_keys:
                if sib.get(k) and sib.get(k) != "null":
                    extra[k] = sib[k]
        for k, v in extra.items():
            e, u, h = classify_identity(v)
            if e:
                f["attrs"].setdefault("lookup_email", e)
            elif u:
                f["attrs"].setdefault("lookup_user", u)
            elif h:
                f["attrs"].setdefault("lookup_hash", h)
        skip = {"service", "trace", "level", "event", "logger", "severity", "instance", "pod", "region", "trace_id",
                "duration_ms", "resource_id", "took_ms", "shard", "latency_ms", "attempt"}
        absorb_fields(f, {k: v for k, v in d.items() if k not in skip})
        # existence semantics by event
        if "rows" in d and d.get("rows") not in (None, ""):
            f["exists"] = 1 if str(d["rows"]) not in ("0", "") else 0
        if d.get("http_status") == "404":
            f["exists"] = 0
        if "result" in d:
            f["state"] = d["result"]
            if ev == "index_query_completed":
                f["exists"] = 1 if d["result"] == "hit" else 0
            if ev == "record_lookup":
                f["exists"] = 0 if d["result"] == "NOT_FOUND" else 1
        if f["exists"] is None and ev.endswith(("_completed", "_loaded", "_scan", "_read", "_evaluated")):
            f["exists"] = 1
        out.append(f)
    return out


# ----------------------------------------------------------------------------- build
SCHEMA = """
create table if not exists facts (
  fact_id integer primary key, file_id text, obs_at text, system text, resource_id text, rtype text,
  topology text, tbl text, exists_flag integer, state text, email text, name text, phone text,
  email_hash text, user_ref text, attrs text, loc text);
create index if not exists ix_res on facts(resource_id);
create index if not exists ix_email on facts(email);
create index if not exists ix_user on facts(user_ref);
create index if not exists ix_hash on facts(email_hash);
create table if not exists evidence (file_id text primary key, path text, origin text, captured_at text);
create table if not exists coverage (file_id text, system text, collection text, obs_at text, n integer, kind text);
create table if not exists docs (file_id text primary key, kind text, content text);
create table if not exists meta (key text primary key, value text);
"""

# The index is derived data. It is reused only while its fingerprint matches: bump INDEX_VERSION when the meaning of the stored
# rows changes without a code change that the fingerprint would see (e.g. a parser rule moved into another module).
INDEX_VERSION = 1
PARSER_SOURCES = ("index.py", "logparse.py", "ocr.py")   # code that decides what ends up in the index


def fingerprint():
    """Hash of everything the index is built from: version, schema, parser code, OCR model, the manifest and every
    evidence file it lists. Any change to any of them yields a different value."""
    from .llm import MODEL

    h = hashlib.sha256()

    def add(label, data):
        h.update(label.encode("utf-8") + b"\x00" + hashlib.sha256(data).digest())

    add("version", str(INDEX_VERSION).encode())
    add("schema", SCHEMA.encode("utf-8"))
    add("ocr-model", MODEL.encode("utf-8"))
    for name in PARSER_SOURCES:
        src = Path(name) if Path(name).is_absolute() else Path(__file__).resolve().parent / name
        add("code:" + src.name, src.read_bytes() if src.exists() else b"<missing>")
    manifest = DATASET / "evidence_files.csv"
    add("manifest", manifest.read_bytes() if manifest.exists() else b"<missing>")
    for ev in (read_csv("evidence_files.csv") if manifest.exists() else []):
        f = DATASET / ev["file_path"]
        add("evidence:" + ev["file_id"], f.read_bytes() if f.exists() else b"<missing>")
    return h.hexdigest()


def stored_fingerprint(path=None):
    """The fingerprint the index at `path` was built with, or None if it has none or cannot be read."""
    try:
        con = sqlite3.connect(path or INDEX_PATH)
        try:
            row = con.execute("select value from meta where key = 'fingerprint'").fetchone()
        finally:
            con.close()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def read_csv(name):
    with open(DATASET / name, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def prefetch_ocr():
    """Transcribe every screenshot concurrently; results are cached on disk and reused by the parser below."""
    from concurrent.futures import ThreadPoolExecutor

    from .ocr import ocr_image

    pngs = [DATASET / e["file_path"] for e in read_csv("evidence_files.csv") if e["file_path"].lower().endswith(".png")]
    if pngs:
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(ocr_image, pngs))


def index_is_fresh():
    return INDEX_PATH.exists() and stored_fingerprint() == fingerprint()


def build_index(force=False, use_llm_ocr=True):
    """Return the index path, rebuilding it when forced or when its fingerprint no longer matches the sources."""
    if not force and index_is_fresh():
        return INDEX_PATH
    return _build(fingerprint())


def _build(fp):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if INDEX_PATH.exists():
        INDEX_PATH.unlink()
    con = sqlite3.connect(INDEX_PATH)
    con.executescript(SCHEMA)
    facts = []
    COVERAGE.clear()
    prefetch_ocr()
    for ev in read_csv("evidence_files.csv"):
        fid, rel, cap = ev["file_id"], ev["file_path"], ev["captured_at"]
        con.execute("insert into evidence values (?,?,?,?)", (fid, rel, ev["origin"], cap))
        path = DATASET / rel
        ext = path.suffix.lower()
        if ext == ".sql":
            facts += parse_sql(fid, path, cap)
        elif ext == ".log":
            facts += parse_log(fid, path)
        elif ext == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            if "snapshots" in data:
                facts += parse_bundle_json(fid, data)
            elif "service_exports" in data:
                facts += parse_service_export(fid, data)
            elif data.get("records") and "system" in data["records"][0]:
                facts += parse_record_list_json(fid, data)
            else:
                facts += parse_collection_json(fid, data)
        elif ext == ".txt":
            facts += parse_handover_txt(fid, path, cap)
        elif ext == ".pdf":
            import pymupdf

            with pymupdf.open(path) as d:
                text = "\n".join(p.get_text() for p in d)
            con.execute("insert into docs values (?,?,?)", (fid, "pdf", text))
        elif ext == ".png":
            from .ocr import ocr_image

            con.execute("insert into docs values (?,?,?)", (fid, "image", json.dumps(ocr_image(path), ensure_ascii=False)))
    for f in facts:
        con.execute(
            "insert into facts (file_id,obs_at,system,resource_id,rtype,topology,tbl,exists_flag,state,email,name,phone,email_hash,user_ref,attrs,loc)"
            " values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f["file_id"], f["obs_at"], f["system"], f["resource_id"], f["rtype"], f["topology"], f["tbl"], f["exists"],
             f["state"], (f["email"] or "").lower() or None, f["name"], f["phone"], f["email_hash"], f["user_ref"],
             json.dumps(f["attrs"], ensure_ascii=False, sort_keys=True), f["loc"]),
        )
    con.executemany("insert into coverage values (?,?,?,?,?,?)", COVERAGE)
    con.execute("insert into meta values ('fingerprint', ?)", (fp,))   # last: an interrupted build has none and is rebuilt
    con.commit()
    con.close()
    return INDEX_PATH


def connect():
    con = sqlite3.connect(build_index())
    con.row_factory = sqlite3.Row
    return con
