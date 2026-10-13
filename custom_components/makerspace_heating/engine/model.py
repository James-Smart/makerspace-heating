"""Plain data types used by the decision engine.

The controller converts Home Assistant state into these types and back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

HUB = "hub"
"""Fault scope for faults that belong to the hub rather than a room."""


class Source(StrEnum):
    """A source of heat demand for a room."""

    EVENT = "event"
    BOOST = "boost"
    FROST = "frost"


class FaultKind(StrEnum):
    """Kinds of fault the engine detects."""

    TEMP_UNAVAILABLE = "temp_unavailable"
    VALVE_UNAVAILABLE = "valve_unavailable"
    VALVE_MISMATCH = "valve_mismatch"
    BUTTON_UNAVAILABLE = "button_unavailable"
    NOT_WARMING = "not_warming"
    BOILER_UNAVAILABLE = "boiler_unavailable"
    CALENDAR_FAILING = "calendar_failing"


@dataclass(frozen=True)
class FaultKey:
    """Identifies one fault: a room id (or HUB) and the kind of fault."""

    scope: str
    kind: FaultKind


@dataclass(frozen=True)
class GlobalSettings:
    """Settings shared by all rooms (the admin options)."""

    default_boost: timedelta = timedelta(minutes=30)
    max_boost: timedelta = timedelta(hours=2)
    boost_step: timedelta = timedelta(minutes=30)
    margin: float = 0.3
    valve_wait: timedelta = timedelta(minutes=3)
    min_run: timedelta = timedelta(minutes=5)
    min_rest: timedelta = timedelta(minutes=5)
    keep_open: timedelta = timedelta(minutes=3)
    fault_delay: timedelta = timedelta(minutes=5)
    not_warming_after: timedelta = timedelta(minutes=60)
    not_warming_rise: float = 0.5
    calendar_fail_after: timedelta = timedelta(minutes=30)


@dataclass(frozen=True)
class RoomSettings:
    """Settings for one room (a room sub-entry)."""

    room_id: str
    name: str
    tag: str
    boost_target: float = 19.0
    event_target: float = 20.0
    frost_target: float = 7.0
    preheat: timedelta = timedelta(minutes=30)
    has_button: bool = False


@dataclass(frozen=True)
class Boost:
    """A running boost."""

    ends: datetime
    length: timedelta
    origin: str


@dataclass(frozen=True)
class CalendarEvent:
    """A timed calendar event and the room tags it carries."""

    summary: str
    start: datetime
    end: datetime
    tags: frozenset[str] = frozenset()


@dataclass(frozen=True)
class EventDemand:
    """The calendar event currently driving a room's event source."""

    summary: str
    start: datetime
    end: datetime
    running: bool


@dataclass(frozen=True)
class RoomInputs:
    """What the controller observed for one room.

    temperature is in °C, or None when unusable. valve_on is None when the
    valve switch is unavailable.
    """

    temperature: float | None
    valve_on: bool | None
    button_available: bool = True


@dataclass(frozen=True)
class Snapshot:
    """Everything the engine needs for one step."""

    now: datetime
    rooms: dict[str, RoomInputs] = field(default_factory=dict)
    boiler_available: bool = True
    events: tuple[CalendarEvent, ...] = ()
    calendar_failing_since: datetime | None = None
    check_valves: bool = True


@dataclass(frozen=True)
class LogEntry:
    """A logbook line, shown by Home Assistant as '<name> <message>'."""

    name: str
    message: str
    room_id: str | None = None
