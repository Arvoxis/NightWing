from __future__ import annotations

import asyncio
import secrets
from typing import Any, Callable, Dict

from backend.config import ENGINE_MODE, ENGINE_SEED


def _make_generator(seed: int | None):
    """Pick the state source. 'real' drives the dashboard from the actual swarm
    engine (the bridge); 'fake' keeps B1's mock for standalone frontend work.
    Imports are lazy so the fake path never pulls in the engine and vice-versa."""
    if ENGINE_MODE == "fake":
        from dashboard.simulator.fake_state import FakeStateGenerator
        return FakeStateGenerator(seed=seed)
    from dashboard.simulator.real_state import RealStateGenerator
    return RealStateGenerator(seed=seed)


class StateService:
    def __init__(self, seed: int | None = None) -> None:
        seed = ENGINE_SEED if seed is None else seed
        self.generator = _make_generator(seed)
        self.latest_state: Dict[str, Any] = self.generator.generate_state()
        self._loop_task: asyncio.Task | None = None
        self._running = False
        self._broadcast_callback: Callable[[Dict[str, Any]], Any] | None = None
        self._reset_lock = asyncio.Lock()

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

    async def reset(self) -> Dict[str, Any]:
        async with self._reset_lock:
            seed = secrets.randbelow(2**32)
            reset = getattr(self.generator, "reset", None)
            if reset is not None:
                reset(seed)
            else:
                self.generator = _make_generator(seed)
            self.latest_state = self.generator.generate_state()
            return self.latest_state
