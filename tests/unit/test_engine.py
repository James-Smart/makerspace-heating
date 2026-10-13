"""Scenario tests for the whole engine (spec §3–§7)."""

from datetime import timedelta

import pytest

from custom_components.makerspace_heating.engine.engine import (
    BOOST_OFF,
    BoostError,
    Engine,
    boost_choices,
)
from custom_components.makerspace_heating.engine.model import (
    HUB,
    CalendarEvent,
    FaultKey,
    FaultKind,
    GlobalSettings,
    LogEntry,
    RoomInputs,
    RoomSettings,
    Snapshot,
    Source,
)
from tests.unit.helpers import at

WORKSHOP = RoomSettings(room_id="w", name="Workshop", tag="workshop", has_button=True)
MAIN = RoomSettings(room_id="m", name="Main Space", tag="main")


def make_engine(settings: GlobalSettings | None = None) -> Engine:
    return Engine(settings or GlobalSettings(), [WORKSHOP, MAIN], at(8))


def snap(now, w_temp=18.0, w_valve=False, m_temp=18.0, m_valve=False, **kwargs) -> Snapshot:
    return Snapshot(
        now=now,
        rooms={"w": RoomInputs(w_temp, w_valve), "m": RoomInputs(m_temp, m_valve)},
        **kwargs,
    )


class TestBoosts:
    def test_press_again_restarts_the_thirty_minutes(self) -> None:
        engine = make_engine()
        engine.press("w", at(10))
        assert engine.room_state["w"].boost.ends == at(10, 30)
        engine.press("w", at(10, 20))
        assert engine.room_state["w"].boost.ends == at(10, 50)

    def test_button_never_shortens_a_longer_boost(self) -> None:
        engine = make_engine()
        engine.boost("w", timedelta(hours=2), "Dashboard (Sam)", at(10))
        assert engine.press("w", at(10, 10)) is False
        assert engine.room_state["w"].boost.ends == at(12)
        log = engine.step(snap(at(10, 10))).log
        assert LogEntry("Workshop", "button pressed; boost already runs until 12:00", "w") in log

    def test_press_records_its_origin(self) -> None:
        engine = make_engine()
        assert engine.press("w", at(10), origin="Dashboard (Sam)") is True
        assert engine.room_state["w"].boost.origin == "Dashboard (Sam)"
        assert engine.press("w", at(10, 10), origin="Dashboard (Sam)") is True
        engine.boost("w", timedelta(hours=2), "Dashboard (Alex)", at(10, 15))
        assert engine.press("w", at(10, 20), origin="Dashboard (Sam)") is False
        assert engine.room_state["w"].boost.origin == "Dashboard (Alex)"
        log = engine.step(snap(at(10, 20))).log
        assert LogEntry("Workshop", "boost until 10:30 (started by Dashboard (Sam))", "w") in log
        assert (
            LogEntry("Workshop", "boost pressed by Dashboard (Sam); boost already runs until 12:15", "w")
            in log
        )

    def test_dashboard_boost_replaces_even_if_shorter(self) -> None:
        engine = make_engine()
        engine.boost("w", timedelta(hours=2), "Dashboard (Sam)", at(10))
        engine.boost("w", timedelta(minutes=30), "Dashboard (Sam)", at(10, 10))
        assert engine.room_state["w"].boost.ends == at(10, 40)

    def test_boost_limits(self) -> None:
        engine = make_engine()
        with pytest.raises(BoostError, match="at most 2 h"):
            engine.boost("w", timedelta(hours=3), "x", at(10))
        with pytest.raises(BoostError):
            engine.boost("w", timedelta(0), "x", at(10))
        engine.set_enabled("w", False)
        with pytest.raises(BoostError, match="Room is off"):
            engine.boost("w", timedelta(minutes=30), "x", at(10))

    def test_boost_heats_the_room(self) -> None:
        engine = make_engine()
        engine.press("w", at(10))
        room = engine.step(snap(at(10))).rooms["w"]
        assert (room.source, room.target, room.calling, room.valve_on) == (
            Source.BOOST,
            19.0,
            True,
            True,
        )
        assert room.status == "Boost until 10:30, heating"
        assert room.boost.origin == "Button"

    def test_boost_end_is_logged(self) -> None:
        engine = make_engine()
        engine.press("w", at(10))
        engine.step(snap(at(10)))
        decision = engine.step(snap(at(10, 30)))
        assert LogEntry("Workshop", "boost ended", "w") in decision.log
        assert decision.rooms["w"].boost is None

    def test_turning_room_off_cancels_boost_and_ignores_presses(self) -> None:
        engine = make_engine()
        engine.press("w", at(10))
        engine.set_enabled("w", False)
        assert engine.room_state["w"].boost is None
        assert engine.press("w", at(10, 1)) is False
        log = engine.step(snap(at(10, 1))).log
        assert LogEntry("Workshop", "button press ignored: room off", "w") in log

    def test_cancel_boost(self) -> None:
        engine = make_engine()
        engine.press("w", at(10))
        engine.cancel_boost("w", "Dashboard (Sam)", at(10, 5))
        assert engine.room_state["w"].boost is None

    def test_boost_choices(self) -> None:
        assert list(boost_choices(GlobalSettings())) == [
            BOOST_OFF,
            "30 min",
            "1 h",
            "1 h 30 min",
            "2 h",
        ]
        odd = GlobalSettings(boost_step=timedelta(minutes=45))
        assert list(boost_choices(odd)) == [BOOST_OFF, "30 min", "45 min", "1 h 30 min"]


