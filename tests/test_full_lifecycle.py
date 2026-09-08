from __future__ import annotations

import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zed_cli_crash_recovery import Runtime  # noqa: E402
from zed_cli_full_lifecycle import (  # noqa: E402
    global_args,
    parse_json_output,
    require_prompt_sequence,
    run_interactive,
    transaction_ids,
    tree_digest,
    validate_declined_r2g_workspace,
)


class FullLifecycleContractTests(unittest.TestCase):
    def test_prompt_sequence_allows_unrelated_diagnostics_but_preserves_order(self) -> None:
        require_prompt_sequence(
            ("first mutation", "recovery diagnostic", "second mutation"),
            ("first", "second"),
        )
        with self.assertRaisesRegex(AssertionError, "was not observed in order"):
            require_prompt_sequence(("second mutation", "first mutation"), ("first", "second"))

    def test_transaction_ids_accept_only_canonical_uuid_v4_values(self) -> None:
        transaction = str(uuid.uuid4())
        self.assertEqual(
            transaction_ids((f"commit transaction {transaction}",)),
            [transaction],
        )
        version_one = str(uuid.uuid1())
        self.assertEqual(transaction_ids((f"transaction {version_one}",)), [])

    def test_json_output_ignores_leading_diagnostics(self) -> None:
        payload = {"version": "1.2.3", "changes": []}
        output = "diagnostic: synthetic repository\n" + json.dumps(payload, indent=2)
        self.assertEqual(parse_json_output(output), payload)

    def test_tree_digest_tracks_bytes_and_structure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            empty = tree_digest(root)
            (root / "nested").mkdir()
            (root / "nested" / "payload.txt").write_text("first", encoding="utf-8")
            first = tree_digest(root)
            (root / "nested" / "payload.txt").write_text("second", encoding="utf-8")
            second = tree_digest(root)
            self.assertEqual(len({empty, first, second}), 3)

    def test_declined_r2g_workspace_requires_one_uuid_v4_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / f"zed-r2g-{uuid.uuid4()}"
            workspace.mkdir()
            self.assertEqual(validate_declined_r2g_workspace(root), workspace.name[-36:])
            (root / f"zed-r2g-{uuid.uuid4()}").mkdir()
            with self.assertRaisesRegex(AssertionError, "exactly one"):
                validate_declined_r2g_workspace(root)

    def test_oci_runtime_rejects_workdirs_outside_the_mounted_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            home = root / "home"
            project.mkdir()
            home.mkdir()
            runtime = Runtime(
                mode="oci",
                zed=root / "unused-zed",
                image="example.invalid/zed:test",
                project=project,
                home=home,
                scratch=root,
                timeout=1,
            )
            with self.assertRaisesRegex(AssertionError, "outside the mounted project"):
                runtime.command(["--version"], interactive=False, cwd=root / "outside")

    def test_global_arguments_are_valid_after_a_subcommand(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            home = root / "home"
            registry = root / "registry"
            project.mkdir()
            home.mkdir()
            registry.mkdir()
            runtime = Runtime(
                mode="host",
                zed=root / "zed",
                image=None,
                project=project,
                home=home,
                scratch=root,
                timeout=1,
            )
            arguments = global_args(runtime, registry)
            self.assertEqual(arguments[0], "--registry")
            self.assertIn(str(home.resolve()), arguments)

    def test_interactive_adapter_rejects_an_empty_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = Runtime(
                mode="host",
                zed=root / "zed",
                image=None,
                project=root,
                home=root,
                scratch=root,
                timeout=1,
            )
            with self.assertRaisesRegex(AssertionError, "must name"):
                run_interactive(
                    runtime,
                    root / "registry",
                    (),
                    cwd=root,
                    checkpoint="unused",
                    action=None,  # type: ignore[arg-type]
                )


if __name__ == "__main__":
    unittest.main()
