"""The decision engine: holds heating state and decides each step."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from .boiler import BoilerState, step_boiler
from .faults import FaultTracker, WarmingWatch
from .model import (
    HUB,
    Boost,
    EventDemand,
    FaultKey,
    FaultKind,
    GlobalSettings,
    LogEntry,
    RoomInputs,
    RoomSettings,
    Snapshot,
    Source,
)
from .room import room_demand, room_status, wants_heat
from .schedule import event_demand
from .stats import Stats, accumulate
from .timefmt import format_clock, format_duration

VALVE_MISMATCH_AFTER = timedelta(seconds=90)
"""A valve not matching its command for this long (3 ticks) is a fault."""

BOOST_OFF = "Off"
"""The boost dropdown option that means 'no boost'."""

PRESS_ORIGIN = "Button"
"""Who started a boost with the physical button (spec §7.4)."""


class BoostError(Exception):
    """A boost request was rejected. The message is shown to the user."""


@dataclass
class RoomState:
    """What the engine remembers about one room between steps."""

    enabled: bool = True
    boost: Boost | None = None
    wants: bool = False


@dataclass(frozen=True)
class RoomDecision:
    """One room's decision for one step."""

    room_id: str
    source: Source
    target: float
    calling: bool
    valve_on: bool
    status: str
    boost: Boost | None
    event: EventDemand | None
    problem: bool


@dataclass(frozen=True)
class Decision:
    """Everything decided in one step."""

    rooms: dict[str, RoomDecision]
    boiler_on: bool
    boiler_status: str
    faults: dict[FaultKey, str]
    log: tuple[LogEntry, ...]


def boost_choices(settings: GlobalSettings) -> dict[str, timedelta]:
    """Dropdown options: 'Off', step multiples up to the maximum, and the default."""
    lengths: set[timedelta] = set()
    length = settings.boost_step
    while settings.boost_step > timedelta(0) and length <= settings.max_boost:
        lengths.add(length)
        length += settings.boost_step
    if timedelta(0) < settings.default_boost <= settings.max_boost:
        lengths.add(settings.default_boost)
    return {BOOST_OFF: timedelta(0)} | {
        format_duration(item): item for item in sorted(lengths)
    }


