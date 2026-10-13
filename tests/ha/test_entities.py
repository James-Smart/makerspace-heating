"""Tests for the entities and the dashboard-facing behaviour (spec §7.1, §7.2)."""

from __future__ import annotations

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from custom_components.makerspace_heating.const import CONF_MAX_BOOST, DOMAIN

from .conftest import ROOM_ID


async def select(hass: HomeAssistant, option: str, context: Context | None = None) -> None:
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.workshop_boost_for", "option": option},
        blocking=True,
        context=context,
    )


async def test_entities_are_created(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    assert hass.states.get("sensor.workshop_status").state == "Shadow: Frost protection"
    assert (
        hass.states.get("sensor.makerspace_heating_boiler_status").state
        == "Shadow: Off: no rooms calling"
    )
    assert hass.states.get("sensor.workshop_target_temperature").state == "7.0"
    assert hass.states.get("sensor.workshop_boost_ends").state == "unknown"
    assert hass.states.get("sensor.workshop_boosts_today").state == "0"
    assert hass.states.get("sensor.workshop_heating_time_today").state == "0.0"
    assert hass.states.get("binary_sensor.workshop_heating").state == "off"
    assert hass.states.get("binary_sensor.workshop_problem").state == "off"
    assert hass.states.get("binary_sensor.makerspace_heating_boiler_demand").state == "off"
    assert hass.states.get("binary_sensor.makerspace_heating_heating_problem").state == "off"
    assert hass.states.get("switch.workshop_enabled").state == "on"
    assert hass.states.get("button.workshop_boost") is not None
    boost_for = hass.states.get("select.workshop_boost_for")
    assert boost_for.state == "Off"
    assert boost_for.attributes["options"] == ["Off", "30 min", "1 h", "1 h 30 min", "2 h"]
    assert (
        hass.states.get("sensor.makerspace_heating_upcoming_heating").state
        == "None in the next 24 h"
    )
    room_device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, ROOM_ID), loaded_entry.entry_id
    )
    assert room_device is not None and room_device.name == "Workshop"


async def test_boost_button_records_dashboard_user(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, hass_admin_user: MockUser
) -> None:
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.workshop_boost"},
        blocking=True,
        context=Context(user_id=hass_admin_user.id),
    )
    status = hass.states.get("sensor.workshop_status")
    assert status.state.startswith("Shadow: Boost until ")
    assert status.attributes["boost_started_by"] == f"Dashboard ({hass_admin_user.name})"
    assert status.attributes["source"] == "boost"
    assert hass.states.get("select.workshop_boost_for").state == "30 min"
    assert hass.states.get("sensor.workshop_boost_ends").state != "unknown"
    assert hass.states.get("binary_sensor.workshop_heating").state == "on"
    assert hass.states.get("sensor.workshop_boosts_today").state == "1"


async def test_boost_dropdown_starts_and_cancels(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await select(hass, "1 h")
    assert hass.states.get("select.workshop_boost_for").state == "1 h"
    assert (
        hass.states.get("sensor.workshop_status").attributes["boost_started_by"]
        == "Automation/script"
    )
    await select(hass, "Off")
    assert hass.states.get("select.workshop_boost_for").state == "Off"
    assert hass.states.get("sensor.workshop_status").state == "Shadow: Frost protection"


async def test_enabled_switch(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.workshop_enabled"}, blocking=True
    )
    assert hass.states.get("switch.workshop_enabled").state == "off"
    assert (
        hass.states.get("sensor.workshop_status").state
        == "Shadow: Off (frost protection only)"
    )


async def test_lowering_max_boost_keeps_running_boost(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await select(hass, "2 h")
    hass.config_entries.async_update_entry(loaded_entry, options={CONF_MAX_BOOST: 60})
    await hass.async_block_till_done()
    boost_for = hass.states.get("select.workshop_boost_for")
    assert boost_for.attributes["options"] == ["Off", "30 min", "1 h"]
    assert boost_for.state == "unknown"
    assert hass.states.get("sensor.workshop_status").state.startswith("Shadow: Boost until ")


async def test_removing_a_room_drops_its_entities_and_state(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await select(hass, "1 h")
    hass.config_entries.async_remove_subentry(loaded_entry, ROOM_ID)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.workshop_status") is None
    assert loaded_entry.runtime_data.rooms == {}
    assert hass.states.get("sensor.makerspace_heating_boiler_status") is not None


async def test_unload(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.workshop_status").state == "unavailable"
