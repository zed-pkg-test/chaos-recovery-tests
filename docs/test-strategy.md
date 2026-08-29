# Deep test strategy

## Scope

Suite: `chaos`
Test organization: `zed-pkg-test`
Primary organization: `zed-pkg`

## Invariants

- every randomized test uses an explicit deterministic seed;
- retries, duplicates, migrations, and rejected inputs are observable assertions, not sleeps;
- test data is synthetic and contains no production credentials or customer payloads;
- the suite runs without network access by default;
- a product adapter must preserve the reference model and publish the seed and minimized trace on failure;
- interactive consent must come from a real PTY; redirected input, decline, and terminal EOF fail before mutation;
- process interruption waits for an application-owned semantic checkpoint and uses a bounded monotonic deadline, never an arbitrary sleep;
- each transaction and scratch root has a UUID-v4 identity, and recovery evidence records that identity;
- host and OCI runs share the same assertions, with every OCI command receiving a fresh hardened runtime;
- scheduled CI is defense in depth; pull-request and main-branch checks remain authoritative.

## Product-adapter matrix

| Boundary | Real PTY | Redirect/decline/EOF denied | Forced termination | Startup rollback | Accepted uninstall | Frozen restore |
|---|---:|---:|---:|---:|---:|---:|
| Ubuntu 24.04 host | yes | yes | `SIGKILL` | exact bytes | yes | yes |
| macOS 15 host | yes | yes | `SIGKILL` | exact bytes | yes | yes |
| pinned Ubuntu 24.04 OCI | yes | yes | container `SIGKILL` | exact bytes | yes | yes |

The kill boundary is `interactive: unmaterialize lock-test/member? [y/N]`. At that point Zed has durably recorded and staged `zed_modules`, so the suite can distinguish an actual hard exit from an early abort. The restart is deliberately declined at the coarse uninstall prompt: successful recovery therefore cannot be confused with a second uninstall transaction.

The OCI adapter kills the container before terminating the attached Docker client. This prevents terminal disconnect handling from unwinding the Zed process and exercising ordinary in-process rollback instead of crash recovery.

## Expansion path

1. Extend the versioned CLI adapter from uninstall staging into install, adapter, lockfile-write, and commit crash windows.
2. Add deterministic child-process marker pipes to the CLI implementation where prompts are not available.
3. Add sanitized golden fixtures owned by the canonical interface repository.
4. Run the same generated trace against the reference model and implementation.
5. Retain failing seeds as regression tests and link behavior changes to `DEN-2046` and the repository PR.
