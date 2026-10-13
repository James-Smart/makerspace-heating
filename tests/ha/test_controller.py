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
    COMMAND_WAIT,
    CONF_BOILER,
    CONF_CALENDAR,
    CONF_CONTROL_MODE,
    CONF_MARGIN,
    CONF_MIN_REST,
    CONF_VALVE,
    DOMAIN,
    EVENT_LOGBOOK_ENTRY,
    HANDOVER_KEY,
    MODE_ACTIVE,
    MODE_PAUSED,
    MODE_SHADOW,
    SAVE_DELAY,
)
from custom_components.makerspace_heating.controller import (
    HeatingController,
    _fresh_handover,
    is_press,
    read_temperature,
)
from custom_components.makerspace_heating.engine.engine import Engine
from custom_components.makerspace_heating.engine.model import Source
from custom_components.makerspace_heating.engine.persist import STORE_VERSION

from .conftest import (
    BOILER,
    BUTTON,
    CALENDAR,
    NEW_BOILER,
    ROOM_ID,
    SPARE_VALVE,
    TEMPERATURE,
    VALVE,
    WORKSHOP,
)

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


@pytest.mark.parametrize("entry_options", [{CONF_CONTROL_MODE: "turbo"}])
async def test_unknown_control_mode_is_shadow(controller: HeatingController) -> None:
    assert not controller.active
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


async def test_only_unload_hands_over_and_only_in_memory(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    controller: HeatingController,
    hass_storage: dict,
) -> None:
    key = f"{DOMAIN}.{controller.entry.entry_id}"
    await advance(hass, freezer, timedelta(seconds=11))
    assert hass_storage[key]["data"]["events"] == []
    assert controller.entry.entry_id not in hass.data.get(HANDOVER_KEY, {})
    await controller.async_shutdown()
    assert "handover" not in hass_storage[key]["data"]  # a restart can never see it
    handover = hass.data[HANDOVER_KEY][controller.entry.entry_id]
    assert handover["at"] == dt_util.now().isoformat()
    assert handover["mode"] == MODE_SHADOW
    assert handover["boiler"] == BOILER
    assert handover["valves"] == {ROOM_ID: VALVE}
    assert handover["calendar"] == CALENDAR
    assert handover["calendar_failing_since"] is None


async def test_shutdown_without_handover_leaves_no_mark(
    hass: HomeAssistant, controller: HeatingController, hass_storage: dict
) -> None:
    await controller.async_shutdown()
    await controller.async_shutdown(handover=False)  # e.g. a failed setup after a reload
    assert controller.entry.entry_id not in hass.data.get(HANDOVER_KEY, {})
    assert "handover" not in hass_storage[f"{DOMAIN}.{controller.entry.entry_id}"]["data"]


def test_fresh_handover() -> None:
    now = dt_util.now()

    def marker(at: object) -> dict:
        return {"at": at, "mode": MODE_ACTIVE}

    fresh = marker((now - timedelta(minutes=2)).isoformat())
    assert _fresh_handover(fresh, now) == fresh
    assert _fresh_handover(marker(now.isoformat()), now) is not None
    assert _fresh_handover(marker((now - timedelta(minutes=2, seconds=1)).isoformat()), now) is None
    assert _fresh_handover(marker((now + timedelta(seconds=1)).isoformat()), now) is None  # future
    assert _fresh_handover(None, now) is None
    assert _fresh_handover(marker("garbage"), now) is None
    assert _fresh_handover(marker(5), now) is None
    assert _fresh_handover(marker("2026-10-13T10:00:00"), now) is None  # no time zone
    assert _fresh_handover("nonsense", now) is None
    assert _fresh_handover({}, now) is None


