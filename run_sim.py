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
        prob=swarm.store.prob_grid(world.cfg.grid_w, world.cfg.grid_h),
        agents=swarm.snapshots(),
        tasks=[t.as_dict() for t in swarm.tasks.values()],
        bids=swarm.last_bids,
        survivors=swarm.store.survivors(),
        mission_complete=mission_complete(world, swarm),
    )


def mission_complete(world: World, swarm: Swarm, coverage_target: float = 0.92) -> bool:
    """Done when the area is covered AND every candidate has resolved to
    confirmed or dismissed — you don't stop a rescue with sightings unchecked."""
    if world.coverage() < coverage_target:
        return False
    if any(swarm.detection_buffer.values()):
        return False                          # a solo agent still holds unmerged finds
    return swarm.store.counts()["candidate"] == 0


def tick_once(world: World, swarm: Swarm):
    """One simulation tick: sync brain<-world, (periodically) auction, drive,
    step the world, resolve arrivals."""
    swarm.sync_from_world(world)
    swarm.update_connectivity(world.cfg.base_xy)
    swarm.check_battery()

    if world.tick % AUCTION_EVERY == 0:
        swarm.rebuild_frontier_tasks(world.known, world.prob,
                                     world.cfg.grid_w, world.cfg.grid_h)
        swarm.rebuild_reobserve_tasks()
        swarm.run_auction(world.t)

    for aid, goal in swarm.commands().items():
        world.command(aid, goal)

    detections = world.step()
    swarm.route_detections(detections)      # solo agents' finds are buffered
    swarm.store.update_statuses()
    swarm.resolve_arrivals()
    return detections                        # let callers (dashboard adapter) surface them


def run(ticks: int = 1500, quiet: bool = False, seed: int | None = 0):
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
            counts = swarm.store.counts()
            print(f"t={world.t:6.1f}s  tick={world.tick:4d}  "
                  f"coverage={cov:5.1f}%  agents={alive}/{wcfg.n_agents}  "
                  f"sensed={found}/{wcfg.n_survivors}  "
                  f"confirmed={counts['confirmed']}  "
                  f"candidates={counts['candidate']}  "
                  f"dismissed={counts['dismissed']}")

        if mission_complete(world, swarm):
            counts = swarm.store.counts()
            print(f"\n>>> MISSION COMPLETE at t={world.t:.1f}s  "
                  f"coverage={world.coverage()*100:.1f}%  "
                  f"sensed={len(world.found_gt)}/{wcfg.n_survivors}  "
                  f"confirmed={counts['confirmed']}  dismissed={counts['dismissed']}")
            break
    else:
        counts = swarm.store.counts()
        print(f"\n>>> ended (max ticks)  coverage={world.coverage()*100:.1f}%  "
              f"sensed={len(world.found_gt)}/{wcfg.n_survivors}  "
              f"confirmed={counts['confirmed']}  dismissed={counts['dismissed']}")

    return world, swarm


def run_headless(ticks: int = 1500, seed: int | None = 0):
    """No printing — for the metrics harness. Returns (world, swarm)."""
    return run(ticks=ticks, quiet=True, seed=seed)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", type=int, default=1500)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    run(ticks=args.ticks, quiet=args.quiet, seed=args.seed)
