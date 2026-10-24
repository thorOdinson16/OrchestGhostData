"""Score predictions against the public sample answers (case status, step, field and sequence accuracy).

    python code/evaluation/evaluate.py [pred.csv] [truth.csv] [--verbose]
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIELDS = ["phase", "system_id", "resource_id", "operation", "state", "evidence_id"]


def load(path):
    with open(path, encoding="utf-8", newline="") as f:
        return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in csv.DictReader(f)}


def key(s):
    return tuple(s[f] for f in FIELDS[:5])


def lcs(a, b):
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a)):
        for j in range(len(b)):
            dp[i + 1][j + 1] = dp[i][j] + 1 if a[i] == b[j] else max(dp[i][j + 1], dp[i + 1][j])
    return dp[-1][-1]


def evaluate(pred, truth, verbose=False):
    n = len(truth)
    status_ok = exact_ok = 0
    tp = fp = fn = 0
    field_ok = field_total = 0
    seq_num = seq_den = 0
    per_case = {}
    for cid, (tstat, tsteps) in truth.items():
        pstat, psteps = pred.get(cid, ("MISSING", []))
        status_ok += pstat == tstat
        exact_ok += (pstat == tstat and psteps == tsteps)
        tk, pk = [key(s) for s in tsteps], [key(s) for s in psteps]
        common = set(tk) & set(pk)
        tp += len(common)
        fn += len(set(tk) - common)
        fp += len(set(pk) - common)
        tmap = {key(s): s for s in tsteps}
        for s in psteps:
            if key(s) in tmap:
                for fld in FIELDS:
                    field_total += 1
                    field_ok += s[fld] == tmap[key(s)][fld]
        if not tk and not pk:        # NOT_APPLICABLE rows: an empty array is the correct sequence
            seq_num += 1
            seq_den += 1
        else:
            seq_num += lcs(tk, pk)
            seq_den += max(len(tk), len(pk))
        per_case[cid] = (pstat == tstat, set(tk) - common, set(pk) - common)
    prec = tp / (tp + fp) if tp + fp else 0
    rec = tp / (tp + fn) if tp + fn else 0
    res = {
        "cases": n,
        "case_status_accuracy": status_ok / n,
        "exact_row_accuracy": exact_ok / n,
        "step_precision": prec,
        "step_recall": rec,
        "step_f1": 2 * prec * rec / (prec + rec) if prec + rec else 0,
        "field_accuracy_on_matched_steps": field_ok / field_total if field_total else 0,
        "sequence_accuracy_lcs": seq_num / seq_den,
    }
    if verbose:
        for cid, (ok, missing, extra) in per_case.items():
            if not ok or missing or extra:
                print(cid, "STATUS_OK" if ok else "STATUS_WRONG")
                for m in sorted(missing):
                    print("   missing", m)
                for e in sorted(extra):
                    print("   extra  ", e)
    return res


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pred = load(args[0] if args else ROOT / "code" / "cache" / "sample_pred.csv")
    truth = load(args[1] if len(args) > 1 else ROOT / "dataset" / "sample_output.csv")
    for k, v in evaluate(pred, truth, "--verbose" in sys.argv).items():
        print(f"{k}: {v:.4f}" if isinstance(v, float) else f"{k}: {v}")
