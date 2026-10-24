"""Resource state reconstruction at `audit_at` from the evidence index.

Only evidence files whose manifest `captured_at` is at or before `audit_at` are eligible; later evidence is
never read, even if its inner snapshot timestamps are earlier.
"""
from dataclasses import dataclass, field

COLLECTION_SYSTEM = {
    "customer_profiles": "account_service", "customer_search_documents": "search_service",
    "customer_analytics_profiles": "analytics_platform", "customer_analytics_reports": "analytics_platform",
    "support_records": "support_platform", "support_conversations": "support_platform",
    "customer_exports": "export_service", "customer_export_jobs": "export_service",
    "connector_subscriptions": "integration_service", "connector_configurations": "integration_service",
    "backup_restore_entries": "backup_service", "security_audit_records": "security_service",
    "security_audit_events": "security_service", "deleted_customer_references": "identity_service",
    "profile_lookups": "account_service", "index_queries": "search_service", "profiles": "analytics_platform",
    "tickets": "support_platform", "exports": "export_service", "connectors": "integration_service",
    "restore_entries": "backup_service", "audit_records": "security_service",
    "search_documents_primary": "search_service", "ticket_attachments": "support_platform",
    "backup_catalog": "backup_service", "retained_order_records": "legal_service",
    "operational_record_history": None,
}


PRESENT, ABSENT, UNKNOWN = "PRESENT", "ABSENT", "UNKNOWN"


def truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "y")


def tri(v):
    """True / False for a recognised boolean spelling, None for anything else (missing, null, malformed): unknown is not false."""
    if isinstance(v, bool):
        return v
    t = str(v).strip().lower()
    return True if t in ("1", "true", "yes", "y") else False if t in ("0", "false", "no", "n") else None


@dataclass
class ResourceState:
    resource_id: str
    system: str
    category: str                 # policy data category
    present: object = None        # record still exists
    identified: bool = False      # carries direct identifiers / attributable
    active: object = None         # data flow enabled / restore allowed
    evidence_id: str = ""
    obs_at: str = ""
    links: set = field(default_factory=set)
    notes: list = field(default_factory=list)
    unresolved: bool = False      # no citable evidence either way (never treated as absence)
    context: dict = field(default_factory=dict)   # evidence-derived inputs for policy conditions
    exposure_unknown: bool = False  # only documents that do not say whether the record exposes identifiers

    @property
    def presence(self):
        """PRESENT / ABSENT / UNKNOWN; missing evidence is UNKNOWN, never ABSENT."""
        return UNKNOWN if self.present is None else (PRESENT if self.present else ABSENT)


def fact_flags(f):
    """Interpret a single fact into (present, identified, active)."""
    a = f.attrs
    ev = a.get("event", "")
    st = (f.state or "").upper()
    present = None if f.exists is None else bool(f.exists)
    if st in ("NOT_FOUND", "404"):
        present = False
    elif st in ("FOUND", "IDENTIFIABLE", "ANONYMIZED", "REDACTED", "ACTIVE", "BLOCKED", "DISABLED"):
        present = True
    identified = bool(f.email or f.name or f.phone or f.email_hash)
    if f.rtype in ("ANALYTICS_PROFILE", "SEARCH_INDEX_ENTRY") and f.user_ref:
        identified = True
    if f.loc == "doc":
        # a document fact's email / user_ref say whose record it is (attribution), not that the record exposes it: only the
        # record's own content, as read for this record, decides exposure
        identified = a.get("doc_exposes") == "true"
    if a.get("text_emails"):
        identified = True
    if st == "IDENTIFIABLE":
        identified = True
    if st in ("ANONYMIZED", "REDACTED"):
        identified = False
    try:
        if int(a.get("direct_identifiers", 0)) > 0:
            identified = True
    except (TypeError, ValueError):
        pass
    active = None
    if f.rtype == "DATA_FLOW":
        if "enabled" in a:
            active = tri(a["enabled"])
            if active and ev in ("connector_heartbeat", "connector_config_loaded") and "scheduler_registered" in a:
                active = tri(a["scheduler_registered"])   # enabled but not scheduled is inactive; unreadable -> unknown
        if st in ("ACTIVE", "DISABLED"):
            active = st == "ACTIVE"
    elif f.rtype == "BACKUP_ARCHIVE":
        if "restore_allowed" in a:
            active = tri(a["restore_allowed"])
        elif "eligible" in a:
            elig, block = tri(a["eligible"]), tri(a.get("deletion_block", "false"))
            active = False if (elig is False or block is True) else (True if (elig and block is False) else None)
        elif a.get("decision"):
            active = a["decision"].lower() not in ("deny", "denied", "blocked")
        if st in ("ACTIVE", "BLOCKED"):
            active = st == "ACTIVE"
    elif f.rtype == "USER_EXPORT":
        for key in ("available", "download_available"):
            if key in a:
                present = truthy(a[key])
    return present, identified, active


def exposure_unknown(facts):
    """True if every fact is a document fact that does not say whether the record exposes identifiers."""
    return bool(facts) and all(f.loc == "doc" and f.attrs.get("doc_exposes") not in ("true", "false") for f in facts)


