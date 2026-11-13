"""No-network unit tests for the bounded Codex wrapper (fake executable only)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
RUNNER = ROOT / "scripts" / "run_codex_review.py"

FAKE_CODEX = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
if args == ["--version"]:
    if os.environ.get("FAKE_CODEX_MODE") == "version_fail":
        print("synthetic version failure", file=sys.stderr)
        sys.exit(9)
    print("codex-cli FAKE-TEST")
    sys.exit(0)
mode = os.environ["FAKE_CODEX_MODE"]
if mode == "abnormal":
    sys.exit(7)
if mode == "ignore_sigint":
    import signal
    import time
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    while True:
        time.sleep(0.5)
output = Path(args[args.index("-o") + 1])
output.write_text("REVIEW_OK\n", encoding="utf-8")
thread_id = "00000000-0000-0000-0000-00000000abcd"
print(json.dumps({"type": "thread.started", "thread_id": thread_id}), flush=True)
codex_home = Path(os.environ["CODEX_HOME"])
session_dir = codex_home / "sessions"
session_dir.mkdir(parents=True, exist_ok=True)
data = {"type": "turn_context", "payload": {
    "model": "wrong-model" if mode == "wrong_model" else "gpt-6.1-sol",
    "effort": "max",
    "sandbox_policy": {"type": "read-only"},
    "approval_policy": "never",
}}
(session_dir / ("rollout-" + thread_id + ".jsonl")).write_text(
    json.dumps(data) + "\n", encoding="utf-8"
)
if mode != "missing_completed":
    print(json.dumps({"type": "turn.completed"}), flush=True)
'''


@unittest.skipUnless(os.name == "posix", "fake codex script uses POSIX executable mode")
class CodexRunnerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="codex-wrapper-test-")
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        fake_path = bin_dir / "codex"
        fake_path.write_text(FAKE_CODEX, encoding="utf-8")
        fake_path.chmod(0o700)
        self.env = os.environ.copy()
        self.env["PATH"] = str(bin_dir) + os.pathsep + self.env.get("PATH", "")
        self.env["CODEX_HOME"] = str(self.tmp / "codex-home")
        self.prompt = self.tmp / "prompt.txt"
        self.prompt.write_text("Return one short review. No tools.", encoding="utf-8")

    def execute(self, mode, label="test"):
        env = dict(self.env, FAKE_CODEX_MODE=mode)
        result = subprocess.run(
            [sys.executable, str(RUNNER), "--prompt-file", str(self.prompt),
             "--label", label, "--timeout", "10"],
            cwd=ROOT, env=env, text=True, capture_output=True, timeout=35,
            check=False,
        )
        self.assertTrue(result.stdout.strip(), result.stderr)
        report = json.loads(result.stdout.strip().splitlines()[-1])
        return result, report

    def test_completed_and_verified(self):
        process, report = self.execute("completed")
        self.assertEqual(process.returncode, 0)
        self.assertEqual(report["return_code"], 0)
        self.assertEqual(report["termination"], "normal_exit")
        self.assertTrue(report["final_message_present"])
        self.assertTrue(report["turn_completed"])
        self.assertTrue(report["settings_verified"])

    def test_exit_zero_without_turn_completed_is_not_success(self):
        process, report = self.execute("missing_completed")
        self.assertEqual(process.returncode, 1)
        self.assertEqual(report["return_code"], 0)
        self.assertFalse(report["turn_completed"])

    def test_model_mismatch_is_not_success(self):
        process, report = self.execute("wrong_model")
        self.assertEqual(process.returncode, 1)
        self.assertTrue(report["turn_completed"])
        self.assertFalse(report["settings_verified"])

    def test_abnormal_exit_is_not_success(self):
        process, report = self.execute("abnormal")
        self.assertEqual(process.returncode, 1)
        self.assertEqual(report["return_code"], 7)
        self.assertEqual(report["termination"], "abnormal_exit")

    def test_cli_preflight_failure_is_logged_not_unhandled(self):
        process, report = self.execute("version_fail")
        self.assertEqual(process.returncode, 1)
        self.assertEqual(report["termination"], "wrapper_error")
        self.assertEqual(report["exception_class"], "RuntimeError")
        self.assertIsNone(report["cli_version"])
        self.assertFalse(report["turn_completed"])

    def test_forced_timeout_is_not_a_success(self):
        process, report = self.execute("ignore_sigint")
        self.assertEqual(process.returncode, 1)
        self.assertEqual(report["termination"], "wrapper_timeout_sigkill")
        self.assertEqual(report["signal"], 9)
        self.assertFalse(report["turn_completed"])

    def test_same_second_repeated_label_gets_unique_directories(self):
        _, first = self.execute("completed", label="same-label")
        _, second = self.execute("completed", label="same-label")
        self.assertNotEqual(first["run_dir"], second["run_dir"])


if __name__ == "__main__":
    unittest.main()