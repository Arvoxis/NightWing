"""
KHOJ — run loop (v0).

Wires the world and the swarm brain together and ticks the mission. Prints a
compact status line so you can watch the swarm self-organize and cover the map
from a terminal. This is also the harness the dashboard and metrics hook into
later (via build_state / run_headless).

Usage:
    python run_sim.py                 # live-ish text view
    python run_sim.py --ticks 400     # run N ticks
    python run_sim.py --quiet         # no per-tick printing, just the summary
"""

from __future__ import annotations

import argparse

from world import World, WorldConfig
from swarm import Swarm, BrainConfig
from protocol import SimState


AUCTION_EVERY = 5   # rebuild frontiers + run auction every N ticks (~0.5s)


def build_state(world: World, swarm: Swarm) -> SimState:
    """Assemble the SimState the dashboard renders. THE contract for Person B."""
    return SimState(
        t=round(world.t, 2),
        tick=world.tick,
        grid_w=world.cfg.grid_w,
        grid_h=world.cfg.grid_h,
        known=world.known,
        prob=world.prob,
        agents=[b.snapshot().as_dict() for b in swarm.brains.values()],
        tasks=[t.as_dict() for t in swarm.tasks.values()],
        bids=swarm.last_bids,
        survivors=[],  # populated in v1 (re-observation + fusion)
        mission_complete=mission_complete(world),
    )


def mission_complete(world: World, coverage_target: float = 0.92) -> bool:
    return world.coverage() >= coverage_target


def tick_once(world: World, swarm: Swarm):
    """One simulation tick: sync brain<-world, (periodically) auction, drive,
    step the world, resolve arrivals."""
    swarm.sync_from_world(world)

    if world.tick % AUCTION_EVERY == 0:
        swarm.rebuild_frontier_tasks(world.known, world.prob,
                                     world.cfg.grid_w, world.cfg.grid_h)
        swarm.run_auction(world.t)

    for aid, goal in swarm.commands().items():
        world.command(aid, goal)

    world.step()
    swarm.resolve_arrivals()


def run(ticks: int = 600, quiet: bool = False, seed: int | None = 0):
    wcfg = WorldConfig(seed=seed)
    world = World(wcfg)
    # keep brain speed in sync with the world so cost==time is honest
    swarm = Swarm(list(world.agents.keys()), BrainConfig(speed=wcfg.agent_speed))

    for _ in range(ticks):
        tick_once(world, swarm)

        if not quiet and world.tick % 10 == 0:
            cov = world.coverage() * 100
            alive = len(world.alive_agents())
            found = len(world.found_gt)
            open_tasks = sum(1 for t in swarm.tasks.values() if t.open)
            print(f"t={world.t:6.1f}s  tick={world.tick:4d}  "
                  f"coverage={cov:5.1f}%  agents={alive}/{wcfg.n_agents}  "
                  f"survivors_sensed={found}/{wcfg.n_survivors}  "
                  f"open_tasks={open_tasks}")

        if mission_complete(world):
            print(f"\n>>> MISSION COMPLETE at t={world.t:.1f}s  "
                  f"coverage={world.coverage()*100:.1f}%  "
                  f"survivors_sensed={len(world.found_gt)}/{wcfg.n_survivors}")
            break
    else:
        print(f"\n>>> ended (max ticks)  coverage={world.coverage()*100:.1f}%  "
              f"survivors_sensed={len(world.found_gt)}/{wcfg.n_survivors}")

    return world, swarm


def run_headless(ticks: int = 600, seed: int | None = 0):
    """No printing — for the metrics harness. Returns (world, swarm)."""
    return run(ticks=ticks, quiet=True, seed=seed)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=600)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    run(ticks=args.ticks, quiet=args.quiet, seed=args.seed)
