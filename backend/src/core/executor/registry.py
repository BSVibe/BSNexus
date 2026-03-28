"""Thread-safe executor registry for resolving executors by name."""

from __future__ import annotations

import threading
from typing import Callable

from backend.src.core.executor.base import ExecutorProtocol


class ExecutorRegistry:
    """Registry mapping executor names to their factory callables."""

    _instance: ExecutorRegistry | None = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> ExecutorRegistry:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._executors = {}
                    cls._instance = instance
        return cls._instance

    def register(self, name: str, executor_factory: Callable[[], ExecutorProtocol]) -> None:
        """Register an executor factory by name. Raises KeyError if already registered."""
        if name in self._executors:
            raise KeyError(f"Executor '{name}' already registered")
        self._executors[name] = executor_factory

    def get(self, name: str) -> ExecutorProtocol:
        """Create and return an executor instance by name. Raises KeyError if not registered."""
        if name not in self._executors:
            raise KeyError(f"Executor '{name}' not registered")
        return self._executors[name]()

    def list_available(self) -> list[str]:
        """Return sorted list of registered executor names."""
        return sorted(self._executors.keys())
