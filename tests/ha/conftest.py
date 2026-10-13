"""Shared fixtures for tests that run inside Home Assistant."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.makerspace_heating.const import (
    CONF_BOILER,
    CONF_BOOST_TARGET,
    CONF_BUTTON,
    CONF_CALENDAR,
    CONF_EVENT_TARGET,
    CONF_FROST_TARGET,
    CONF_NAME,
    CONF_PREHEAT,
    CONF_TAG,
    CONF_TEMPERATURE,
    CONF_VALVE,
    DOMAIN,
    NAME,
    SUBENTRY_ROOM,
)
from custom_components.makerspace_heating.controller import HeatingController

BOILER = "input_boolean.boiler"
CALENDAR = "calendar.events"
VALVE = "input_boolean.workshop_valve"
TEMPERATURE = "sensor.workshop_temperature"
BUTTON = "event.workshop_button"
ROOM_ID = "01WORKSHOPROOM"

WORKSHOP: dict[str, Any] = {
    CONF_NAME: "Workshop",
    CONF_TAG: "workshop",
    CONF_VALVE: VALVE,
    CONF_TEMPERATURE: TEMPERATURE,
    CONF_BUTTON: BUTTON,
    CONF_BOOST_TARGET: 19.0,
    CONF_EVENT_TARGET: 20.0,
    CONF_FROST_TARGET: 7.0,
    CONF_PREHEAT: 30,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load custom_components/ in every test."""


@pytest.fixture
def entry_options() -> dict[str, Any]:
    """Options for the test entry. Override with pytest.mark.parametrize."""
    return {}


@pytest.fixture
def entry(hass: HomeAssistant, entry_options: dict[str, Any]) -> MockConfigEntry:
    """A config entry with one room (Workshop), added but not set up."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title=NAME,
        data={CONF_BOILER: BOILER, CONF_CALENDAR: CALENDAR},
        options=entry_options,
        subentries_data=[
            {
                "data": WORKSHOP,
                "subentry_id": ROOM_ID,
                "subentry_type": SUBENTRY_ROOM,
                "title": "Workshop",
                "unique_id": None,
            }
        ],
    )
    config_entry.add_to_hass(hass)
    return config_entry


@pytest.fixture
async def devices(hass: HomeAssistant) -> None:
    """Fake boiler, valve, temperature sensor, button and calendar."""
    assert await async_setup_component(
        hass,
        "input_boolean",
        {"input_boolean": {"boiler": {"name": "Boiler"}, "workshop_valve": {"name": "Valve"}}},
    )
    hass.states.async_set(CALENDAR, "off")
    hass.states.async_set(TEMPERATURE, "18.0", {"unit_of_measurement": "°C"})
    hass.states.async_set(BUTTON, "unknown", {"event_types": ["single_push"]})
    await hass.async_block_till_done()


@pytest.fixture
def calendar_events() -> list[dict[str, Any]]:
    """Events the fake calendar returns. Tests append to this list."""
    return []


@pytest.fixture
def calendar_calls(
    hass: HomeAssistant, calendar_events: list[dict[str, Any]]
) -> list[ServiceCall]:
    """A fake calendar.get_events action."""
    calls: list[ServiceCall] = []

    async def handle(call: ServiceCall) -> dict[str, Any]:
        calls.append(call)
        return {CALENDAR: {"events": calendar_events}}

    hass.services.async_register(
        "calendar", "get_events", handle, supports_response=SupportsResponse.ONLY
    )
    return calls


@pytest.fixture
async def controller(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    devices: None,
    calendar_calls: list[ServiceCall],
) -> AsyncGenerator[HeatingController]:
    """A running controller for the test entry (no entity platforms)."""
    heating = HeatingController(hass, entry)
    await heating.async_setup()
    await hass.async_block_till_done()
    yield heating
    await heating.async_shutdown()