class TestEvents:
    def test_preheat_before_event(self) -> None:
        engine = make_engine()
        events = (CalendarEvent("Class", at(10, 30), at(12), frozenset({"workshop"})),)
        room = engine.step(snap(at(10), events=events)).rooms["w"]
        assert room.source is Source.EVENT
        assert room.status == "Pre-heating for Class (starts 10:30), heating"

    def test_boost_outlasts_event(self) -> None:
        engine = make_engine()
        events = (CalendarEvent("Class", at(10), at(10, 30), frozenset({"workshop"})),)
        engine.press("w", at(10, 20))
        room = engine.step(snap(at(10, 40), events=events)).rooms["w"]
        assert room.source is Source.BOOST


class TestHeatingCycle:
    def test_valve_then_boiler_then_hold_then_close(self) -> None:
        engine = make_engine()
        engine.press("w", at(10))
        first = engine.step(snap(at(10)))
        assert first.rooms["w"].valve_on
        assert first.boiler_status == "Waiting for valves: Workshop (valve not open yet)"

        opening = engine.step(snap(at(10, 0, 5), w_valve=True))
        assert opening.boiler_status == "Waiting for valves: Workshop (3 min)"

        on = engine.step(snap(at(10, 3, 5), w_valve=True))
        assert on.boiler_on
        assert LogEntry("Boiler", "turned on (Workshop calling)") in on.log
        assert engine.stats.boiler_starts == 1

        warm = engine.step(snap(at(10, 10), w_temp=19.0, w_valve=True))
        assert not warm.boiler_on
        assert LogEntry("Boiler", "turned off (no rooms calling)") in warm.log
        assert warm.rooms["w"].valve_on
        assert warm.rooms["w"].status == "Boost until 10:30, valve held open for boiler"

        closed = engine.step(snap(at(10, 13), w_temp=19.0, w_valve=True))
        assert not closed.rooms["w"].valve_on
        assert closed.rooms["w"].status == "Boost until 10:30, at temperature"

    def test_off_room_still_gets_frost_protection(self) -> None:
        engine = make_engine()
        engine.set_enabled("w", False)
        room = engine.step(snap(at(10), w_temp=5.0)).rooms["w"]
        assert (room.source, room.calling) == (Source.FROST, True)
        assert room.status == "Off (frost protection only), heating"

    def test_stats_count_heating_time_and_boosts(self) -> None:
        engine = make_engine()
        engine.press("w", at(10))
        engine.step(snap(at(10)))
        engine.step(snap(at(10, 0, 30)))
        engine.step(snap(at(10, 1)))
        assert engine.stats.room("w").heating_seconds == 60
        assert engine.stats.room("w").boosts == 1

    def test_commanded_valve(self) -> None:
        engine = make_engine()
        assert engine.commanded_valve("w") is False
        engine.press("w", at(10))
        engine.step(snap(at(10)))
        assert engine.commanded_valve("w") is True


