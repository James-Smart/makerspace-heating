"""Tests for the makerspace_heating.boost action (spec §7.3)."""

from __future__ import annotations

from types import MappingProxyType

import pytest
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.makerspace_heating.const import (
    CONF_BUTTON,
    CONF_NAME,
    CONF_TAG,
    CONF_TEMPERATURE,
    CONF_VALVE,
    DOMAIN,
    SUBENTRY_ROOM,
)

from .conftest import ROOM_ID, SPARE_VALVE, WORKSHOP


def device_id(hass: HomeAssistant, entry: MockConfigEntry, identifier: str) -> str:
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, identifier), entry.entry_id
    )
    assert device is not None
    return device.id


async def boost(hass: HomeAssistant, data: dict) -> None:
    await hass.services.async_call(DOMAIN, "boost", data, blocking=True)


async def test_boost_with_duration(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    await boost(
        hass, {"device_id": device_id(hass, loaded_entry, ROOM_ID), "duration": {"minutes": 90}}
    )
    assert hass.states.get("select.workshop_boost_for").state == "1 h 30 min"
    assert (
        hass.states.get("sensor.workshop_status").attributes["boost_started_by"]
        == "Automation/script"
    )


async def test_boost_default_duration(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    await boost(hass, {"device_id": [device_id(hass, loaded_entry, ROOM_ID)]})
    assert hass.states.get("select.workshop_boost_for").state == "30 min"


async def test_boost_rejects_too_long(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    with pytest.raises(ServiceValidationError, match="at most 2 h"):
        await boost(
            hass, {"device_id": device_id(hass, loaded_entry, ROOM_ID), "duration": {"hours": 3}}
        )


async def test_boost_rejects_non_room_devices(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    hub = device_id(hass, loaded_entry, loaded_entry.entry_id)
    with pytest.raises(ServiceValidationError, match="not a Makerspace Heating room"):
        await boost(hass, {"device_id": hub})


async def test_boost_rejects_off_room(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.workshop_enabled"}, blocking=True
    )
    with pytest.raises(ServiceValidationError, match="Room is off"):
        await boost(hass, {"device_id": device_id(hass, loaded_entry, ROOM_ID)})


async def test_boost_is_all_or_nothing(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    hass.states.async_set("sensor.main_temperature", "18.0", {"unit_of_measurement": "°C"})
    main = ConfigSubentry(
        data=MappingProxyType(
            {key: value for key, value in WORKSHOP.items() if key != CONF_BUTTON}
            | {
                CONF_NAME: "Main Space",
                CONF_TAG: "main",
                CONF_VALVE: SPARE_VALVE,
                CONF_TEMPERATURE: "sensor.main_temperature",
            }
        ),
        subentry_type=SUBENTRY_ROOM,
        title="Main Space",
        unique_id=None,
    )
    hass.config_entries.async_add_subentry(loaded_entry, main)
    await hass.async_block_till_done()
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.main_space_enabled"}, blocking=True
    )
    rooms = [
        device_id(hass, loaded_entry, ROOM_ID),
        device_id(hass, loaded_entry, main.subentry_id),
    ]
    with pytest.raises(ServiceValidationError, match="Room is off"):
        await boost(hass, {"device_id": rooms})
    assert hass.states.get("sensor.workshop_boost_ends").state == "unknown"
    assert hass.states.get("sensor.workshop_boosts_today").state == "0"
