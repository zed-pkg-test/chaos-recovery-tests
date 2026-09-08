#!/usr/bin/env python3
"""Black-box interactive uninstall and crash-recovery certification for Zed."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deep_tests.process_checkpoint import (  # noqa: E402
    CheckpointAction,
    CheckpointResult,
    run_at_checkpoint,
)


ROOT_MANIFEST = """[package]
org = "lock-test"
name = "workspace-root"
version = "1.0.0"
description = "interactive crash recovery root"
license = "MIT"

[package.repository]
vcs = "git"
url = "https://example.invalid/lock-test/workspace-root"

[workspace]
members = ["packages/*"]

[dependencies]
"lock-test/member" = "^1"

[install]
adapter = "none"
"""

MEMBER_MANIFEST = """[package]
org = "lock-test"
name = "member"
version = "1.2.3"
description = "workspace member used by the crash recovery adapter"
license = "MIT"

[package.repository]
vcs = "git"
url = "https://example.invalid/lock-test/member"

[publish]
exclude = []
"""

PAYLOAD = b"deterministic workspace payload\n"
COMMIT = re.compile(r"[0-9a-f]{40}")
CONTAINER_ID = re.compile(r"[0-9a-f]{12,64}")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require_uuid_v4(value: str) -> uuid.UUID:
    parsed = uuid.UUID(value)
    if parsed.version != 4 or str(parsed) != value:
        raise AssertionError(f"transaction directory is not canonical UUID-v4: {value!r}")
    return parsed


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class Runtime:
    def __init__(
        self,
        *,
        mode: str,
        zed: Path,
        image: str | None,
        project: Path,
        home: Path,
        scratch: Path,
        timeout: float,
    ) -> None:
        self.mode = mode
        self.zed = zed
        self.image = image
        self.project = project
        self.home = home
        self.scratch = scratch
        self.timeout = timeout

    def environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        for key in tuple(environment):
            if key.startswith("ZED_PKG_CONTEXT_"):
                environment.pop(key)
        environment.update(
            {
                "HOME": str(self.home),
                "TERM": "xterm-256color",
                "ZED_PKG_ADAPTER": "none",
                "ZED_PKG_FORCE_CI": "0",
                "ZED_PKG_HOME": str(self.home),
                "ZED_PKG_INSTALL_MODE": "copy",
            }
        )
        return environment

    def command(
        self,
        arguments: list[str],
        *,
        interactive: bool,
        stdin_open: bool = False,
        cidfile: Path | None = None,
        cwd: Path | None = None,
    ) -> list[str]:
        if self.mode == "host":
            return [str(self.zed), *arguments]
        if self.image is None:
            raise AssertionError("OCI mode requires an image")
        command = ["docker", "run", "--rm"]
        if interactive:
            command.extend(["--interactive", "--tty"])
        elif stdin_open:
            command.append("--interactive")
        if cidfile is not None:
            command.extend(["--cidfile", str(cidfile)])
        host_workdir = (cwd or self.project).resolve()
        try:
            relative_workdir = host_workdir.relative_to(self.project.resolve())
        except ValueError as error:
            raise AssertionError(
                f"OCI working directory is outside the mounted project: {host_workdir}"
            ) from error
        container_workdir = Path("/work") / relative_workdir
        command.extend(
            [
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=67108864",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--mount",
                f"type=bind,src={self.project},dst=/work",
                "--mount",
                f"type=bind,src={self.home},dst=/zed-home",
                "--workdir",
                container_workdir.as_posix(),
                "--env",
                "HOME=/zed-home",
                "--env",
                "TERM=xterm-256color",
                "--env",
                "ZED_PKG_ADAPTER=none",
                "--env",
                "ZED_PKG_FORCE_CI=0",
                "--env",
                "ZED_PKG_HOME=/zed-home",
                "--env",
                "ZED_PKG_INSTALL_MODE=copy",
                self.image,
                *arguments,
            ]
        )
        return command

    def run(
        self,
        arguments: list[str],
        *,
        cwd: Path | None = None,
        input_text: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            self.command(
                arguments,
                interactive=False,
                stdin_open=input_text is not None,
                cwd=cwd,
            ),
            cwd=cwd or self.project,
            env=self.environment(),
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=self.timeout,
            check=False,
        )
        if check and completed.returncode != 0:
            raise AssertionError(
                f"command failed with {completed.returncode}: {completed.args!r}\n"
                f"{completed.stdout[-4_000:]}"
            )
        return completed

    def at_checkpoint(
        self,
        checkpoint: str,
        action: CheckpointAction,
        *,
        arguments: list[str] | None = None,
        cwd: Path | None = None,
    ) -> CheckpointResult:
        cidfile = None
        before_action = None
        if self.mode == "oci" and action is CheckpointAction.KILL:
            cidfile = self.scratch / f"container-{uuid.uuid4()}.cid"

            def kill_container() -> None:
                if not cidfile.is_file():
                    raise AssertionError(f"Docker did not publish its cidfile: {cidfile}")
                container_id = cidfile.read_text(encoding="utf-8").strip()
                if not CONTAINER_ID.fullmatch(container_id):
                    raise AssertionError(f"invalid Docker container id: {container_id!r}")
                killed = subprocess.run(
                    ["docker", "kill", "--signal", "KILL", container_id],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=30,
                    check=False,
                )
                if killed.returncode != 0:
                    raise AssertionError(
                        f"could not kill checkpoint container {container_id}: {killed.stdout}"
                    )

            before_action = kill_container

        return run_at_checkpoint(
            self.command(
                arguments or ["uninstall", "--interactive"],
                interactive=True,
                cidfile=cidfile,
                cwd=cwd,
            ),
            checkpoint,
            action,
            cwd=cwd or self.project,
            env=self.environment(),
            timeout=self.timeout,
            before_action=before_action,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=("host", "oci"))
    parser.add_argument("--oci-image")
    parser.add_argument("--oci-base-digest")
    parser.add_argument("--cli-commit", required=True)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=120)
    arguments = parser.parse_args()

    if not COMMIT.fullmatch(arguments.cli_commit):
        parser.error("--cli-commit must be an exact 40-character lowercase commit")
    if arguments.mode == "oci" and not arguments.oci_image:
        parser.error("--oci-image is required in OCI mode")
    if arguments.mode == "host" and not arguments.zed.is_file():
        parser.error(f"--zed is not a file: {arguments.zed}")

    parent = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
    run_id = uuid.uuid4()
    scratch = parent / f"zed-cli-crash-recovery-{arguments.mode}-{run_id}"
    project = scratch / "project"
    member = project / "packages" / "member"
    home = scratch / "home"
    member.mkdir(parents=True)
    home.mkdir(parents=True)
    (project / ".zpkg.toml").write_text(ROOT_MANIFEST, encoding="utf-8")
    (member / ".zpkg.toml").write_text(MEMBER_MANIFEST, encoding="utf-8")
    (member / "payload.txt").write_bytes(PAYLOAD)

    evidence: dict[str, object] = {
        "schema": "zed-cli-interactive-crash-recovery/v1",
        "status": "running",
        "mode": arguments.mode,
        "platform": platform.platform(),
        "run_uuid": str(run_id),
        "cli_commit": arguments.cli_commit,
        "oci_image": arguments.oci_image,
        "oci_base_digest": arguments.oci_base_digest,
        "checkpoints": {},
        "fixture": {
            "package": "lock-test/member@1.2.3",
            "payload_sha256": digest(PAYLOAD),
        },
    }

    runtime = Runtime(
        mode=arguments.mode,
        zed=arguments.zed.resolve(),
        image=arguments.oci_image,
        project=project,
        home=home,
        scratch=scratch,
        timeout=arguments.timeout,
    )
    projection = project / "zed_modules" / "lock-test" / "member" / "payload.txt"
    lockfile = project / ".zpkg.lock"
    staging = project / ".zpkg-staging"
    checkpoints = evidence["checkpoints"]
    assert isinstance(checkpoints, dict)

    try:
        version = runtime.run(["--version"]).stdout.strip()
        evidence["zed_version"] = version

        runtime.run(["install"])
        if projection.read_bytes() != PAYLOAD:
            raise AssertionError("initial install did not materialize the exact workspace payload")
        original_lock = lockfile.read_bytes()
        checkpoints["initial_install"] = {
            "lock_sha256": digest(original_lock),
            "projection_sha256": digest(projection.read_bytes()),
        }

        redirected = runtime.run(
            ["uninstall", "--interactive"],
            input_text="yes\n",
            check=False,
        )
        if redirected.returncode == 0 or "requires terminal stdin and stderr" not in redirected.stdout:
            raise AssertionError("redirected input did not fail closed in interactive mode")
        if staging.exists() or projection.read_bytes() != PAYLOAD or lockfile.read_bytes() != original_lock:
            raise AssertionError("redirected interactive attempt mutated project state")
        checkpoints["redirected_input"] = {
            "returncode": redirected.returncode,
            "failed_closed": True,
            "state_unchanged": True,
        }

        declined = runtime.at_checkpoint("uninstall 1 package(s)", CheckpointAction.DECLINE)
        if declined.returncode == 0 or "confirmation declined" not in declined.output:
            raise AssertionError("declining the coarse uninstall checkpoint did not fail closed")
        if staging.exists() or projection.read_bytes() != PAYLOAD or lockfile.read_bytes() != original_lock:
            raise AssertionError("declined uninstall mutated project state")
        checkpoints["decline"] = {
            "returncode": declined.returncode,
            "prompts": list(declined.prompts),
            "state_unchanged": True,
        }

        closed = runtime.at_checkpoint("uninstall 1 package(s)", CheckpointAction.EOF)
        if closed.returncode == 0 or "confirmation closed" not in closed.output:
            raise AssertionError("terminal EOF at the coarse uninstall checkpoint did not fail closed")
        if staging.exists() or projection.read_bytes() != PAYLOAD or lockfile.read_bytes() != original_lock:
            raise AssertionError("terminal EOF during interactive uninstall mutated project state")
        checkpoints["terminal_eof"] = {
            "returncode": closed.returncode,
            "prompts": list(closed.prompts),
            "state_unchanged": True,
        }

        interrupted = runtime.at_checkpoint(
            "unmaterialize lock-test/member",
            CheckpointAction.KILL,
        )
        if not interrupted.killed or interrupted.returncode == 0:
            raise AssertionError("checkpoint did not forcefully terminate the uninstall owner")
        if projection.exists():
            raise AssertionError("uninstall owner was terminated before staged mutation became observable")
        if lockfile.read_bytes() != original_lock:
            raise AssertionError("interrupted uninstall changed the retained lockfile")
        transaction_roots = sorted(path for path in staging.iterdir() if path.is_dir())
        if len(transaction_roots) != 1:
            raise AssertionError(f"expected one pending transaction, found {transaction_roots}")
        transaction_id = require_uuid_v4(transaction_roots[0].name)
        metadata = json.loads(
            (transaction_roots[0] / "transaction.json").read_text(encoding="utf-8")
        )
        if metadata.get("id") != str(transaction_id) or metadata.get("state") != "active":
            raise AssertionError(f"invalid pending transaction metadata: {metadata}")
        if "zed_modules" not in {entry.get("relative") for entry in metadata.get("entries", [])}:
            raise AssertionError("pending transaction did not journal the materialized projection")
        checkpoints["forced_termination"] = {
            "returncode": interrupted.returncode,
            "prompts": list(interrupted.prompts),
            "transaction_uuid": str(transaction_id),
            "transaction_uuid_version": transaction_id.version,
            "transaction_state": metadata["state"],
            "projection_staged": True,
            "lock_unchanged": True,
        }

        recovered = runtime.at_checkpoint("uninstall 1 package(s)", CheckpointAction.DECLINE)
        recovery_marker = f"recovered interrupted zed transaction {transaction_id}"
        if recovery_marker not in recovered.output:
            raise AssertionError(f"restart did not report exact recovery marker: {recovered.output}")
        if staging.exists() or projection.read_bytes() != PAYLOAD or lockfile.read_bytes() != original_lock:
            raise AssertionError("restart recovery did not restore the exact pre-transaction tree")
        checkpoints["startup_recovery"] = {
            "returncode": recovered.returncode,
            "transaction_uuid": str(transaction_id),
            "diagnostic_observed": True,
            "projection_restored": True,
            "staging_removed": True,
            "lock_unchanged": True,
        }

        committed = runtime.at_checkpoint(
            "record 0 remaining installed package(s) and commit transaction",
            CheckpointAction.ACCEPT,
        )
        if committed.returncode != 0 or projection.exists() or staging.exists():
            raise AssertionError("accepted interactive uninstall did not commit cleanly")
        if lockfile.read_bytes() != original_lock:
            raise AssertionError("committed uninstall did not retain the exact lockfile")
        checkpoints["accepted_uninstall"] = {
            "returncode": committed.returncode,
            "prompts": list(committed.prompts),
            "projection_removed": True,
            "staging_removed": True,
            "lock_unchanged": True,
        }

        runtime.run(["install", "--frozen"])
        if projection.read_bytes() != PAYLOAD or lockfile.read_bytes() != original_lock:
            raise AssertionError("frozen reinstall did not restore the exact projection and lock")
        checkpoints["frozen_reinstall"] = {
            "projection_sha256": digest(projection.read_bytes()),
            "lock_sha256": digest(lockfile.read_bytes()),
            "exact_restoration": True,
        }
        evidence["status"] = "passed"
        print(
            f"certified {arguments.mode} interactive crash recovery "
            f"transaction={transaction_id} cli={arguments.cli_commit}"
        )
        return 0
    except BaseException as error:
        evidence["status"] = "failed"
        evidence["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        atomic_json(arguments.evidence, evidence)


if __name__ == "__main__":
    raise SystemExit(main())
