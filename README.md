# zed-pkg-test/chaos-recovery-tests

Black-box interactive lifecycle and crash-recovery certification for `zed-pkg/zed-cli` on host machines and in fresh OCI containers.

This repository is the `chaos` deep-test suite for `zed-pkg`. It is intentionally dependency-light and deterministic so failures can be reproduced locally without production credentials or customer data.

## Run

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
python scripts/verify_repository.py
```

The dependency-free reference model remains the fast oracle. The versioned product adapters pin one exact CLI revision and prove two complementary real lifecycles.

The full lifecycle adapter:

1. generates and commits a synthetic package in a UUID-v4 scratch root;
2. obtains a release plan and proves independent package archives are byte-identical;
3. runs `r2g` through a real PTY, including a declined checkpoint and the accepted pack/publish/install/smoke/cleanup path;
4. proves redirected and declined interactive publishes fail before an isolated `file://` registry changes;
5. publishes, verifies immutable metadata and artifact bytes, and retries idempotently;
6. proves declined install leaves no lock, projection, or staging transaction;
7. accepts every copy-install prompt and validates the exact projected payload;
8. accepts every uninstall prompt, removes the projection, retains the exact lock, and leaves no staging area;
9. accepts an interactive `install --frozen` and proves byte-exact lock and payload restoration.

The crash-recovery adapter:

1. install a synthetic workspace package in copy mode;
2. reject `--interactive` when consent is redirected instead of supplied by a terminal;
3. decline or close the terminal at the first uninstall checkpoint without mutating the project;
4. accept the first checkpoint and forcibly terminate at `unmaterialize lock-test/member`;
5. inspect the single UUID-v4 `.zpkg-staging` transaction left by the hard exit;
6. restart Zed, observe the exact recovery diagnostic, and verify byte-for-byte rollback;
7. accept every uninstall checkpoint and then restore the retained lock with `install --frozen`.

Both adapters run through a real PTY on Ubuntu and macOS hosts. The Ubuntu lane repeats both in fresh, non-root, network-disabled, read-only OCI containers built from an immutable Ubuntu digest. Linux also exercises `r2g --docker` against a digest-pinned clean container. Process synchronization uses Zed's prompt text and monotonic deadlines; it never uses timing sleeps.

The Python code is deliberately a thin, dependency-free orchestration boundary around `os.forkpty`, process ownership, and filesystem assertions; the package manager and transaction state machine under test remain Rust. Replacing this PTY adapter is not part of the product runtime or the DEN-3908 registry tranche.

For a local host run, first build the pinned CLI commit recorded in `project.json`, then run:

```bash
python scripts/zed_cli_crash_recovery.py \
  --zed /absolute/path/to/zed \
  --mode host \
  --cli-commit e5f114d2c905b37b3d11628b9ded841254b768e2 \
  --evidence /tmp/zed-cli-host-evidence.json

python scripts/zed_cli_full_lifecycle.py \
  --zed /absolute/path/to/zed \
  --mode host \
  --cli-commit e5f114d2c905b37b3d11628b9ded841254b768e2 \
  --evidence /tmp/zed-cli-full-host-evidence.json
```

CI uploads host and OCI evidence under the `zed-cli-interactive-crash-recovery/v1` and `zed-cli-interactive-full-lifecycle/v1` schemas.

The registry used here is always an isolated, disposable `file://` authority. Passing this suite does not assert that the public R2 registry is deployed, credentialed, reachable, or populated with the separate 10–15-package production tranche.

Tracking: https://github.com/ORESoftware/ai-agent-coordinator.rs/issues/139 and https://github.com/ORESoftware/ai-agent-coordinator.rs/issues/199

Linear: `DEN-2046` and `DEN-3908`
