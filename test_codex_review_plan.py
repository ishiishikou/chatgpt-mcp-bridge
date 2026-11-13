"""No-network plan checkpoint/recovery tests using a synthetic Codex executable."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from scripts.run_codex_review_plan import stage_process_state
from test_codex_review_runner import FAKE_CODEX

ROOT = Path(__file__).resolve().parent
RUN_PLAN = ROOT / "scripts" / "run_codex_review_plan.py"
RUNS = ROOT / ".review-runs"


class CodexPlanTests(unittest.TestCase):
    def setUp(self):
        RUNS.mkdir(exist_ok=True, mode=0o700)
        t = tempfile.TemporaryDirectory(dir=RUNS, prefix="plan-fixture-")
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.state_dir = self.root / "state"
        self.prompt1 = self.root / "first.txt"
        self.prompt2 = self.root / "second.txt"
        self.prompt1.write_text("Review only stage one. No tools.", encoding="utf-8")
        self.prompt2.write_text("Review only stage two. No tools.", encoding="utf-8")
        self.plan = self.root / "manifest.json"
        self.plan.write_text(json.dumps({"steps": [
            {"id": "first", "prompt_file": "first.txt", "timeout": 10},
            {"id": "second", "prompt_file": "second.txt", "timeout": 10},
        ]}), encoding="utf-8")
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        binary = bin_dir / "codex"
        binary.write_text(FAKE_CODEX, encoding="utf-8")
        binary.chmod(0o700)
        self.env = dict(os.environ)
        self.env["PATH"] = str(bin_dir) + os.pathsep + self.env.get("PATH", "")
        self.env["CODEX_HOME"] = str(self.root / "codex-home")

    def invoke(self, mode="completed", *flags, plan=None):
        env = dict(self.env, FAKE_CODEX_MODE=mode)
        return subprocess.run(
            [sys.executable, str(RUN_PLAN),
             "--plan", str(plan or self.plan),
             "--state-dir", str(self.state_dir), *flags],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=45,
            check=False,
        )

    def state(self):
        return json.loads((self.state_dir / "state.json").read_text())

    def test_two_steps_success_and_resume_idempotent(self):
        a = self.invoke()
        self.assertEqual(a.returncode, 0, a.stderr)
        state = self.state()
        self.assertEqual(state["status"], "complete")
        self.assertEqual([s["status"] for s in state["steps"]],
                         ["completed", "completed"])
        outputs = [s["output"] for s in state["steps"]]
        b = self.invoke("abnormal", "--resume")
        self.assertEqual(b.returncode, 0, b.stderr)
        self.assertEqual([s["output"] for s in self.state()["steps"]], outputs)
        self.assertEqual([s["attempts"] for s in self.state()["steps"]], [1, 1])

    def test_existing_state_without_resume_refused(self):
        self.assertEqual(self.invoke().returncode, 0)
        self.assertEqual(self.invoke().returncode, 2)

    def test_failed_stage_stops_without_retry_and_requires_explicit_resume(self):
        a = self.invoke("abnormal")
        self.assertEqual(a.returncode, 1)
        self.assertEqual([s["status"] for s in self.state()["steps"]],
                         ["failed", "pending"])
        self.assertEqual(self.invoke("completed", "--resume").returncode, 2)
        self.assertEqual(self.invoke("completed", "--resume",
                                     "--retry-incomplete").returncode, 0)
        self.assertEqual([s["attempts"] for s in self.state()["steps"]], [2, 1])

    def test_recovery_of_completed_runner_output_without_second_execution(self):
        self.assertEqual(self.invoke().returncode, 0)
        file = self.state_dir / "state.json"
        state = self.state()
        state["steps"][0].update(status="running", pid=987654321)
        state["status"] = "incomplete"
        file.write_text(json.dumps(state), encoding="utf-8")
        a = self.invoke("abnormal", "--resume")
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertTrue(self.state()["steps"][0]["recovered"])
        self.assertEqual(self.state()["steps"][0]["attempts"], 1)

    def test_prompt_mutation_refuses_resume(self):
        self.assertEqual(self.invoke().returncode, 0)
        self.prompt1.write_text("Changed scope!", encoding="utf-8")
        self.assertEqual(self.invoke("completed", "--resume").returncode, 2)

    def test_corrupt_metadata_cannot_be_resumed_as_success(self):
        self.assertEqual(self.invoke().returncode, 0)
        state = self.state()
        out = Path(state["steps"][0]["output"])
        recorded = json.loads(out.read_text().splitlines()[-1])
        (Path(recorded["run_dir"]) / "metadata.json").write_text("[]")
        self.assertEqual(self.invoke("completed", "--resume").returncode, 2)

    def test_unverified_turn_completion_is_failure(self):
        a = self.invoke("missing_completed")
        self.assertEqual(a.returncode, 1)
        self.assertEqual([s["status"] for s in self.state()["steps"]],
                         ["failed", "pending"])

    def test_invalid_plan_path_and_large_prompt_rejected(self):
        outside = self.root.parent.parent / "outside-review.txt"
        self.plan.write_text(json.dumps({"steps":[
            {"id": "bad", "prompt_file": str(outside), "timeout": 10},
        ]}), encoding="utf-8")
        self.assertEqual(self.invoke().returncode, 2)
        self.prompt1.write_text("x" * 12001, encoding="utf-8")
        self.plan.write_text(json.dumps({"steps":[
            {"id": "large", "prompt_file": "first.txt", "timeout": 10},
        ]}), encoding="utf-8")
        self.assertEqual(self.invoke().returncode, 2)

    def test_lock_prevents_parallel_execution(self):
        self.state_dir.mkdir(mode=0o700)
        with (self.state_dir / "lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.invoke()
            self.assertEqual(result.returncode, 2)
            self.assertIn("locked", result.stderr)

    def test_orphaned_prior_stage_blocks_explicit_retry(self):
        self.assertEqual(self.invoke("abnormal").returncode, 1)
        state = self.state()
        token = state["steps"][0]["token"]
        state["steps"][0].update(status="running", pid=None)
        (self.state_dir / "state.json").write_text(json.dumps(state))
        env = dict(self.env, CODEX_REVIEW_STAGE_TOKEN=token)
        orphan = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(25)", "codex-stub"],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            result = self.invoke("completed", "--resume", "--retry-incomplete")
            self.assertEqual(result.returncode, 2)
            self.assertIn("status unknown", result.stderr)
            self.assertEqual(self.state()["steps"][0]["attempts"], 1)
        finally:
            orphan.terminate()
            orphan.wait(timeout=5)
        self.assertEqual(self.invoke("completed", "--resume",
                                     "--retry-incomplete").returncode, 0)

    def test_unreadable_process_environment_is_unknown_not_gone(self):
        with tempfile.TemporaryDirectory(dir=RUNS, prefix="proc-fixture-") as tmp:
            proc = Path(tmp) / "4242"
            proc.mkdir()
            (proc / "environ").write_bytes(b"KEY=VALUE" + bytes([0]))
            with mock.patch("pathlib.Path.read_bytes", side_effect=PermissionError):
                self.assertEqual(stage_process_state("a" * 32, Path(tmp)), "unknown")

    def test_unknown_state_does_not_auto_retry(self):
        self.assertEqual(self.invoke().returncode, 0)
        state = self.state()
        state["steps"][0].update(status="unknown", pid=987654321)
        (self.state_dir / "state.json").write_text(json.dumps(state))
        # Existing verified proof can still be recovered, even after a parent crash.
        self.assertEqual(self.invoke("completed", "--resume").returncode, 0)
        state = self.state()
        state["steps"][0].update(status="unknown", output="missing.jsonl",
                                  pid=987654321)
        (self.state_dir / "state.json").write_text(json.dumps(state))
        self.assertEqual(self.invoke("completed", "--resume",
                                     "--retry-incomplete").returncode, 2)


if __name__ == "__main__":
    unittest.main()