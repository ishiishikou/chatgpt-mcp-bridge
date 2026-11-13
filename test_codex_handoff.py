"""Offline synthetic fixtures for the independent Codex termination decision gate."""
import json
from pathlib import Path
import tempfile
import unittest

from scripts.check_codex_handoff import check
from scripts.run_codex_review_plan import LOGS


class CodexHandoffTests(unittest.TestCase):
    def setUp(self):
        LOGS.mkdir(mode=0o700, exist_ok=True)
        t = tempfile.TemporaryDirectory(dir=LOGS, prefix="handoff-fixture-")
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.run_dir = self.root / "codex-run"
        self.run_dir.mkdir()
        self.state = self.root / "state"
        self.state.mkdir()
        self.out = self.root / "runner-output.jsonl"
        record = {
            "run_dir": str(self.run_dir),
            "termination": "normal_exit",
            "return_code": 0,
            "final_message_present": True,
            "turn_completed": True,
            "settings_verified": True,
            "thread_id": "synthetic-test-thread",
            "prompt_sha256": "synthetic-prompt-hash",
            "observed_model": "gpt-6.1-sol",
            "observed_effort": "max",
            "observed_sandbox": {"type": "read-only"},
            "observed_approval": "never",
        }
        self.out.write_text(json.dumps(record) + "\n")
        (self.run_dir / "metadata.json").write_text(json.dumps(record))
        self.report = {
            "outcome": "blocked_external",
            "completed": ["All safe offline fixes"],
            "verified": ["CI passed; synthetic test fixture here"],
            "unverified": ["real ChatGPT host display"],
            "safe_work_remaining": [],
            "user_action_required": ["Authorization for live send test"],
            "stop_reason": "Live host proof requires separate approval",
        }
        self.write_report()
        self.steps = [{"id": "closure", "status": "completed",
                       "prompt_sha256": "synthetic-prompt-hash",
                       "output": str(self.out)}]
        self.write_state()

    def write_report(self):
        (self.run_dir / "final.txt").write_text(json.dumps(self.report))

    def write_state(self, status="complete"):
        (self.state / "state.json").write_text(json.dumps(
            {"status": status, "steps": self.steps}))

    def test_blocked_external_with_concrete_action_is_valid(self):
        result, code = check(self.state)
        self.assertEqual(code, 0)
        self.assertEqual(result["gate"], "blocked_external")
        self.assertTrue(result["user_action_required"])

    def test_scoped_completion_allowed_without_claiming_e2e(self):
        self.report.update(outcome="scoped_complete",
                           user_action_required=[],
                           stop_reason="Scoped offline implementation is complete")
        self.write_report()
        result, code = check(self.state)
        self.assertEqual(code, 0)
        self.assertEqual(result["gate"], "scoped_complete")

    def test_remaining_safe_work_prevents_handoff(self):
        self.report.update(outcome="continue_safe_work",
                           safe_work_remaining=["Add collision test"])
        self.write_report()
        result, code = check(self.state)
        self.assertEqual(code, 2)
        self.assertEqual(result["safe_work_remaining"], ["Add collision test"])

    def test_contradictory_safe_work_prevents_handoff(self):
        self.report["safe_work_remaining"] = ["Could still run offline test"]
        self.write_report()
        _, code = check(self.state)
        self.assertEqual(code, 2)

    def test_external_block_must_name_action(self):
        self.report["user_action_required"] = []
        self.write_report()
        with self.assertRaisesRegex(ValueError, "concrete user action"):
            check(self.state)

    def test_incomplete_plan_is_not_completion(self):
        self.write_state(status="incomplete")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            check(self.state)

    def test_missing_independent_closure_stage_rejected(self):
        self.steps[0]["id"] = "security"
        self.write_state()
        with self.assertRaisesRegex(ValueError, "closure"):
            check(self.state)

    def test_forged_success_missing_turn_completed_rejected(self):
        record = json.loads((self.run_dir / "metadata.json").read_text())
        record["turn_completed"] = False
        (self.run_dir / "metadata.json").write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, "review proof missing"):
            check(self.state)

    def test_malformed_or_non_json_model_answer_is_rejected(self):
        (self.run_dir / "final.txt").write_text("Everything looks fine!")
        with self.assertRaises(ValueError):
            check(self.state)

    def test_missing_explanation_is_rejected(self):
        self.report["stop_reason"] = ""
        self.write_report()
        with self.assertRaisesRegex(ValueError, "stop reason"):
            check(self.state)


if __name__ == "__main__":
    unittest.main()