@pytest.mark.parametrize("entry_options", [ACTIVE])
@pytest.mark.parametrize("legacy", ["handover", "handover_at"])
async def test_a_stored_handover_is_never_read(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    devices: None,
    calendar_calls: list,
    hass_storage: dict,
    legacy: str,
) -> None:
    """A marker on disk (a crash soon after a reload) must not look like a reload."""
    now = dt_util.now()
    handover = {
        "at": now.isoformat(),
        "mode": MODE_ACTIVE,
        "boiler": BOILER,
        "valves": {ROOM_ID: VALVE},
    }
    store_holds(
        hass_storage,
        entry,
        {
            "rooms": {
                ROOM_ID: {
                    "enabled": True,
                    "boost": {
                        "ends": (now + timedelta(minutes=20)).isoformat(),
                        "length_seconds": 1800,
                        "origin": "test",
                    },
                }
            },
            "boiler": {
                "on": True,
                "last_on": (now - timedelta(minutes=1)).isoformat(),
                "last_open_calling": [ROOM_ID],
            },
            legacy: handover if legacy == "handover" else now.isoformat(),
        },
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    decision = entry.runtime_data.decision
    assert not decision.boiler_on
    assert decision.boiler_status.startswith("Off: resting")
    assert hass.states.get(BOILER).state == "off"


@pytest.mark.parametrize("entry_options", [ACTIVE])
async def test_a_future_dated_handover_is_not_a_reload(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, loaded_entry: MockConfigEntry
) -> None:
    await boiler_running(hass, freezer, loaded_entry)
    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    handover = hass.data[HANDOVER_KEY][loaded_entry.entry_id]
    handover["at"] = (dt_util.now() + timedelta(hours=5)).isoformat()  # clock was behind
    assert await hass.config_entries.async_setup(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert loaded_entry.runtime_data.decision.boiler_status.startswith("Off: resting")
    assert hass.states.get(BOILER).state == "off"


async def test_takes_over_only_the_same_devices(controller: HeatingController) -> None:
    same = {"mode": MODE_SHADOW, "boiler": BOILER, "valves": {ROOM_ID: VALVE}}
    assert controller._same_devices(same)
    # Rooms added or removed since do not matter; a room's valve does.
    assert controller._same_devices(same | {"valves": {}})
    assert controller._same_devices(same | {"valves": {ROOM_ID: VALVE, "gone": SPARE_VALVE}})
    assert not controller._same_devices(same | {"valves": {ROOM_ID: SPARE_VALVE}})
    assert not controller._same_devices(same | {"mode": MODE_ACTIVE})
    assert not controller._same_devices(same | {"boiler": NEW_BOILER})
    assert not controller._same_devices(same | {"valves": "nonsense"})
    assert not controller._same_devices({})


def record_boiler_statuses(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Collect the boiler status of every decision from now on."""
    statuses: list[str] = []
    step = Engine.step

    def recording_step(engine: Engine, snapshot):
        decision = step(engine, snapshot)
        statuses.append(decision.boiler_status)
        return decision

    monkeypatch.setattr(Engine, "step", recording_step)
    return statuses


async def test_reenabling_as_active_does_not_take_over_a_simulated_boiler(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    loaded_entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded_entry.runtime_data.async_boost(ROOM_ID, None, "test")
    await hass.async_block_till_done()
    await advance(hass, freezer, timedelta(seconds=30))
    await advance(hass, freezer, timedelta(minutes=3))
    assert loaded_entry.runtime_data.decision.boiler_on  # Shadow: simulated only
    await hass.config_entries.async_set_disabled_by(
        loaded_entry.entry_id, ConfigEntryDisabler.USER
    )
    await hass.async_block_till_done()
    # No minimum rest, so only the valve wait keeps the boiler off.
    hass.config_entries.async_update_entry(loaded_entry, options=ACTIVE | {CONF_MIN_REST: 0})
    statuses = record_boiler_statuses(monkeypatch)
    await advance(hass, freezer, timedelta(seconds=30))
    await hass.config_entries.async_set_disabled_by(loaded_entry.entry_id, None)
    await hass.async_block_till_done()
    assert loaded_entry.runtime_data.active
    assert not statuses[0].startswith("On")
    assert hass.states.get(VALVE).state == "on"  # really open from now
    for _ in range(5):
        await advance(hass, freezer, timedelta(seconds=30))
        assert hass.states.get(BOILER).state == "off"
    assert not [status for status in statuses if status.startswith("On")]
    await advance(hass, freezer, timedelta(seconds=30))  # valve open for 3 min
    assert hass.states.get(BOILER).state == "on"


@pytest.mark.parametrize("entry_options", [ACTIVE])
async def test_failed_reload_turns_off_a_boiler_it_took_over(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, loaded_entry: MockConfigEntry
) -> None:
    await boiler_running(hass, freezer, loaded_entry)
    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        side_effect=RuntimeError("platform broke"),
    ):
        hass.config_entries.async_update_entry(loaded_entry, options=ACTIVE | {CONF_MARGIN: 0.5})
        await hass.async_block_till_done()
    assert loaded_entry.state is ConfigEntryState.SETUP_ERROR
    assert hass.states.get(BOILER).state == "off"


@pytest.mark.parametrize("entry_options", [ACTIVE])
async def test_changing_the_boiler_switch_rests_the_new_boiler(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, loaded_entry: MockConfigEntry
) -> None:
    await boiler_running(hass, freezer, loaded_entry)
    logbook = async_capture_events(hass, EVENT_LOGBOOK_ENTRY)
    result = await loaded_entry.start_reconfigure_flow(hass)
    await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BOILER: NEW_BOILER, CONF_CALENDAR: CALENDAR}
    )
    await hass.async_block_till_done()
    assert loaded_entry.runtime_data.boiler_entity == NEW_BOILER
    assert hass.states.get(BOILER).state == "off"
    assert released(logbook)
    assert hass.states.get(NEW_BOILER).state == "off"
    assert hass.states.get("sensor.makerspace_heating_boiler_status").state.startswith(
        "Off: resting"
    )
    await advance(hass, freezer, timedelta(minutes=4, seconds=30))
    assert hass.states.get(NEW_BOILER).state == "off"
    await advance(hass, freezer, timedelta(seconds=30))
    assert hass.states.get(NEW_BOILER).state == "on"
    assert hass.states.get(BOILER).state == "off"


@pytest.mark.parametrize("entry_options", [ACTIVE])
async def test_changing_a_valve_rests_the_boiler(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, loaded_entry: MockConfigEntry
) -> None:
    await boiler_running(hass, freezer, loaded_entry)
    result = await loaded_entry.start_subentry_reconfigure_flow(hass, ROOM_ID)
    await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**WORKSHOP, CONF_VALVE: SPARE_VALVE}
    )
    await hass.async_block_till_done()
    assert loaded_entry.runtime_data.rooms[ROOM_ID].valve == SPARE_VALVE
    assert hass.states.get(BOILER).state == "off"
    assert hass.states.get(SPARE_VALVE).state == "on"
    assert hass.states.get("sensor.makerspace_heating_boiler_status").state.startswith(
        "Off: resting"
    )


@pytest.mark.parametrize("entry_options", [ACTIVE])
async def test_release_waits_for_a_boiler_command_in_flight(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, controller: HeatingController
) -> None:
    controller.async_boost(ROOM_ID, None, "test")
    await hass.async_block_till_done()
    assert hass.states.get(VALVE).state == "on"
    gate = asyncio.Event()

    async def slow_turn_on(call: ServiceCall) -> None:
        await gate.wait()  # a Shelly slow to answer
        hass.states.async_set(call.data["entity_id"], "on")

    hass.services.async_register("input_boolean", "turn_on", slow_turn_on)
    freezer.tick(timedelta(minutes=3))
    controller.async_step()
    await asyncio.sleep(0)
    assert controller.decision.boiler_on
    shutdown = hass.async_create_task(
        controller.async_shutdown(release_boiler=True, handover=False)
    )
    for _ in range(10):
        await asyncio.sleep(0)
    gate.set()
    await shutdown
    await hass.async_block_till_done()
    assert hass.states.get(BOILER).state == "off"


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


@pytest.mark.parametrize("entry_options", [ACTIVE])
async def test_reload_keeps_timing_a_stuck_valve(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, loaded_entry: MockConfigEntry
) -> None:
    loaded_entry.runtime_data.async_boost(ROOM_ID, None, "test")
    await hass.async_block_till_done()

    async def stuck(call: ServiceCall) -> None:
        """The relay no longer answers."""

    hass.services.async_register("input_boolean", "turn_on", stuck)
    await hass.services.async_call(
        "input_boolean", "turn_off", {"entity_id": VALVE}, blocking=True
    )
    await advance(hass, freezer, timedelta(seconds=60))
    hass.config_entries.async_update_entry(loaded_entry, options=ACTIVE | {CONF_MARGIN: 0.5})
    await hass.async_block_till_done()
    assert loaded_entry.runtime_data.decision.rooms[ROOM_ID].calling
    await advance(hass, freezer, timedelta(seconds=30))  # 90 s without following commands
    room = loaded_entry.runtime_data.decision.rooms[ROOM_ID]
    assert room.status == "Fault: valve not responding"


async def test_frequent_updates_do_not_postpone_saving(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    controller: HeatingController,
    hass_storage: dict,
) -> None:
    key = f"{DOMAIN}.{controller.entry.entry_id}"
    assert key not in hass_storage
    written: list[int] = []
    for seconds in range(5, 65, 5):
        freezer.tick(timedelta(seconds=5))
        hass.states.async_set(TEMPERATURE, f"18.{seconds}", {"unit_of_measurement": "°C"})
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        if key in hass_storage:
            written.append(seconds)
            hass_storage.pop(key)
    assert written  # saved while the sensor kept reporting
    assert written[0] <= SAVE_DELAY + 5
    assert len(written) <= 60 // SAVE_DELAY  # and not on every step


def logged(logbook: list, name: str, start: str) -> dict:
    """The data of the one logbook entry for name whose message starts with start."""
    entries = [
        event.data
        for event in logbook
        if event.data["name"] == name and event.data["message"].startswith(start)
    ]
    assert len(entries) == 1, entries
    return entries[0]


@pytest.mark.parametrize("entry_options", [ACTIVE])
async def test_logbook_entries_link_to_entities(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, loaded_entry: MockConfigEntry
) -> None:
    logbook = async_capture_events(hass, EVENT_LOGBOOK_ENTRY)
    await boiler_running(hass, freezer, loaded_entry)
    assert logged(logbook, "Workshop", "boost until ")["entity_id"] == "sensor.workshop_status"
    assert (
        logged(logbook, "Boiler", "turned on ")["entity_id"]
        == "sensor.makerspace_heating_boiler_status"
    )
    await hass.services.async_call(
        "input_boolean", "turn_off", {"entity_id": VALVE}, blocking=True
    )
    await hass.async_block_till_done()
    assert logged(logbook, "Workshop valve", "was switched off by hand")["entity_id"] == VALVE
    hass.config_entries.async_update_entry(loaded_entry, options={CONF_CONTROL_MODE: MODE_SHADOW})
    await hass.async_block_till_done()
    assert logged(logbook, "Boiler", "turned off: ")["entity_id"] == BOILER


async def test_logbook_entry_without_its_entity_names_none(
    hass: HomeAssistant, controller: HeatingController
) -> None:
    logbook = async_capture_events(hass, EVENT_LOGBOOK_ENTRY)
    controller.async_boost(ROOM_ID, None, "test")  # no entity platforms here
    await hass.async_block_till_done()
    assert "entity_id" not in logged(logbook, "Workshop", "boost until ")


def store_holds(hass_storage: dict, entry: MockConfigEntry, data: object) -> None:
    key = f"{DOMAIN}.{entry.entry_id}"
    hass_storage[key] = {"version": STORE_VERSION, "minor_version": 1, "key": key, "data": data}


@pytest.mark.parametrize(
    "saved",
    [
        [1, 2],
        "nonsense",
        {"rooms": [1], "boiler": "x", "stats": 5, "last_step": {}, "events": "x", "handover": [1]},
        {"rooms": {ROOM_ID: {"boost": {"ends": "x", "length_seconds": 60}}}, "events": [5]},
        {  # a valid end, so the overflow itself is reached
            "rooms": {
                ROOM_ID: {"boost": {"ends": "2099-01-01T00:00:00+00:00", "length_seconds": 1e300}}
            }
        },
    ],
)
async def test_odd_saved_state_does_not_stop_setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    devices: None,
    calendar_calls: list,
    hass_storage: dict,
    saved: object,
) -> None:
    store_holds(hass_storage, entry, saved)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.workshop_status").state == "Shadow: Frost protection"


async def test_odd_handover_does_not_stop_setup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    devices: None,
    calendar_calls: list,
    hass_storage: dict,
) -> None:
    hass.data.setdefault(HANDOVER_KEY, {})[entry.entry_id] = {
        "at": dt_util.now().isoformat(),
        "mode": MODE_SHADOW,
        "boiler": BOILER,
        "valves": {ROOM_ID: VALVE},
        "temperatures": "nonsense",
        "calendar": CALENDAR,
        "calendar_failing_since": "garbage",
        "raised": [{"scope": ROOM_ID, "kind": "no_such_kind"}, 5],
        "engine": {"faults_started_at": 5, "warming": "x", "commanded": [1]},
    }
    store_holds(hass_storage, entry, {"boiler": {"on": True}})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data._raised == {}
    assert entry.entry_id not in hass.data[HANDOVER_KEY]  # used up


async def test_calendar_change_during_a_fetch_fetches_again(
    hass: HomeAssistant, controller: HeatingController, calendar_events: list[dict]
) -> None:
    gate = asyncio.Event()
    calls: list[ServiceCall] = []

    async def slow(call: ServiceCall) -> dict:
        calls.append(call)
        events = list(calendar_events)  # what the calendar held when asked
        if len(calls) == 1:
            await gate.wait()
        return {CALENDAR: {"events": events}}

    hass.services.async_register(
        "calendar", "get_events", slow, supports_response=SupportsResponse.ONLY
    )
    fetch = hass.async_create_task(controller.async_refresh_calendar())
    await asyncio.sleep(0)
    assert controller._fetching
    now = dt_util.now()
    calendar_events.append(
        {
            "start": (now - timedelta(minutes=5)).isoformat(),
            "end": (now + timedelta(hours=1)).isoformat(),
            "summary": "Pottery night #workshop",
        }
    )
    hass.states.async_set(CALENDAR, "on")  # the change arrives mid-fetch
    hass.states.async_set(CALENDAR, "on", {"message": "Pottery night"})
    await asyncio.sleep(0)
    gate.set()
    await fetch
    await hass.async_block_till_done()
    assert len(calls) == 2  # one more fetch for any number of changes
    assert controller.decision.rooms[ROOM_ID].source is Source.EVENT
    assert not controller._fetching


@pytest.mark.parametrize("entry_options", [ACTIVE])
async def test_changing_the_boiler_switch_keeps_the_held_valve_open(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, loaded_entry: MockConfigEntry
) -> None:
    await boiler_running(hass, freezer, loaded_entry)
    hass.states.async_set(TEMPERATURE, "25.0", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    assert loaded_entry.runtime_data.decision.boiler_status == "On: holding for minimum run time"
    result = await loaded_entry.start_reconfigure_flow(hass)
    await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BOILER: NEW_BOILER, CONF_CALENDAR: CALENDAR}
    )
    await hass.async_block_till_done()
    assert hass.states.get(BOILER).state == "off"  # the old boiler's pump overrun starts
    for _ in range(6):  # keep-open is 3 min
        assert hass.states.get(VALVE).state == "on"
        await advance(hass, freezer, timedelta(seconds=30))
    assert hass.states.get(VALVE).state == "off"
    assert hass.states.get(NEW_BOILER).state == "off"


@pytest.mark.parametrize("entry_options", [ACTIVE])
async def test_release_cancels_a_command_that_never_finishes(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, controller: HeatingController
) -> None:
    controller.async_boost(ROOM_ID, None, "test")
    await hass.async_block_till_done()
    started = asyncio.Event()

    async def hung_turn_on(call: ServiceCall) -> None:
        started.set()
        await asyncio.Event().wait()  # a Shelly that never answers
        hass.states.async_set(call.data["entity_id"], "on")

    hass.services.async_register("input_boolean", "turn_on", hung_turn_on)
    freezer.tick(timedelta(minutes=3))
    controller.async_step()
    await started.wait()
    (command,) = controller._commands
    try:
        shutdown = hass.async_create_task(
            controller.async_shutdown(release_boiler=True, handover=False)
        )
        await asyncio.sleep(0)
        assert not shutdown.done()  # waiting for the command
        freezer.tick(COMMAND_WAIT + timedelta(seconds=1))
        async_fire_time_changed(hass)
        await shutdown
        assert command.cancelled()
    finally:
        command.cancel()  # never leave it for teardown to wait on
    await hass.async_block_till_done()
    assert hass.states.get(BOILER).state == "off"


@pytest.mark.parametrize(
    ("entry_options", "boiler_after"), [(ACTIVE, "off"), ({CONF_CONTROL_MODE: MODE_SHADOW}, "on")]
)
async def test_a_controller_that_cannot_be_built_releases_the_boiler(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    loaded_entry: MockConfigEntry,
    entry_options: dict,
    boiler_after: str,
) -> None:
    if loaded_entry.runtime_data.active:
        await boiler_running(hass, freezer, loaded_entry)
    else:  # Shadow never switches: whatever runs the boiler is left alone
        await hass.services.async_call(
            "input_boolean", "turn_on", {"entity_id": BOILER}, blocking=True
        )
    with patch(
        "custom_components.makerspace_heating.HeatingController",
        side_effect=ValueError("odd room data"),
    ):
        hass.config_entries.async_update_entry(
            loaded_entry, options=entry_options | {CONF_MARGIN: 0.5}
        )
        await hass.async_block_till_done()
    assert loaded_entry.state is ConfigEntryState.SETUP_ERROR
    assert hass.states.get(BOILER).state == boiler_after
    assert loaded_entry.entry_id not in hass.data.get(HANDOVER_KEY, {})


@pytest.mark.parametrize("entry_options", [ACTIVE | {CONF_MIN_REST: 0}])
async def test_a_new_valve_gets_the_full_valve_wait(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, loaded_entry: MockConfigEntry
) -> None:
    """Valve tracking is not carried over to a room's new valve switch."""
    await boiler_running(hass, freezer, loaded_entry)
    await hass.services.async_call(  # the new relay happens to be on already
        "input_boolean", "turn_on", {"entity_id": SPARE_VALVE}, blocking=True
    )
    result = await loaded_entry.start_subentry_reconfigure_flow(hass, ROOM_ID)
    await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**WORKSHOP, CONF_VALVE: SPARE_VALVE}
    )
    await hass.async_block_till_done()
    engine = loaded_entry.runtime_data.engine
    assert engine.mismatch_since == {}
    for _ in range(5):
        assert hass.states.get(BOILER).state == "off"
        await advance(hass, freezer, timedelta(seconds=30))
    assert hass.states.get(BOILER).state == "off"
    await advance(hass, freezer, timedelta(seconds=30))  # watched open for 3 min
    assert hass.states.get(BOILER).state == "on"