class TestFaults:
    def test_missing_temperature_closes_valve_then_raises(self) -> None:
        engine = make_engine()
        engine.press("w", at(10))
        room = engine.step(snap(at(10), w_temp=None)).rooms["w"]
        assert (room.calling, room.valve_on) == (False, False)
        assert room.status == "Fault: no temperature reading"
        assert not room.problem
        later = engine.step(snap(at(10, 5), w_temp=None))
        assert later.faults[FaultKey("w", FaultKind.TEMP_UNAVAILABLE)] == (
            "Workshop: temperature sensor unavailable"
        )
        assert later.rooms["w"].problem

    def test_unavailable_valve_stops_calling_but_keeps_command(self) -> None:
        engine = make_engine()
        engine.press("w", at(10))
        decision = engine.step(snap(at(10), w_valve=None))
        room = decision.rooms["w"]
        assert (room.calling, room.valve_on) == (False, True)
        assert room.status == "Fault: valve unavailable"
        assert decision.boiler_status == "Off: no rooms calling"

    def test_valve_not_following_commands(self) -> None:
        engine = make_engine()
        engine.press("w", at(10))
        engine.step(snap(at(10)))
        assert engine.step(snap(at(10, 0, 30))).rooms["w"].calling
        assert engine.step(snap(at(10, 1, 59))).rooms["w"].calling
        room = engine.step(snap(at(10, 2))).rooms["w"]
        assert room.status == "Fault: valve not responding"
        assert not room.calling
        assert room.valve_on

    def test_mismatch_timer_restarts_after_valve_outage(self) -> None:
        engine = make_engine()
        engine.press("w", at(10))
        engine.step(snap(at(10)))
        assert engine.step(snap(at(10, 0, 30))).rooms["w"].calling  # mismatch starts
        engine.step(snap(at(10, 1), w_valve=None))
        engine.step(snap(at(10, 3), w_valve=None))
        back = engine.step(snap(at(10, 3, 30))).rooms["w"]  # back, still mismatched
        assert back.calling
        assert back.status != "Fault: valve not responding"
        assert engine.step(snap(at(10, 4, 59))).rooms["w"].calling
        assert engine.step(snap(at(10, 5))).rooms["w"].status == "Fault: valve not responding"

    def test_no_mismatch_faults_when_not_checking_valves(self) -> None:
        engine = make_engine()
        engine.press("w", at(10))
        engine.step(snap(at(10), check_valves=False))
        room = engine.step(snap(at(10, 5), check_valves=False)).rooms["w"]
        assert room.calling

    def test_hub_faults(self) -> None:
        engine = make_engine()
        engine.step(snap(at(10), boiler_available=False))
        decision = engine.step(
            snap(at(10, 5), boiler_available=False, calendar_failing_since=at(9, 30))
        )
        assert decision.faults[FaultKey(HUB, FaultKind.BOILER_UNAVAILABLE)] == (
            "Boiler switch unavailable"
        )
        assert decision.faults[FaultKey(HUB, FaultKind.CALENDAR_FAILING)] == (
            "Calendar could not be read for over 30 min"
        )

    def test_unavailable_button_is_alert_only(self) -> None:
        engine = make_engine()
        engine.press("w", at(10))
        rooms = {"w": RoomInputs(18.0, False, button_available=False)}
        engine.step(Snapshot(now=at(10), rooms=rooms))
        decision = engine.step(Snapshot(now=at(10, 5), rooms=rooms))
        assert FaultKey("w", FaultKind.BUTTON_UNAVAILABLE) in decision.faults
        assert decision.rooms["w"].calling
        assert decision.rooms["w"].status == "Boost until 10:30, heating"
