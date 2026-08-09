# zed-pkg-test/chaos-recovery-tests

Deterministic fault-injection, crash recovery, retry, partition, duplicate-delivery, and convergence tests.

This repository is the `chaos` deep-test suite for `zed-pkg`. It is intentionally dependency-light and deterministic so failures can be reproduced locally without production credentials or customer data.

## Run

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
python scripts/verify_repository.py
```

The initial model is executable rather than a placeholder. Product adapters should be added through focused pull requests while preserving the reference-model tests as an oracle.

Tracking: https://github.com/ORESoftware/ai-agent-coordinator.rs/issues/139
