"""Save and restore engine state as JSON-compatible dicts (spec §8.2)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from typing import Any

from .boiler import BoilerState
from .engine import Engine
from .model import Boost
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


def restore(engine: Engine, data: Mapping[str, Any], now: datetime) -> None:
    """Load saved state into a fresh engine.

    Rooms that no longer exist are dropped. An unreadable section is skipped
    and the engine keeps its defaults for it.
    """
    sections: tuple[Callable[[Engine, Mapping[str, Any], datetime], None], ...] = (
        _restore_rooms,
        _restore_boiler,
        _restore_stats,
        _restore_last_step,
    )
    for section in sections:
        try:
            section(engine, data, now)
        except (AttributeError, KeyError, TypeError, ValueError):
            _LOGGER.warning("Ignoring unreadable saved state in %s", section.__name__)


def _restore_rooms(engine: Engine, data: Mapping[str, Any], now: datetime) -> None:
    for room_id, item in data.get("rooms", {}).items():
        state = engine.room_state.get(room_id)
        if state is None:
            continue
        state.enabled = bool(item.get("enabled", True))
        boost = _load_boost(item.get("boost"))
        state.boost = boost if boost is not None and boost.ends > now and state.enabled else None


def _restore_boiler(engine: Engine, data: Mapping[str, Any], now: datetime) -> None:
    item = data.get("boiler", {})
    boiler = BoilerState(
        last_on=_load_time(item.get("last_on")),
        last_off=_load_time(item.get("last_off")),
    )
    known = engine.rooms
    if item.get("on"):
        # The boiler is switched off at startup: rest counts from now and the
        # valves that were open stay open for the keep-open time.
        boiler.last_off = now
        if engine.settings.keep_open > timedelta(0):
            boiler.held_rooms = frozenset(
                room for room in item.get("last_open_calling", []) if room in known
            )
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


def _dump_time(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def _load_time(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


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
