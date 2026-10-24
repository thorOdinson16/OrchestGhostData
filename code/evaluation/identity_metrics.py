"""Identity-association, citation and contract metrics, with measured results kept apart from unverifiable ones.

    python code/evaluation/identity_metrics.py

Ground truth exists ONLY for the 32 public samples (`dataset/sample_output.csv`) and for the synthetic fixtures; every number
for the 518 evaluation requests is a count of what the pipeline did, not a measure of correctness.
"""
import collections
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from citations import check_run  # noqa: E402
from evaluate import evaluate  # noqa: E402
from ghost.classify import classify_requests  # noqa: E402
from ghost.pipeline import Pipeline  # noqa: E402
from validate_output import validate  # noqa: E402

ROOT = HERE.parents[1]
CLASSES = ("identifier-confirmed", "corroborated activity", "wide-window activity")


def link_class(kinds):
    if "wide_window" in kinds:
        return CLASSES[2]
    return CLASSES[1] if "activity" in kinds else CLASSES[0]


def main():
    p = Pipeline()
    everything = p.samples + p.requests
    results = p.run(everything, classify_requests(everything))
    req = {r["case_id"]: r for r in everything}
    sample_ids = {r["case_id"] for r in p.samples}
    with open(ROOT / "dataset" / "sample_output.csv", encoding="utf-8", newline="") as f:
        truth = {r["case_id"]: r for r in csv.DictReader(f)}
    truth_res = {cid: {s["resource_id"] for s in json.loads(r["steps_json"])} for cid, r in truth.items()}

    for label, ids in (("public samples (ground truth available)", sample_ids),
                       ("evaluation requests (no ground truth)", set(req) - sample_ids)):
        counts = collections.defaultdict(collections.Counter)
        for cid in ids:
            for s in results[cid][2]:
                if not s.resource_id.startswith(("account_", "analytics_", "legal_hold_")):
                    continue
                kind = s.resource_id.split("_")[0]
                cls = link_class(s.links)
                counts[cls][kind] += 1
                if cid in sample_ids:
                    counts[cls][kind + (" correct" if s.resource_id in truth_res[cid] else " INCORRECT")] += 1
        print(f"\n== Resource-to-case associations, {label}")
        for cls in CLASSES:
            print(f"   {cls:24s} {dict(counts[cls])}")
        un = collections.Counter()
        for cid in ids:
            for kind, info in p.unresolved_identities.get(cid, {}).items():
                un[(kind, info["reason"])] += 1
        print("   unresolved:", sum(un.values()))
        for (kind, why), n in sorted(un.items(), key=lambda x: -x[1]):
            print(f"      {n:3d}  {kind}: {why}")

    pred = {}
    for cid in sample_ids:
        status, steps, _ = results[cid]
        pred[cid] = (status, steps)
    tr = {cid: (r["deletion_status"], json.loads(r["steps_json"])) for cid, r in truth.items()}
    m = evaluate(pred, tr)
    print("\n== Public-sample metrics (measured)")
    for k in ("case_status_accuracy", "step_precision", "step_recall", "step_f1", "field_accuracy_on_matched_steps"):
        print(f"   {k}: {m[k]:.4f}")

    # citations: FORMAT (manifest, captured <= audit_at) and SUPPORT (the cited file backs the claim about that resource) are
    # separate checks (citations.py); any violation fails this script, and citation_check.py lists them
    counts, bad = check_run(p, results, everything)
    print("\n== Citations over all 550 requests")
    print(f"   cited steps {counts['cited']}; format valid {counts['format_ok']}; semantically supported {counts['support_ok']}; "
          f"violations {len(bad)}")
    for v in bad[:10]:
        print("   VIOLATION", v)

    rows, errors = validate(ROOT / "output.csv")
    print(f"\n== Output contract (output.csv): {len(rows)} rows, {len(errors)} violations")
    return 1 if (bad or errors) else 0


if __name__ == "__main__":
    sys.exit(main())
