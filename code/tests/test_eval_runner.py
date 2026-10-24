"""The evaluation runner must fail when anything mandatory fails.   python -m unittest discover -s code/tests -v

The top-level command is exercised as a real subprocess (evaluation/main.py --stages-json) with stages that exit 0, exit
non-zero, crash, time out, skip, or fail to produce their output.
"""
import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
EVAL = TESTS.parent / "evaluation"
sys.path.insert(0, str(EVAL))

import main as runner  # noqa: E402
import validate_output  # noqa: E402

PY = ["-c"]


def stage(name, code, required=True, **kw):
    return dict(name=name, argv=PY + [code], required=required, produces=[], gate=None, **kw)


def top_level(stages):
    """Run the real command on a stage list; -> (exit code, stdout)."""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "stages.json"
        f.write_text(json.dumps(stages), encoding="utf-8")
        p = subprocess.run([sys.executable, "-X", "utf8", str(EVAL / "main.py"), "--stages-json", str(f)],
                           capture_output=True, text=True, encoding="utf-8")
    return p.returncode, p.stdout


class TopLevelExitCode(unittest.TestCase):
    def test_all_passing_stages_exit_zero(self):
        code, out = top_level([stage("a", "print('hello')"), stage("b", "pass")])
        self.assertEqual(code, 0)
        self.assertIn("required stages failed: 0", out)

    def test_a_failing_child_fails_the_run_but_later_stages_still_run_and_are_reported(self):
        code, out = top_level([stage("first", "import sys; print('diagnostic-1'); sys.exit(3)"), stage("second", "print('diagnostic-2')")])
        self.assertEqual(code, 1)
        self.assertIn("diagnostic-1", out)               # output of the failing stage is kept
        self.assertIn("diagnostic-2", out)               # ... and the run continued
        self.assertIn("FAIL required exit=  3", out)
        self.assertIn("required stages failed: 1", out)

    def test_a_crashing_child_keeps_its_traceback(self):
        code, out = top_level([stage("boom", "raise RuntimeError('kaput')")])
        self.assertEqual(code, 1)
        self.assertIn("kaput", out)

    def test_optional_stage_failure_is_reported_but_does_not_fail_the_run(self):
        code, out = top_level([stage("info", "import sys; sys.exit(7)", required=False), stage("main", "pass")])
        self.assertEqual(code, 0)
        self.assertIn("FAIL optional", out)
        self.assertIn("optional stages with problems: 1", out)

    def test_missing_required_output_fails(self):
        missing = str(TESTS / "no_such_output.csv")
        code, out = top_level([dict(stage("writes nothing", "pass"), produces=[missing])])
        self.assertEqual(code, 1)
        self.assertIn("expected output missing", out)

    def test_missing_required_input_fails(self):
        code, out = top_level([dict(stage("needs a file", "pass"), requires=[str(TESTS / "no_such_input.csv")])])
        self.assertEqual(code, 1)
        self.assertIn("required file missing", out)

    def test_unknown_gate_fails(self):
        code, out = top_level([dict(stage("x", "pass"), gate="nonexistent")])
        self.assertEqual(code, 1)

    def test_unstartable_command_fails(self):
        r = runner.run_stage(dict(name="x", argv=[str(TESTS / "no_such_script.py")], required=True))
        self.assertNotEqual(r["code"], 0)
        self.assertTrue(r["problems"])


class Timeouts(unittest.TestCase):
    def test_timeout_is_a_failure_with_its_output_kept(self):
        old, runner.TIMEOUT = runner.TIMEOUT, 1
        try:
            r = runner.run_stage(stage("slow", "import time; print('partial', flush=True); time.sleep(10)"))
        finally:
            runner.TIMEOUT = old
        self.assertTrue(any("timed out" in p for p in r["problems"]))


