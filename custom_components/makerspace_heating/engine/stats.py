"""Daily runtime statistics (spec §7.1, §7.2)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta

MAX_STEP_GAP = timedelta(seconds=60)
"""Longest interval credited by one step, so downtime is never counted."""


@dataclass
class RoomStats:
    """Today's counters for one room."""

    heating_seconds: float = 0.0
    boosts: int = 0


@dataclass
class Stats:
    """Today's counters for all rooms and the boiler."""

    day: date | None = None
    rooms: dict[str, RoomStats] = field(default_factory=dict)
    boiler_on_seconds: float = 0.0
    boiler_starts: int = 0

    def roll(self, today: date) -> None:
        """Reset every counter when the local date changes."""
        if self.day != today:
            self.day = today
            self.rooms = {}
            self.boiler_on_seconds = 0.0
            self.boiler_starts = 0

    def room(self, room_id: str) -> RoomStats:
        """Return (creating if needed) one room's counters."""
        return self.rooms.setdefault(room_id, RoomStats())


def accumulate(
    stats: Stats, elapsed: timedelta, calling_rooms: Iterable[str], boiler_on: bool
) -> None:
    """Credit the interval since the last step to calling rooms and the boiler."""
    seconds = min(elapsed, MAX_STEP_GAP).total_seconds()
    if seconds <= 0:
        return
    for room_id in calling_rooms:
        stats.room(room_id).heating_seconds += seconds
    if boiler_on:
        stats.boiler_on_seconds += seconds
