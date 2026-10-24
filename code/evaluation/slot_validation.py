"""Leave-one-group-out check of the activity-based account pairing (ghost/slots.py).

For every request group (same reported_at) whose accounts are all named by exact identifiers in the evidence, hide the
answer, re-derive the pairing from the deletion-worker activity alone, and count how many cases get the right account.

    python code/evaluation/slot_validation.py
"""
import collections
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghost.classify import classify_requests  # noqa: E402
from ghost.pipeline import Pipeline, ts  # noqa: E402
from ghost.slots import case_key, solve_group  # noqa: E402


def main():
    p = Pipeline()
    everything = p.samples + p.requests
    intents = classify_requests(everything)
    events = p._account_events()
    known = {}
    for r in everything:
        if intents[r["case_id"]]["intent"] != "DELETE":
            continue
        subject = p.ids.resolve(r["email"])
        for rid in p.linked_resources(subject, r):
            if rid.startswith("account_") and any(e["event_type"] == "DELETE_RECORD" for e in p.act.by_res[rid]):
                known[r["case_id"]] = rid
    pools = collections.defaultdict(list)
    for r in everything:
        if intents[r["case_id"]]["intent"] == "DELETE":
            pools[r["reported_at"]].append(r)
    full = {}
    for T, cs in pools.items():
        t0 = ts(T)
        evs = [e for e in events if t0 - timedelta(minutes=76) <= e["t"] <= t0 - timedelta(minutes=43)]
        if len(cs) >= 2 and len(evs) == len(cs) and all(c["case_id"] in known for c in cs):
            order = sorted(cs, key=lambda c: (c["requested_at"], case_key(c["case_id"])))
            rank = {e["resource_id"]: i for i, e in enumerate(sorted(evs, key=lambda e: e["t"]))}
            full[T] = (len(cs), rank[known[order[0]["case_id"]]], cs[0]["audit_at"], cs, evs)
    per_case = {}
    for r in everything:
        if intents[r["case_id"]]["intent"] == "DELETE":
            per_case[r["case_id"]] = dict(req=r, linked=p.linked_resources(p.ids.resolve(r["email"]), r))
    right, wrong, unres = collections.Counter(), collections.Counter(), collections.Counter()
    total = 0
    for T, (k, r0, aud, cs, evs) in full.items():
        held_out = p.phase_bonus(per_case, learn_exclude={c["case_id"] for c in cs})   # phases learned without this group
        for name, use_bonus in (("class only (diagnostic: the pipeline does not accept it)", False), ("class + phase", True)):
            res, un = solve_group(cs, evs, {}, True, held_out if use_bonus else (lambda cid, ev: 0),
                                  held_out.checkable if use_bonus else None, require_evidence=use_bonus)
            for c in cs:
                cid = c["case_id"]
                if cid in res:
                    (right if res[cid] == known[cid] else wrong)[name] += 1
                else:
                    unres[name] += 1
        total += len(cs)
    print(f"groups validated: {len(full)}  cases: {total} (answers hidden; ambiguity is left unresolved, not guessed)")
    for name in ("class only (diagnostic: the pipeline does not accept it)", "class + phase"):
        done = right[name] + wrong[name]
        print(f"{name:60s}: correct {right[name]}, wrong {wrong[name]}, unresolved {unres[name]}  "
              f"(precision of resolved {right[name] / max(1, done):.3f}, coverage {done / total:.3f})")

if __name__ == "__main__":
    main()