class Gates(unittest.TestCase):
    def test_skipped_or_empty_unit_tests_fail(self):
        ok = runner.gate_unit_tests("", "Ran 10 tests in 1s\n\nOK\n")
        self.assertEqual(ok, [])
        self.assertTrue(runner.gate_unit_tests("", "Ran 10 tests in 1s\n\nOK (skipped=2)\n"))
        self.assertTrue(runner.gate_unit_tests("", "Ran 0 tests in 0s\n\nOK\n"))
        self.assertTrue(runner.gate_unit_tests("", ""))

    def test_metric_gate(self):
        good = "case_status_accuracy: 1.0000\nstep_precision: 1.0000\nstep_recall: 0.9\n"
        self.assertEqual(runner.gate_metrics(good, ""), [])
        self.assertTrue(runner.gate_metrics(good.replace("case_status_accuracy: 1.0000", "case_status_accuracy: 0.9688"), ""))
        self.assertTrue(runner.gate_metrics(good.replace("step_precision: 1.0000", "step_precision: 0.9900"), ""))
        self.assertTrue(runner.gate_metrics("nothing printed", ""))                 # a skipped scoring step is a failure

    def test_validator_gate(self):
        self.assertEqual(runner.gate_validator("518 rows checked; 0 problems", ""), [])
        self.assertTrue(runner.gate_validator("518 rows checked; 3 problems", ""))
        self.assertTrue(runner.gate_validator("", ""))

    def test_default_stages_are_all_present_and_required_ones_are_marked(self):
        for st in runner.default_stages():
            self.assertTrue(Path(st["argv"][0]).exists() or st["argv"][0] == "-m", st["name"])
            self.assertIn(st["gate"], (None, *runner.GATES), st["name"])
        by = {s["name"]: s for s in runner.default_stages()}
        self.assertTrue(by["unit tests"]["required"] and by["output.csv contract"]["required"] and by["citation support"]["required"])
        self.assertFalse(by["timing consistency (informational)"]["required"])


class OutputValidator(unittest.TestCase):
    def mutated(self, fn):
        root = TESTS.parents[1]
        with open(root / "output.csv", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        fn(rows)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "o.csv"
            with open(path, "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["case_id", "deletion_status", "steps_json"], lineterminator="\n")
                w.writeheader()
                w.writerows(rows)
            return validate_output.validate(path)[1]

    def test_unmodified_output_passes(self):
        self.assertEqual(self.mutated(lambda rows: None), [])

    def test_malformed_json_missing_row_and_bad_status_are_reported(self):
        def bad(rows):
            rows[0]["steps_json"] = "{not json"
            rows[1]["deletion_status"] = "DONE"
            del rows[2]
        errors = " | ".join(self.mutated(bad))
        self.assertIn("not JSON", errors)
        self.assertIn("bad status", errors)
        self.assertIn("case_id mismatch", errors)

    def test_uncited_finding_and_uncited_complete_step_are_reported(self):
        def first_row(rows, status, phase):
            for r in rows:
                steps = json.loads(r["steps_json"])
                if r["deletion_status"] == status and any(x["phase"] == phase for x in steps):
                    return r, steps, next(x for x in steps if x["phase"] == phase)

        def bad(rows):
            for status, phase in (("INCOMPLETE", "FINDING"), ("COMPLETE", "VERIFICATION")):
                r, steps, step = first_row(rows, status, phase)
                step["evidence_id"] = ""
                r["steps_json"] = json.dumps(steps)
        errors = " | ".join(self.mutated(bad))
        self.assertIn("finding at step", errors)
        self.assertIn("COMPLETE verification at step", errors)

    def test_citing_a_file_that_is_not_in_the_manifest_is_reported(self):
        def bad(rows):
            for r in rows:
                steps = json.loads(r["steps_json"])
                if steps and steps[0]["evidence_id"]:
                    steps[0]["evidence_id"] = "evidence_9999"
                    r["steps_json"] = json.dumps(steps)
                    break
        self.assertIn("not in the manifest", " | ".join(self.mutated(bad)))


if __name__ == "__main__":
    unittest.main()
