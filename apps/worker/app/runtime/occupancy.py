"""Shared GPU occupancy across worker runtime families."""

from __future__ import annotations

from collections import Counter
from typing import Any, Protocol


class OccupyingRuntime(Protocol):
    def occupied_gpus(self, *, exclude: str | None = None) -> set[int]: ...

    def lane_counts(self, *, exclude: str | None = None) -> dict[int, int]: ...


def union_lane_counts(
    *managers: Any,
    exclude: str | None = None,
) -> dict[int, int]:
    """Sum per-GPU lane occupancy across every active runtime manager."""
    totals: Counter[int] = Counter()
    for manager in managers:
        if manager is None:
            continue
        if not (
            getattr(manager, "enabled", False) or getattr(manager, "dry_run", False)
        ):
            continue
        method = getattr(manager, "lane_counts", None)
        if callable(method):
            for index, count in method(exclude=exclude).items():
                totals[int(index)] += int(count)
            continue
        # Backward-compatible fallback: occupied_gpus ⇒ 1 lane each.
        occupied = getattr(manager, "occupied_gpus", None)
        if callable(occupied):
            for index in occupied(exclude=exclude):
                totals[int(index)] += 1
    return dict(totals)


def union_occupied_gpus(
    *managers: Any,
    exclude: str | None = None,
    max_lanes_per_gpu: int = 3,
) -> set[int]:
    """GPUs that are at or above the soft lane cap (not merely non-empty)."""
    counts = union_lane_counts(*managers, exclude=exclude)
    return {index for index, count in counts.items() if count >= max_lanes_per_gpu}
