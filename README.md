# zed-pkg-test/chaos-recovery-tests

Deterministic fault-injection plus black-box interactive crash recovery for `zed-pkg/zed-cli` on host machines and in fresh OCI containers.

This repository is the `chaos` deep-test suite for `zed-pkg`. It is intentionally dependency-light and deterministic so failures can be reproduced locally without production credentials or customer data.

## Run

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
python scripts/verify_repository.py
```

The dependency-free reference model remains the fast oracle. The versioned product adapter pins one exact CLI revision and proves this real lifecycle:

1. install a synthetic workspace package in copy mode;
2. reject `--interactive` when consent is redirected instead of supplied by a terminal;
3. decline or close the terminal at the first uninstall checkpoint without mutating the project;
4. accept the first checkpoint and forcibly terminate at `unmaterialize lock-test/member`;
5. inspect the single UUID-v4 `.zpkg-staging` transaction left by the hard exit;
6. restart Zed, observe the exact recovery diagnostic, and verify byte-for-byte rollback;
7. accept every uninstall checkpoint and then restore the retained lock with `install --frozen`.

The same adapter runs through a real PTY on Ubuntu and macOS hosts. The Ubuntu lane repeats it in fresh, non-root, network-disabled, read-only OCI containers built from an immutable Ubuntu digest. Process synchronization uses Zed's prompt text and monotonic deadlines; it never uses timing sleeps.

For a local host run, first build the pinned CLI commit recorded in `project.json`, then run:

```bash
python scripts/zed_cli_crash_recovery.py \
  --zed /absolute/path/to/zed \
  --mode host \
  --cli-commit c122d3b53f4d0a0021e60928e000132d33ea2c72 \
  --evidence /tmp/zed-cli-host-evidence.json
```

CI uploads the host and OCI evidence documents under the `zed-cli-interactive-crash-recovery/v1` schema.

Tracking: https://github.com/ORESoftware/ai-agent-coordinator.rs/issues/139

Linear: `DEN-2046`
