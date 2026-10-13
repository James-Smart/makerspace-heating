"""Tests for fault alerts: Repairs, notifications and logbook (spec §7.5)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.makerspace_heating.const import (
    CONF_CONTROL_MODE,
    CONF_MARGIN,
    CONF_NOTIFY_ACTIONS,
    CONF_NOTIFY_ENTITIES,
    DOMAIN,
    EVENT_LOGBOOK_ENTRY,
    HANDOVER_KEY,
    MODE_ACTIVE,
)
from custom_components.makerspace_heating.controller import HeatingController

from .conftest import BOILER, ROOM_ID, TEMPERATURE

ACTIVE = {CONF_CONTROL_MODE: MODE_ACTIVE}

NOTIFY = {
    CONF_NOTIFY_ENTITIES: ["notify.admin"],
    CONF_NOTIFY_ACTIONS: ["notify.mobile_app_phone"],
}
ISSUE_ID = f"{ROOM_ID}_temp_unavailable"
HUB_PROBLEM = "binary_sensor.makerspace_heating_heating_problem"
MESSAGE = "Workshop: temperature sensor unavailable"


async def advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


@pytest.mark.parametrize("entry_options", [NOTIFY])
async def test_fault_is_raised_after_delay_and_cleared(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, controller: HeatingController
) -> None:
    send_message = async_mock_service(hass, "notify", "send_message")
    mobile = async_mock_service(hass, "notify", "mobile_app_phone")
    logbook = async_capture_events(hass, EVENT_LOGBOOK_ENTRY)
    issues = ir.async_get(hass)

    hass.states.async_set(TEMPERATURE, "unavailable")
    await hass.async_block_till_done()
    await advance(hass, freezer, timedelta(minutes=2))
    assert issues.async_get_issue(DOMAIN, ISSUE_ID) is None

    await advance(hass, freezer, timedelta(minutes=3))
    issue = issues.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.translation_key == "fault"
    assert issue.translation_placeholders == {"message": MESSAGE}
    assert send_message[-1].data == {
        "entity_id": "notify.admin",
        "message": f"Heating fault: {MESSAGE}",
        "title": "Makerspace Heating",
    }
    assert mobile[-1].data == {
        "message": f"Heating fault: {MESSAGE}",
        "title": "Makerspace Heating",
    }
    assert controller.decision.rooms[ROOM_ID].problem

    hass.states.async_set(TEMPERATURE, "18.0", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, ISSUE_ID) is None
    assert send_message[-1].data["message"] == f"Heating fault cleared: {MESSAGE}"
    assert any(event.data["message"] == f"fault: {MESSAGE}" for event in logbook)
    assert any(event.data["message"] == f"fault cleared: {MESSAGE}" for event in logbook)


async def test_shutdown_without_handover_removes_issues(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, controller: HeatingController
) -> None:
    hass.states.async_set(TEMPERATURE, "unavailable")
    await hass.async_block_till_done()
    await advance(hass, freezer, timedelta(minutes=5))
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None
    await controller.async_shutdown(handover=True)
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None
    await controller.async_shutdown(handover=False)
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None


def messages(calls: list[ServiceCall], prefix: str) -> list[str]:
    """Notification texts that start with prefix ("Heating fault: " and so on)."""
    return [call.data["message"] for call in calls if call.data["message"].startswith(prefix)]


async def raise_fault(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, entity_id: str
) -> None:
    hass.states.async_set(entity_id, "unavailable")
    await hass.async_block_till_done()
    await advance(hass, freezer, timedelta(minutes=5))


@pytest.mark.parametrize("entry_options", [ACTIVE | NOTIFY])
@pytest.mark.parametrize(
    ("entity_id", "back", "issue_id", "problem"),
    [
        (TEMPERATURE, "18.0", ISSUE_ID, "binary_sensor.workshop_problem"),
        (BOILER, "off", "hub_boiler_unavailable", HUB_PROBLEM),
    ],
)
async def test_reload_does_not_repeat_a_fault(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    loaded_entry: MockConfigEntry,
    entity_id: str,
    back: str,
    issue_id: str,
    problem: str,
) -> None:
    send_message = async_mock_service(hass, "notify", "send_message")
    await raise_fault(hass, freezer, entity_id)
    assert hass.states.get(problem).state == "on"
    assert len(messages(send_message, "Heating fault: ")) == 1

    changes = async_capture_events(hass, EVENT_STATE_CHANGED)
    hass.config_entries.async_update_entry(
        loaded_entry, options=ACTIVE | NOTIFY | {CONF_MARGIN: 0.5}
    )
    await hass.async_block_till_done()
    assert loaded_entry.state is ConfigEntryState.LOADED
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    assert hass.states.get(problem).state == "on"
    assert not [
        event
        for event in changes
        if event.data["entity_id"] == problem and event.data["new_state"].state == "off"
    ]
    await advance(hass, freezer, timedelta(minutes=10))
    assert len(messages(send_message, "Heating fault: ")) == 1
    assert hass.states.get(problem).state == "on"

    hass.states.async_set(entity_id, back, {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    await advance(hass, freezer, timedelta(minutes=1))
    assert len(messages(send_message, "Heating fault cleared: ")) == 1
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert hass.states.get(problem).state == "off"


@pytest.mark.parametrize("entry_options", [NOTIFY])
async def test_fault_cleared_during_a_reload_is_notified_once(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, loaded_entry: MockConfigEntry
) -> None:
    send_message = async_mock_service(hass, "notify", "send_message")
    await raise_fault(hass, freezer, TEMPERATURE)
    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    hass.states.async_set(TEMPERATURE, "18.0", {"unit_of_measurement": "°C"})
    assert await hass.config_entries.async_setup(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert messages(send_message, "Heating fault cleared: ") == [
        f"Heating fault cleared: {MESSAGE}"
    ]
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None
    await advance(hass, freezer, timedelta(minutes=10))
    assert len(messages(send_message, "Heating fault")) == 2


@pytest.mark.parametrize("entry_options", [NOTIFY])
async def test_removed_room_takes_its_issues_quietly(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, loaded_entry: MockConfigEntry
) -> None:
    send_message = async_mock_service(hass, "notify", "send_message")
    await raise_fault(hass, freezer, TEMPERATURE)
    hass.config_entries.async_remove_subentry(loaded_entry, ROOM_ID)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None
    assert messages(send_message, "Heating fault cleared: ") == []
    assert loaded_entry.runtime_data._raised == {}


async def test_restart_deletes_leftover_issues(
    hass: HomeAssistant, entry: MockConfigEntry, devices: None, calendar_calls: list
) -> None:
    for issue_id in (ISSUE_ID, "gone_room_temp_unavailable"):
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="fault",
            translation_placeholders={"message": "left behind"},
        )
    ir.async_create_issue(
        hass,
        "other_domain",
        "keep_me",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="other",
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert [key for key in ir.async_get(hass).issues if key[0] == DOMAIN] == []
    assert ir.async_get(hass).async_get_issue("other_domain", "keep_me") is not None


async def test_removing_the_entry_deletes_its_issues(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, loaded_entry: MockConfigEntry
) -> None:
    await raise_fault(hass, freezer, TEMPERATURE)
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None
    await hass.config_entries.async_remove(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert [key for key in ir.async_get(hass).issues if key[0] == DOMAIN] == []


async def test_fault_logbook_entries_link_to_entities(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, loaded_entry: MockConfigEntry
) -> None:
    logbook = async_capture_events(hass, EVENT_LOGBOOK_ENTRY)
    await raise_fault(hass, freezer, TEMPERATURE)
    await raise_fault(hass, freezer, BOILER)
    hass.states.async_set(TEMPERATURE, "18.0", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    linked = {
        event.data["message"]: event.data.get("entity_id")
        for event in logbook
        if event.data["name"] == "Makerspace Heating"
    }
    assert linked == {
        f"fault: {MESSAGE}": "sensor.workshop_status",
        "fault: Boiler switch unavailable": HUB_PROBLEM,
        f"fault cleared: {MESSAGE}": "sensor.workshop_status",
    }


@pytest.mark.parametrize("entry_options", [NOTIFY])
async def test_reload_does_not_repeat_a_calendar_fault(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, loaded_entry: MockConfigEntry
) -> None:
    send_message = async_mock_service(hass, "notify", "send_message")

    async def broken(call: ServiceCall) -> dict:
        raise HomeAssistantError("CalDAV server said no")

    hass.services.async_register(
        "calendar", "get_events", broken, supports_response=SupportsResponse.ONLY
    )
    for _ in range(7):
        await advance(hass, freezer, timedelta(minutes=5))
    assert hass.states.get(HUB_PROBLEM).state == "on"
    hass.config_entries.async_update_entry(loaded_entry, options=NOTIFY | {CONF_MARGIN: 0.5})
    await hass.async_block_till_done()
    await advance(hass, freezer, timedelta(minutes=5))
    assert hass.states.get(HUB_PROBLEM).state == "on"
    assert [call.data["message"] for call in send_message] == [
        "Heating fault: Calendar could not be read for over 30 min"
    ]


@pytest.mark.parametrize("entry_options", [NOTIFY])
async def test_disabling_never_hands_over(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, loaded_entry: MockConfigEntry
) -> None:
    await raise_fault(hass, freezer, TEMPERATURE)
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None
    await hass.config_entries.async_set_disabled_by(
        loaded_entry.entry_id, ConfigEntryDisabler.USER
    )
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None
    assert loaded_entry.entry_id not in hass.data.get(HANDOVER_KEY, {})


@pytest.mark.parametrize("entry_options", [NOTIFY])
async def test_going_active_does_not_repeat_a_fault(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, loaded_entry: MockConfigEntry
) -> None:
    send_message = async_mock_service(hass, "notify", "send_message")
    await raise_fault(hass, freezer, TEMPERATURE)  # raised while in Shadow
    hass.config_entries.async_update_entry(loaded_entry, options=NOTIFY | ACTIVE)
    await hass.async_block_till_done()
    assert loaded_entry.runtime_data.active
    await advance(hass, freezer, timedelta(minutes=10))
    assert messages(send_message, "Heating fault") == [f"Heating fault: {MESSAGE}"]
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None
