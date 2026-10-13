"""Tests for boiler decisions and valve protections (spec §4)."""

from datetime import timedelta

from custom_components.makerspace_heating.engine.boiler import BoilerState, step_boiler
from custom_components.makerspace_heating.engine.model import GlobalSettings
from tests.unit.helpers import at

SETTINGS = GlobalSettings()
WORKSHOP = {"w": "Workshop"}


def test_off_when_nothing_calls() -> None:
    decision = step_boiler(BoilerState(), SETTINGS, {}, {}, at(10))
    assert (decision.on, decision.status, decision.held_rooms) == (
        False,
        "Off: no rooms calling",
        frozenset(),
    )


def test_waits_for_valve_to_report_open() -> None:
    decision = step_boiler(BoilerState(), SETTINGS, WORKSHOP, {}, at(10))
    assert not decision.on
    assert decision.status == "Waiting for valves: Workshop (valve not open yet)"


def test_waits_for_valve_open_time_then_fires() -> None:
    state = BoilerState()
    since = {"w": at(10)}
    waiting = step_boiler(state, SETTINGS, WORKSHOP, since, at(10, 1, 20))
    assert not waiting.on
    assert waiting.status == "Waiting for valves: Workshop (1 min 40 s)"
    fired = step_boiler(state, SETTINGS, WORKSHOP, since, at(10, 3))
    assert fired.on and fired.turned_on
    assert fired.status == "On: Workshop calling"


def test_zero_wait_still_needs_valve_reported_on() -> None:
    settings = GlobalSettings(valve_wait=timedelta(0))
    state = BoilerState()
    assert not step_boiler(state, settings, WORKSHOP, {}, at(10)).on
    assert step_boiler(state, settings, WORKSHOP, {"w": at(10)}, at(10)).on


def test_minimum_run_holds_last_valve_open() -> None:
    state = BoilerState()
    since = {"w": at(9)}
    step_boiler(state, SETTINGS, WORKSHOP, since, at(10))
    decision = step_boiler(state, SETTINGS, {}, since, at(10, 2))
    assert decision.on
    assert decision.status == "On: holding for minimum run time"
    assert decision.held_rooms == {"w"}


def test_turns_off_after_minimum_run_and_keeps_valve_open() -> None:
    state = BoilerState()
    since = {"w": at(9)}
    step_boiler(state, SETTINGS, WORKSHOP, since, at(10))
    stopped = step_boiler(state, SETTINGS, {}, since, at(10, 5))
    assert not stopped.on and stopped.turned_off
    assert stopped.status == "Off: no rooms calling"
    assert stopped.held_rooms == {"w"}
    assert step_boiler(state, SETTINGS, {}, since, at(10, 7, 59)).held_rooms == {"w"}
    assert step_boiler(state, SETTINGS, {}, since, at(10, 8)).held_rooms == frozenset()


def test_no_keep_open_when_zero() -> None:
    settings = GlobalSettings(keep_open=timedelta(0))
    state = BoilerState()
    since = {"w": at(9)}
    step_boiler(state, settings, WORKSHOP, since, at(10))
    assert step_boiler(state, settings, {}, since, at(10, 5)).held_rooms == frozenset()


def test_minimum_rest_blocks_restart() -> None:
    state = BoilerState()
    since = {"w": at(9)}
    step_boiler(state, SETTINGS, WORKSHOP, since, at(10))
    step_boiler(state, SETTINGS, {}, since, at(10, 5))
    resting = step_boiler(state, SETTINGS, WORKSHOP, since, at(10, 7))
    assert not resting.on
    assert resting.status == "Off: resting (3 min left)"
    assert step_boiler(state, SETTINGS, WORKSHOP, since, at(10, 10)).on


def test_new_room_waiting_while_on_holds_previous_valve() -> None:
    state = BoilerState()
    step_boiler(state, SETTINGS, WORKSHOP, {"w": at(9)}, at(10))
    main = {"m": "Main Space"}
    waiting = step_boiler(state, SETTINGS, main, {"w": at(9)}, at(10, 1))
    assert waiting.on
    assert waiting.status == "On: Main Space calling"
    assert waiting.held_rooms == {"w"}
    opened = step_boiler(state, SETTINGS, main, {"w": at(9), "m": at(10, 2)}, at(10, 2))
    assert opened.held_rooms == frozenset()


def test_names_all_calling_rooms_sorted() -> None:
    since = {"w": at(9), "m": at(9)}
    decision = step_boiler(
        BoilerState(), SETTINGS, {"w": "Workshop", "m": "Main Space"}, since, at(10)
    )
    assert decision.status == "On: Main Space, Workshop calling"
