from __future__ import annotations

import os
import signal
import sys
import unittest
from pathlib import Path

from deep_tests.process_checkpoint import CheckpointAction, run_at_checkpoint


CHILD = r"""
import sys

for index, step in enumerate(("first mutation", "second mutation"), start=1):
    sys.stderr.write(f"interactive: {step}? [y/N] ")
    sys.stderr.flush()
    answer = sys.stdin.readline()
    if answer == "":
        print(f"closed-{index}", file=sys.stderr)
        raise SystemExit(20 + index)
    if answer.strip().lower() not in {"y", "yes"}:
        print(f"declined-{index}", file=sys.stderr)
        raise SystemExit(10 + index)
print("completed", file=sys.stderr)
"""


class ProcessCheckpointTests(unittest.TestCase):
    def run_child(
        self,
        action: CheckpointAction,
        *,
        before_action=None,
    ):
        environment = dict(os.environ)
        environment["TERM"] = "xterm-256color"
        environment["ZED_PKG_FORCE_CI"] = "0"
        return run_at_checkpoint(
            (sys.executable, "-c", CHILD),
            "second mutation",
            action,
            cwd=Path.cwd(),
            env=environment,
            timeout=10,
            before_action=before_action,
        )

    def test_accepts_each_prompt_through_the_checkpoint(self) -> None:
        result = self.run_child(CheckpointAction.ACCEPT)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.prompts, ("first mutation", "second mutation"))
        self.assertIn("completed", result.output)

    def test_decline_fails_closed_at_the_named_checkpoint(self) -> None:
        result = self.run_child(CheckpointAction.DECLINE)
        self.assertEqual(result.returncode, 12)
        self.assertIn("declined-2", result.output)

    def test_terminal_eof_fails_closed_at_the_named_checkpoint(self) -> None:
        result = self.run_child(CheckpointAction.EOF)
        self.assertEqual(result.returncode, 22)
        self.assertIn("closed-2", result.output)

    def test_forced_termination_runs_the_external_kill_hook_first(self) -> None:
        calls: list[str] = []
        result = self.run_child(
            CheckpointAction.KILL,
            before_action=lambda: calls.append("external-owner-killed"),
        )
        self.assertEqual(calls, ["external-owner-killed"])
        self.assertTrue(result.killed)
        self.assertEqual(result.returncode, -signal.SIGKILL)


if __name__ == "__main__":
    unittest.main()
