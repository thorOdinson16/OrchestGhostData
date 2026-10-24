"""Ghost Data Still Exists - deletion-audit agent. Run from the repository root:  python code/main.py"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ghost.classify import classify_requests  # noqa: E402
from ghost.index import build_index, read_csv  # noqa: E402
from ghost.pipeline import Pipeline  # noqa: E402
from ghost.planner import serialise  # noqa: E402
from ghost.slots import case_key  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=["eval", "sample"], default="eval")
    ap.add_argument("--out", default=None)
    ap.add_argument("--rebuild-index", action="store_true")
    args = ap.parse_args()
    build_index(force=args.rebuild_index)
    pipe = Pipeline()
    everything = pipe.samples + pipe.requests   # all cases are resolved together so shared resources are not double-claimed
    intents = classify_requests(everything)
    results = pipe.run(everything, intents)
    wanted = {r["case_id"] for r in (pipe.requests if args.dataset == "eval" else pipe.samples)}
    results = {c: v for c, v in results.items() if c in wanted}
    out = Path(args.out) if args.out else (ROOT / "output.csv" if args.dataset == "eval" else ROOT / "code" / "cache" / "sample_pred.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["case_id", "deletion_status", "steps_json"])
        for cid in sorted(results, key=case_key):
            status, steps, _ = results[cid]
            w.writerow([cid, status, serialise(steps)])
    print(f"wrote {len(results)} rows to {out}")


if __name__ == "__main__":
    main()
