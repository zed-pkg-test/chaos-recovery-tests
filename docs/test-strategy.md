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
- r2g and direct publication use distinct disposable registries so one accepted path cannot mask a mutation in a declined path;
- declined interactive publication, r2g, and install prove their relevant registry or consumer tree remains byte-for-byte unchanged;
- successful publication validates immutable metadata, archive digest, required members, and idempotent retry;
- uninstall retains the authoritative lock exactly, while interactive frozen reinstall restores the copy projection exactly;
- scheduled CI is defense in depth; pull-request and main-branch checks remain authoritative.

## Product-adapter matrix

| Boundary | Release / pack | Interactive r2g | Interactive publish | Interactive install | Crash rollback | Interactive uninstall | Frozen restore |
|---|---:|---:|---:|---:|---:|---:|---:|
| Ubuntu 24.04 host | yes | host + OCI smoke | yes | copy mode | `SIGKILL`, exact bytes | yes | exact bytes |
| macOS 15 host | yes | host smoke | yes | copy mode | `SIGKILL`, exact bytes | yes | exact bytes |
| pinned Ubuntu 24.04 OCI | pack only | host-style smoke | yes | copy mode | container `SIGKILL`, exact bytes | yes | exact bytes |

The kill boundary is `interactive: unmaterialize lock-test/member? [y/N]`. At that point Zed has durably recorded and staged `zed_modules`, so the suite can distinguish an actual hard exit from an early abort. The restart is deliberately declined at the coarse uninstall prompt: successful recovery therefore cannot be confused with a second uninstall transaction.

The OCI adapter kills the container before terminating the attached Docker client. This prevents terminal disconnect handling from unwinding the Zed process and exercising ordinary in-process rollback instead of crash recovery.

The minimal OCI image deliberately omits Git, so `release plan` is asserted on both host operating systems while deterministic pack and every registry/consumer transition run in OCI. Every OCI invocation uses a fresh container with no network, a read-only root filesystem, dropped capabilities, `no-new-privileges`, a non-root host UID/GID, and only bounded writable mounts/tmpfs.

The acceptance registry is local and disposable. The matrix is evidence for CLI behavior and package compatibility, not evidence for public R2 availability or the DEN-3908 10–15-package publication tranche.

## Expansion path

1. Extend forced termination from uninstall staging into install, adapter, lockfile-write, and commit crash windows.
2. Add deterministic child-process marker pipes to the CLI implementation where prompts are not available.
3. Add sanitized golden fixtures owned by the canonical interface repository.
4. Run the same generated trace against the reference model and implementation.
5. Retain failing seeds as regression tests and link behavior changes to `DEN-2046` and the repository PR.
