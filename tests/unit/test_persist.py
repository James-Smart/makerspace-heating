"""Tests for saving and restoring engine state (spec §8.2)."""

import json
from datetime import timedelta

from custom_components.makerspace_heating.engine.engine import Engine
from custom_components.makerspace_heating.engine.model import (
    CalendarEvent,
    GlobalSettings,
    RoomInputs,
    RoomSettings,
    Snapshot,
)
from custom_components.makerspace_heating.engine.persist import (
    dump,
    dump_events,
    load_events,
    restore,
)
from tests.unit.helpers import at

WORKSHOP = RoomSettings(room_id="w", name="Workshop", tag="workshop")
MAIN = RoomSettings(room_id="m", name="Main Space", tag="main")


def make_engine(now=None) -> Engine:
    return Engine(GlobalSettings(), [WORKSHOP, MAIN], now or at(8))


def snap(now, valve: bool) -> Snapshot:
    return Snapshot(
        now=now,
        rooms={"w": RoomInputs(18.0, valve), "m": RoomInputs(18.0, False)},
    )


def saved(engine: Engine) -> dict:
    return json.loads(json.dumps(dump(engine)))


def test_round_trip_while_boiler_was_on() -> None:
    engine = make_engine()
    engine.press("w", at(10))
    engine.set_enabled("m", False)
    engine.step(snap(at(10), valve=True))
    engine.step(snap(at(10, 3), valve=True))
    assert engine.boiler.on

    fresh = make_engine(at(10, 4))
    restore(fresh, saved(engine), at(10, 4))

    assert fresh.room_state["w"].boost == engine.room_state["w"].boost
    assert fresh.room_state["m"].enabled is False
    assert fresh.stats.room("w").boosts == 1
    assert fresh.boiler.on is False
    assert fresh.boiler.last_off == at(10, 4)
    assert fresh.boiler.held_rooms == {"w"}
    assert fresh.boiler.hold_until == at(10, 7)
    assert fresh.last_step == engine.last_step


def test_handover_keeps_a_running_boiler_on() -> None:
    engine = make_engine()
    engine.press("w", at(10))
    engine.step(snap(at(10), valve=True))
    engine.step(snap(at(10, 3), valve=True))
    assert engine.boiler.on

    fresh = make_engine(at(10, 4))
    restore(fresh, saved(engine), at(10, 4), handover=True)

    assert fresh.boiler.on is True
    assert fresh.boiler.last_on == at(10, 3)
    assert fresh.boiler.last_off is None
    assert fresh.boiler.last_open_calling == {"w"}
    decision = fresh.step(snap(at(10, 4), valve=True))
    assert decision.boiler_on
    assert decision.boiler_status == "On: Workshop calling"
    assert fresh.stats.boiler_starts == 1


def test_handover_stops_a_boiler_whose_open_rooms_have_gone() -> None:
    engine = make_engine()
    engine.press("w", at(10))
    engine.step(snap(at(10), valve=True))
    engine.step(snap(at(10, 3), valve=True))
    fresh = Engine(GlobalSettings(), [MAIN], at(10, 4))
    restore(fresh, saved(engine), at(10, 4), handover=True)
    assert fresh.boiler.on is False
    assert fresh.boiler.last_off == at(10, 4)


def test_handover_keeps_held_valves_of_a_stopped_boiler() -> None:
    engine = make_engine()
    engine.boiler.last_off = at(9, 59)
    engine.boiler.held_rooms = frozenset({"w"})
    engine.boiler.hold_until = at(10, 2)
    fresh = make_engine(at(10))
    restore(fresh, saved(engine), at(10), handover=True)
    assert fresh.boiler.on is False
    assert fresh.boiler.last_off == at(9, 59)
    assert fresh.boiler.held_rooms == {"w"}
    assert fresh.boiler.hold_until == at(10, 2)


def test_events_round_trip() -> None:
    events = (
        CalendarEvent("Pottery night", at(19), at(21, 30), frozenset({"workshop", "main"})),
        CalendarEvent("Untitled event", at(22), at(23)),
    )
    raw = json.loads(json.dumps(dump_events(events)))
    assert load_events(raw) == events


def test_malformed_saved_events_are_skipped() -> None:
    good = CalendarEvent("Class", at(10), at(12), frozenset({"workshop"}))
    raw = [
        "nonsense",
        {"summary": "No end", "start": at(10).isoformat(), "tags": []},
        {"summary": "Naive", "start": "2026-10-13T10:00:00", "end": "2026-10-13T11:00:00"},
        {"summary": "Bad", "start": "garbage", "end": at(11).isoformat(), "tags": []},
        {"summary": "Tags", "start": at(10).isoformat(), "end": at(11).isoformat(), "tags": "w"},
        *dump_events([good]),
    ]
    assert load_events(raw) == (good,)
    assert load_events(None) == ()
    assert load_events({"not": "a list"}) == ()


def test_boiler_off_times_are_kept_when_it_was_off() -> None:
    engine = make_engine()
    engine.boiler.last_off = at(9, 58)
    fresh = make_engine(at(10))
    restore(fresh, saved(engine), at(10))
    assert fresh.boiler.last_off == at(9, 58)
    assert fresh.boiler.held_rooms == frozenset()


def test_expired_boost_is_dropped() -> None:
    engine = make_engine()
    engine.press("w", at(10))
    fresh = make_engine(at(11))
    restore(fresh, saved(engine), at(11))
    assert fresh.room_state["w"].boost is None


def test_unknown_rooms_are_dropped() -> None:
    data = {
        "rooms": {"gone": {"enabled": False, "boost": None}},
        "boiler": {
            "on": False,
            "last_on": None,
            "last_off": None,
            "held_rooms": ["gone"],
            "last_open_calling": ["gone"],
            "hold_until": at(10, 2).isoformat(),
        },
        "stats": {
            "day": "2026-10-13",
            "rooms": {"gone": {"heating_seconds": 5.0, "boosts": 1}},
            "boiler_on_seconds": 0.0,
            "boiler_starts": 0,
        },
        "last_step": None,
    }
    fresh = make_engine(at(10))
    restore(fresh, data, at(10))
    assert set(fresh.room_state) == {"w", "m"}
    assert "gone" not in fresh.stats.rooms
    assert fresh.boiler.held_rooms == frozenset()


def test_bad_sections_are_skipped() -> None:
    fresh = make_engine(at(10))
    restore(
        fresh,
        {"rooms": "nonsense", "boiler": {"last_on": "garbage"}, "stats": {"day": 5}},
        at(10),
    )
    assert fresh.room_state["w"].enabled is True
    assert fresh.boiler.last_on is None
    assert fresh.stats.day is None


def test_boost_length_survives() -> None:
    engine = make_engine()
    engine.boost("w", timedelta(hours=1), "Dashboard (Sam)", at(10))
    fresh = make_engine(at(10, 1))
    restore(fresh, saved(engine), at(10, 1))
    assert fresh.room_state["w"].boost.length == timedelta(hours=1)
    assert fresh.room_state["w"].boost.origin == "Dashboard (Sam)"