class Engine:
    """Holds heating state and turns snapshots into decisions."""

    def __init__(
        self, settings: GlobalSettings, rooms: Iterable[RoomSettings], now: datetime
    ) -> None:
        self.settings = settings
        self.rooms = {room.room_id: room for room in rooms}
        self.room_state = {room_id: RoomState() for room_id in self.rooms}
        self.boiler = BoilerState()
        self.stats = Stats()
        self.last_step: datetime | None = None
        self._faults = FaultTracker(started_at=now)
        self._warming = WarmingWatch()
        self._valve_on_since: dict[str, datetime] = {}
        self._mismatch_since: dict[str, datetime] = {}
        self._commanded: dict[str, bool] = {}
        self._prev_calling: frozenset[str] = frozenset()
        self._prev_boiler_on = False
        self._pending_log: list[LogEntry] = []

    def commanded_valve(self, room_id: str) -> bool:
        """The valve state the last step asked for (False before the first step)."""
        return self._commanded.get(room_id, False)

    def press(self, room_id: str, now: datetime, origin: str = PRESS_ORIGIN) -> bool:
        """Handle a Boost press (the physical button unless origin says otherwise).

        The boost ends at max(current end, now + default length). Returns True
        if the boost changed.
        """
        room = self.rooms[room_id]
        state = self.room_state[room_id]
        if not state.enabled:
            self._log(room, "button press ignored: room off")
            return False
        new_end = now + self.settings.default_boost
        current = state.boost
        if current is not None and current.ends >= new_end:
            pressed = "button pressed" if origin == PRESS_ORIGIN else f"boost pressed by {origin}"
            self._log(
                room,
                f"{pressed}; boost already runs until {_clock(current.ends, now)}",
            )
            return False
        state.boost = Boost(ends=new_end, length=self.settings.default_boost, origin=origin)
        self._started(room, state.boost, now)
        return True

    def boost(self, room_id: str, length: timedelta, origin: str, now: datetime) -> Boost:
        """Start or replace a boost of a chosen length (dashboard or action)."""
        room = self.rooms[room_id]
        state = self.room_state[room_id]
        if not state.enabled:
            raise BoostError("Room is off")
        if length <= timedelta(0) or length > self.settings.max_boost:
            raise BoostError(
                "Boost length must be more than 0 and at most "
                f"{format_duration(self.settings.max_boost)}"
            )
        state.boost = Boost(ends=now + length, length=length, origin=origin)
        self._started(room, state.boost, now)
        return state.boost

    def cancel_boost(self, room_id: str, origin: str, now: datetime) -> None:
        """End a running boost early."""
        state = self.room_state[room_id]
        if state.boost is not None and state.boost.ends > now:
            state.boost = None
            self._log(self.rooms[room_id], f"boost cancelled by {origin}")

    def set_enabled(self, room_id: str, enabled: bool) -> None:
        """Turn a room on or off. Off cancels its boost; frost protection stays."""
        state = self.room_state[room_id]
        if state.enabled == enabled:
            return
        state.enabled = enabled
        if not enabled:
            state.boost = None
        self._log(
            self.rooms[room_id],
            "turned on" if enabled else "turned off (frost protection only)",
        )

    def step(self, snapshot: Snapshot) -> Decision:
        """Decide valves, boiler, statuses and faults for this moment."""
        now = snapshot.now
        settings = self.settings
        self.stats.roll(now.date())
        if self.last_step is not None:
            accumulate(self.stats, now - self.last_step, self._prev_calling, self._prev_boiler_on)
        self.last_step = now
        log = self._pending_log
        self._pending_log = []
        conditions: dict[FaultKey, str] = {}
        calling: dict[str, str] = {}
        evaluated = {}

        for room_id, room in self.rooms.items():
            state = self.room_state[room_id]
            inputs = snapshot.rooms.get(room_id, RoomInputs(temperature=None, valve_on=None))
            if state.boost is not None and state.boost.ends <= now:
                state.boost = None
                log.append(LogEntry(room.name, "boost ended", room_id))
            self._track_valve(room_id, inputs.valve_on, now)
            event = event_demand(snapshot.events, room.tag, room.preheat, now)
            demand = room_demand(room, state.enabled, state.boost, event, now)
            fault = self._room_conditions(room, inputs, snapshot.check_valves, now, conditions)
            if inputs.temperature is None:
                state.wants = False
            else:
                state.wants = wants_heat(
                    inputs.temperature, demand.target, settings.margin, state.wants
                )
            room_calling = state.wants and fault is None
            if room_calling:
                calling[room_id] = room.name
            warming = self._warming.update(
                room_id, room_calling, inputs.temperature, settings, now
            )
            if warming is not None:
                conditions[FaultKey(room_id, FaultKind.NOT_WARMING)] = f"{room.name}: {warming}"
            evaluated[room_id] = (demand, fault)

        if not snapshot.boiler_available:
            conditions[FaultKey(HUB, FaultKind.BOILER_UNAVAILABLE)] = "Boiler switch unavailable"
        failing_since = snapshot.calendar_failing_since
        if failing_since is not None and now - failing_since >= settings.calendar_fail_after:
            conditions[FaultKey(HUB, FaultKind.CALENDAR_FAILING)] = (
                "Calendar could not be read for over "
                f"{format_duration(settings.calendar_fail_after)}"
            )

        boiler = step_boiler(self.boiler, settings, calling, self._valve_on_since, now)
        if boiler.turned_on:
            self.stats.boiler_starts += 1
            names = ", ".join(sorted(calling.values()))
            log.append(LogEntry("Boiler", f"turned on ({names} calling)"))
        if boiler.turned_off:
            log.append(LogEntry("Boiler", "turned off (no rooms calling)"))

        raised = self._faults.update(conditions, settings, now)
        rooms_out: dict[str, RoomDecision] = {}
        for room_id, (demand, fault) in evaluated.items():
            state = self.room_state[room_id]
            is_calling = room_id in calling
            held = room_id in boiler.held_rooms
            valve_on = state.wants or held
            if self._commanded.get(room_id) != valve_on:
                self._mismatch_since.pop(room_id, None)
            self._commanded[room_id] = valve_on
            rooms_out[room_id] = RoomDecision(
                room_id=room_id,
                source=demand.source,
                target=demand.target,
                calling=is_calling,
                valve_on=valve_on,
                status=room_status(
                    state.enabled, demand, is_calling, held and not is_calling, fault, now
                ),
                boost=state.boost,
                event=demand.event,
                problem=any(key.scope == room_id for key in raised),
            )
        self._prev_calling = frozenset(calling)
        self._prev_boiler_on = boiler.on
        return Decision(
            rooms=rooms_out,
            boiler_on=boiler.on,
            boiler_status=boiler.status,
            faults=raised,
            log=tuple(log),
        )

    def _room_conditions(
        self,
        room: RoomSettings,
        inputs: RoomInputs,
        check_valves: bool,
        now: datetime,
        conditions: dict[FaultKey, str],
    ) -> str | None:
        """Add the room's fault conditions; return its heating fault text, if any."""
        fault: str | None = None
        if inputs.temperature is None:
            conditions[FaultKey(room.room_id, FaultKind.TEMP_UNAVAILABLE)] = (
                f"{room.name}: temperature sensor unavailable"
            )
            fault = "no temperature reading"
        if inputs.valve_on is None:
            conditions[FaultKey(room.room_id, FaultKind.VALVE_UNAVAILABLE)] = (
                f"{room.name}: valve switch unavailable"
            )
            fault = fault or "valve unavailable"
            # A mismatch from before the outage must not fault the moment it is back.
            self._mismatch_since.pop(room.room_id, None)
        elif not check_valves:
            self._mismatch_since.pop(room.room_id, None)
        elif self._valve_mismatched(room.room_id, inputs.valve_on, now):
            conditions[FaultKey(room.room_id, FaultKind.VALVE_MISMATCH)] = (
                f"{room.name}: valve is not following commands"
            )
            fault = fault or "valve not responding"
        if room.has_button and not inputs.button_available:
            conditions[FaultKey(room.room_id, FaultKind.BUTTON_UNAVAILABLE)] = (
                f"{room.name}: button unavailable"
            )
        return fault

    def _valve_mismatched(self, room_id: str, reported: bool, now: datetime) -> bool:
        commanded = self._commanded.get(room_id)
        if commanded is None or commanded == reported:
            self._mismatch_since.pop(room_id, None)
            return False
        since = self._mismatch_since.setdefault(room_id, now)
        return now - since >= VALVE_MISMATCH_AFTER

    def _track_valve(self, room_id: str, reported: bool | None, now: datetime) -> None:
        if reported:
            self._valve_on_since.setdefault(room_id, now)
        else:
            self._valve_on_since.pop(room_id, None)

    def _started(self, room: RoomSettings, boost: Boost, now: datetime) -> None:
        self.stats.roll(now.date())
        self.stats.room(room.room_id).boosts += 1
        self._log(room, f"boost until {_clock(boost.ends, now)} (started by {boost.origin})")

    def _log(self, room: RoomSettings, message: str) -> None:
        self._pending_log.append(LogEntry(room.name, message, room.room_id))


def _clock(moment: datetime, now: datetime) -> str:
    return format_clock(moment, now.tzinfo)
