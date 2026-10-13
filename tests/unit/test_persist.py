"""Tests for saving and restoring engine state (spec §8.2)."""

import json
from datetime import timedelta

import pytest

from custom_components.makerspace_heating.engine.engine import Engine
from custom_components.makerspace_heating.engine.model import (
    HUB,
    CalendarEvent,
    FaultKey,
    FaultKind,
    GlobalSettings,
    RoomInputs,
    RoomSettings,
    Snapshot,
)
from custom_components.makerspace_heating.engine.persist import (
    dump,
    dump_events,
    dump_handover,
    load_events,
    restore,
    restore_handover,
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


def test_keep_boiler_keeps_a_running_boiler_on() -> None:
    engine = make_engine()
    engine.press("w", at(10))
    engine.step(snap(at(10), valve=True))
    engine.step(snap(at(10, 3), valve=True))
    assert engine.boiler.on

    fresh = make_engine(at(10, 4))
    restore(fresh, saved(engine), at(10, 4), keep_boiler=True)

    assert fresh.boiler.on is True
    assert fresh.boiler.last_on == at(10, 3)
    assert fresh.boiler.last_off is None
    assert fresh.boiler.last_open_calling == {"w"}
    decision = fresh.step(snap(at(10, 4), valve=True))
    assert decision.boiler_on
    assert decision.boiler_status == "On: Workshop calling"
    assert fresh.stats.boiler_starts == 1


def test_keep_boiler_stops_a_boiler_whose_open_rooms_have_gone() -> None:
    engine = make_engine()
    engine.press("w", at(10))
    engine.step(snap(at(10), valve=True))
    engine.step(snap(at(10, 3), valve=True))
    fresh = Engine(GlobalSettings(), [MAIN], at(10, 4))
    restore(fresh, saved(engine), at(10, 4), keep_boiler=True)
    assert fresh.boiler.on is False
    assert fresh.boiler.last_off == at(10, 4)


def test_keep_boiler_keeps_held_valves_of_a_stopped_boiler() -> None:
    engine = make_engine()
    engine.boiler.last_off = at(9, 59)
    engine.boiler.held_rooms = frozenset({"w"})
    engine.boiler.hold_until = at(10, 2)
    fresh = make_engine(at(10))
    restore(fresh, saved(engine), at(10), keep_boiler=True)
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


TEMP = FaultKey("w", FaultKind.TEMP_UNAVAILABLE)
BOILER_DOWN = FaultKey(HUB, FaultKind.BOILER_UNAVAILABLE)


def handed_over(engine: Engine) -> dict:
    return json.loads(json.dumps(dump_handover(engine)))


def faulty_engine() -> Engine:
    """Workshop's sensor and the boiler switch failed at 10:00; Main is heating."""
    engine = make_engine()
    engine.press("m", at(9, 50))
    rooms = {"w": RoomInputs(None, False), "m": RoomInputs(15.0, True)}
    for moment in (at(10), at(10, 5)):
        decision = engine.step(Snapshot(now=moment, rooms=rooms, boiler_available=False))
    assert set(decision.faults) == {TEMP, BOILER_DOWN}
    return engine


def test_handover_keeps_fault_timing() -> None:
    engine = faulty_engine()
    fresh = make_engine(at(10, 6))
    restore_handover(fresh, handed_over(engine), devices_unchanged=False)

    assert fresh.faults.started_at == at(8)
    assert fresh.faults.since == {TEMP: at(10), BOILER_DOWN: at(10)}
    assert fresh.warming.tracks == {"m": (at(10), 15.0)}
    rooms = {"w": RoomInputs(None, False), "m": RoomInputs(15.0, True)}
    decision = fresh.step(Snapshot(now=at(10, 6), rooms=rooms, boiler_available=False))
    assert set(decision.faults) == {TEMP, BOILER_DOWN}  # no fresh grace or delay


def valves_step(engine: Engine, moment, m_valve: bool) -> None:
    rooms = {"w": RoomInputs(18.0, False), "m": RoomInputs(15.0, m_valve)}
    engine.step(Snapshot(now=moment, rooms=rooms))


def test_handover_keeps_valve_tracking_only_for_unchanged_devices() -> None:
    engine = faulty_engine()
    valves_step(engine, at(10, 6), m_valve=False)
    assert engine.mismatch_since == {"m": at(10, 6)}

    kept = make_engine(at(10, 7))
    restore_handover(kept, handed_over(engine), devices_unchanged=True)
    assert kept.commanded == {"w": False, "m": True}
    assert kept.commanded_valve("m") is True
    assert kept.valve_on_since == {}
    assert kept.mismatch_since == {"m": at(10, 6)}

    valves_step(engine, at(10, 7), m_valve=True)
    kept = make_engine(at(10, 8))
    restore_handover(kept, handed_over(engine), devices_unchanged=True)
    assert kept.valve_on_since == {"m": at(10, 7)}

    changed = make_engine(at(10, 8))
    restore_handover(changed, handed_over(engine), devices_unchanged=False)
    assert (changed.commanded, changed.valve_on_since, changed.mismatch_since) == ({}, {}, {})


def test_handover_drops_rooms_that_have_gone() -> None:
    engine = faulty_engine()
    fresh = Engine(GlobalSettings(), [MAIN], at(10, 6))
    restore_handover(fresh, handed_over(engine), devices_unchanged=True)
    assert fresh.faults.since == {BOILER_DOWN: at(10)}
    assert set(fresh.commanded) == {"m"}
    assert set(fresh.valve_on_since) == {"m"}


def restored_sections(engine: Engine) -> dict[str, bool]:
    return {
        "faults": engine.faults.started_at != at(10, 6) or bool(engine.faults.since),
        "warming": bool(engine.warming.tracks),
        "valves": bool(engine.commanded or engine.valve_on_since or engine.mismatch_since),
    }


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "nonsense",
        [1, 2],
        {
            "faults_started_at": "garbage",
            "fault_since": "nonsense",
            "warming": ["m"],
            "commanded": {"m": "yes"},
            "valve_on_since": {"m": 5},
            "mismatch_since": "nonsense",
        },
    ],
)
def test_unreadable_handover_leaves_defaults(bad: object) -> None:
    fresh = make_engine(at(10, 6))
    restore_handover(fresh, bad, devices_unchanged=True)
    assert restored_sections(fresh) == {"faults": False, "warming": False, "valves": False}


@pytest.mark.parametrize(
    ("change", "section"),
    [
        ({"faults_started_at": "2026-10-13T08:00:00"}, "faults"),  # no time zone
        ({"fault_since": [{"scope": "w", "kind": "no_such_kind", "since": "x"}]}, "faults"),
        ({"warming": {"m": {"since": "2026-10-13T10:00:00+01:00", "temperature": "warm"}}}, "warming"),
        ({"commanded": {"m": "yes"}}, "valves"),
        ({"valve_on_since": {"m": 5}}, "valves"),
        ({"mismatch_since": {"m": "garbage"}}, "valves"),
    ],
)
def test_one_unreadable_handover_section_is_skipped(change: dict, section: str) -> None:
    engine = faulty_engine()
    valves_step(engine, at(10, 5, 30), m_valve=True)
    good = handed_over(engine)
    fresh = make_engine(at(10, 6))
    restore_handover(fresh, good, devices_unchanged=True)
    assert restored_sections(fresh) == {"faults": True, "warming": True, "valves": True}

    fresh = make_engine(at(10, 6))
    restore_handover(fresh, good | change, devices_unchanged=True)
    restored = restored_sections(fresh)
    assert restored == {name: name != section for name in restored}
