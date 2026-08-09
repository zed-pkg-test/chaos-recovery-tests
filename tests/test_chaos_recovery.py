import unittest

from deep_tests.chaos_model import DurableService, Operation, SimulatedTimeout, simulate


class ChaosRecoveryTests(unittest.TestCase):
    def test_fault_matrix_converges_without_duplicate_side_effects(self) -> None:
        for seed in range(40):
            result = simulate(seed, operations=120)
            self.assertTrue(result.retries >= 0)
            self.assertEqual(len(result.side_effect_counts), result.operations)
            self.assertTrue(all(count == 1 for count in result.side_effect_counts.values()))
            for replica_state in result.replica_states:
                self.assertEqual(replica_state, result.primary_state, f"seed={seed}")

    def test_timeout_after_commit_is_safe_to_retry(self) -> None:
        service = DurableService()
        operation = Operation("key", "entity", 7, 1)
        with self.assertRaises(SimulatedTimeout):
            service.receive(operation, "timeout_after_commit")
        service.receive(operation)
        self.assertEqual(service.side_effect_counts["key"], 1)
        self.assertEqual(service.materialized["entity"], 7)

    def test_conflicting_retry_fails_closed(self) -> None:
        service = DurableService()
        service.receive(Operation("key", "entity", 7, 1))
        with self.assertRaises(ValueError):
            service.receive(Operation("key", "entity", 8, 1))


if __name__ == "__main__":
    unittest.main()
