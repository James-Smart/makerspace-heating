"""Tests for the config flow, options flow and room sub-entries (spec §6)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.makerspace_heating.const import (
    CONF_BOILER,
    CONF_BOOST_TARGET,
    CONF_BUTTON,
    CONF_CALENDAR,
    CONF_CONTROL_MODE,
    CONF_DEFAULT_BOOST,
    CONF_EVENT_TARGET,
    CONF_FROST_TARGET,
    CONF_MAX_BOOST,
    CONF_NAME,
    CONF_NOTIFY_ACTIONS,
    CONF_PREHEAT,
    CONF_TAG,
    CONF_TEMPERATURE,
    CONF_VALVE,
    DOMAIN,
    MODE_ACTIVE,
    OPTION_DEFAULTS,
    SUBENTRY_ROOM,
)

from .conftest import BOILER, CALENDAR, ROOM_ID, VALVE, WORKSHOP

NEW_ROOM = {
    CONF_NAME: "Main Space",
    CONF_TAG: "#Main",
    CONF_VALVE: "input_boolean.main_valve",
    CONF_TEMPERATURE: "sensor.main_temperature",
    CONF_BOOST_TARGET: 19.0,
    CONF_EVENT_TARGET: 20.0,
    CONF_FROST_TARGET: 7.0,
    CONF_PREHEAT: 45,
}


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    with patch("custom_components.makerspace_heating.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BOILER: BOILER, CONF_CALENDAR: CALENDAR}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Makerspace Heating"
    assert result["data"] == {CONF_BOILER: BOILER, CONF_CALENDAR: CALENDAR}


async def test_only_one_entry_allowed(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_reconfigure_main_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BOILER: VALVE, CONF_CALENDAR: CALENDAR}
    )
    assert result["errors"] == {CONF_BOILER: "boiler_is_valve"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BOILER: "input_boolean.new_boiler", CONF_CALENDAR: CALENDAR}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_BOILER] == "input_boolean.new_boiler"


async def test_options_flow_saves(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**OPTION_DEFAULTS, CONF_CONTROL_MODE: MODE_ACTIVE, CONF_MAX_BOOST: 90},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_CONTROL_MODE] == MODE_ACTIVE
    assert entry.options[CONF_MAX_BOOST] == 90


@pytest.mark.parametrize(
    ("change", "field", "error"),
    [
        ({CONF_DEFAULT_BOOST: 150}, CONF_DEFAULT_BOOST, "default_above_max"),
        ({CONF_NOTIFY_ACTIONS: ["mobile_app_phone"]}, CONF_NOTIFY_ACTIONS, "invalid_notify_action"),
    ],
)
async def test_options_flow_validates(
    hass: HomeAssistant, entry: MockConfigEntry, change: dict, field: str, error: str
) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**OPTION_DEFAULTS, **change}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {field: error}


async def test_add_room(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ROOM), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], NEW_ROOM)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    added = next(sub for sub in entry.subentries.values() if sub.title == "Main Space")
    assert added.data[CONF_TAG] == "main"
    assert CONF_BUTTON not in added.data


@pytest.mark.parametrize(
    ("change", "field", "error"),
    [
        ({CONF_TAG: "workshop"}, CONF_TAG, "tag_in_use"),
        ({CONF_TAG: "main space"}, CONF_TAG, "invalid_tag"),
        ({CONF_VALVE: VALVE}, CONF_VALVE, "valve_in_use"),
        ({CONF_VALVE: BOILER}, CONF_VALVE, "valve_is_boiler"),
        ({CONF_FROST_TARGET: 21.0}, CONF_FROST_TARGET, "frost_above_targets"),
        ({CONF_NAME: "  "}, CONF_NAME, "name_required"),
    ],
)
async def test_room_validation(
    hass: HomeAssistant, entry: MockConfigEntry, change: dict, field: str, error: str
) -> None:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ROOM), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**NEW_ROOM, **change}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {field: error}


async def test_reconfigure_room(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    result = await entry.start_subentry_reconfigure_flow(hass, ROOM_ID)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**WORKSHOP, CONF_EVENT_TARGET: 21.0}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.subentries[ROOM_ID].data[CONF_EVENT_TARGET] == 21.0
