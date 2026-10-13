"""Tests for fault alerts: Repairs, notifications and logbook (spec §7.5)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.makerspace_heating.const import (
    CONF_NOTIFY_ACTIONS,
    CONF_NOTIFY_ENTITIES,
    DOMAIN,
    EVENT_LOGBOOK_ENTRY,
)
from custom_components.makerspace_heating.controller import HeatingController

from .conftest import ROOM_ID, TEMPERATURE

NOTIFY = {
    CONF_NOTIFY_ENTITIES: ["notify.admin"],
    CONF_NOTIFY_ACTIONS: ["notify.mobile_app_phone"],
}
ISSUE_ID = f"{ROOM_ID}_temp_unavailable"
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


async def test_shutdown_removes_issues(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, controller: HeatingController
) -> None:
    hass.states.async_set(TEMPERATURE, "unavailable")
    await hass.async_block_till_done()
    await advance(hass, freezer, timedelta(minutes=5))
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None
    await controller.async_shutdown()
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None
