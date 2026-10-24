"""Evaluation entry point:  python code/evaluation/main.py   (exit code 0 only if every REQUIRED stage passed)

Runs the unit tests, the public-sample scoring, the structural validation of ./output.csv and our extra evaluation cases.
(The reports in this folder were written from the outputs of these scripts.)

A stage passes only if its process exits 0, the files it must produce exist, and its gate (if any) finds no problem.
A required stage that fails, crashes, times out, is skipped or produces nothing fails the whole run; every stage's output is
kept and printed either way. OPTIONAL stages are informational: they are reported but cannot fail the run.

    --stages-json FILE   run the stages listed in FILE instead (used by the tests of this runner; see STAGE_FIELDS)
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TIMEOUT = 3600
STAGE_FIELDS = ("name", "argv", "required", "produces", "requires", "gate")   # argv runs under the current Python; gate names a GATES entry

# Regression gates on measured metrics (the values the project currently reports; lowering them must be a deliberate edit).
MIN_STATUS_ACCURACY = 1.0
MIN_STEP_PRECISION = 1.0


def metrics_of(text):
    out = {}
    for line in text.splitlines():
        m = re.match(r"^([a-z_0-9]+): (-?\d+(?:\.\d+)?)$", line.strip())
        if m:
            out[m.group(1)] = float(m.group(2))
    return out


def gate_unit_tests(stdout, stderr):
    text = stdout + stderr
    problems = []
    m = re.search(r"^Ran (\d+) tests?", text, re.M)
    if not m or int(m.group(1)) == 0:
        problems.append("no tests ran")
    if re.search(r"skipped=\d+", text):
        problems.append("mandatory tests were skipped: " + re.search(r"skipped=\d+", text).group(0))
    return problems


def gate_metrics(stdout, stderr):
    m = metrics_of(stdout)
    problems = [f"metric {k} missing from the output" for k in ("case_status_accuracy", "step_precision") if k not in m]
    if m.get("case_status_accuracy", 1) < MIN_STATUS_ACCURACY:
        problems.append(f"case_status_accuracy {m['case_status_accuracy']:.4f} < {MIN_STATUS_ACCURACY}")
    if m.get("step_precision", 1) < MIN_STEP_PRECISION:
        problems.append(f"step_precision {m['step_precision']:.4f} < {MIN_STEP_PRECISION}")
    return problems


def gate_validator(stdout, stderr):
    m = re.search(r"(\d+) rows checked; (\d+) problems", stdout)
    if not m:
        return ["validator printed no summary"]
    return [f"{m.group(2)} contract problems"] if int(m.group(2)) else []


GATES = {"unit_tests": gate_unit_tests, "metrics": gate_metrics, "validator": gate_validator}


def default_stages():
    py = lambda *a: [str(x) for x in a]    # noqa: E731
    return [
        dict(name="unit tests", argv=py("-m", "unittest", "discover", "-s", ROOT / "code" / "tests"), required=True,
             produces=[], gate="unit_tests"),
        dict(name="sample predictions", argv=py(ROOT / "code" / "main.py", "--dataset", "sample"), required=True,
             produces=[str(ROOT / "code" / "cache" / "sample_pred.csv")], gate=None),
        dict(name="sample scoring", argv=py(HERE / "evaluate.py", "--verbose"), required=True, produces=[], gate="metrics"),
        dict(name="output.csv contract", argv=py(HERE / "validate_output.py"), required=True, produces=[], requires=[str(ROOT / "output.csv")],
             gate="validator"),
        dict(name="custom cases", argv=py(HERE / "run_custom.py"), required=True,
             produces=[str(HERE / "custom" / "predicted_output.csv")], gate="metrics"),
        dict(name="adversarial cases", argv=py(HERE / "adversarial" / "run_adversarial.py"), required=True, produces=[], gate=None),
        dict(name="slot validation", argv=py(HERE / "slot_validation.py"), required=True, produces=[], gate=None),
        dict(name="pairing robustness", argv=py(HERE / "pairing_robustness.py"), required=True, produces=[], gate=None),
        dict(name="citation support", argv=py(HERE / "citation_check.py"), required=True, produces=[], gate=None),
        dict(name="identity metrics", argv=py(HERE / "identity_metrics.py"), required=True, produces=[], gate=None),
        dict(name="timing consistency (informational)", argv=py(HERE / "consistency_check.py"), required=False, produces=[], gate=None),
    ]


def run_stage(stage):
    """-> dict(name, required, code, problems, stdout, stderr, seconds). `problems` empty means the stage passed."""
    started = time.time()
    argv = [sys.executable, "-X", "utf8", *stage["argv"]]
    problems, code, out, err = [], None, "", ""
    t0 = time.time()
    try:
        p = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT)
        code, out, err = p.returncode, p.stdout, p.stderr
        if code != 0:
            problems.append(f"exit code {code}")
    except subprocess.TimeoutExpired as e:
        code, out, err = -1, (e.stdout or ""), (e.stderr or "")
        problems.append(f"timed out after {TIMEOUT}s")
    except OSError as e:
        code = -2
        problems.append(f"could not start: {e}")
    for path in stage.get("requires") or []:
        if not Path(path).exists() or Path(path).stat().st_size == 0:
            problems.append(f"required file missing or empty: {Path(path).name}")
    for path in stage.get("produces") or []:
        f = Path(path)
        if not f.exists():
            problems.append(f"expected output missing: {f.name}")
        elif f.stat().st_mtime < t0 - 1:
            problems.append(f"expected output was not (re)written by this run: {f.name}")
        elif f.stat().st_size == 0:
            problems.append(f"expected output is empty: {f.name}")
    gate = stage.get("gate")
    if gate:
        if gate not in GATES:
            problems.append(f"unknown gate {gate!r}")
        elif code == 0:
            problems += GATES[gate](out, err)
    return dict(name=stage["name"], required=bool(stage.get("required", True)), code=code, problems=problems, stdout=out,
                stderr=err, seconds=time.time() - started)


def main(stages=None):
    stages = stages if stages is not None else default_stages()
    results = []
    for st in stages:
        print(f"\n===== {st['name']} ({'required' if st.get('required', True) else 'OPTIONAL'}) =====")
        print("$", " ".join(st["argv"]))
        r = run_stage(st)
        sys.stdout.write(r["stdout"])
        if r["stderr"]:
            sys.stdout.write("--- stderr ---\n" + r["stderr"])
        print(f"--- {'PASS' if not r['problems'] else 'FAIL'}: exit={r['code']} {'; '.join(r['problems'])}")
        results.append(r)
    print("\n===== evaluation summary =====")
    for r in results:
        kind = "required" if r["required"] else "optional"
        print(f"{'PASS' if not r['problems'] else 'FAIL':4s} {kind:8s} exit={r['code']!s:>3s} {r['seconds']:7.1f}s  {r['name']}"
              + (f"  <- {'; '.join(r['problems'])}" if r["problems"] else ""))
    failed = [r for r in results if r["problems"] and r["required"]]
    warned = [r for r in results if r["problems"] and not r["required"]]
    print(f"required stages failed: {len(failed)}; optional stages with problems: {len(warned)}")
    return 1 if failed else 0


if __name__ == "__main__":
    custom = None
    if "--stages-json" in sys.argv:
        custom = json.loads(Path(sys.argv[sys.argv.index("--stages-json") + 1]).read_text(encoding="utf-8"))
    sys.exit(main(custom))
