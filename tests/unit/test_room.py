"""Tests for room demand, thermostat and status text."""

from datetime import timedelta

from custom_components.makerspace_heating.engine.model import (
    Boost,
    EventDemand,
    RoomSettings,
    Source,
)
from custom_components.makerspace_heating.engine.room import (
    Demand,
    room_demand,
    room_status,
    wants_heat,
)
from tests.unit.helpers import at

ROOM = RoomSettings(room_id="w", name="Workshop", tag="workshop")
BOOST = Boost(ends=at(14, 32), length=timedelta(minutes=30), origin="Button")
EVENT = EventDemand("Pottery night", at(19), at(21, 30), running=True)
PREHEAT = EventDemand("Pottery night", at(19), at(21, 30), running=False)


class TestRoomDemand:
    def test_frost_only(self) -> None:
        demand = room_demand(ROOM, True, None, None, at(14))
        assert (demand.source, demand.target) == (Source.FROST, 7.0)

    def test_boost(self) -> None:
        demand = room_demand(ROOM, True, BOOST, None, at(14))
        assert (demand.source, demand.target, demand.boost) == (Source.BOOST, 19.0, BOOST)

    def test_expired_boost_is_ignored(self) -> None:
        demand = room_demand(ROOM, True, BOOST, None, at(14, 32))
        assert (demand.source, demand.boost) == (Source.FROST, None)

    def test_event_wins_when_higher_and_boost_still_reported(self) -> None:
        demand = room_demand(ROOM, True, BOOST, EVENT, at(14))
        assert (demand.source, demand.target) == (Source.EVENT, 20.0)
        assert demand.boost == BOOST

    def test_boost_wins_when_higher(self) -> None:
        hot = RoomSettings(room_id="w", name="Workshop", tag="workshop", boost_target=22.0)
        demand = room_demand(hot, True, BOOST, EVENT, at(14))
        assert (demand.source, demand.target) == (Source.BOOST, 22.0)

    def test_tie_prefers_event(self) -> None:
        tied = RoomSettings(room_id="w", name="Workshop", tag="workshop", boost_target=20.0)
        assert room_demand(tied, True, BOOST, EVENT, at(14)).source is Source.EVENT

    def test_off_room_gets_frost_only(self) -> None:
        demand = room_demand(ROOM, False, BOOST, EVENT, at(14))
        assert (demand.source, demand.boost, demand.event) == (Source.FROST, None, None)


class TestWantsHeat:
    def test_opens_below_margin(self) -> None:
        assert wants_heat(18.6, 19.0, 0.3, False)

    def test_opens_exactly_at_margin(self) -> None:
        assert wants_heat(18.7, 19.0, 0.3, False)

    def test_closes_at_target(self) -> None:
        assert not wants_heat(19.0, 19.0, 0.3, True)

    def test_keeps_state_inside_margin(self) -> None:
        assert wants_heat(18.8, 19.0, 0.3, True)
        assert not wants_heat(18.8, 19.0, 0.3, False)


class TestRoomStatus:
    def test_event_heating(self) -> None:
        demand = Demand(Source.EVENT, 20.0, None, EVENT)
        assert (
            room_status(True, demand, True, False, None, at(20))
            == "Event: Pottery night until 21:30, heating"
        )

    def test_preheat_at_temperature(self) -> None:
        demand = Demand(Source.EVENT, 20.0, None, PREHEAT)
        assert (
            room_status(True, demand, False, False, None, at(18, 40))
            == "Pre-heating for Pottery night (starts 19:00), at temperature"
        )

    def test_boost(self) -> None:
        demand = Demand(Source.BOOST, 19.0, BOOST, None)
        assert (
            room_status(True, demand, False, False, None, at(14))
            == "Boost until 14:32, at temperature"
        )

    def test_frost_idle_and_heating(self) -> None:
        demand = Demand(Source.FROST, 7.0, None, None)
        assert room_status(True, demand, False, False, None, at(14)) == "Frost protection"
        assert (
            room_status(True, demand, True, False, None, at(14))
            == "Frost protection, heating"
        )

    def test_held_open_for_boiler(self) -> None:
        demand = Demand(Source.FROST, 7.0, None, None)
        assert (
            room_status(True, demand, False, True, None, at(14))
            == "Frost protection, valve held open for boiler"
        )

    def test_off(self) -> None:
        demand = Demand(Source.FROST, 7.0, None, None)
        assert (
            room_status(False, demand, False, False, None, at(14))
            == "Off (frost protection only)"
        )

    def test_fault_wins(self) -> None:
        demand = Demand(Source.BOOST, 19.0, BOOST, None)
        assert (
            room_status(True, demand, False, False, "no temperature reading", at(14))
            == "Fault: no temperature reading"
        )
