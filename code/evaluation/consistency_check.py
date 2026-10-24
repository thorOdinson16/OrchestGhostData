"""Independent consistency check between evidence-based verdicts and the shape of the activity stream.

The deletion worker logs the operations that *succeeded cleanly* shortly before `reported_at` and the ones that went
wrong afterwards. That timing is never used to decide a case; this script only compares it with the verdicts we derived
from evidence, as a sanity check that is independent of the evidence parsers.

    python code/evaluation/consistency_check.py
"""
import collections
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghost.classify import classify_requests  # noqa: E402
from ghost.pipeline import Pipeline, ts  # noqa: E402


def main():
    p = Pipeline()
    everything = p.samples + p.requests
    results = p.run(everything, classify_requests(everything))
    case_ok = case_total = 0
    tally = collections.Counter()
    off = []
    for r in everything:
        cid = r["case_id"]
        if results[cid][0] == "NOT_APPLICABLE":
            continue
        t0 = ts(r["reported_at"])
        treat = {s.resource_id: p.policy.decide(s) for s in results[cid][2]}
        post_any = False
        for s in results[cid][2]:
            if s.resource_id.startswith("account_"):
                continue
            evs = [e for e in p.act.case_events(s.resource_id) if abs((e["t"] - t0).total_seconds()) < 4 * 3600]
            if not evs:
                continue
            post = any(e["t"] > t0 + datetime.timedelta(minutes=20) for e in evs)
            post_any |= post
            tally[(post, treat[s.resource_id].violated)] += 1
            if post != treat[s.resource_id].violated:
                off.append((cid, s.resource_id, s.category))
        if any(e["t"] > t0 for s in results[cid][2] for e in p.act.by_res.get(s.resource_id, []) if e["event_type"] == "SYNC_RECORD"):
            post_any = True
        case_total += 1
        case_ok += (results[cid][0] == "INCOMPLETE") == post_any
    n = sum(tally.values())
    agree = tally[(True, True)] + tally[(False, False)]
    print(f"case level: {case_ok}/{case_total} verdicts agree with the activity timing ({case_ok / case_total:.3f})")
    print(f"resource level: {agree}/{n} agree ({agree / n:.3f}); disagreements: {off}")


if __name__ == "__main__":
    main()
