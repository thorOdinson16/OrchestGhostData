"""Run the whole pipeline on the adversarial dataset and print machine-readable results (used by tests/test_adversarial.py).

    python code/evaluation/adversarial/run_adversarial.py > result.json

No model call is made: every request is declared a deletion request and the dataset holds no PDFs or screenshots.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def main():
    subprocess.run([sys.executable, str(HERE / "build_adversarial_dataset.py")], check=True, stdout=subprocess.DEVNULL)
    os.environ["GHOST_DATASET"] = str(HERE / "dataset")
    sys.path.insert(0, str(ROOT / "code"))
    from ghost.index import build_index
    from ghost.pipeline import Pipeline

    build_index(force=True)
    pipe = Pipeline()
    intents = {r["case_id"]: {"intent": "DELETE"} for r in pipe.requests}
    results = pipe.run(pipe.requests, intents)
    out = {"cases": {}, "unresolved": pipe.unresolved_identities, "captured": {e["file_id"]: e["captured_at"] for e in pipe.evidence_rows},
           "audit_at": {r["case_id"]: r["audit_at"] for r in pipe.requests}, "links": {}}
    for cid, (status, steps, states) in results.items():
        out["cases"][cid] = {"status": status, "steps": steps}
        out["links"][cid] = {s.resource_id: sorted(s.links) for s in states}
    print(json.dumps(out))


if __name__ == "__main__":
    main()
