import unittest
from bmo_ai.benchmark import benchmark


class BenchmarkTests(unittest.TestCase):
    def test_offline_report_and_history_bound(self):
        result = benchmark(50)
        self.assertTrue(result["mock_only"])
        self.assertEqual(result["network_calls"], 0)
        self.assertEqual(result["hardware_operations"], 0)
        self.assertLessEqual(result["retained_messages"], 16)
        self.assertGreaterEqual(result["latency_ms"]["max"], result["latency_ms"]["median"])

    def test_invalid_count(self):
        for count in (True, 0, 10001, "200"):
            with self.assertRaises(ValueError):
                benchmark(count)
