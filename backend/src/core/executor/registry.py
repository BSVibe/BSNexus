"""Thread-safe executor registry for resolving executors by name or capability."""

from __future__ import annotations

import threading
from typing import Callable

from backend.src.core.executor.base import ExecutorCapability, ExecutorInfo, ExecutorProtocol


class ExecutorRegistry:
    """Registry mapping executor names to their factory callables."""

    _instance: ExecutorRegistry | None = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> ExecutorRegistry:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._executors: dict[str, Callable[[], ExecutorProtocol]] = {}
                    instance._infos: dict[str, ExecutorInfo] = {}
                    cls._instance = instance
        return cls._instance

    def register(
        self,
        name: str,
        executor_factory: Callable[[], ExecutorProtocol],
        info: ExecutorInfo | None = None,
    ) -> None:
        """Register an executor factory by name. Raises KeyError if already registered."""
        if name in self._executors:
            raise KeyError(f"Executor '{name}' already registered")
        self._executors[name] = executor_factory
        if info is not None:
            self._infos[name] = info

    def get(self, name: str) -> ExecutorProtocol:
        """Create and return an executor instance by name. Raises KeyError if not registered."""
        if name not in self._executors:
            raise KeyError(f"Executor '{name}' not registered")
        return self._executors[name]()

    def get_info(self, name: str) -> ExecutorInfo | None:
        """Return ExecutorInfo for a registered executor, or None."""
        return self._infos.get(name)

    def list_available(self) -> list[str]:
        """Return sorted list of registered executor names."""
        return sorted(self._executors.keys())

    def list_by_capability(self, capability: ExecutorCapability) -> list[str]:
        """Return executor names that support the given capability."""
        return sorted(
            name for name, info in self._infos.items() if capability in info.capabilities
        )
