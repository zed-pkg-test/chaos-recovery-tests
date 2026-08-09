from __future__ import annotations

import random
from dataclasses import dataclass


class SimulatedCrash(RuntimeError):
    pass


class SimulatedTimeout(TimeoutError):
    pass


@dataclass(frozen=True)
class Operation:
    key: str
    entity_id: str
    value: int
    sequence: int


class DurableService:
    def __init__(self) -> None:
        self.journal: dict[str, Operation] = {}
        self.committed: set[str] = set()
        self.materialized: dict[str, int] = {}
        self.side_effect_counts: dict[str, int] = {}
        self.crashed = False

    def _commit(self, operation: Operation) -> None:
        if operation.key in self.committed:
            return
        self.materialized[operation.entity_id] = operation.value
        self.side_effect_counts[operation.key] = self.side_effect_counts.get(operation.key, 0) + 1
        self.committed.add(operation.key)

    def receive(self, operation: Operation, fault: str = "none") -> str:
        if self.crashed:
            raise SimulatedCrash("service is crashed")
        prior = self.journal.get(operation.key)
        if prior is not None and prior != operation:
            raise ValueError("idempotency key conflict")
        self.journal.setdefault(operation.key, operation)
        if fault == "crash_after_journal":
            self.crashed = True
            raise SimulatedCrash("crash after durable journal append")
        self._commit(operation)
        if fault == "timeout_after_commit":
            raise SimulatedTimeout("response was lost after commit")
        return "committed"

    def recover(self) -> None:
        self.crashed = False
        for key in sorted(self.journal):
            self._commit(self.journal[key])


class Replica:
    def __init__(self) -> None:
        self.applied: set[str] = set()
        self.state: dict[str, int] = {}
        self.entity_sequences: dict[str, int] = {}

    def apply(self, operation: Operation) -> None:
        if operation.key in self.applied:
            return
        self.applied.add(operation.key)
        current = self.entity_sequences.get(operation.entity_id, -1)
        if operation.sequence >= current:
            self.entity_sequences[operation.entity_id] = operation.sequence
            self.state[operation.entity_id] = operation.value


@dataclass(frozen=True)
class SimulationResult:
    retries: int
    operations: int
    primary_state: dict[str, int]
    replica_states: tuple[dict[str, int], ...]
    side_effect_counts: dict[str, int]


def simulate(seed: int, operations: int = 140) -> SimulationResult:
    randomizer = random.Random(seed)
    primary = DurableService()
    replicas = [Replica(), Replica(), Replica()]
    delayed: list[tuple[int, Operation]] = []
    expected: dict[str, int] = {}
    retries = 0

    for index in range(operations):
        operation = Operation(
            key=f"seed-{seed}-op-{index}",
            entity_id=f"entity-{randomizer.randrange(17)}",
            value=randomizer.randrange(1_000_000),
            sequence=index,
        )
        expected[operation.entity_id] = operation.value
        fault = randomizer.choices(
            ["none", "drop_before_send", "duplicate", "crash_after_journal", "timeout_after_commit"],
            weights=[55, 10, 12, 10, 13],
            k=1,
        )[0]
        delivered = False
        for attempt in range(6):
            try:
                if fault == "drop_before_send" and attempt == 0:
                    raise SimulatedTimeout("request was dropped before send")
                active_fault = fault if attempt == 0 else "none"
                primary.receive(operation, active_fault)
                if fault == "duplicate" and attempt == 0:
                    primary.receive(operation)
                delivered = True
                break
            except SimulatedCrash:
                retries += 1
                primary.recover()
            except SimulatedTimeout:
                retries += 1
                primary.recover()
        if not delivered and operation.key not in primary.committed:
            raise AssertionError(f"operation did not recover: {operation.key}")

        for replica_index, replica in enumerate(replicas):
            if randomizer.random() < 0.28:
                delayed.append((replica_index, operation))
            else:
                replica.apply(operation)
                if randomizer.random() < 0.22:
                    replica.apply(operation)

    primary.recover()
    randomizer.shuffle(delayed)
    for replica_index, operation in delayed:
        replicas[replica_index].apply(operation)
        if randomizer.random() < 0.5:
            replicas[replica_index].apply(operation)
    # Reconciliation after partitions heal is authoritative and idempotent.
    for operation in primary.journal.values():
        for replica in replicas:
            replica.apply(operation)

    if primary.materialized != expected:
        raise AssertionError("primary state diverged from acknowledged intent")
    if any(replica.state != expected for replica in replicas):
        raise AssertionError("replicas did not converge after healing")
    if any(count != 1 for count in primary.side_effect_counts.values()):
        raise AssertionError("a retried operation produced duplicate side effects")
    return SimulationResult(
        retries=retries,
        operations=operations,
        primary_state=dict(primary.materialized),
        replica_states=tuple(dict(replica.state) for replica in replicas),
        side_effect_counts=dict(primary.side_effect_counts),
    )
