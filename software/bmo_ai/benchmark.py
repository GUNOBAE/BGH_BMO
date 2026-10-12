"""Offline host overhead benchmark. These numbers are not cloud latency."""
import platform
import statistics
import time
import tracemalloc
from types import SimpleNamespace

from .core import BMOBrain


def benchmark(iterations=200):
    if not isinstance(iterations, int) or isinstance(iterations, bool) or not 1 <= iterations <= 10000:
        raise ValueError("iterations must be between 1 and 10000")
    response = SimpleNamespace(output_text="Hello, friend!", status="completed", incomplete_details=None)
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: response))
    brain = BMOBrain(client)
    samples = []
    tracing_already = tracemalloc.is_tracing()
    if not tracing_already:
        tracemalloc.start()
    try:
        for _ in range(iterations):
            start = time.perf_counter()
            brain.ask("Hello BMO!")
            samples.append((time.perf_counter() - start) * 1000)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        if not tracing_already:
            tracemalloc.stop()
    samples.sort()
    return {
        "measurement": "mock_conversation_host_overhead",
        "mock_only": True,
        "network_calls": 0,
        "hardware_operations": 0,
        "python": platform.python_version(),
        "architecture": platform.machine(),
        "iterations": iterations,
        "latency_ms": {
            "median": round(statistics.median(samples), 4),
            "p95": round(samples[max(0, (95 * iterations + 99) // 100 - 1)], 4),
            "max": round(max(samples), 4),
        },
        "traced_peak_bytes": peak,
        "retained_messages": len(brain.history),
        "limitations": "Includes tracemalloc overhead; excludes recording, cloud, synthesis, playback, display and motors.",
    }
