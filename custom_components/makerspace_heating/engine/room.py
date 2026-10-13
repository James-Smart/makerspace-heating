"""Per-room demand, thermostat and status decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .model import Boost, EventDemand, RoomSettings, Source
from .timefmt import format_clock

_TIE_ORDER = {Source.EVENT: 0, Source.BOOST: 1, Source.FROST: 2}


@dataclass(frozen=True)
class Demand:
    """The room's winning source and target, plus the active boost and event."""

    source: Source
    target: float
    boost: Boost | None
    event: EventDemand | None


def room_demand(
    settings: RoomSettings,
    enabled: bool,
    boost: Boost | None,
    event: EventDemand | None,
    now: datetime,
) -> Demand:
    """Pick the highest target among active sources (ties: event, boost, frost).

    Frost is always active. Events and boosts count only while the room is
    enabled, and a boost only until it ends.
    """
    active_boost = boost if enabled and boost is not None and boost.ends > now else None
    active_event = event if enabled else None
    candidates = [(Source.FROST, settings.frost_target)]
    if active_event is not None:
        candidates.append((Source.EVENT, settings.event_target))
    if active_boost is not None:
        candidates.append((Source.BOOST, settings.boost_target))
    source, target = max(candidates, key=lambda item: (item[1], -_TIE_ORDER[item[0]]))
    return Demand(source=source, target=target, boost=active_boost, event=active_event)


def wants_heat(temperature: float, target: float, margin: float, was_wanting: bool) -> bool:
    """Thermostat with a margin: open at target − margin, close at target."""
    if temperature <= round(target - margin, 3):
        return True
    if temperature >= target:
        return False
    return was_wanting


def room_status(
    enabled: bool,
    demand: Demand,
    calling: bool,
    held: bool,
    fault: str | None,
    now: datetime,
) -> str:
    """Plain-English status naming the winning source (spec §3.6)."""
    if fault is not None:
        return f"Fault: {fault}"
    if calling:
        suffix = ", heating"
    elif held:
        suffix = ", valve held open for boiler"
    else:
        suffix = ", at temperature"
    zone = now.tzinfo
    if not enabled:
        return "Off (frost protection only)" + (suffix if calling or held else "")
    if demand.source is Source.EVENT and demand.event is not None:
        event = demand.event
        if event.running:
            return f"Event: {event.summary} until {format_clock(event.end, zone)}{suffix}"
        return (
            f"Pre-heating for {event.summary} "
            f"(starts {format_clock(event.start, zone)}){suffix}"
        )
    if demand.source is Source.BOOST and demand.boost is not None:
        return f"Boost until {format_clock(demand.boost.ends, zone)}{suffix}"
    return "Frost protection" + (suffix if calling or held else "")
