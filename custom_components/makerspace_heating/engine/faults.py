"""Fault debounce and the heating-but-not-warming watch (spec §7.5)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta

from .model import FaultKey, FaultKind, GlobalSettings
from .timefmt import format_duration

STARTUP_GRACE = timedelta(minutes=2)
"""No faults are raised this soon after the engine starts."""

_SELF_TIMED = frozenset({FaultKind.NOT_WARMING, FaultKind.CALENDAR_FAILING})
"""Kinds whose condition already includes its own threshold."""


class FaultTracker:
    """Turns fault conditions into raised faults once they have lasted long enough."""

    def __init__(self, started_at: datetime) -> None:
        self._started_at = started_at
        self._since: dict[FaultKey, datetime] = {}

    def update(
        self, conditions: Mapping[FaultKey, str], settings: GlobalSettings, now: datetime
    ) -> dict[FaultKey, str]:
        """Record this step's conditions and return the raised ones (key -> message)."""
        for key in list(self._since):
            if key not in conditions:
                del self._since[key]
        for key in conditions:
            self._since.setdefault(key, now)
        if now - self._started_at < STARTUP_GRACE:
            return {}
        return {
            key: message
            for key, message in conditions.items()
            if now - self._since[key] >= _delay(key, settings)
        }


def _delay(key: FaultKey, settings: GlobalSettings) -> timedelta:
    return timedelta(0) if key.kind in _SELF_TIMED else settings.fault_delay


class WarmingWatch:
    """Spots rooms that call for heat for a long time without warming up."""

    def __init__(self) -> None:
        self._tracks: dict[str, tuple[datetime, float]] = {}

    def update(
        self,
        room_id: str,
        calling: bool,
        temperature: float | None,
        settings: GlobalSettings,
        now: datetime,
    ) -> str | None:
        """Return a fault message if the room has called too long without warming."""
        if not calling or temperature is None:
            self._tracks.pop(room_id, None)
            return None
        since, start_temperature = self._tracks.setdefault(room_id, (now, temperature))
        rise = temperature - start_temperature
        if rise >= settings.not_warming_rise:
            self._tracks[room_id] = (now, temperature)
            return None
        if now - since >= settings.not_warming_after:
            return (
                f"heating for over {format_duration(settings.not_warming_after)}, "
                f"only warmed {max(rise, 0.0):.1f} °C. "
                "Stuck valve, airlock or window open?"
            )
        return None