def merge_flags(facts):
    """Combine facts about one resource. Copies observed at the same instant (primary + replicas) are OR-ed, since any
    copy that still exposes data counts; a later observation overrides an earlier one for the flags it reports."""
    present = identified = active = None
    groups = {}
    for f in facts:
        groups.setdefault(f.obs_at or "", []).append(f)
    for _, grp in sorted(groups.items()):
        gp = gi = ga = None
        for f in grp:
            p, i, a = fact_flags(f)
            if p is not None:
                gp = bool(gp) or p
            gi = bool(gi) or i
            if a is not None:
                ga = bool(ga) or a
        if gp is not None:
            present = gp
        if gi is not None:
            identified = gi if (gp is not None or gi) else identified
        if ga is not None:
            active = ga
    return present, bool(identified), active


class EvidenceView:
    """Eligibility-filtered access to facts and file coverage for one audit_at."""

    def __init__(self, facts, evidence_rows, coverage_rows):
        self.cap = {e["file_id"]: e["captured_at"] for e in evidence_rows}
        self.by_res = {}
        for f in facts:
            self.by_res.setdefault(f.resource_id, []).append(f)
        self.coverage = {}
        self.snapshots = []   # (system or '*', file_id, snapshot time) of full table/collection dumps
        self.system_wide = set()   # (system or '*', file_id) of snapshots whose coverage metadata declares scope == "system"
        for c in coverage_rows:
            if c.get("kind") == "snapshot":
                self.snapshots.append((c["system"] or "*", c["file_id"], c["obs_at"] or self.cap[c["file_id"]]))
                if c.get("scope") == "system":
                    self.system_wide.add((c["system"] or "*", c["file_id"]))
            sysn = c["system"] or COLLECTION_SYSTEM.get(c["collection"])
            sysn = COLLECTION_SYSTEM.get(c["collection"], sysn)
            self.coverage.setdefault(sysn, set()).add(c["file_id"])
        for f in facts:
            self.coverage.setdefault(f.system, set()).add(f.file_id)

    def citable(self, file_id, audit_at):
        """Only evidence captured at or before audit_at may be cited."""
        return self.cap[file_id] <= audit_at

    def valid_facts(self, rid, audit_at, since=""):
        """Observations of the resource that were made inside [since, audit_at] (observation time, any file)."""
        return [f for f in self.by_res.get(rid, []) if f.obs_at and since <= f.obs_at <= audit_at]

    def state_facts(self, rid, audit_at, since=""):
        """(facts describing the latest state, citation file) using only citable evidence.

        Only files captured at or before audit_at may determine state or be cited. Observations that exist solely in
        later-captured files are still usable to *discover* that a resource belongs to a subject (see valid_facts), but
        they say nothing about its state: with no citable observation the state is unknown, not absent."""
        elig = [f for f in self.valid_facts(rid, audit_at, since) if self.citable(f.file_id, audit_at)]
        if not elig:
            return [], None
        best = max(elig, key=lambda f: (f.obs_at or "", self.cap[f.file_id]))
        same = [f for f in elig if f.file_id == best.file_id]
        if not any(f.loc.startswith("line") or f.tbl for f in same):   # snapshot-style files: newest observation only
            mx = max(f.obs_at or "" for f in same)
            same = [f for f in same if (f.obs_at or "") == mx]
        return same, best.file_id

    def _speaks_for_subject(self, sysn, fid, subject_files):
        """A snapshot describes the batch it was taken for, not every customer of the system. It may prove anything about
        a subject only if it also holds records of that subject (`subject_files`), or if its coverage metadata explicitly
        declares system-wide scope. With neither, whether it covers the subject is unknown, so it proves nothing."""
        return fid in (subject_files or ()) or (sysn, fid) in self.system_wide

    def superseding_snapshot(self, system, after, audit_at, since="", subject_files=None):
        """Latest citable snapshot of `system` taken after `after` and no later than audit_at that speaks for the subject.
        A resource last seen before that snapshot and missing from it no longer existed when the snapshot was taken."""
        best = None
        for sysn, fid, at in self.snapshots:
            if sysn not in (system, "*") or not self.citable(fid, audit_at):
                continue
            if not self._speaks_for_subject(sysn, fid, subject_files):
                continue
            if after < at <= audit_at and at >= since and (best is None or at > best[0]):
                best = (at, fid)
        return best[1] if best else None

    def covering_snapshot(self, system, audit_at, since="", subject_files=None):
        """Latest citable snapshot of `system` that can prove the subject's record absent (a record missing from it did not
        exist at that time).

        * The subject has observations in some files (a known batch): only a snapshot that is one of those files, or whose
          coverage metadata declares system-wide scope, qualifies. A snapshot of another batch never does - its silence
          about the subject says nothing - so with no such snapshot the state stays unknown.
        * The subject has no observation anywhere (no batch to compare against): the system's own snapshot taken inside
          the request window is accepted. This is the one path the public samples require (case_01/case_02 cite the
          account_service replica snapshot for a profile that no file observes); it is bounded by `since`/`audit_at`."""
        batch = bool(subject_files)
        cands = [(at, fid) for sysn, fid, at in self.snapshots
                 if sysn in (system, "*") and self.citable(fid, audit_at) and since <= at <= audit_at
                 and (not batch or self._speaks_for_subject(sysn, fid, subject_files))]
        return max(cands)[1] if cands else None

    def covering_file(self, system, audit_at, since=""):
        """Latest citable file that covers `system` (even with zero records), for citing absence."""
        files = [fid for fid in self.coverage.get(system, set()) if self.citable(fid, audit_at) and self.cap[fid] >= since]
        return max(files, key=lambda x: self.cap[x]) if files else None
