"""System-activity stream: claims about operations. Used to (a) tell which request a resource belongs to
by its time cluster around `reported_at`, and (b) read catalog identity links. Activity results are never
treated as proof of the resulting state."""
import re
from collections import defaultdict
from datetime import datetime, timedelta

from .index import read_csv

CASE_EVENT_TYPES = {
    "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD", "DISABLE_DATA_FLOW", "CHECK_DATA_FLOW", "BLOCK_RESTORE",
    "CHECK_RESTORE_ACCESS", "RETAIN_RECORD",
}
LINK_RE = re.compile(r"linked (?P<rid>\S+) \((?P<disp>.*?)\) to (?P<email>\S+@\S+) and (?P<user>user_\w+)")
ATTACH_RE = re.compile(r"confirmed (?P<rid>\S+) \((?P<disp>.*?)\) belongs only to (?P<email>\S+@\S+) and (?P<user>user_\w+)")


def ts(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


class Activity:
    def __init__(self):
        self.rows = read_csv("system_activity.csv")
        for r in self.rows:
            r["t"] = ts(r["event_time"])
        self.by_res = defaultdict(list)
        self.links = []   # catalog identity links
        for r in self.rows:
            self.by_res[r["resource_id"]].append(r)
            if r["event_type"] == "LINK_RESOURCE_IDENTITY":
                m = LINK_RE.search(r["details"]) or ATTACH_RE.search(r["details"])
                if m:
                    self.links.append(dict(rid=m["rid"], disp=m["disp"], email=m["email"].lower(), user=m["user"],
                                           t=r["t"], activity_id=r["activity_id"]))

    def case_events(self, rid):
        out = []
        for e in self.by_res.get(rid, []):
            if e["event_type"] in CASE_EVENT_TYPES and e["source_system"] in ("deletion_worker", "backup_service"):
                out.append(e)
            elif e["event_type"] == "SYNC_RECORD" and ("replica" in e["target_system"] or e["target_system"] == "account_replication_cluster"):
                out.append(e)
        return out

    def in_window(self, rid, reported_at, before=timedelta(minutes=90), after=timedelta(hours=3)):
        """True if the resource has no case-type activity at all, or has some inside the request's window."""
        ev = self.case_events(rid)
        if not ev:
            return None  # no information
        t0 = ts(reported_at)
        return any(t0 - before <= e["t"] <= t0 + after for e in ev)
