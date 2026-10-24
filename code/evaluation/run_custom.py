"""Run the full pipeline on our synthetic evaluation cases and score them against hand-derived expectations.

    python code/evaluation/run_custom.py
"""
import csv
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CUSTOM = Path(__file__).resolve().parent / "custom"


def main():
    subprocess.run([sys.executable, str(CUSTOM / "build_custom_dataset.py")], check=True)
    os.environ["GHOST_DATASET"] = str(CUSTOM / "dataset")
    sys.path.insert(0, str(ROOT / "code"))
    sys.path.insert(0, str(ROOT / "code" / "evaluation"))
    from ghost.classify import classify_requests
    from ghost.index import build_index, read_csv
    from ghost.pipeline import Pipeline
    from ghost.planner import serialise
    from evaluate import evaluate, load

    build_index(force=True)
    pipe = Pipeline()
    intents = classify_requests(pipe.requests)
    results = pipe.run(pipe.requests, intents)
    pred = CUSTOM / "predicted_output.csv"
    with open(pred, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["case_id", "deletion_status", "steps_json"])
        for cid in sorted(results):
            status, steps, _ = results[cid]
            w.writerow([cid, status, serialise(steps)])
    truth = load(CUSTOM / "expected_output.csv")
    metrics = evaluate(load(pred), truth, verbose=True)
    for k, v in metrics.items():
        print(f"{k}: {v:.4f}" if isinstance(v, float) else f"{k}: {v}")


if __name__ == "__main__":
    main()
