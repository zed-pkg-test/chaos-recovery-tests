#!/usr/bin/env python3
"""Certify the current Zed CLI lifecycle through real interactive terminals."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import uuid
from pathlib import Path
from typing import Iterable, Sequence

from zed_cli_crash_recovery import Runtime, atomic_json, digest, require_uuid_v4

from deep_tests.process_checkpoint import CheckpointAction, CheckpointResult


PACKAGE_ORG = "zed-pkg-test"
PACKAGE_NAME = "interactive-lifecycle"
PACKAGE_VERSION = "1.0.0"
PACKAGE_SPEC = f"{PACKAGE_ORG}/{PACKAGE_NAME}@{PACKAGE_VERSION}"
PAYLOAD = b"zed interactive lifecycle payload\n"
COMMIT = re.compile(r"[0-9a-f]{40}")
TRANSACTION = re.compile(r"\btransaction ([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\b")

PACKAGE_MANIFEST = f'''[package]
org = "{PACKAGE_ORG}"
name = "{PACKAGE_NAME}"
version = "{PACKAGE_VERSION}"
description = "black-box interactive lifecycle fixture"
license = "MIT"

[package.repository]
vcs = "git"
url = "https://github.com/zed-pkg-test/chaos-recovery-tests"

[publish]
smoke_test = 'test -f "$ZED_PKG_TEST_TARGET/payload.txt" && grep -qx "zed interactive lifecycle payload" "$ZED_PKG_TEST_TARGET/payload.txt"'
'''

CONSUMER_MANIFEST = f'''[package]
org = "zed-pkg-test"
name = "interactive-lifecycle-consumer"
version = "0.0.0"
description = "isolated consumer for the interactive lifecycle fixture"
license = "MIT"

[package.repository]
vcs = "git"
url = "https://github.com/zed-pkg-test/chaos-recovery-tests"

[install]
dir = "zed_modules"

[dependencies]
"{PACKAGE_ORG}/{PACKAGE_NAME}" = "={PACKAGE_VERSION}"
'''


def tree_digest(root: Path) -> str:
    rows: list[tuple[str, str, str]] = []
    if root.exists():
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                rows.append((relative, "symlink", os.readlink(path)))
            elif path.is_file():
                rows.append((relative, "file", digest(path.read_bytes())))
            elif path.is_dir():
                rows.append((relative, "directory", ""))
    encoded = json.dumps(rows, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def require_prompt_sequence(prompts: Sequence[str], fragments: Sequence[str]) -> None:
    cursor = 0
    for fragment in fragments:
        while cursor < len(prompts) and fragment not in prompts[cursor]:
            cursor += 1
        if cursor == len(prompts):
            raise AssertionError(
                f"interactive prompt fragment {fragment!r} was not observed in order: {prompts!r}"
            )
        cursor += 1


def transaction_ids(prompts: Iterable[str]) -> list[str]:
    values = [match.group(1) for prompt in prompts for match in TRANSACTION.finditer(prompt)]
    for value in values:
        require_uuid_v4(value)
    return values


def parse_json_output(output: str) -> object:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for start in range(len(lines)):
        try:
            return json.loads("\n".join(lines[start:]))
        except json.JSONDecodeError:
            continue
    raise AssertionError(f"release plan did not emit JSON: {output[-2_000:]}")


def run_git(source: Path, arguments: Sequence[str]) -> None:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=source,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(f"synthetic git command failed: {completed.args!r}\n{completed.stdout}")


def initialize_source(source: Path) -> None:
    source.mkdir(parents=True)
    (source / ".zpkg.toml").write_text(PACKAGE_MANIFEST, encoding="utf-8")
    (source / "payload.txt").write_bytes(PAYLOAD)
    run_git(source, ["init", "-q", "-b", "main"])
    run_git(source, ["add", "--", ".zpkg.toml", "payload.txt"])
    run_git(
        source,
        [
            "-c",
            "user.name=Zed Lifecycle Test",
            "-c",
            "user.email=zed-lifecycle@example.invalid",
            "commit",
            "-q",
            "-m",
            "feat: add interactive lifecycle fixture",
        ],
    )


def mounted_path(runtime: Runtime, path: Path) -> str:
    if runtime.mode == "host":
        return str(path.resolve())
    relative = path.resolve().relative_to(runtime.project.resolve())
    return (Path("/work") / relative).as_posix()


def global_args(runtime: Runtime, registry: Path) -> list[str]:
    home = str(runtime.home.resolve()) if runtime.mode == "host" else "/zed-home"
    return [
        "--registry",
        f"file://{mounted_path(runtime, registry)}",
        "--home",
        home,
    ]


def run_interactive(
    runtime: Runtime,
    registry: Path,
    command: Sequence[str],
    *,
    cwd: Path,
    checkpoint: str,
    action: CheckpointAction,
) -> CheckpointResult:
    if not command:
        raise AssertionError("interactive command must name a Zed subcommand")
    return runtime.at_checkpoint(
        checkpoint,
        action,
        arguments=[
            command[0],
            "--interactive",
            *command[1:],
            *global_args(runtime, registry),
        ],
        cwd=cwd,
    )


def require_clean_transaction(project: Path) -> None:
    staging = project / ".zpkg-staging"
    if staging.exists() or staging.is_symlink():
        raise AssertionError(f"lifecycle left an abandoned transaction staging area: {staging}")


def require_projection(consumer: Path) -> Path:
    projection = consumer / "zed_modules" / PACKAGE_ORG / PACKAGE_NAME / "payload.txt"
    if projection.read_bytes() != PAYLOAD:
        raise AssertionError("installed consumer payload differs from the published package")
    for path in (consumer / "zed_modules").rglob("*"):
        if path.is_symlink():
            raise AssertionError(f"copy-mode lifecycle leaked a symlink: {path}")
    return projection


def validate_registry(registry: Path) -> tuple[str, int]:
    metadata_path = (
        registry
        / "packages"
        / PACKAGE_ORG
        / PACKAGE_NAME
        / "versions"
        / f"{PACKAGE_VERSION}.json"
    )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if (metadata.get("org"), metadata.get("name"), str(metadata.get("version"))) != (
        PACKAGE_ORG,
        PACKAGE_NAME,
        PACKAGE_VERSION,
    ):
        raise AssertionError(f"registry metadata identity drift: {metadata}")
    artifact_sha = str(metadata.get("sha256", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", artifact_sha):
        raise AssertionError(f"registry metadata has no valid artifact digest: {metadata}")
    artifact = registry / "artifacts" / f"{artifact_sha}.tar.gz"
    raw = artifact.read_bytes()
    if digest(raw) != artifact_sha:
        raise AssertionError("registry artifact does not match its SHA-256 authority")
    with tarfile.open(artifact, "r:gz") as archive:
        names = set(archive.getnames())
    if "pkg/payload.txt" not in names or "pkg/.zpkg.toml" not in names:
        raise AssertionError(f"published artifact is missing required members: {sorted(names)}")
    return artifact_sha, len(raw)


def validate_declined_r2g_workspace(root: Path) -> str:
    workspaces = sorted(path for path in root.iterdir() if path.is_dir())
    if len(workspaces) != 1:
        raise AssertionError(f"declined r2g did not leave exactly one diagnostic workspace: {workspaces}")
    value = workspaces[0].name[-36:]
    require_uuid_v4(value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=("host", "oci"))
    parser.add_argument("--oci-image")
    parser.add_argument("--oci-base-digest")
    parser.add_argument("--cli-commit", required=True)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--exercise-r2g-container", action="store_true")
    parser.add_argument("--r2g-container-image")
    arguments = parser.parse_args()

    if not COMMIT.fullmatch(arguments.cli_commit):
        parser.error("--cli-commit must be an exact 40-character lowercase commit")
    if arguments.mode == "oci" and not arguments.oci_image:
        parser.error("--oci-image is required in OCI mode")
    if arguments.mode == "host" and not arguments.zed.is_file():
        parser.error(f"--zed is not a file: {arguments.zed}")
    if arguments.exercise_r2g_container and arguments.mode != "host":
        parser.error("--exercise-r2g-container is valid only in host mode")
    if arguments.exercise_r2g_container and not arguments.r2g_container_image:
        parser.error("--r2g-container-image is required with --exercise-r2g-container")

    parent = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
    run_id = uuid.uuid4()
    scratch = parent / f"zed-cli-full-lifecycle-{arguments.mode}-{run_id}"
    if scratch.exists():
        raise AssertionError(f"UUID-v4 lifecycle root already exists: {scratch}")
    source = scratch / "source"
    consumer = scratch / "consumer"
    registry = scratch / "registry"
    r2g_registry = scratch / "r2g-registry"
    home = scratch / "home"
    initialize_source(source)
    consumer.mkdir(parents=True)
    registry.mkdir()
    r2g_registry.mkdir()
    home.mkdir()
    (consumer / ".zpkg.toml").write_text(CONSUMER_MANIFEST, encoding="utf-8")

    evidence: dict[str, object] = {
        "schema": "zed-cli-interactive-full-lifecycle/v1",
        "status": "running",
        "mode": arguments.mode,
        "platform": platform.platform(),
        "run_uuid": str(run_id),
        "run_uuid_version": run_id.version,
        "cli_commit": arguments.cli_commit,
        "oci_image": arguments.oci_image,
        "oci_base_digest": arguments.oci_base_digest,
        "package": PACKAGE_SPEC,
        "payload_sha256": digest(PAYLOAD),
        "checkpoints": {},
    }
    checkpoints = evidence["checkpoints"]
    assert isinstance(checkpoints, dict)

    runtime = Runtime(
        mode=arguments.mode,
        zed=arguments.zed.resolve(),
        image=arguments.oci_image,
        project=scratch,
        home=home,
        scratch=scratch,
        timeout=arguments.timeout,
    )
    base = global_args(runtime, registry)
    manifest_bytes = (source / ".zpkg.toml").read_bytes()

    try:
        version = runtime.run([*base, "--version"]).stdout.strip()
        evidence["zed_version"] = version

        if arguments.mode == "host":
            release = runtime.run([*base, "release", "plan", "--json"], cwd=source)
            plan = parse_json_output(release.stdout)
            checkpoints["release_plan"] = {"json": True, "kind": type(plan).__name__}
        else:
            checkpoints["release_plan"] = {
                "status": "host_only",
                "reason": "minimal OCI runtime deliberately contains no Git executable",
            }

        pack_a = scratch / "pack-a"
        pack_b = scratch / "pack-b"
        runtime.run([*base, "pack", "--out", mounted_path(runtime, pack_a)], cwd=source)
        runtime.run([*base, "pack", "--out", mounted_path(runtime, pack_b)], cwd=source)
        archives_a = sorted(pack_a.glob("*.tar.gz"))
        archives_b = sorted(pack_b.glob("*.tar.gz"))
        if len(archives_a) != 1 or len(archives_b) != 1:
            raise AssertionError(f"deterministic pack did not emit one archive per output: {archives_a}, {archives_b}")
        pack_sha = digest(archives_a[0].read_bytes())
        if pack_sha != digest(archives_b[0].read_bytes()):
            raise AssertionError("independent pack outputs were not byte-identical")
        checkpoints["deterministic_pack"] = {"sha256": pack_sha, "byte_identical": True}

        empty_registry = tree_digest(registry)
        redirected = runtime.run(
            ["publish", "--interactive", "--skip-vcs-checks", *base],
            cwd=source,
            input_text="yes\n",
            check=False,
        )
        if redirected.returncode == 0 or "requires terminal stdin and stderr" not in redirected.stdout:
            raise AssertionError("redirected --interactive publish did not fail closed")
        if tree_digest(registry) != empty_registry:
            raise AssertionError("redirected interactive publish mutated the registry")
        checkpoints["redirected_publish"] = {
            "returncode": redirected.returncode,
            "failed_closed": True,
            "registry_unchanged": True,
        }

        r2g_decline_root = scratch / "r2g-decline"
        declined_r2g = run_interactive(
            runtime,
            r2g_registry,
            ["r2g", "--r2g-root", mounted_path(runtime, r2g_decline_root), "--clean"],
            cwd=source,
            checkpoint="r2g step 1/5: pack",
            action=CheckpointAction.DECLINE,
        )
        if declined_r2g.returncode == 0 or "confirmation declined" not in declined_r2g.output:
            raise AssertionError("declined r2g checkpoint did not fail closed")
        if tree_digest(r2g_registry) != empty_registry:
            raise AssertionError("declined r2g mutated its configured registry input")
        declined_workspace_uuid = validate_declined_r2g_workspace(r2g_decline_root)
        checkpoints["declined_r2g"] = {
            "returncode": declined_r2g.returncode,
            "prompts": list(declined_r2g.prompts),
            "workspace_uuid": declined_workspace_uuid,
            "workspace_uuid_version": 4,
            "registry_unchanged": True,
        }
        shutil.rmtree(next(r2g_decline_root.iterdir()))

        r2g_root = scratch / "r2g-host"
        accepted_r2g = run_interactive(
            runtime,
            r2g_registry,
            ["r2g", "--r2g-root", mounted_path(runtime, r2g_root), "--clean"],
            cwd=source,
            checkpoint="remove successful r2g workspace",
            action=CheckpointAction.ACCEPT,
        )
        require_prompt_sequence(
            accepted_r2g.prompts,
            (
                "r2g step 1/5: pack",
                "r2g step 2/5: publish",
                "r2g step 3/5: create a fresh mock consumer",
                "r2g step 4/5: install",
                "materialize 1 resolved package(s)",
                f"install {PACKAGE_SPEC}",
                "write .zpkg.lock, update project references, and commit transaction",
                "r2g step 5/5: run the smoke test",
                "remove successful r2g workspace",
            ),
        )
        if accepted_r2g.returncode != 0 or (r2g_root.exists() and any(r2g_root.iterdir())):
            raise AssertionError("accepted interactive r2g did not pass and clean its UUID workspace")
        checkpoints["accepted_r2g_host"] = {
            "prompts": list(accepted_r2g.prompts),
            "transaction_uuids": transaction_ids(accepted_r2g.prompts),
            "smoke_test": True,
            "workspace_cleaned": True,
        }

        if arguments.exercise_r2g_container:
            r2g_container_root = scratch / "r2g-container"
            accepted_container = run_interactive(
                runtime,
                r2g_registry,
                [
                    "r2g",
                    "--docker",
                    "--runtime",
                    "docker",
                    "--image",
                    str(arguments.r2g_container_image),
                    "--r2g-root",
                    mounted_path(runtime, r2g_container_root),
                    "--clean",
                ],
                cwd=source,
                checkpoint="remove successful r2g workspace",
                action=CheckpointAction.ACCEPT,
            )
            require_prompt_sequence(
                accepted_container.prompts,
                ("r2g step 1/5: pack", "r2g step 4/5: install", "r2g step 5/5: run the smoke test inside a fresh OCI container", "remove successful r2g workspace"),
            )
            if accepted_container.returncode != 0:
                raise AssertionError("interactive r2g --docker did not pass")
            checkpoints["accepted_r2g_container"] = {
                "image": arguments.r2g_container_image,
                "prompts": list(accepted_container.prompts),
                "smoke_test": True,
            }

        declined_publish = run_interactive(
            runtime,
            registry,
            ["publish", "--skip-vcs-checks"],
            cwd=source,
            checkpoint=f"publish {PACKAGE_SPEC}",
            action=CheckpointAction.DECLINE,
        )
        if declined_publish.returncode == 0 or tree_digest(registry) != empty_registry:
            raise AssertionError("declined interactive publish mutated the registry")
        checkpoints["declined_publish"] = {
            "prompts": list(declined_publish.prompts),
            "registry_unchanged": True,
        }

        accepted_publish = run_interactive(
            runtime,
            registry,
            ["publish", "--skip-vcs-checks"],
            cwd=source,
            checkpoint=f"publish {PACKAGE_SPEC}",
            action=CheckpointAction.ACCEPT,
        )
        if accepted_publish.returncode != 0:
            raise AssertionError("accepted interactive publish failed")
        artifact_sha, artifact_size = validate_registry(registry)
        published_tree = tree_digest(registry)
        runtime.run([*base, "publish", "--skip-vcs-checks"], cwd=source)
        if tree_digest(registry) != published_tree:
            raise AssertionError("idempotent publish changed immutable registry bytes")
        checkpoints["accepted_publish"] = {
            "prompts": list(accepted_publish.prompts),
            "artifact_sha256": artifact_sha,
            "artifact_size": artifact_size,
            "idempotent": True,
        }

        install_command = [
            "install",
            "--install-mode",
            "copy",
            "--adapter",
            "none",
            "--allow-ecosystem-mismatch",
        ]
        declined_install = run_interactive(
            runtime,
            registry,
            install_command,
            cwd=consumer,
            checkpoint="materialize 1 resolved package(s)",
            action=CheckpointAction.DECLINE,
        )
        if declined_install.returncode == 0:
            raise AssertionError("declined interactive install unexpectedly succeeded")
        for path in (consumer / ".zpkg.lock", consumer / "zed_modules"):
            if path.exists() or path.is_symlink():
                raise AssertionError(f"declined install mutated the consumer: {path}")
        require_clean_transaction(consumer)
        checkpoints["declined_install"] = {
            "prompts": list(declined_install.prompts),
            "consumer_unchanged": True,
        }

        accepted_install = run_interactive(
            runtime,
            registry,
            install_command,
            cwd=consumer,
            checkpoint="write .zpkg.lock, update project references, and commit transaction",
            action=CheckpointAction.ACCEPT,
        )
        require_prompt_sequence(
            accepted_install.prompts,
            (
                "materialize 1 resolved package(s)",
                f"install {PACKAGE_SPEC}",
                "write .zpkg.lock, update project references, and commit transaction",
            ),
        )
        if accepted_install.returncode != 0:
            raise AssertionError("accepted interactive install failed")
        projection = require_projection(consumer)
        lock_bytes = (consumer / ".zpkg.lock").read_bytes()
        require_clean_transaction(consumer)
        install_transactions = transaction_ids(accepted_install.prompts)
        if len(install_transactions) != 1:
            raise AssertionError(f"install did not expose one UUID-v4 transaction: {install_transactions}")
        checkpoints["accepted_install"] = {
            "prompts": list(accepted_install.prompts),
            "transaction_uuid": install_transactions[0],
            "lock_sha256": digest(lock_bytes),
            "projection_sha256": digest(projection.read_bytes()),
            "copy_mode": True,
        }

        accepted_uninstall = run_interactive(
            runtime,
            registry,
            ["uninstall"],
            cwd=consumer,
            checkpoint="record 0 remaining installed package(s) and commit transaction",
            action=CheckpointAction.ACCEPT,
        )
        require_prompt_sequence(
            accepted_uninstall.prompts,
            (
                "uninstall 1 package(s)",
                f"unmaterialize {PACKAGE_ORG}/{PACKAGE_NAME}",
                "record 0 remaining installed package(s) and commit transaction",
            ),
        )
        if accepted_uninstall.returncode != 0 or projection.exists():
            raise AssertionError("accepted interactive uninstall did not remove the projection")
        if (consumer / ".zpkg.lock").read_bytes() != lock_bytes:
            raise AssertionError("interactive uninstall changed the retained lockfile")
        require_clean_transaction(consumer)
        uninstall_transactions = transaction_ids(accepted_uninstall.prompts)
        if len(uninstall_transactions) != 1:
            raise AssertionError(f"uninstall did not expose one UUID-v4 transaction: {uninstall_transactions}")
        checkpoints["accepted_uninstall"] = {
            "prompts": list(accepted_uninstall.prompts),
            "transaction_uuid": uninstall_transactions[0],
            "projection_removed": True,
            "lock_retained_exactly": True,
        }

        accepted_frozen = run_interactive(
            runtime,
            registry,
            [*install_command, "--frozen"],
            cwd=consumer,
            checkpoint="preserve .zpkg.lock byte-for-byte, update project references, and commit transaction",
            action=CheckpointAction.ACCEPT,
        )
        require_prompt_sequence(
            accepted_frozen.prompts,
            (
                "materialize 1 resolved package(s)",
                f"install {PACKAGE_SPEC}",
                "preserve .zpkg.lock byte-for-byte, update project references, and commit transaction",
            ),
        )
        if accepted_frozen.returncode != 0:
            raise AssertionError("accepted interactive frozen reinstall failed")
        projection = require_projection(consumer)
        if (consumer / ".zpkg.lock").read_bytes() != lock_bytes:
            raise AssertionError("interactive frozen reinstall rewrote the lockfile")
        require_clean_transaction(consumer)
        frozen_transactions = transaction_ids(accepted_frozen.prompts)
        if len(frozen_transactions) != 1:
            raise AssertionError(f"frozen reinstall did not expose one UUID-v4 transaction: {frozen_transactions}")
        checkpoints["accepted_frozen_reinstall"] = {
            "prompts": list(accepted_frozen.prompts),
            "transaction_uuid": frozen_transactions[0],
            "lock_sha256": digest(lock_bytes),
            "projection_sha256": digest(projection.read_bytes()),
            "exact_restoration": True,
        }

        if (source / ".zpkg.toml").read_bytes() != manifest_bytes or (source / "payload.txt").read_bytes() != PAYLOAD:
            raise AssertionError("lifecycle mutated authoritative source bytes")
        evidence["status"] = "passed"
        print(f"certified {arguments.mode} full interactive lifecycle package={PACKAGE_SPEC} cli={arguments.cli_commit}")
        return 0
    except BaseException as error:
        evidence["status"] = "failed"
        evidence["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        atomic_json(arguments.evidence, evidence)


if __name__ == "__main__":
    raise SystemExit(main())
