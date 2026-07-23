"""Dependency-free process metrics for worker operations."""

from __future__ import annotations

from threading import Lock


class MetricsRegistry:
    def __init__(self) -> None:
        self._counts: dict[str, float] = {}
        self._lock = Lock()

    def increment(self, name: str, amount: float = 1) -> None:
        with self._lock:
            self._counts[name] = self._counts.get(name, 0) + amount

    def render_prometheus(self) -> str:
        with self._lock:
            lines = []
            for name in sorted(self._counts):
                value = self._counts[name]
                rendered = str(int(value)) if value.is_integer() else str(value)
                lines.append(f"{name} {rendered}\n")
            return "".join(lines)


metrics = MetricsRegistry()
