"""Mandatory citation checks over every request the pipeline answers (public samples and evaluation set).

    python code/evaluation/citation_check.py

Exits 1 if any cited step fails the FORMAT or SUPPORT checks (see citations.py). The public samples' reference answers are
run through the same checks as a calibration of the checker itself; their count is informational.
"""
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from citations import check_run, format_problems, support_problems  # noqa: E402
from ghost.classify import classify_requests  # noqa: E402
from ghost.pipeline import Pipeline  # noqa: E402

ROOT = HERE.parents[1]


def main():
    p = Pipeline()
    everything = p.samples + p.requests
    results = p.run(everything, classify_requests(everything))
    counts, bad = check_run(p, results, everything)
    print("== Citations in the pipeline's answers (550 requests)")
    print(f"   cited steps: {counts['cited']}")
    print(f"   FORMAT  (in manifest, captured at or before audit_at): {counts['format_ok']} / {counts['cited']}")
    print(f"   SUPPORT (cited file backs this claim about this resource): {counts['support_ok']} / {counts['format_ok']}")
    for v in bad[:40]:
        print("   VIOLATION", v)
    # calibration: the same checks on the public reference answers
    req = {r["case_id"]: r for r in p.samples}
    n = ok = 0
    with open(ROOT / "dataset" / "sample_output.csv", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            r = req[row["case_id"]]
            pc = p.per_case.get(row["case_id"])
            files = {x.file_id for rid in (pc["linked"] if pc else []) for x in p.view.by_res.get(rid, [])
                     if x.obs_at and r["requested_at"] <= x.obs_at <= r["audit_at"]}
            for s in json.loads(row["steps_json"]):
                if s["evidence_id"]:
                    n += 1
                    ok += not format_problems(p.view, s, r["audit_at"]) and not support_problems(
                        p.view, s, r["requested_at"], r["audit_at"], files)
    print(f"== Calibration: the public reference answers' own citations pass the same checks: {ok} / {n} (informational)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
