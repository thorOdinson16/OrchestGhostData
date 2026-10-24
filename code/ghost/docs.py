"""Facts from PDFs and screenshots.

The document text (PDF text layer / model OCR of screenshots) is searched for the resource id or display
reference named by a data-catalog identity link; a match makes the document the evidence for that resource.

Two different questions are kept apart: ATTRIBUTION (whose record is this - from the catalog link or the identifiers in the
same record, stored in the fact's email / user_ref) and EXPOSURE (does the record itself show direct identifiers - the
model's answer about the record's own text, stored as attrs["doc_exposes"] = "true" | "false", absent when unknown).
Attribution alone never makes a record count as exposing anything. The text given to the model is scoped to the record.
"""
import json
import re

from .index import PREFIX_SYSTEM, sys_of
from .llm import chat_json
from .mapping import Fact


def doc_text(row):
    if row["kind"] == "image":
        try:
            return json.dumps(json.loads(row["content"]), ensure_ascii=False)
        except ValueError:
            return row["content"]
    return re.sub(r"\s+", " ", row["content"])


QUESTION = {
    "account": "Does the evidence show that the customer account/profile record still exists (is visible/retrievable)?",
    "search": "Does the evidence show that this search-index entry still exists (is returned/visible)?",
    "export": "Does the evidence show that this customer data export/statement file still exists and is available?",
    "marketing": "Does the evidence show that this marketing profile still exists?",
    "object": "Does the evidence show that this stored attachment/object still exists?",
    "support": "Does the evidence show this support record/attachment still exists, and does it expose the customer's direct identifiers (name, email, phone)?",
    "analytics": "Does the evidence show this analytics profile still carries direct identifiers or a link to a user (name, email, phone, hash, user id)?",
    "tombstone": "Does the evidence show this retained deleted-account reference still carries direct identifiers (name, email, phone, email hash)? A bare user_id is NOT a direct identifier.",
    "data_flow": "Does the evidence show this data flow/connector is still enabled or syncing (an active process that can copy the user's data)?",
    "backup": "Does the evidence show that this backup archive can still be restored for the user (restore not blocked)? If the evidence says nothing about restore being blocked, answer true.",
    "audit_record": "Does the evidence show this security audit record exists, and when does its retention end?",
}
SYSTEM = (
    "You read one business artifact (PDF text or the transcription of an application screenshot) and answer a "
    "single factual question about one named record. Answer only from the artifact. Return JSON: "
    '{"present": true|false|null, "identifiers_exposed": true|false|null, "active": true|false|null, '
    '"retain_until": "<ISO date or null>", "quote": "<short supporting quote>"}. '
    "present = the named record is shown as existing; identifiers_exposed = direct identifiers of a person are shown "
    "in that record; active = process/restore path is enabled; use null when not applicable."
)


def interpret(prefix, rid, disp, text, file_id):
    q = QUESTION.get(prefix, "Does the evidence show this record exists?")
    user = "\n".join([f"Record id: {rid}", f"Display reference: {disp}", f"Question: {q}", "", "ARTIFACT:", text[:12000]])
    return chat_json("docfact", [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], SYSTEM + user)


def _flags(prefix, text, rid="", disp="", file_id=""):
    """Interpret the document for one record with the model; map the answer onto fact attributes."""
    res = interpret(prefix, rid, disp, text, file_id)
    a = {"interp_quote": res.get("quote")}
    active = res.get("active")
    if prefix == "data_flow" and isinstance(active, bool):   # null / missing / malformed stays unknown: no "enabled" at all
        a["enabled"] = "true" if active else "false"
    elif prefix == "backup" and isinstance(active, bool):
        a["restore_allowed"] = "true" if active else "false"
    elif prefix == "export":
        a["available"] = "true" if res.get("present") is not False else "false"
    if res.get("present") is False:
        a["absent"] = "true"
    if isinstance(res.get("identifiers_exposed"), bool):    # null / missing / malformed -> unknown, not "false"
        a["doc_exposes"] = "true" if res["identifiers_exposed"] else "false"
        if res["identifiers_exposed"]:
            a["identified"] = "true"
    if res.get("retain_until"):
        a["retain_until"] = res["retain_until"]
    return a


RID_FIND = re.compile(r"\b(?:account|search|analytics|support|export|data_flow|backup|audit_record|marketing|tombstone|object)_\d+\b")
EMAIL_FIND = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
USER_FIND = re.compile(r"\buser_\d+\b")
BARE_ID = re.compile(r"^(user_\d+|[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})$")

DOC_UNRESOLVED = []   # (file_id, resource_id, reason): references whose owner the document does not establish


def segments(raw):
    """Split PDF text into records. A pipe-delimited line is a table row (one record); otherwise a record is a card that
    starts at a line reading 'Record' and runs to the next one. Text before the first card is a header, not a record."""
    segs, card = [], None
    for line in raw.splitlines():
        if " | " in line:
            segs.append(("row", line))
        elif line.strip() == "Record":
            if card is not None:
                segs.append(("card", "\n".join(card)))
            card = []
        elif card is not None:
            card.append(line)
    if card is not None:
        segs.append(("card", "\n".join(card)))
    return segs


