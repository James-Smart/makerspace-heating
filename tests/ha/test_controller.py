"""Tests for the controller: inputs, commands, calendar and persistence."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import HomeAssistant, ServiceCall, State, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.makerspace_heating.const import (
    CONF_CONTROL_MODE,
    CONF_MARGIN,
    DOMAIN,
    EVENT_LOGBOOK_ENTRY,
    MODE_ACTIVE,
    MODE_PAUSED,
    MODE_SHADOW,
)
from custom_components.makerspace_heating.controller import (
    HeatingController,
    _is_handover,
    is_press,
    read_temperature,
)
from custom_components.makerspace_heating.engine.engine import Engine
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


async def boiler_running(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, entry: MockConfigEntry
) -> None:
    """Boost the Workshop and wait for the (Active) boiler to fire."""
    entry.runtime_data.async_boost(ROOM_ID, None, "test")
    await hass.async_block_till_done()
    await advance(hass, freezer, timedelta(seconds=30))
    await advance(hass, freezer, timedelta(minutes=3))
    assert hass.states.get(BOILER).state == "on"


@pytest.mark.parametrize("entry_options", [ACTIVE])
async def test_settings_change_does_not_short_cycle_boiler(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, loaded_entry: MockConfigEntry
) -> None:
    await boiler_running(hass, freezer, loaded_entry)
    await advance(hass, freezer, timedelta(seconds=30))
    old = loaded_entry.runtime_data
    changes = async_capture_events(hass, EVENT_STATE_CHANGED)
    hass.config_entries.async_update_entry(loaded_entry, options=ACTIVE | {CONF_MARGIN: 0.5})
    await hass.async_block_till_done()
    assert loaded_entry.runtime_data is not old
    await advance(hass, freezer, timedelta(seconds=30))
    assert hass.states.get(BOILER).state == "on"
    assert hass.states.get(VALVE).state == "on"
    assert loaded_entry.runtime_data.decision.boiler_on
    assert (
        hass.states.get("sensor.makerspace_heating_boiler_status").state
        == "On: Workshop calling"
    )
    assert not [
        event
        for event in changes
        if event.data["entity_id"] in (BOILER, VALVE)
        and event.data["new_state"].state != "on"
    ]


def released(logbook: list) -> bool:
    return any(
        event.data["name"] == "Boiler"
        and event.data["message"] == "turned off: Makerspace Heating is no longer in control"
        for event in logbook
    )


@pytest.mark.parametrize("entry_options", [ACTIVE])
@pytest.mark.parametrize("mode", [MODE_PAUSED, MODE_SHADOW])
async def test_leaving_active_turns_boiler_off(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    loaded_entry: MockConfigEntry,
    mode: str,
) -> None:
    await boiler_running(hass, freezer, loaded_entry)
    logbook = async_capture_events(hass, EVENT_LOGBOOK_ENTRY)
    hass.config_entries.async_update_entry(loaded_entry, options={CONF_CONTROL_MODE: mode})
    await hass.async_block_till_done()
    assert hass.states.get(BOILER).state == "off"
    assert hass.states.get(VALVE).state == "on"  # valves are left as they are
    assert released(logbook)
    assert not loaded_entry.runtime_data.engine.boiler.on
    await advance(hass, freezer, timedelta(hours=1))
    assert hass.states.get(BOILER).state == "off"


@pytest.mark.parametrize("entry_options", [ACTIVE])
async def test_disabling_turns_boiler_off(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, loaded_entry: MockConfigEntry
) -> None:
    await boiler_running(hass, freezer, loaded_entry)
    logbook = async_capture_events(hass, EVENT_LOGBOOK_ENTRY)
    await hass.config_entries.async_set_disabled_by(
        loaded_entry.entry_id, ConfigEntryDisabler.USER
    )
    await hass.async_block_till_done()
    assert hass.states.get(BOILER).state == "off"
    assert hass.states.get(VALVE).state == "on"
    assert released(logbook)


@pytest.mark.parametrize("entry_options", [ACTIVE])
async def test_removing_turns_boiler_off_and_deletes_state(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    loaded_entry: MockConfigEntry,
    hass_storage: dict,
) -> None:
    await boiler_running(hass, freezer, loaded_entry)
    logbook = async_capture_events(hass, EVENT_LOGBOOK_ENTRY)
    await hass.config_entries.async_remove(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(BOILER).state == "off"
    assert hass.states.get(VALVE).state == "on"
    assert released(logbook)
    assert f"{DOMAIN}.{loaded_entry.entry_id}" not in hass_storage


@pytest.mark.parametrize("entry_options", [ACTIVE])
async def test_removing_a_disabled_entry_leaves_boiler_alone(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await hass.config_entries.async_set_disabled_by(
        loaded_entry.entry_id, ConfigEntryDisabler.USER
    )
    await hass.services.async_call(  # e.g. the old automations are back in charge
        "input_boolean", "turn_on", {"entity_id": BOILER}, blocking=True
    )
    await hass.config_entries.async_remove(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(BOILER).state == "on"


@pytest.mark.parametrize("entry_options", [ACTIVE])
async def test_failed_boiler_release_does_not_stop_reload(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    loaded_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await boiler_running(hass, freezer, loaded_entry)

    async def broken(call: ServiceCall) -> None:
        raise HomeAssistantError("Shelly did not answer")

    hass.services.async_register("input_boolean", "turn_off", broken)
    hass.config_entries.async_update_entry(loaded_entry, options={CONF_CONTROL_MODE: MODE_PAUSED})
    await hass.async_block_till_done()
    assert loaded_entry.state is ConfigEntryState.LOADED
    assert loaded_entry.runtime_data.status_prefix() == "Paused: "
    assert "Could not turn off the boiler input_boolean.boiler: Shelly did not answer" in caplog.text


async def test_failed_platform_setup_stops_controller(
    hass: HomeAssistant, entry: MockConfigEntry, devices, calendar_calls
) -> None:
    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        side_effect=RuntimeError("platform broke"),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert entry.runtime_data._closed
    assert not entry.runtime_data._unsubscribe


async def test_only_unload_marks_a_handover(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    controller: HeatingController,
    hass_storage: dict,
) -> None:
    key = f"{DOMAIN}.{controller.entry.entry_id}"
    await advance(hass, freezer, timedelta(seconds=11))
    assert "handover_at" not in hass_storage[key]["data"]
    assert hass_storage[key]["data"]["events"] == []
    await controller.async_shutdown()
    assert "handover_at" in hass_storage[key]["data"]


def test_is_handover() -> None:
    now = dt_util.now()
    assert _is_handover({"handover_at": (now - timedelta(minutes=2)).isoformat()}, now)
    assert not _is_handover(
        {"handover_at": (now - timedelta(minutes=2, seconds=1)).isoformat()}, now
    )
    assert not _is_handover({}, now)
    assert not _is_handover({"handover_at": "garbage"}, now)


async def test_going_active_does_not_take_over_a_simulated_boiler(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, loaded_entry: MockConfigEntry
) -> None:
    loaded_entry.runtime_data.async_boost(ROOM_ID, None, "test")
    await hass.async_block_till_done()
    await advance(hass, freezer, timedelta(seconds=30))
    await advance(hass, freezer, timedelta(minutes=3))
    assert loaded_entry.runtime_data.decision.boiler_on  # Shadow: simulated only
    hass.config_entries.async_update_entry(loaded_entry, options=ACTIVE)
    await hass.async_block_till_done()
    assert hass.states.get(VALVE).state == "on"
    assert hass.states.get(BOILER).state == "off"  # the valve has only just opened
    assert not loaded_entry.runtime_data.decision.boiler_on


async def test_reload_keeps_calendar_events(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    calendar_events: list[dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = dt_util.now()
    calendar_events.append(
        {
            "start": (now - timedelta(minutes=5)).isoformat(),
            "end": (now + timedelta(hours=1)).isoformat(),
            "summary": "Pottery night #workshop",
        }
    )
    await loaded_entry.runtime_data.async_refresh_calendar()
    assert loaded_entry.runtime_data.decision.rooms[ROOM_ID].source is Source.EVENT

    sources: list[Source] = []
    step = Engine.step

    def recording_step(engine: Engine, snapshot):
        decision = step(engine, snapshot)
        sources.append(decision.rooms[ROOM_ID].source)
        return decision

    async def network_calendar(call: ServiceCall) -> dict:
        await asyncio.sleep(0)  # a real provider waits for I/O
        return {CALENDAR: {"events": calendar_events}}

    hass.services.async_register(
        "calendar", "get_events", network_calendar, supports_response=SupportsResponse.ONLY
    )
    monkeypatch.setattr(Engine, "step", recording_step)
    calendar_events.clear()  # the event has gone by the next successful fetch
    hass.config_entries.async_update_entry(loaded_entry, options={CONF_MARGIN: 0.5})
    await hass.async_block_till_done()
    assert sources[0] is Source.EVENT
    assert loaded_entry.runtime_data.decision.rooms[ROOM_ID].source is Source.FROST


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
