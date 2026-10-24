"""Inspect how one case was resolved:  python code/debug_case.py case_123 [more case ids]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ghost.classify import classify_requests  # noqa: E402
from ghost.pipeline import Pipeline  # noqa: E402


def main(ids):
    p = Pipeline()
    everything = p.samples + p.requests
    results = p.run(everything, classify_requests(everything))
    by_id = {r["case_id"]: r for r in everything}
    for cid in ids:
        req = by_id[cid]
        subj = p.ids.resolve(req["email"])
        print("=" * 100)
        print(cid, req["email"], "->", subj.user_id if subj else None, "| req", req["requested_at"], req["reported_status"],
              "rep", req["reported_at"], "audit", req["audit_at"])
        print("  emails:", sorted(subj.emails) if subj else None)
        status, steps, states = results[cid]
        print("  STATUS:", status)
        for st in states:
            facts, cite = p.view.state_facts(st.resource_id, req["audit_at"], req["requested_at"])
            print(f"  - {st.resource_id:20s} cat={st.category:20s} present={st.present} ident={st.identified} active={st.active} cite={st.evidence_id} links={sorted(st.links)}")
            for f in facts[:3]:
                print(f"        fact {f.file_id} {f.obs_at} {f.tbl or ''} {f.topology or ''} state={f.state} email={f.email} user={f.user_ref} ev={f.attrs.get('event')}")
        for s in steps:
            print("   ", s["step_no"], s["phase"][:4], s["system_id"], s["resource_id"], s["operation"], s["evidence_id"])


if __name__ == "__main__":
    main(sys.argv[1:])
