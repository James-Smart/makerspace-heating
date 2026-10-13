"""Save and restore engine state as JSON-compatible dicts (spec §8.2)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, timedelta
from typing import Any

from .boiler import BoilerState
from .engine import Engine
from .model import HUB, Boost, CalendarEvent, FaultKey, FaultKind
from .stats import RoomStats, Stats

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1


def dump(engine: Engine) -> dict[str, Any]:
    """Return the engine state that should survive a restart."""
    boiler = engine.boiler
    stats = engine.stats
    return {
        "rooms": {
            room_id: {"enabled": state.enabled, "boost": _dump_boost(state.boost)}
            for room_id, state in engine.room_state.items()
        },
        "boiler": {
            "on": boiler.on,
            "last_on": _dump_time(boiler.last_on),
            "last_off": _dump_time(boiler.last_off),
            "held_rooms": sorted(boiler.held_rooms),
            "last_open_calling": sorted(boiler.last_open_calling),
            "hold_until": _dump_time(boiler.hold_until),
        },
        "stats": {
            "day": stats.day.isoformat() if stats.day else None,
            "rooms": {
                room_id: {"heating_seconds": item.heating_seconds, "boosts": item.boosts}
                for room_id, item in stats.rooms.items()
            },
            "boiler_on_seconds": stats.boiler_on_seconds,
            "boiler_starts": stats.boiler_starts,
        },
        "last_step": _dump_time(engine.last_step),
    }


def restore(
    engine: Engine, data: Mapping[str, Any], now: datetime, keep_boiler: bool = False
) -> None:
    """Load saved state into a fresh engine.

    Rooms that no longer exist are dropped. An unreadable section is skipped
    and the engine keeps its defaults for it. keep_boiler is True when the
    state was handed over moments ago by a reload that switches the same
    devices in the same mode: a running boiler then stays on, so a settings
    change does not short-cycle it.
    """
    sections: dict[str, Callable[[], None]] = {
        "rooms": lambda: _restore_rooms(engine, data, now),
        "boiler": lambda: _restore_boiler(engine, data, now, keep_boiler),
        "stats": lambda: _restore_stats(engine, data, now),
        "last_step": lambda: _restore_last_step(engine, data, now),
    }
    for name, section in sections.items():
        try:
            section()
        except (AttributeError, KeyError, TypeError, ValueError):
            _LOGGER.warning("Ignoring unreadable saved state in %s", name)


def _restore_rooms(engine: Engine, data: Mapping[str, Any], now: datetime) -> None:
    for room_id, item in data.get("rooms", {}).items():
        state = engine.room_state.get(room_id)
        if state is None:
            continue
        state.enabled = bool(item.get("enabled", True))
        boost = _load_boost(item.get("boost"))
        state.boost = boost if boost is not None and boost.ends > now and state.enabled else None


def _restore_boiler(
    engine: Engine, data: Mapping[str, Any], now: datetime, keep_boiler: bool
) -> None:
    item = data.get("boiler", {})
    boiler = BoilerState(
        last_on=_load_time(item.get("last_on")),
        last_off=_load_time(item.get("last_off")),
    )
    known = engine.rooms
    open_calling = frozenset(
        room for room in item.get("last_open_calling", []) if room in known
    )
    if item.get("on") and keep_boiler and open_calling:
        # Handed over by a reload: the boiler keeps running, and the valves
        # that were open keep it company.
        boiler.on = True
        boiler.last_open_calling = open_calling
    elif item.get("on"):
        # The boiler is switched off at startup (or its open rooms have gone):
        # rest counts from now and the valves that were open stay open for the
        # keep-open time.
        boiler.last_off = now
        if engine.settings.keep_open > timedelta(0):
            boiler.held_rooms = open_calling
            boiler.hold_until = now + engine.settings.keep_open
    else:
        hold_until = _load_time(item.get("hold_until"))
        if hold_until is not None and hold_until > now:
            boiler.held_rooms = frozenset(
                room for room in item.get("held_rooms", []) if room in known
            )
            boiler.hold_until = hold_until
    engine.boiler = boiler


def _restore_stats(engine: Engine, data: Mapping[str, Any], now: datetime) -> None:
    item = data.get("stats", {})
    day = item.get("day")
    engine.stats = Stats(
        day=date.fromisoformat(day) if day is not None else None,
        rooms={
            room_id: RoomStats(
                heating_seconds=float(values.get("heating_seconds", 0.0)),
                boosts=int(values.get("boosts", 0)),
            )
            for room_id, values in item.get("rooms", {}).items()
            if room_id in engine.rooms
        },
        boiler_on_seconds=float(item.get("boiler_on_seconds", 0.0)),
        boiler_starts=int(item.get("boiler_starts", 0)),
    )


def _restore_last_step(engine: Engine, data: Mapping[str, Any], now: datetime) -> None:
    engine.last_step = _load_time(data.get("last_step"))


def dump_handover(engine: Engine) -> dict[str, Any]:
    """Return the fault timing and valve tracking a reload hands to the next engine.

    This is not kept across a restart, which starts both afresh.
    """
    return {
        "faults_started_at": engine.faults.started_at.isoformat(),
        "fault_since": [
            {"scope": key.scope, "kind": key.kind.value, "since": since.isoformat()}
            for key, since in engine.faults.since.items()
        ],
        "warming": {
            room_id: {"since": since.isoformat(), "temperature": temperature}
            for room_id, (since, temperature) in engine.warming.tracks.items()
        },
        "commanded": dict(engine.commanded),
        "valve_on_since": _dump_times(engine.valve_on_since),
        "mismatch_since": _dump_times(engine.mismatch_since),
    }


def restore_handover(engine: Engine, data: Any, devices_unchanged: bool) -> None:
    """Load what a reload handed over into a fresh engine.

    Fault timing carries on, so a raised fault stays raised and a pending
    one keeps its delay. Valve tracking (commands, open and mismatch times)
    only carries on when the devices and mode are unchanged. Rooms that no
    longer exist are dropped; an unreadable section keeps its defaults.
    """
    if not isinstance(data, Mapping):
        _LOGGER.warning("Ignoring unreadable handover state")
        return
    sections: dict[str, Callable[[], None]] = {
        "faults": lambda: _restore_faults(engine, data),
        "warming": lambda: _restore_warming(engine, data),
    }
    if devices_unchanged:
        sections["valves"] = lambda: _restore_valves(engine, data)
    for name, section in sections.items():
        try:
            section()
        except Exception:  # noqa: BLE001 - odd saved state must never stop setup
            _LOGGER.warning("Ignoring unreadable handover state in %s", name)


def _restore_faults(engine: Engine, data: Mapping[str, Any]) -> None:
    started_at = _load_moment(data["faults_started_at"])
    since: dict[FaultKey, datetime] = {}
    for item in data["fault_since"]:
        key = FaultKey(str(item["scope"]), FaultKind(item["kind"]))
        if key.scope == HUB or key.scope in engine.rooms:
            since[key] = _load_moment(item["since"])
    engine.faults.started_at = started_at
    engine.faults.since = since


def _restore_warming(engine: Engine, data: Mapping[str, Any]) -> None:
    engine.warming.tracks = {
        room_id: (_load_moment(item["since"]), float(item["temperature"]))
        for room_id, item in data["warming"].items()
        if room_id in engine.rooms
    }


def _restore_valves(engine: Engine, data: Mapping[str, Any]) -> None:
    commanded: dict[str, bool] = {}
    for room_id, on in data["commanded"].items():
        if not isinstance(on, bool):
            raise TypeError(f"commanded valve state is not true/false: {on!r}")
        if room_id in engine.rooms:
            commanded[room_id] = on
    valve_on_since = _load_times(data["valve_on_since"], engine.rooms)
    mismatch_since = _load_times(data["mismatch_since"], engine.rooms)
    engine.commanded = commanded
    engine.valve_on_since = valve_on_since
    engine.mismatch_since = mismatch_since


def dump_events(events: Iterable[CalendarEvent]) -> list[dict[str, Any]]:
    """Return the cached calendar events as JSON-compatible dicts."""
    return [
        {
            "summary": event.summary,
            "start": event.start.isoformat(),
            "end": event.end.isoformat(),
            "tags": sorted(event.tags),
        }
        for event in events
    ]


def load_events(raw: Any) -> tuple[CalendarEvent, ...]:
    """Rebuild saved calendar events, skipping malformed items."""
    if not isinstance(raw, list):
        return ()
    events: list[CalendarEvent] = []
    for item in raw:
        try:
            start = datetime.fromisoformat(item["start"])
            end = datetime.fromisoformat(item["end"])
            tags = item.get("tags", [])
            if start.tzinfo is None or end.tzinfo is None or not isinstance(tags, list):
                continue
            events.append(
                CalendarEvent(
                    summary=str(item["summary"]),
                    start=start,
                    end=end,
                    tags=frozenset(str(tag) for tag in tags),
                )
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return tuple(events)


def _dump_time(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def _load_time(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


def _load_moment(text: Any) -> datetime:
    """Parse a saved time, which must carry a time zone."""
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise ValueError(f"saved time has no time zone: {text}")
    return moment


def _dump_times(times: Mapping[str, datetime]) -> dict[str, str]:
    return {room_id: moment.isoformat() for room_id, moment in times.items()}


def _load_times(raw: Mapping[str, Any], known: Mapping[str, Any]) -> dict[str, datetime]:
    return {room_id: _load_moment(text) for room_id, text in raw.items() if room_id in known}


def _dump_boost(boost: Boost | None) -> dict[str, Any] | None:
    if boost is None:
        return None
    return {
        "ends": boost.ends.isoformat(),
        "length_seconds": boost.length.total_seconds(),
        "origin": boost.origin,
    }


def _load_boost(item: Mapping[str, Any] | None) -> Boost | None:
    if not item:
        return None
    return Boost(
        ends=datetime.fromisoformat(item["ends"]),
        length=timedelta(seconds=float(item["length_seconds"])),
        origin=str(item.get("origin", "Unknown")),
    )
