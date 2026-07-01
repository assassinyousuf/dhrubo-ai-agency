"""In-process session memory.

A simple async-safe key-value store. The :class:`WorkflowEngine` writes
agent outputs here, and downstream agents read from it.

This is the *only* concrete memory backend the engine depends on. A
Redis-backed variant can implement the same surface (read/write/get_keys)
and be injected at construction time.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any


class SessionMemory:
    """An async-safe dict-like store scoped to a single run."""

    def __init__(self, *, namespace: str = "default") -> None:
        self._store: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._namespace = namespace

    @property
    def namespace(self) -> str:
        return self._namespace

    async def write(self, key: str, value: Any) -> None:
        async with self._lock:
            self._store[key] = value

    async def read(self, key: str, default: Any = None) -> Any:
        async with self._lock:
            return self._store.get(key, default)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()

    def keys(self) -> Iterator[str]:
        # Snapshot — the underlying dict may mutate under us.
        return iter(list(self._store))

    def snapshot(self) -> dict[str, Any]:
        return dict(self._store)