def associate(raw):
    """Resource -> (email, user) for the records of one document, only where the record itself supports it.

    A resource id is tied to the identifiers found in the SAME record (card or table row). It stays unresolved when
    several resources share the record, when the record names more than one distinct email or user id, or when no
    identifier sits in the record. Returns ({rid: (email or None, user or None)}, [(rid, reason)])."""
    found, unresolved = {}, []
    seen_rids = set(RID_FIND.findall(raw))
    for kind, seg in segments(raw):
        rids = sorted(set(RID_FIND.findall(seg)))
        emails = sorted({e.lower() for e in EMAIL_FIND.findall(seg)})
        users = sorted(set(USER_FIND.findall(seg)))
        if not rids or not (emails or users):
            continue
        if len(rids) > 1:
            unresolved += [(r, "several resources share one record with identifiers") for r in rids]
        elif len(emails) > 1 or len(users) > 1:
            unresolved.append((rids[0], "the record names more than one email or user id"))
        else:
            ident = (emails[0] if emails else None, users[0] if users else None)
            if found.get(rids[0], ident) != ident:
                unresolved.append((rids[0], "the document gives two different owners"))
            found[rids[0]] = ident
    bad = {r for r, _ in unresolved}
    for r in bad:
        found.pop(r, None)
    for r in sorted(seen_rids - set(found) - bad):
        unresolved.append((r, "no identifier in the same record"))
    return found, unresolved


def _header(raw):
    """Lines that belong to no record: the title and a table's column-header row (a pipe row naming no resource)."""
    keep, in_card = [], False
    for line in raw.splitlines():
        if line.strip() == "Record":
            in_card = True
        elif in_card:
            continue
        elif " | " in line and RID_FIND.search(line):
            continue
        else:
            keep.append(line)
    return "\n".join(keep).strip()


def scoped_texts(raw, tokens):
    """The text of just the records (cards / rows) that mention one of `tokens`, with the document header. None if the
    document is not divided into records or no record mentions a token (the caller then has only the whole document)."""
    segs = segments(raw)
    hit = [seg for _, seg in segs if any(_mentions(seg, t) for t in tokens if t)]
    if not hit:
        return None
    return "\n".join(filter(None, [_header(raw)] + hit))


def _mentions(text, token):
    return bool(re.search(r"(?<![A-Za-z0-9_])" + re.escape(token) + r"(?![A-Za-z0-9_])", text))


def _principal_field(row, token):
    """True if a screenshot's own record fields (not table rows) carry `token` as a whole value."""
    try:
        data = json.loads(row["content"])
    except (TypeError, ValueError):
        return False
    vals = []
    for key in ("other", "detail"):
        part = data.get(key)
        if isinstance(part, dict):
            vals += [v for v in part.values() if isinstance(v, str)]
    return token in vals


def doc_mentions_record(row, link, text):
    """Does this document describe the record of a catalog link?

    The resource id (whole token) always counts. The display reference counts only when it is not a bare identifier;
    a bare user id / email names a person, so it identifies the record only as a screenshot's own record field -
    never because it appears somewhere in a table or list of many people (e.g. a register of disputed orders)."""
    if _mentions(text, link["rid"]):
        return True
    disp = link["disp"]
    if not disp or disp == link["rid"]:
        return False
    if BARE_ID.match(disp):
        return row["kind"] == "image" and _principal_field(row, disp)
    return _mentions(text, disp)


def pdf_facts(doc_rows, evidence_rows, start):
    """Facts for resources named in PDFs, with identifiers attached record by record (see `associate`)."""
    cap = {e["file_id"]: e["captured_at"] for e in evidence_rows}
    facts, n = [], start
    for d in doc_rows:
        if d["kind"] != "pdf":
            continue
        text = doc_text(d)
        owners, unresolved = associate(d["content"])
        DOC_UNRESOLVED.extend((d["file_id"], r, why) for r, why in unresolved)
        for rid, (email, user) in sorted(owners.items()):
            prefix = re.match(r"^([a-z_]+?)_\d", rid).group(1)
            system, rtype = PREFIX_SYSTEM[prefix]
            n += 1
            attrs = _flags(prefix, re.sub(r"[ \t]+", " ", scoped_texts(d["content"], [rid]) or text), rid, "", d["file_id"])
            attrs.update(event="doc_observation", doc_pdf=True)
            if prefix == "support" and "attachment" in text.lower():
                attrs["attachment"] = "true"
            facts.append(Fact(-n, d["file_id"], cap[d["file_id"]], system, rid, rtype, None, None, 1, None,
                              email, None, None, None, user, attrs, "doc"))
    return facts


def doc_facts(links, doc_rows, evidence_rows):
    cap = {e["file_id"]: e["captured_at"] for e in evidence_rows}
    DOC_UNRESOLVED.clear()
    facts = pdf_facts(doc_rows, evidence_rows, 1000)
    n = 0
    for L in links:
        prefix = re.match(r"^([a-z_]+?)_\d", L["rid"]).group(1) if re.match(r"^([a-z_]+?)_\d", L["rid"]) else ""
        system, rtype = PREFIX_SYSTEM.get(prefix, (None, None))
        for d in doc_rows:
            text = doc_text(d)
            if doc_mentions_record(d, L, text):
                n += 1
                scoped = scoped_texts(d["content"], [L["rid"], L["disp"]]) if d["kind"] == "pdf" else None
                attrs = _flags(prefix, re.sub(r"[ \t]+", " ", scoped) if scoped else text, L["rid"], L["disp"], d["file_id"])
                if d["kind"] == "pdf" and not scoped and len(set(RID_FIND.findall(text))) > 1:
                    attrs.pop("doc_exposes", None)     # an undivided multi-record document says nothing about this record alone
                # the catalog link is itself an observation made at link time; it cannot be used before it existed
                attrs.update(catalog=True, link_activity=L["activity_id"], event="doc_observation",
                             link_t=L["t"].strftime("%Y-%m-%dT%H:%M:%SZ"))
                email = L["email"] if prefix in ("analytics", "support", "marketing", "backup", "export", "search", "account") else None
                facts.append(Fact(-n, d["file_id"], cap[d["file_id"]], system, L["rid"], rtype, None, None, 1, None,
                                  email, None, None, None, L["user"], attrs, "doc"))
    return facts
