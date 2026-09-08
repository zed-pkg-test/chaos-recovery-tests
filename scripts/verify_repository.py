#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
metadata = json.loads((ROOT / "project.json").read_text(encoding="utf-8"))
required = {
    "README.md",
    "AGENTS.md",
    "project.json",
    "pyproject.toml",
    ".zpkg.toml",
    "docs/test-strategy.md",
    "scripts/verify_repository.py",
    "scripts/zed_cli_crash_recovery.py",
    "scripts/zed_cli_full_lifecycle.py",
    ".github/workflows/deep-tests.yml",
    ".github/docker/zed-cli-crash.Dockerfile",
    "src/deep_tests/__init__.py",
    "src/deep_tests/process_checkpoint.py",
    "tests/test_process_checkpoint.py",
    "tests/test_full_lifecycle.py",
}
missing = sorted(path for path in required if not (ROOT / path).exists())
if missing:
    raise SystemExit(f"missing required paths: {missing}")
if not (ROOT / "tests").is_dir() or not list((ROOT / "tests").glob("test_*.py")):
    raise SystemExit("at least one executable test module is required")

marker = re.compile(r"^(<{7}|={7}|>{7})", re.MULTILINE)
credential = re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|lin_api_[A-Za-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY")
for path in ROOT.rglob("*"):
    if not path.is_file() or ".git" in path.parts or path.stat().st_size > 1_000_000:
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
    if marker.search(text):
        raise SystemExit(f"unresolved conflict marker: {path.relative_to(ROOT)}")
    if credential.search(text):
        raise SystemExit(f"credential-shaped content: {path.relative_to(ROOT)}")

agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
for phrase in ("merge base", "3–10 relevant commits", "ours", "theirs", "Fail closed"):
    if phrase not in agents:
        raise SystemExit(f"semantic conflict policy missing phrase: {phrase}")

workflow = (ROOT / ".github/workflows/deep-tests.yml").read_text(encoding="utf-8")
if "permissions:\n  contents: read" not in workflow or "pull_request_target" in workflow:
    raise SystemExit("workflow permission boundary is unsafe")
action_pattern = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")
actions = [line.split("uses:", 1)[1].strip() for line in workflow.splitlines() if "uses:" in line]
if len(actions) < 2 or any(not action_pattern.fullmatch(action) for action in actions):
    raise SystemExit(f"workflow actions are not immutably pinned: {actions}")

adapter = metadata.get("product_adapter", {})
if adapter.get("linear_issue") != "DEN-2046":
    raise SystemExit("product adapter is not bound to DEN-2046")
if adapter.get("linear_issues") != ["DEN-2046", "DEN-3908"]:
    raise SystemExit("product adapter is not bound to the crash and lifecycle Linear issues")
zed_cli_commit = str(adapter.get("zed_cli_commit", ""))
if not re.fullmatch(r"[0-9a-f]{40}", zed_cli_commit) or zed_cli_commit not in workflow:
    raise SystemExit("zed-cli product adapter is not pinned to one exact commit")
if adapter.get("execution_boundaries") != ["host", "oci"]:
    raise SystemExit("zed-cli product adapter must cover host and OCI execution")

dockerfile = (ROOT / ".github/docker/zed-cli-crash.Dockerfile").read_text(encoding="utf-8")
base_digest = str(adapter.get("oci_base_digest", ""))
if not re.fullmatch(r"sha256:[0-9a-f]{64}", base_digest) or base_digest not in dockerfile:
    raise SystemExit("OCI product adapter base is not immutably pinned")

checkpoint_driver = (ROOT / "src/deep_tests/process_checkpoint.py").read_text(encoding="utf-8")
product_adapter = (ROOT / "scripts/zed_cli_crash_recovery.py").read_text(encoding="utf-8")
lifecycle_adapter = (ROOT / "scripts/zed_cli_full_lifecycle.py").read_text(encoding="utf-8")
for unsafe_wait in ("time.sleep(", "sleep("):
    if unsafe_wait in checkpoint_driver or unsafe_wait in product_adapter or unsafe_wait in lifecycle_adapter:
        raise SystemExit(f"product adapter contains a nondeterministic wait: {unsafe_wait}")
for checkpoint in (
    "uninstall 1 package(s)",
    "unmaterialize lock-test/member",
    "record 0 remaining installed package(s) and commit transaction",
):
    if checkpoint not in product_adapter:
        raise SystemExit(f"missing semantic process checkpoint: {checkpoint}")
for contract in (
    "ZED_PKG_INTERACTIVE",
    "uuid.uuid4()",
    "CheckpointAction.EOF",
    "CheckpointAction.KILL",
    "install\", \"--frozen",
):
    if contract not in product_adapter and contract not in workflow:
        raise SystemExit(f"missing interactive recovery contract: {contract}")
for contract in (
    '"release", "plan", "--json"',
    '"r2g", "--r2g-root"',
    '"publish", "--skip-vcs-checks"',
    '"publish", "--interactive"',
    '"install",',
    '["uninstall"]',
    '"--frozen"',
    'uuid.uuid4()',
    'zed-cli-interactive-full-lifecycle/v1',
):
    if contract not in lifecycle_adapter:
        raise SystemExit(f"missing full lifecycle contract: {contract}")
for expected_pin in (
    "e5f114d2c905b37b3d11628b9ded841254b768e2",
    "3c54298fc7a8c1b2f9c1d74f588c6118b38f197e",
    "1db0da00d30fcf2e0762f50eedb1f88458020b52",
    "2946224fb3a1bd84c0e39146b24f5f1ca8d69862",
):
    if expected_pin not in workflow:
        raise SystemExit(f"workflow is missing exact source pin: {expected_pin}")

if metadata.get("bootstrap_operation") != "deep-test-fleet-20260808":
    raise SystemExit("bootstrap operation identity drift")
if not str(metadata.get("organization", "")).endswith("-test"):
    raise SystemExit("repository is not bound to a test organization")
print(f"validated {metadata['organization']}/{metadata['repository']} suite={metadata['suite']}")
