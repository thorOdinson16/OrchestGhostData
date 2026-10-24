"""Identity resolution: request email -> user, plus every current and historical identifier.

Matching is exact (case-insensitive for emails, which are case-insensitive by definition).
No fuzzy matching is ever used.

Time: user_identities.csv holds the CURRENT values (with the time the snapshot was updated) and user_audit_log.csv the
changes (event_time, old_value -> new_value). A case may only use what existed at its audit_at (`Subject.as_of`):
* a value introduced by a change with event_time after the cutoff did not exist yet, so it is not an identifier of the case;
* the value such a change replaced is the one in force at the cutoff (the original value of a field is the old_value of its
  earliest change) and stays an identifier, like every value introduced before the cutoff;
* a snapshot updated after the cutoff does not by itself make its values new: fields the audit log says nothing about keep
  their value (the log, not the snapshot time, records when a value changed);
* old values whose history is inconsistent (they continue no earlier change) are ambiguous: we do not guess that they
  existed or not at the cutoff, so they are not used as of that time and are listed in `Subject.ambiguous`.
"""
import hashlib
from dataclasses import dataclass, field

from .index import read_csv


def email_hash(email):
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


@dataclass
class Subject:
    user_id: str
    names: set = field(default_factory=set)
    emails: set = field(default_factory=set)      # lower-cased, current + historical
    phones: set = field(default_factory=set)
    hashes: set = field(default_factory=set)
    current_email: str = ""
    history: list = field(default_factory=list)   # (audit_id, event_time, field, old, new)
    updated_at: str = ""                          # when the current snapshot was updated
    ambiguous: set = field(default_factory=set)   # identifiers whose existence at the cutoff the log cannot establish
    as_of_time: str = ""                          # set on views returned by as_of()

    def as_of(self, cutoff):
        """The subject restricted to identifiers that existed at `cutoff` (an ISO time); see the module docstring."""
        if not cutoff:
            return self
        out = Subject(self.user_id, set(), set(), set(), set(), "", [h for h in self.history if h[1] <= cutoff],
                      self.updated_at, set(), cutoff)
        snapshot = {"email": {self.current_email} if self.current_email else set(), "phone": set(self.phones) if not any(
            h[2] == "phone" for h in self.history) else set(), "name": set()}
        mine = {"email": out.emails, "phone": out.phones, "name": out.names}
        for fld, bucket in mine.items():
            events = sorted((h for h in self.history if h[2] == fld), key=lambda h: h[1])
            norm = str.lower if fld == "email" else str
            if not events:   # no recorded change: the snapshot value is the value (a later snapshot time proves nothing)
                bucket |= snapshot[fld] if fld != "name" else set(self.names)
                if fld == "email":
                    out.current_email = self.current_email
                continue
            prev_new, in_force = None, None
            for k, h in enumerate(events):
                old, new = (norm(h[3]) if h[3] else None), (norm(h[4]) if h[4] else None)
                if old and (k == 0 or h[1] <= cutoff):
                    bucket.add(old)                     # the original value, or a value that existed before a past change
                    in_force = in_force or old
                elif old and old != prev_new:
                    out.ambiguous.add(old)              # a later change's old value that no earlier change introduced
                if new and h[1] <= cutoff:
                    bucket.add(new)
                    in_force = new
                prev_new = new
            if fld == "email":
                out.current_email = in_force or ""
        out.ambiguous -= out.emails | out.phones | out.names
        out.hashes = {email_hash(e) for e in out.emails}
        return out


class IdentityStore:
    def __init__(self):
        self.users = {}
        self.by_email = {}
        for r in read_csv("user_identities.csv"):
            s = Subject(user_id=r["user_id"])
            s.names.add(r["name"])
            if r["email"]:
                s.emails.add(r["email"].lower())
                s.current_email = r["email"].lower()
            s.updated_at = r["updated_at"]
            if r["phone"]:
                s.phones.add(r["phone"])
            if r["email_hash"]:
                s.hashes.add(r["email_hash"].lower())
            self.users[s.user_id] = s
        for r in read_csv("user_audit_log.csv"):
            s = self.users.get(r["user_id"])
            if not s:
                continue
            s.history.append((r["audit_id"], r["event_time"], r["field_name"], r["old_value"], r["new_value"]))
            for v in (r["old_value"], r["new_value"]):
                if not v:
                    continue
                if r["field_name"] == "email":
                    s.emails.add(v.lower())
                elif r["field_name"] == "phone":
                    s.phones.add(v)
                elif r["field_name"] == "name":
                    s.names.add(v)
        for s in self.users.values():
            for e in s.emails:
                s.hashes.add(email_hash(e))
                self.by_email.setdefault(e, set()).add(s.user_id)

    def resolve(self, email, as_of=None):
        """Return the Subject owning `email` (current or historical) or None; ambiguous -> None.

        With `as_of` (a case's audit_at) only identifiers that existed then count, and the Subject returned is the view as of
        that time. An email that was introduced after the cutoff, or that two users both held at the cutoff, resolves to
        None (see `why_unresolved`)."""
        e = (email or "").strip().lower()
        ids = self.by_email.get(e, set())
        if as_of:
            ids = {u for u in ids if e in self.users[u].as_of(as_of).emails}
        return self.users[next(iter(ids))].as_of(as_of) if len(ids) == 1 else None

    def why_unresolved(self, email, as_of=None):
        e = (email or "").strip().lower()
        ever = self.by_email.get(e, set())
        if not ever:
            return "no user has ever held this email"
        if as_of:
            now = {u for u in ever if e in self.users[u].as_of(as_of).emails}
            if not now:
                amb = any(e in self.users[u].as_of(as_of).ambiguous for u in ever)
                return ("the log cannot establish that this email existed at the cutoff" if amb else
                        "this email was only introduced after the cutoff")
            ever = now
        return "several users hold this email" if len(ever) > 1 else ""

    def candidates(self, email):
        return sorted(self.by_email.get((email or "").strip().lower(), set()))
