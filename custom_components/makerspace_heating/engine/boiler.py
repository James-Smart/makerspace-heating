"""Boiler on/off decisions with valve protections (spec §4)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from .model import GlobalSettings
from .timefmt import format_duration

_ZERO = timedelta(0)


@dataclass
class BoilerState:
    """What the boiler logic remembers between steps."""

    on: bool = False
    last_on: datetime | None = None
    last_off: datetime | None = None
    last_open_calling: frozenset[str] = frozenset()
    held_rooms: frozenset[str] = frozenset()
    hold_until: datetime | None = None


@dataclass(frozen=True)
class BoilerDecision:
    """The boiler decision for one step."""

    on: bool
    status: str
    held_rooms: frozenset[str]
    turned_on: bool = False
    turned_off: bool = False


def step_boiler(
    state: BoilerState,
    settings: GlobalSettings,
    calling: Mapping[str, str],
    valve_on_since: Mapping[str, datetime],
    now: datetime,
) -> BoilerDecision:
    """Decide the boiler for this step, updating state in place.

    calling maps room id -> room name for rooms calling for heat.
    valve_on_since maps room id -> when its valve was first seen reporting on.
    A calling room counts as open once its valve has reported on for the
    valve wait: a relay reports on long before its thermal actuator opens.
    """
    ready_calling = frozenset(
        room
        for room in calling
        if room in valve_on_since and now - valve_on_since[room] >= settings.valve_wait
    )
    turned_on = turned_off = False

    if state.on:
        if ready_calling:
            state.last_open_calling = ready_calling
        if calling:
            status = f"On: {_names(calling)} calling"
        elif state.last_on is not None and now - state.last_on < settings.min_run:
            status = "On: holding for minimum run time"
        else:
            state.on = False
            state.last_off = now
            turned_off = True
            if settings.keep_open > _ZERO:
                state.held_rooms = state.last_open_calling
                state.hold_until = now + settings.keep_open
            status = "Off: no rooms calling"
    else:
        rest_left = (
            settings.min_rest - (now - state.last_off)
            if state.last_off is not None
            else _ZERO
        )
        if not calling:
            status = "Off: no rooms calling"
        elif rest_left > _ZERO:
            status = f"Off: resting ({format_duration(rest_left)} left)"
        elif ready_calling:
            state.on = True
            state.last_on = now
            state.last_open_calling = ready_calling
            state.held_rooms = frozenset()
            state.hold_until = None
            turned_on = True
            status = f"On: {_names(calling)} calling"
        else:
            status = (
                "Waiting for valves: "
                f"{_waiting(calling, valve_on_since, settings.valve_wait, now)}"
            )

    if state.on:
        held = frozenset() if ready_calling else state.last_open_calling
    elif state.hold_until is not None and now < state.hold_until:
        held = state.held_rooms
    else:
        state.held_rooms = frozenset()
        state.hold_until = None
        held = frozenset()
    return BoilerDecision(
        on=state.on,
        status=status,
        held_rooms=held,
        turned_on=turned_on,
        turned_off=turned_off,
    )


def _names(calling: Mapping[str, str]) -> str:
    return ", ".join(sorted(calling.values()))


def _waiting(
    calling: Mapping[str, str],
    valve_on_since: Mapping[str, datetime],
    wait: timedelta,
    now: datetime,
) -> str:
    best: tuple[timedelta, str] | None = None
    for room, name in calling.items():
        since = valve_on_since.get(room)
        if since is None:
            continue
        left = wait - (now - since)
        if best is None or left < best[0]:
            best = (left, name)
    if best is None:
        return f"{_names(calling)} (valve not open yet)"
    return f"{best[1]} ({format_duration(best[0])})"
