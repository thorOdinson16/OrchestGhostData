"""Data mapping: attach indexed facts to a subject using exact identifier matches only."""
import json
from dataclasses import dataclass, field

from .identity import Subject


@dataclass
class Fact:
    fact_id: int
    file_id: str
    obs_at: str
    system: str
    resource_id: str
    rtype: str
    topology: str
    tbl: str
    exists: object
    state: str
    email: str
    name: str
    phone: str
    email_hash: str
    user_ref: str
    attrs: dict
    loc: str
    reasons: set = field(default_factory=set)


def load_facts(con):
    facts = []
    for r in con.execute("select * from facts order by fact_id"):
        facts.append(
            Fact(r["fact_id"], r["file_id"], r["obs_at"], r["system"], r["resource_id"], r["rtype"], r["topology"],
                 r["tbl"], r["exists_flag"], r["state"], r["email"], r["name"], r["phone"], r["email_hash"],
                 r["user_ref"], json.loads(r["attrs"] or "{}"), r["loc"])
        )
    return facts


def direct_links(f, s: Subject):
    """Reasons (identifier kinds) by which fact `f` points at subject `s` via exact matches."""
    why = set()
    a = f.attrs
    if f.email and f.email in s.emails:
        why.add("email" if f.email == s.current_email else "old_email")
    for e in a.get("text_emails", []):
        if e in s.emails:
            why.add("email" if e == s.current_email else "old_email")
    if a.get("lookup_email") and a["lookup_email"].lower() in s.emails:
        why.add("email" if a["lookup_email"].lower() == s.current_email else "old_email")
    for h in (f.email_hash, a.get("lookup_hash")):
        if h and h.lower() in s.hashes:
            cur = {x for x in s.hashes}  # hashes of all emails; classify later by comparing with current email hash
            why.add("hash")
    if f.user_ref == s.user_id or a.get("lookup_user") == s.user_id:
        why.add("user_ref")
    if f.phone and f.phone in s.phones:
        why.add("phone")
    return why


def map_subject(facts, s: Subject, audit_at):
    """All facts observed at or before audit_at that belong to the subject, grouped by resource."""
    linked = {}
    for f in facts:
        if f.obs_at and f.obs_at > audit_at:
            continue
        why = direct_links(f, s)
        if why:
            f.reasons = why
            linked.setdefault(f.resource_id, []).append(f)
    return linked
