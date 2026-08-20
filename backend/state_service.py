from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict

from dashboard.simulator.fake_state import FakeStateGenerator


class StateService:
    def __init__(self, seed: int | None = None) -> None:
        self.generator = FakeStateGenerator(seed=seed)
        self.latest_state: Dict[str, Any] = self.generator.generate_state()
        self._loop_task: asyncio.Task | None = None
        self._running = False
        self._broadcast_callback: Callable[[Dict[str, Any]], Any] | None = None

    def set_broadcast_callback(self, callback: Callable[[Dict[str, Any]], Any]) -> None:
        self._broadcast_callback = callback

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._loop_task = asyncio.create_task(self._update_loop())

    async def stop(self) -> None:
        self._running = False
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None

    async def _update_loop(self) -> None:
        while self._running:
            await asyncio.sleep(0.1)
            self.latest_state = self.generator.generate_state()
            if self._broadcast_callback is not None:
                await self._broadcast_callback(self.latest_state)

    def get_state(self) -> Dict[str, Any]:
        return self.latest_state
