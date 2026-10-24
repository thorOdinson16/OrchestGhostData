"""Robustness checks for the activity-based account pairing (ghost/slots.py) beyond slot_validation.py.

    python code/evaluation/pairing_robustness.py

A  Phase statistics learned WITHOUT the held-out group's whole audit batch (not just the group).
B  Same, with the rotation-slot hypothesis switched off (generic class + phase pairing only).
C  Sweep of MARGIN to show how the choice trades coverage against wrong pairings.
D  Decoys: a foreign event added to a pool (D1) or substituted for a case's true event (D2); pairings made for the
   orphaned case are wrong by construction.
E  Evidence composition of the pairings actually made for the evaluation set, including pools with no
   identifier-confirmed member (where no ground truth exists).
"""
import collections
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ghost.slots as slots  # noqa: E402
from ghost.classify import classify_requests  # noqa: E402
from ghost.pipeline import Pipeline, ts  # noqa: E402


def main():
    p = Pipeline()
    everything = p.samples + p.requests
    p.run(everything, classify_requests(everything))
    per_case = p.per_case
    events = p._account_events()
    known = {cid: next(r for r in pc["linked"] if r.startswith("account_") and "activity" not in pc["linked"][r])
             for cid, pc in per_case.items()
             if any(r.startswith("account_") and "activity" not in k for r, k in pc["linked"].items())}
    pools = collections.defaultdict(list)
    for cid, pc in per_case.items():
        pools[pc["req"]["reported_at"]].append(pc["req"])
    full = {}
    for T, cs in pools.items():
        t0 = ts(T)
        evs = [e for e in events if t0 - timedelta(minutes=76) <= e["t"] <= t0 - timedelta(minutes=43)]
        if len(cs) >= 2 and len(evs) == len(cs) and all(c["case_id"] in known for c in cs):
            full[T] = (cs, evs)
    batch_of = {cid: pc["req"]["audit_at"] for cid, pc in per_case.items()}

    def heldout_bonus(cs):
        batches = {batch_of[c["case_id"]] for c in cs}
        return p.phase_bonus(per_case, learn_exclude={cid for cid, b in batch_of.items() if b in batches})

    def run(single_family=True, margin=6):
        slots.MARGIN = margin
        ok = bad = un = 0
        for T, (cs, evs) in full.items():
            hb = heldout_bonus(cs)
            res, _ = slots.solve_group(cs, evs, {}, single_family, hb, hb.checkable)
            for c in cs:
                cid = c["case_id"]
                if cid in res:
                    ok += res[cid] == known[cid]
                    bad += res[cid] != known[cid]
                else:
                    un += 1
        slots.MARGIN = 6
        return ok, bad, un

    total = sum(len(cs) for cs, _ in full.values())
    print(f"identifier-confirmed groups: {len(full)} ({total} cases), answers hidden")
    ok, bad, un = run()
    print(f"A  phases learned without the held-out audit batch : correct {ok}, wrong {bad}, unresolved {un}")
    ok, bad, un = run(single_family=False)
    print(f"B  rotation hypothesis off (generic pairing)       : correct {ok}, wrong {bad}, unresolved {un}")
    for m in (3, 6, 9, 12):
        ok, bad, un = run(margin=m)
        print(f"C  MARGIN={m:<2d}                                      : correct {ok}, wrong {bad}, unresolved {un}")

    # D: decoys in the pool window. A decoy copies a real event's offset from its own reported_at (so it passes the
    # window), gets a fresh resource id and, for the worst-case variants, the true event's result class.
    #   D1  one foreign extra event is added to the pool
    #   D2  the orphaned case's true event is replaced by a foreign event from another group
    #   D3  worst case: the replacement sits in the SAME slot band as the true event, with the true event's result class and
    #       a random seconds-phase (20 deterministic seeds)
    #   D4  as D3 but the replacement also keeps the true seconds-phase (misleading class AND phase)
    import random
    keys = sorted(full)

    def shifted(e, src_T, dst_T, rid):
        d = dict(e)
        d["t"] = e["t"] + (ts(dst_T) - ts(src_T))
        d["resource_id"] = rid
        return d

    def tally(res, cs, orphan, out):
        for c in cs:
            cid = c["case_id"]
            tag = "unresolved" if cid not in res else ("correct" if res[cid] == known[cid] else "WRONG")
            out[("orphaned case " if cid == orphan else "other cases ") + tag] += 1

    d1, d2, d3, d4 = (collections.Counter() for _ in range(4))
    for i, T in enumerate(keys):
        cs, evs = full[T]
        hb = heldout_bonus(cs)
        order = sorted(cs, key=lambda c: (c["requested_at"], slots.case_key(c["case_id"])))
        orphan = order[0]["case_id"]
        true_ev = next(e for e in evs if e["resource_id"] == known[orphan])
        src = keys[(i + 1) % len(keys)]
        foreign = shifted(full[src][1][0], src, T, "account_decoy")
        res, _ = slots.solve_group(cs, evs + [foreign], {}, True, hb, hb.checkable)
        for c in cs:
            d1["unresolved" if c["case_id"] not in res else ("correct" if res[c["case_id"]] == known[c["case_id"]] else "WRONG")] += 1
        kept = [e for e in evs if e is not true_ev]
        res, _ = slots.solve_group(cs, kept + [foreign], {}, True, hb, hb.checkable)
        tally(res, cs, orphan, d2)
        for seed in range(20):
            rng = random.Random(seed * 1000 + i)
            for keep_phase, out in ((False, d3), (True, d4)):
                dt = timedelta(minutes=rng.uniform(-2, 2))
                if not keep_phase:
                    dt = timedelta(seconds=round(dt.total_seconds()) + rng.randint(0, 59))
                else:
                    dt = timedelta(seconds=60 * round(dt.total_seconds() / 60))
                decoy = dict(true_ev, resource_id="account_decoy", t=true_ev["t"] + dt)
                res, _ = slots.solve_group(cs, kept + [decoy], {}, True, hb, hb.checkable)
                tally(res, cs, orphan, out)
    for name, c in (("D1 one foreign extra event in the pool", d1), ("D2 true event replaced by a foreign one", d2),
                    ("D3 worst case: same band, same class, random phase", d3),
                    ("D4 worse: same band, same class, same phase", d4)):
        print(f"{name}: {dict(sorted(c.items()))}")

    # F: corroboration on pools WITHOUT identifier-confirmed members. Two independent signals - the reported-result class
    # and the seconds-phase of the case's other events - each pair the cases on their own; where both decide, do they agree?
    # same statistics the pipeline used: learned only from identifier-confirmed cases
    full_bonus = p.phase_bonus(per_case, learn_exclude={c for c in per_case if c not in known})
    agree = disagree = only_class = only_phase = neither = 0
    for T, cs0 in pools.items():
        t0 = ts(T)
        evs = [e for e in events if t0 - timedelta(minutes=76) <= e["t"] <= t0 - timedelta(minutes=43)]
        pin = {c["case_id"]: known[c["case_id"]] for c in cs0 if c["case_id"] in known}
        if len(pin) == len(cs0) or len(evs) < len(cs0) or len({c["audit_at"] for c in cs0}) != 1:
            continue
        evs = [e for e in evs if e["resource_id"] not in {r for cid, r in known.items() if cid not in pin}]
        a, _ = slots.solve_group(cs0, evs, pin, True, lambda cid, ev: 0, None, require_evidence=False)
        slots.CLASS_POINTS = 0
        b, _ = slots.solve_group(cs0, evs, pin, True, full_bonus, full_bonus.checkable, require_evidence=False)
        slots.CLASS_POINTS = 10
        for c in cs0:
            cid = c["case_id"]
            if cid in pin:
                continue
            if cid in a and cid in b:
                agree += a[cid] == b[cid]
                disagree += a[cid] != b[cid]
            elif cid in a:
                only_class += 1
            elif cid in b:
                only_phase += 1
            else:
                neither += 1
    print(f"F  unconfirmed pools, class-only vs phase-only pairing: both decide and agree {agree}, both decide and DISAGREE "
          f"{disagree}, only class decides {only_class}, only phase decides {only_phase}, neither {neither}")


    comp = collections.Counter()
    for cid, pc in per_case.items():
        for r, kind in pc["linked"].items():
            if r.startswith("account_") and "activity" in kind:
                if "wide_window" in kind:
                    comp[("wide-window path (unique unclaimed event within 30 min of reported_at)", "", "")] += 1
                    continue
                ev = next(e for e in p.act.by_res[r] if e["event_type"] == "DELETE_RECORD")
                req = pc["req"]
                cls = ev["reported_result"] == slots.RESULT_OF_STATUS[req["reported_status"]]
                pts = full_bonus(cid, ev)
                pool = len(pools[req["reported_at"]])
                confirmed_pool = all(c["case_id"] in known for c in pools[req["reported_at"]] if c["case_id"] != cid)
                comp[("result class matches" if cls else "class differs (hand-crafted window)",
                      f"phase points {pts}" if pts < 6 else "phase points >=6",
                      "pool of 1" if pool == 1 else f"pool of {min(pool, 3)}+" if pool >= 3 else "pool of 2")] += 1
    print("E  composition of the activity-paired accounts in the evaluation set:")
    for k, v in sorted(comp.items(), key=lambda x: -x[1]):
        print(f"     {v:4d}  {k}")
    print(f"   total {sum(comp.values())}; unresolved identities recorded: {sum('account' in d for d in p.unresolved_identities.values())}")


if __name__ == "__main__":
    main()
