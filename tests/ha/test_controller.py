"""Tests for the controller: inputs, commands, calendar and persistence."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall, State, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.makerspace_heating.const import (
    CONF_CONTROL_MODE,
    EVENT_LOGBOOK_ENTRY,
    MODE_ACTIVE,
)
from custom_components.makerspace_heating.controller import (
    HeatingController,
    is_press,
    read_temperature,
)
from custom_components.makerspace_heating.engine.model import Source

from .conftest import BOILER, BUTTON, CALENDAR, ROOM_ID, VALVE

ACTIVE = {CONF_CONTROL_MODE: MODE_ACTIVE}


def press(hass: HomeAssistant, second: int, event_type: str = "single_push") -> None:
    """Simulate a Shelly event entity firing (each press needs a new timestamp)."""
    hass.states.async_set(
        BUTTON,
        f"2026-10-13T10:00:{second:02d}.000+00:00",
        {"event_type": event_type, "event_types": ["single_push"]},
    )


async def advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    ("state", "attributes", "expected"),
    [
        ("18.5", {"unit_of_measurement": "°C"}, 18.5),
        ("64.4", {"unit_of_measurement": "°F"}, 18.0),
        ("unknown", {}, None),
        ("unavailable", {}, None),
        ("", {}, None),
        ("nan", {}, None),
        ("warm", {}, None),
    ],
)
def test_read_temperature(state: str, attributes: dict, expected: float | None) -> None:
    result = read_temperature(State("sensor.t", state, attributes))
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_is_press() -> None:
    first, second = "2026-10-13T10:00:00+00:00", "2026-10-13T10:00:05+00:00"

    def event(state: str, event_type: str = "single_push") -> State:
        return State(BUTTON, state, {"event_type": event_type})

    def binary(state: str) -> State:
        return State("binary_sensor.button", state)

    assert is_press(event("unknown"), event(first))
    assert is_press(event(first), event(second))
    assert not is_press(event("unavailable"), event(first))
    assert not is_press(event(first), event("unavailable"))
    assert not is_press(None, event(first))
    assert not is_press(event(first), event(second, "btn_up"))
    assert not is_press(event(first), event(first))
    assert is_press(binary("off"), binary("on"))
    assert not is_press(binary("unavailable"), binary("on"))
    assert not is_press(binary("on"), binary("off"))


async def test_starts_in_frost_protection(controller: HeatingController) -> None:
    room = controller.decision.rooms[ROOM_ID]
    assert room.source is Source.FROST
    assert room.status == "Frost protection"
    assert controller.decision.boiler_status == "Off: no rooms calling"
    assert controller.status_prefix() == "Shadow: "


async def test_button_press_starts_one_boost(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, controller: HeatingController
) -> None:
    press(hass, 1)
    press(hass, 2)  # the same physical press reported twice
    await hass.async_block_till_done()
    room = controller.decision.rooms[ROOM_ID]
    assert room.boost is not None and room.boost.origin == "Button"
    assert controller.engine.stats.room(ROOM_ID).boosts == 1

    await advance(hass, freezer, timedelta(seconds=3))
    press(hass, 3)
    await hass.async_block_till_done()
    assert controller.engine.stats.room(ROOM_ID).boosts == 2


async def test_reconnecting_button_does_not_boost(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, controller: HeatingController
) -> None:
    press(hass, 1)
    await hass.async_block_till_done()
    controller.async_cancel_boost(ROOM_ID, "test")
    await advance(hass, freezer, timedelta(seconds=3))
    hass.states.async_set(BUTTON, "unavailable")
    await hass.async_block_till_done()
    press(hass, 1)  # back online, reporting the old timestamp
    await hass.async_block_till_done()
    assert controller.decision.rooms[ROOM_ID].boost is None


async def test_shadow_mode_sends_no_commands(
    hass: HomeAssistant, controller: HeatingController
) -> None:
    controller.async_boost(ROOM_ID, None, "test")
    await hass.async_block_till_done()
    assert controller.decision.rooms[ROOM_ID].valve_on is True
    assert hass.states.get(VALVE).state == "off"


async def test_shadow_mode_never_switches_boiler(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, controller: HeatingController
) -> None:
    controller.async_boost(ROOM_ID, None, "test")
    await hass.async_block_till_done()
    await advance(hass, freezer, timedelta(seconds=30))  # simulated valve opens
    await advance(hass, freezer, timedelta(minutes=3))
    assert controller.decision.boiler_on
    assert hass.states.get(BOILER).state == "off"
    assert hass.states.get(VALVE).state == "off"


@pytest.mark.parametrize("entry_options", [ACTIVE])
async def test_active_mode_opens_valve_then_fires_boiler(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, controller: HeatingController
) -> None:
    controller.async_boost(ROOM_ID, None, "test")
    await hass.async_block_till_done()
    assert hass.states.get(VALVE).state == "on"
    assert hass.states.get(BOILER).state == "off"

    await advance(hass, freezer, timedelta(minutes=3))
    assert controller.decision.boiler_on
    assert hass.states.get(BOILER).state == "on"


@pytest.mark.parametrize("entry_options", [ACTIVE])
async def test_manual_change_is_switched_back(
    hass: HomeAssistant, controller: HeatingController
) -> None:
    logbook = async_capture_events(hass, EVENT_LOGBOOK_ENTRY)
    controller.async_boost(ROOM_ID, None, "test")
    await hass.async_block_till_done()
    await hass.services.async_call(
        "input_boolean", "turn_off", {"entity_id": VALVE}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(VALVE).state == "on"
    assert any(
        event.data["name"] == "Workshop valve"
        and event.data["message"] == "was switched off by hand; switching it back on"
        for event in logbook
    )


async def test_boost_writes_logbook_entry(
    hass: HomeAssistant, controller: HeatingController
) -> None:
    logbook = async_capture_events(hass, EVENT_LOGBOOK_ENTRY)
    controller.async_boost(ROOM_ID, timedelta(hours=1), "Dashboard (Sam)")
    await hass.async_block_till_done()
    assert any(
        event.data["name"] == "Workshop"
        and event.data["message"].startswith("boost until ")
        and event.data["message"].endswith("(started by Dashboard (Sam))")
        for event in logbook
    )


async def test_calendar_events_drive_event_demand(
    hass: HomeAssistant,
    controller: HeatingController,
    calendar_events: list[dict],
    calendar_calls: list,
) -> None:
    now = dt_util.now()
    calendar_events.append(
        {
            "start": (now - timedelta(minutes=5)).isoformat(),
            "end": (now + timedelta(hours=1)).isoformat(),
            "summary": "Pottery night",
            "description": "#workshop #wrokshop",
        }
    )
    await controller.async_refresh_calendar()
    room = controller.decision.rooms[ROOM_ID]
    assert room.source is Source.EVENT
    assert room.status.startswith("Event: Pottery night until ")
    assert controller.unknown_tags == {"wrokshop"}
    assert [event.summary for event in controller.upcoming] == ["Pottery night"]
    call = calendar_calls[-1]
    assert call.data["entity_id"] == CALENDAR
    assert call.data["end_date_time"] - call.data["start_date_time"] == timedelta(hours=24)


async def test_calendar_failure_is_recorded(
    hass: HomeAssistant, controller: HeatingController
) -> None:
    hass.services.async_remove("calendar", "get_events")
    await controller.async_refresh_calendar()
    assert controller.calendar_failing_since is not None


def fail_calendar(hass: HomeAssistant) -> None:
    """Make calendar.get_events raise a provider error HA passes straight through."""

    async def broken(call: ServiceCall) -> dict:
        raise RuntimeError("CalDAV server said no")

    hass.services.async_register(
        "calendar", "get_events", broken, supports_response=SupportsResponse.ONLY
    )


async def test_hung_calendar_times_out(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, controller: HeatingController
) -> None:
    async def hang(call: ServiceCall) -> dict:
        await asyncio.Event().wait()
        return {}

    hass.services.async_register(
        "calendar", "get_events", hang, supports_response=SupportsResponse.ONLY
    )
    fetch = hass.async_create_task(controller.async_refresh_calendar())
    await asyncio.sleep(0)
    assert controller._fetching
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await fetch
    assert controller.calendar_failing_since is not None
    assert not controller._fetching


async def test_unexpected_calendar_error_is_a_failure(
    hass: HomeAssistant, controller: HeatingController, caplog: pytest.LogCaptureFixture
) -> None:
    fail_calendar(hass)
    await controller.async_refresh_calendar()
    assert controller.calendar_failing_since is not None
    assert not controller._fetching
    assert "CalDAV server said no" in caplog.text


async def test_failed_fetch_keeps_last_events(
    hass: HomeAssistant, controller: HeatingController, calendar_events: list[dict]
) -> None:
    now = dt_util.now()
    calendar_events.append(
        {
            "start": (now - timedelta(minutes=5)).isoformat(),
            "end": (now + timedelta(hours=1)).isoformat(),
            "summary": "Pottery night #workshop",
        }
    )
    await controller.async_refresh_calendar()
    assert controller.decision.rooms[ROOM_ID].source is Source.EVENT
    fail_calendar(hass)
    await controller.async_refresh_calendar()
    assert controller.calendar_failing_since is not None
    assert controller.decision.rooms[ROOM_ID].source is Source.EVENT


async def test_calendar_failing_for_30_minutes_raises_fault(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, loaded_entry
) -> None:
    problem = "binary_sensor.makerspace_heating_heating_problem"
    fail_calendar(hass)
    await advance(hass, freezer, timedelta(minutes=5))
    assert loaded_entry.runtime_data.calendar_failing_since is not None
    for _ in range(5):
        await advance(hass, freezer, timedelta(minutes=5))
    assert hass.states.get(problem).state == "off"
    await advance(hass, freezer, timedelta(minutes=5))
    assert hass.states.get(problem).state == "on"


async def test_boost_survives_restart(
    hass: HomeAssistant, entry, devices, calendar_calls
) -> None:
    first = HeatingController(hass, entry)
    await first.async_setup()
    first.async_boost(ROOM_ID, timedelta(hours=1), "test")
    await first.async_shutdown()

    second = HeatingController(hass, entry)
    await second.async_setup()
    assert second.decision.rooms[ROOM_ID].boost.length == timedelta(hours=1)
    await second.async_shutdown()


async def test_off_room_rejects_boost(
    hass: HomeAssistant, controller: HeatingController
) -> None:
    controller.async_set_enabled(ROOM_ID, False)
    with pytest.raises(ServiceValidationError, match="Room is off"):
        controller.async_boost(ROOM_ID, None, "test")


@pytest.mark.parametrize("entry_options", [ACTIVE])
async def test_active_boost_logs_once(
    hass: HomeAssistant, controller: HeatingController
) -> None:
    logbook = async_capture_events(hass, EVENT_LOGBOOK_ENTRY)
    controller.async_boost(ROOM_ID, timedelta(hours=1), "Dashboard (Sam)")
    await hass.async_block_till_done()
    assert hass.states.get(VALVE).state == "on"
    started = [
        event
        for event in logbook
        if event.data["name"] == "Workshop"
        and event.data["message"].startswith("boost until ")
        and event.data["message"].endswith("(started by Dashboard (Sam))")
    ]
    assert len(started) == 1


@pytest.mark.parametrize("entry_options", [ACTIVE])
async def test_no_action_after_shutdown(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    controller: HeatingController,
    calendar_events: list[dict],
) -> None:
    controller.async_boost(ROOM_ID, None, "test")
    await hass.async_block_till_done()
    assert hass.states.get(VALVE).state == "on"

    gate = asyncio.Event()

    async def slow(call: ServiceCall) -> dict:
        await gate.wait()
        return {CALENDAR: {"events": calendar_events}}

    hass.services.async_register(
        "calendar", "get_events", slow, supports_response=SupportsResponse.ONLY
    )
    fetch = hass.async_create_task(controller.async_refresh_calendar())
    await asyncio.sleep(0)
    await controller.async_shutdown()
    calls: list[int] = []
    controller.async_add_listener(lambda: calls.append(1))
    await hass.services.async_call(
        "input_boolean", "turn_off", {"entity_id": VALVE}, blocking=True
    )
    freezer.tick(timedelta(seconds=31))
    gate.set()
    await fetch
    await hass.async_block_till_done()
    assert calls == []
    assert hass.states.get(VALVE).state == "off"
